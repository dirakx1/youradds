# Clerk authentication operations

The app owns every sign-in, sign-up, verification, recovery, account and logout screen. Clerk owns password verification, OAuth, session creation and token refresh. No Clerk UI bundle, prebuilt component or Account Portal is part of this integration.

## SDK contract

- Browser SDK: `@clerk/clerk-js` 6.40.1, loaded from the Frontend API hostname encoded in `CLERK_PUBLISHABLE_KEY`; the page loads only `clerk.browser.js`.
- Browser resource API: pinned `@clerk/clerk-js` 6.40.1 public legacy `SignIn`/`SignUp` resource methods (`create`, factor prepare/attempt, email verification, `authenticateWithRedirect`, and `setActive`) plus `Clerk.handleRedirectCallback`. The 6.40.1 published declarations expose these public operations. Clerk's current docs label the legacy resources as deprecated for a future release; moving to Core 3 requires a separate review and replacement with stable public methods. This integration does not access `__internal_*`, `__experimental_*`, or load the Clerk UI bundle.
- Backend SDK: official `clerk-backend-api` 7.0.0, restricted to `session_token`, configured with the instance JWT public key and authorized parties. SDK verification is networkless; the secret key is used only for trusted user-profile requests.
- Verification docs consulted 2026-10-25: [ClerkJS quickstart](https://clerk.com/docs/js-frontend/getting-started/quickstart), [SignIn object](https://clerk.com/docs/js-frontend/reference/objects/sign-in), [SignUp object](https://clerk.com/docs/js-frontend/reference/objects/sign-up), and [official Python SDK](https://github.com/clerk/clerk-sdk-python). Pin versions and re-check the current SDK before upgrades.

The OAuth button calls the documented public `SignIn.authenticateWithRedirect` with `strategy: 'oauth_google'`, `/sso-callback`, and `continueSignUp: true`. The callback route calls the public `Clerk.handleRedirectCallback` with custom sign-in/sign-up destinations and leaves its navigation handler unset, so Clerk activates the session and owns sign-in/sign-up continuation. The app checks for an active session after callback handling and shows a recoverable error if the callback returns without one; it does not restart auth initialization on the callback route. Exact API names and redirect parameters were checked against the 6.40.1 published declarations and [Clerk's legacy SignIn reference](https://clerk.com/docs/js-frontend/reference/objects/sign-in), [legacy SignUp reference](https://clerk.com/docs/js-frontend/reference/objects/sign-up), and [Clerk callback reference](https://clerk.com/docs/js-frontend/reference/objects/clerk). Clerk labels these public resources legacy and plans to remove them in a future release, so an SDK upgrade must replace and re-review this adapter. Automated browser-flow tests exercise the call contract; a real Clerk development instance and ordinary-browser run are still required for provider acceptance.

Keep bot protection enabled and provide the SDK-prescribed `clerk-captcha` challenge container. The runtime SDK and frontend adapter are separate artifacts; only the `clerk.browser.js` runtime is loaded, with app-owned UI.

## Configuration

Create a dedicated Clerk development instance for yourads. Configure email/password, email verification by code, Google, optional first/last name, and no required username, phone, legal acceptance, MFA, or other unimplemented task. Use `http://localhost:8000` as the canonical origin and authorized party. Set the Clerk issuer to the exact issuer emitted in the instance's session JWT.

Set `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CLERK_JWT_KEY`, `CLERK_ISSUER`, and comma-separated `CLERK_AUTHORIZED_PARTIES` in ignored `backend/.env` or a secret manager. Keep all but the publishable key on the server. `GET /api/auth/config` returns only the publishable key, validated Frontend API URL and constant route allowlist. Keep `YOURADS_SECRET_KEY` stable for encrypted ad credentials.

The backend verifies signatures, expiry, issuer, authorized party, token type, subject, session ID and pending status. A valid Bearer credential is required for mutations; only GET/HEAD may use Clerk's same-origin `__session` cookie for native image/video/download requests. The legacy `yourads_session` cookie and database session records are dormant. Local logout clears browser Clerk state; a previously issued signed JWT can remain valid until its short expiry because local JWT verification cannot revoke it immediately.

## Existing account mapping

Never attach an existing local account by email alone. An unknown Clerk subject with an email already on a local row receives a migration conflict. An operator who has independently verified account ownership can check or apply one explicit mapping:

```sh
cd backend
.venv/bin/python -m app.link_clerk_user --local-user-id 42 --clerk-user-id user_... --check
.venv/bin/python -m app.link_clerk_user --local-user-id 42 --clerk-user-id user_... --apply
```

The command requires a verified Clerk primary email matching the selected local row, rejects either-side conflicts, and updates only that row. Local integer IDs, owner foreign keys, webhook keys, encrypted credentials, media references, settings, and dormant password hashes remain intact. New Clerk accounts get one local row and demo data on first authenticated API use. `ALLOW_SIGNUP=false` blocks new local account provisioning.

Legacy SQLite imports omit the new Clerk subject column and preserve old session/hash data as inert history. Imported users must be linked explicitly. Existing password hashes are not imported into Clerk.

## Cutover and rollback

Implementation and provider acceptance are separate from production database cutover. Before switching a real application database, take verified PostgreSQL, Fernet-key, and media backups. Stop the app only after Clerk keys and selected account mappings are ready; apply `0002_clerk_identity`, then start the app with Clerk configuration. Startup validates Clerk configuration and migration head before worker recovery.

Rollback restores the prior application revision and configuration. A schema downgrade refuses while any user has a NULL password hash; it never fabricates credentials or deletes data. Existing sessions sign out at cutover. Do not restore an old database snapshot after accepting new writes.

## Verification status

The dedicated Clerk instance, keys, Google configuration and ordinary-browser acceptance must be recorded separately. Mocks and SDK tests do not count as Google acceptance; Google OAuth must be tested in a normal external browser.
