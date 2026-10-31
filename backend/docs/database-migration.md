# SQLite to Supabase PostgreSQL

## What the application expects

The backend uses synchronous SQLAlchemy 2.0 ORM sessions and Psycopg 3. The application-owned tables and Alembic
history live in the dedicated `yourads` schema. The schema is not intended for browser/Data API access; application
authorization and owner filters remain in FastAPI. The browser continues to use the existing API and session cookie.

Resolved versions in the implementation environment on 2026-10-25: SQLAlchemy 2.0.55, Alembic 1.20.1, Psycopg
3.3.6, FastAPI 0.162.0, and HTTPX 0.28.1. Version constraints are in `requirements.txt`; deployments should install
from the deployment's managed environment and record the resolved set there.

`DATABASE_URL` is the non-owner runtime login. It must have PostgreSQL DML and sequence access only. `MIGRATION_DATABASE_URL`
is a separate administrative connection used for Alembic and the offline importer. Neither URL is a Supabase API key.
Never commit connection URLs or credentials. Use a deployment secret store or a private ignored `.env` file. PostgreSQL
URLs accept `postgres://`, `postgresql://`, and `postgresql+psycopg://`; the app normalizes the first two. For remote
connections TLS is mandatory. Prefer `sslmode=verify-full` with the server CA certificate from the project; the
session pooler is the IPv4 choice for a persistent backend when direct IPv6 is unavailable. Do not use the transaction
pooler for this deployment because first-user signup uses transaction-scoped advisory locking.

The application-side pool starts at five connections with five overflow connections, a 15-second pool timeout,
10-second connection timeout, and pre-ping. Run one application process initially. The in-process video recovery and
WhatsApp follow-up schedulers are not distributed worker locks.

Keep the existing `YOURADS_SECRET_KEY` (or key file) unchanged. Creative and video files, active job data, and provider
request metadata are not moved into Postgres. Persist `YOURADS_CREATIVES` and its sibling `videos` and `jobs` directories
at the same paths after deployment. A database-only restore is incomplete without the exact Fernet key and media/job files.

## Database setup

1. Create a fresh Supabase project/database for this application. Inspect the actual PostgreSQL major version and
   network capabilities. Copy the connection string from Dashboard → Connect. Use a direct endpoint from an IPv6-capable
   persistent host, or a session pooler from IPv4-only hosting. Keep the dashboard URL private.
2. In Supabase API settings, ensure `yourads` is not in **Exposed schemas**. Verify the `anon` and `authenticated` roles
   have no `USAGE` on `yourads` and no table/sequence privileges. The migration revokes these privileges if those roles
   exist, but the exposure setting is a separate control.
3. Provision a dedicated non-owner runtime database role out-of-band. Keep its password in the deployment secret store.
   Apply these grants as the database administrator, replacing only role identifiers with the provisioned names:

   ```sql
   GRANT USAGE ON SCHEMA yourads TO yourads_runtime;
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA yourads TO yourads_runtime;
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA yourads TO yourads_runtime;
   GRANT SELECT ON yourads.alembic_version TO yourads_runtime;
   ALTER DEFAULT PRIVILEGES IN SCHEMA yourads
     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO yourads_runtime;
   ALTER DEFAULT PRIVILEGES IN SCHEMA yourads
     GRANT USAGE, SELECT ON SEQUENCES TO yourads_runtime;
   ```

   Apply default privileges for the role that will own future migration objects. Do not grant `CREATE` on the schema,
   ownership, `TRUNCATE`, or access to `public` application tables. The runtime role cannot apply Alembic changes.
4. Set `MIGRATION_DATABASE_URL` to the administrative project connection and run from `backend/`:

   ```sh
   python -m alembic upgrade head
   python -m alembic upgrade head
   python -m alembic current
   python -m alembic check
   ```

   The first migration creates only `yourads` objects. Startup checks the Alembic head and refuses to start against a
   missing or older schema. Migrations are a separate deployment step; application startup does not run DDL.
5. Set `DATABASE_URL` to the runtime role URL and verify it can connect, read `yourads.alembic_version`, and perform
   DML/sequence operations while DDL and Data API roles remain denied. Run the integration suite against a separate
   disposable Postgres database of the same major version, never against a linked production project.

`backend/.env.example` shows names only. For Supabase, retain the exact endpoint host and username from Dashboard →
Connect, URL-encode password characters, and add the SSL parameters without changing the connection mode.

## Disposable PostgreSQL integration suite

Install `requirements.txt` in an isolated environment, create a disposable local PostgreSQL database, and set only its
URL in `TEST_DATABASE_URL`. Set `YOURADS_TEST_DATABASE_DISPOSABLE=1` to enable tests that drop and recreate the
application schema. The test setup overrides `DATABASE_URL` and `MIGRATION_DATABASE_URL` with that test URL before
importing application modules, disables `.env` loading, generates a test-only encryption key, and uses temporary media.
Never use a production/runtime URL for this variable.

```sh
export TEST_DATABASE_URL='postgresql+psycopg://.../yourads_test'
export YOURADS_TEST_DATABASE_DISPOSABLE=1
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

With either variable missing, test setup must fail clearly rather than skip or use another database. Tests use stdlib
`unittest`; Postgres integration cases are required and are not silently skipped.

## Offline SQLite import

The importer is intentionally separate from app startup and provider code. It opens `--source` with SQLite `mode=ro`,
requires the full account-era schema including all seven video job columns, and validates source integrity, FKs, field
types, unique keys, account ownership, timestamps, metric days, finite numbers, and decryptability of stored credentials.
It refuses unexpected columns, old/pre-account schemas, absent/wrong Fernet keys when ciphertext exists, orphan rows,
cross-owner campaign references, nonempty targets, and source files larger than 1 GiB. It never repairs rows or emits row
values, keys, emails, phone numbers, hashes, or ciphertext.

The command requires `MIGRATION_DATABASE_URL` and the database at Alembic head:

```sh
python -m app.migrate_sqlite --help
python -m app.migrate_sqlite --source /private/path/snapshot.sqlite --check
python -m app.migrate_sqlite --source /private/path/snapshot.sqlite --import
python -m app.migrate_sqlite --source /private/path/snapshot.sqlite --verify
```

`--check` and `--verify` only read. `--import` requires every application table to be empty, copies explicit columns in
one target transaction in bounded batches, and validates all ten counts and canonical digests before commit. Identity
sequences are reset from imported maxima only after row validation. PostgreSQL sequence updates can survive a transaction
rollback; a failed import may leave harmless sequence gaps. The importer does not truncate, overwrite, merge, resume
partially, or dual-write. Digests and outputs are aggregate implementation evidence; protect any retained reports.

An import fixture is not a cutover rehearsal. Rehearse with a representative private copy, same key, target Postgres
major version, media/jobs, and runtime-role permissions before scheduling production downtime.

## Production rehearsal and cutover

1. Provision the target and role; verify schema exposure, grants, PostgreSQL major version, network mode, TLS, Alembic
   head/check, disposable integration results, and the runtime role's DML/DDL boundaries.
2. Rehearse using a representative private SQLite copy and the existing Fernet key. Record the aggregate table counts,
   command durations, parity result, generated-ID checks, login/session checks, and runtime-role checks. Stop if any
   check fails; do not repair or reassign data during the migration.
3. Schedule a maintenance window and stop every writer: HTTP mutations, WhatsApp webhook intake and delivery, follow-up
   loop, video background jobs, and all application processes. Drain or pause paid video jobs safely, retaining provider
   request/status URLs so they cannot be submitted twice. Decide how webhook deliveries will be buffered/retried.
4. Use SQLite's backup API to produce a private snapshot that includes a consistent view of WAL state. Back up the exact
   Fernet key and complete creatives, videos, and jobs directories. Keep backups out of Git and protect their permissions.
   Check the source integrity and use this snapshot path for each importer command.
5. Run `--check`, then `--import`, then `--verify` while the app is stopped. Abort on any nonzero result. Do not run
   smoke-test writes on the imported dataset before final parity. If generated-ID smoke tests are needed, perform them
   in rehearsal or delete only an explicitly identified test row after recording the result.
6. Configure the application with the runtime database URL, original Fernet key, and persistent media paths. With public
   writes, webhooks, and workers still closed, verify existing login/session continuity, dashboard totals, credential
   decryptability and masking, owner isolation, creative/video retrieval, settings, and mocked/offline job recovery.
7. Resume one application process and reopen writes, webhooks, and workers only after go/no-go acceptance. Monitor
   connection usage, HTTP errors, session failures, webhook retries, and provider job recovery. Keep the deployment
   single-process until worker ownership is designed separately.
8. Before accepting new writes, rollback means stop the new app, keep Postgres intact for diagnosis, restore the prior
   application/config pointing at untouched SQLite and its original key/media, then reopen traffic. Once Postgres writes
   have been accepted, switching to the old SQLite snapshot loses those writes. Stop writes and use a separately reviewed
   delta-recovery procedure instead. A schema downgrade is not a production data rollback.

Production cutover has not been run by this code change. The source database, Supabase project, credentials, deployment
host, and operator approval window were not available in the implementation workspace.
# Clerk identity compatibility

The current schema head is `0002_clerk_identity`, which adds nullable `users.clerk_user_id` and makes `users.password_hash` nullable. The legacy SQLite importer deliberately validates, copies and compares only its explicit pre-Clerk column projection; it writes no Clerk identity and does not treat imported session rows as valid authentication. Imported local accounts require an explicit operator mapping after Clerk ownership is verified. The importer checks the Alembic graph's current head instead of hard-coding `0001_postgres`.

