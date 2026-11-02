# EC2 and Nginx deployment

This is a single-instance deployment starting point for an Ubuntu LTS EC2 host. PostgreSQL remains on the existing
managed Postgres service; do not install or expose Postgres on the web instance. Nginx terminates HTTPS and proxies to
one Uvicorn process bound to `127.0.0.1:8000`. The frontend is served by FastAPI.

```text
Browser ── HTTPS ──> Nginx :443 ── HTTP loopback ──> Uvicorn :8000 ── TLS ──> managed PostgreSQL
                                  EC2 EBS: /var/lib/yourads
```

The one-process limit is intentional: video recovery and WhatsApp follow-ups currently use in-process state and loops.
The app must keep its Fernet key and creative, video, and active-job files on persistent storage. A database backup
without these files and the original encryption key is incomplete. This setup is a deployable baseline, not a claim
that the application has passed a production security audit.

## Before making it public

- Finish and record live Clerk setup: production instance, HTTPS application origin, Google OAuth acceptance, exact
  JWT issuer and authorized party, and production keys. Set `CLERK_AUTHORIZED_PARTIES` to the exact public origin,
  such as `https://ads.example.com`; do not leave the localhost value. Follow [the Clerk runbook](clerk-auth.md).
- Replace the operator, jurisdiction, and contact placeholders in both legal pages, then have the operator approve
  the final text.
- Decide whether public signup should remain enabled. Set `ALLOW_SIGNUP=false` until ready if signup should be closed.
- Provision the PostgreSQL runtime role separately from the owner/migration role. Follow the database runbook and
  verify the remote URL uses TLS.
- Confirm a verified backup/restore plan for Postgres, `/var/lib/yourads`, and the original Fernet key.
- Review real ad-platform accounts, webhook URLs, usage limits, and API billing before enabling live integrations.

The repository README currently labels the app unaudited. Do not treat “starts successfully behind Nginx” as a security
review or a production launch approval.

## EC2, DNS, and ports

1. Use a supported Ubuntu LTS AMI. Attach persistent encrypted EBS storage sized for creative and video output; monitor
   disk use. A small instance can run the app, but image processing and video compositing use memory and CPU, so choose
   capacity based on the expected workload and monitor before increasing user access.
2. Assign an Elastic IP and point the app hostname's DNS `A` record to it. Add an `AAAA` record only if IPv6 is enabled
   and reachable on the instance and security group.
3. Allow inbound TCP 80 and 443 for the public web. Allow SSH only from an administrator's fixed IP, or use AWS Systems
   Manager Session Manager and keep SSH closed. Do not add inbound port 8000 or a database port to this instance's
   security group. AWS's [security-group guidance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/security-group-rules-reference.html)
   describes the public HTTP/HTTPS rules and the restricted SSH source.

## Install the app and persistent state

Use a reviewed release or commit, not a developer working tree. The paths below match
[`deploy/systemd/yourads.service`](../../deploy/systemd/yourads.service):

```sh
sudo useradd --system --user-group --home-dir /var/lib/yourads --create-home --shell /usr/sbin/nologin yourads
sudo install -d -o root -g yourads -m 0750 /opt/yourads
sudo install -d -o yourads -g yourads -m 0750 /var/lib/yourads
```

Install the reviewed repository under `/opt/yourads`, with the code and virtual environment readable/executable by
`yourads` but not writable by the service account. Create its Python virtual environment and install
`backend/requirements.txt` using the Python version selected for the server. The requirements are range constrained,
not fully locked, so record the resolved package set for the deployed release.

For Supabase PostgreSQL, download the project's CA certificate from **Database Settings → SSL Configuration** in the
Supabase dashboard. Install it at a path the sandboxed service account can traverse and read:

```sh
sudo install -o root -g yourads -m 0644 /path/to/prod-ca-2021.crt \
  /usr/local/share/ca-certificates/supabase-prod-ca-2021.crt
```

Use `sslmode=verify-full` and this certificate path in both the runtime `DATABASE_URL` and the one-off
`MIGRATION_DATABASE_URL`. `verify-full` checks the CA chain and the database hostname. Do not use `sslrootcert=system`
for Supabase: its database certificate is signed by Supabase's CA, which is not in the operating system trust store.

Create `/opt/yourads/backend/.env` as a root-owned file readable by the `yourads` group (mode `0640`). The app loads
this standard dotenv file at startup. It should contain runtime settings only:

```ini
DATABASE_URL=postgresql+psycopg://RUNTIME_USER:URL_ENCODED_PASSWORD@DB_HOST:5432/DB_NAME?sslmode=verify-full&sslrootcert=/usr/local/share/ca-certificates/supabase-prod-ca-2021.crt
CLERK_PUBLISHABLE_KEY=pk_live_REPLACE_ME
CLERK_SECRET_KEY=sk_live_REPLACE_ME
CLERK_JWT_KEY='-----BEGIN PUBLIC KEY-----
REPLACE_WITH_THE_COMPLETE_CLERK_JWT_PUBLIC_KEY
-----END PUBLIC KEY-----'
CLERK_ISSUER=https://REPLACE_WITH_EXACT_CLERK_ISSUER
CLERK_AUTHORIZED_PARTIES=https://ads.example.com
ALLOW_SIGNUP=false
ANTHROPIC_API_KEY=
```

Create and edit the file without putting secrets in shell history:

```sh
sudo install -o root -g yourads -m 0640 /dev/null /opt/yourads/backend/.env
sudoedit /opt/yourads/backend/.env
```

Use the real production values. Do not commit this file. Keep `MIGRATION_DATABASE_URL` out of the runtime `.env`;
supply it only to the one-off migration step below. The service sets `YOURADS_SECRET_KEY_FILE` and `YOURADS_CREATIVES`
to persistent paths. Back up the generated `.secret_key` and never replace it after encrypted credentials have been
saved.

Install and enable the service:

```sh
sudo install -o root -g root -m 0644 deploy/systemd/yourads.service /etc/systemd/system/yourads.service
sudo systemctl daemon-reload
```

Before starting it, apply migrations once with the separate migration role. One approach is a root-owned
`/etc/yourads/migration.env` containing only `MIGRATION_DATABASE_URL`, mode `0600`, then run. Its URL should also include
`sslmode=verify-full&sslrootcert=/usr/local/share/ca-certificates/supabase-prod-ca-2021.crt`:

```sh
sudo systemd-run --wait --collect \
  --property=WorkingDirectory=/opt/yourads/backend \
  --property=EnvironmentFile=/etc/yourads/migration.env \
  --setenv=YOURADS_SKIP_DOTENV=1 \
  /opt/yourads/backend/.venv/bin/python -m alembic upgrade head
```

The application startup checks the migration head but does not apply migrations. Do not use `backend/run-local.sh` on
the server: it is a developer launcher and runs Alembic on every start. Remove the migration environment file after
the migration and keep the owner credential outside the long-running app service.

Then start and inspect the service:

```sh
sudo systemctl enable --now yourads
sudo systemctl status yourads
sudo journalctl -u yourads -n 100 --no-pager
curl -fsS http://127.0.0.1:8000/api/auth/config
```

The last endpoint returns the public Clerk key and callback allowlist, not secret keys. Confirm the response has the
production Clerk configuration before wiring public traffic.

## Nginx and TLS

The repository has separate HTTP and HTTPS site files. The EC2 instance uses
[`deploy/nginx/yourads-http.conf`](../../deploy/nginx/yourads-http.conf) for `youradds.co` and `www.youradds.co`.
It listens on port 80 and proxies to Uvicorn on `127.0.0.1:8000`. Requests return `502` until the app is running.

To install or restore the current HTTP site:

```sh
sudo install -o root -g root -m 0644 deploy/nginx/yourads-http.conf /etc/nginx/sites-available/yourads
sudo ln -sfn /etc/nginx/sites-available/yourads /etc/nginx/sites-enabled/yourads
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Install Nginx and Certbot using the [official Certbot instructions](https://certbot.eff.org/instructions).
With both DNS names pointing to the instance and inbound ports 80 and 443 allowed, obtain a certificate for the apex
and `www` hostnames using Certbot's Nginx integration. Certbot's Snap timer checks for renewal automatically. A deploy
hook at [`deploy/certbot/reload-nginx`](../../deploy/certbot/reload-nginx) reloads Nginx after successful renewals.
Then install
[`deploy/nginx/yourads-https.conf`](../../deploy/nginx/yourads-https.conf). This HTTPS site redirects HTTP to HTTPS
and serves both names on port 443. Validate and reload after installing it with:

```sh
sudo install -o root -g root -m 0644 deploy/nginx/yourads-https.conf /etc/nginx/sites-available/yourads
sudo nginx -t
sudo systemctl reload nginx
```

Both files set the forwarded scheme and client address explicitly, permit 12 MiB JSON bodies for 8 MiB base64 image
uploads, and give long-running creative requests up to five minutes. Nginx's
[`proxy_set_header`](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header) controls the headers
sent upstream. Keep Uvicorn private on loopback and do not trust arbitrary forwarded headers from the internet.

Verify Certbot renewal is enabled for the chosen installation method. After HTTPS works, test sign-up, sign-in,
Google callback, email verification and reset, sign-out, profile updates, image upload, creative generation, downloads,
WhatsApp callbacks, and a backup restore in a non-production instance before opening public access.

## Release and operations

For each reviewed release, install dependencies and run the Alembic migration as a separate deployment step, then
restart `yourads`. Keep one worker. Back up Postgres and the persistent state directory together; record the exact
release, Python and dependency versions, Clerk instance, and secret-key backup location. Watch systemd logs, Nginx
access/error logs, disk, memory, Postgres connections, and external AI/provider spend.

This single EC2 host is a first live baseline, not highly available. Its local media and in-process jobs are not shared
or coordinated across multiple instances. A future scale-out should move media/jobs to shared durable services and
move scheduled/background work to a single coordinated worker before adding application replicas.
