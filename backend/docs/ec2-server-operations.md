# EC2 server operations record

Last checked: 2026-11-02. This records the live server setup and the changes made during deployment. It contains no
connection strings, passwords, Clerk keys, provider tokens, or private-key material. For the general repeatable
deployment procedure, see [the EC2 and Nginx deployment guide](ec2-nginx-deployment.md).

## Host and public URLs

- EC2 public IPv4: look up the current address in the AWS EC2 console or DNS before changing records. Keep the
  numeric address in the private deployment inventory, not in the code repository; use an Elastic IP if DNS needs a
  stable target.
- Hostnames: `youradds.co` and `www.youradds.co`.
- Nginx accepts public HTTP and HTTPS. HTTP redirects to HTTPS; Uvicorn is bound to loopback only.
- App health: `https://youradds.co/api/health` returned `{"ok":true,"ai":false}` during the last check. The `ai`
  field reports that AI is unavailable in the running app.

## Installed application and persistent state

- App checkout/runtime: `/opt/yourads/backend`.
- Python environment: `/opt/yourads/backend/.venv`.
- The `yourads` systemd service runs as the `yourads` user and group, from `/opt/yourads/backend`, with one Uvicorn
  worker on `127.0.0.1:8000`.
- Service state: active and enabled at boot.
- Persistent state directory: `/var/lib/yourads`, mode `0750`, owned by `yourads:yourads`.
- The unit sets `YOURADS_SECRET_KEY_FILE=/var/lib/yourads/.secret_key` and
  `YOURADS_CREATIVES=/var/lib/yourads/creatives`. Keep the encryption key and any generated creative/video/job files
  backed up with the database; do not replace the key after encrypted provider credentials have been saved.
- The unit has systemd sandboxing enabled, including `ProtectSystem=strict` and `ProtectHome=true`.

The separate clone formerly at `/home/ubuntu/yourads` was removed after confirming that the running service uses
`/opt/yourads/backend` and that the clone held no database or media data. The operator's environment file was
preserved at `/home/ubuntu/yourads.env` with mode `0600`, owner `ubuntu:ubuntu`. It is not read by the running service.
The runtime copy is `/opt/yourads/backend/.env`, mode `0640`, owner `root:yourads`.

## PostgreSQL

- The app uses the managed Supabase PostgreSQL project, database `postgres`, PostgreSQL 17.11, through its shared
  pooler on port `6543` (transaction mode). Supabase uses port `5432` for shared session mode; see
  [Supabase connection modes](https://supabase.com/docs/guides/database/connecting-to-postgres).
- Client TLS uses `sslmode=verify-full` with the Supabase project CA at
  `/usr/local/share/ca-certificates/supabase-prod-ca-2021.crt`. The certificate is public, mode `0644`, owner
  `root:yourads`; it is readable by the sandboxed service account. Do not use `sslrootcert=system` for this Supabase
  endpoint.
- `/etc/yourads/migration.env` is mode `0600`, owner `root:root`, and contains only `MIGRATION_DATABASE_URL`. This
  migration credential is not present in the runtime `.env` and is not passed to the long-running service.
- Alembic was run against the production database and verified at revision `0002_clerk_identity`.
- Created application tables in the `yourads` schema: `campaigns`, `creatives`, `credentials`, `leads`, `messages`,
  `metrics`, `sessions`, `settings`, `users`, and `videos`. The schema migration created tables only; it did not
  import SQLite records.
- **Outstanding privilege reduction:** the runtime credential needs to be moved to a separate least-privilege role;
  database account names and grants for the live project belong in the private deployment inventory. Preserve a
  separate administrative credential for Alembic and keep it out of the app runtime environment.
- **Pooler follow-up:** the checked-in database migration guide recommends shared session mode for this persistent app
  because account provisioning uses `pg_advisory_xact_lock`; the current URLs use port `6543` (transaction mode).
  Verify the project's connection settings and move both URLs to the shared session pooler on port `5432` before
  treating the deployment as fully aligned with that guide.

## Nginx and HTTPS

- Active site: `/etc/nginx/sites-available/yourads`, enabled at `/etc/nginx/sites-enabled/yourads`.
- The active config matches the repository's `deploy/nginx/yourads-https.conf`.
- Port `80` returns a `301` redirect to HTTPS. Port `443` serves both configured hostnames and proxies to
  `http://127.0.0.1:8000`.
- The proxy forwards the host, client address, and HTTPS scheme. The site allows 12 MiB request bodies and five-minute
  proxy timeouts for creative/video requests.
- Response headers include HSTS (`max-age=31536000`), `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, and `X-Frame-Options: DENY`.
- The HTTP-only bootstrap template is `deploy/nginx/yourads-http.conf`; it is not the active configuration once HTTPS
  has been installed.

Let's Encrypt certificate:

- Certbot certificate name: `youradds.co`.
- Covers `youradds.co` and `www.youradds.co`.
- Certificate and private-key paths are `/etc/letsencrypt/live/youradds.co/fullchain.pem` and
  `/etc/letsencrypt/live/youradds.co/privkey.pem`.
- At last check it was valid through 2027-01-30 15:17 UTC.
- Automatic renewal is enabled by the `snap.certbot.renew.timer`. The deploy hook
  `/etc/letsencrypt/renewal-hooks/deploy/yourads-reload-nginx` reloads Nginx after renewal.

## Useful operations

```sh
sudo systemctl status yourads
sudo journalctl -u yourads -n 100 --no-pager
sudo nginx -t
sudo systemctl reload nginx
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS https://youradds.co/api/health
sudo certbot certificates
systemctl list-timers --all | grep certbot
```

For a future migration, run Alembic from `/opt/yourads/backend` using the root-only migration environment file; do
not start the app with the migration URL. After changing application files or runtime settings, restart the service
with `sudo systemctl restart yourads` and verify the health endpoint. No automatic application-code deployment is
configured.

## Secret-file housekeeping

Environment files contain production database credentials and application/provider secrets. Keep them out of Git,
terminal output, and support logs. Root-only `.env` backups may remain under `/opt/yourads/backend`; review and remove
obsolete copies after confirming the active runtime and migration configurations. The CA certificate is public and is
not a secret. The operator copy `/home/ubuntu/yourads.env` includes the migration URL; do not copy that file directly
over the app's runtime `.env`.
