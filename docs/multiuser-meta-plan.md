# Multi-user youradds with Meta connections: plan

Status: plan only, no code written. Written 2026-11-18.

## Goals

1. Anyone can sign up with email or Gmail (Clerk). Meta is not required to log in.
2. A user can connect their own Meta ad account ("Connect Meta Ads") and see, create and manage their campaigns in youradds.
3. Two modes per account, switchable:
   - **Self-managed**: the user operates their own ads.
   - **Managed by youradds**: our staff operate the ads for the user.

## Decisions already taken

- Login and ad access are separate. Clerk handles identity; Meta is a connection attached to a youradds account.
- A Meta user or ad account may be connected to only one youradds account at a time (block by default, with a clear message).
- Ad spend is always billed to the client's own payment method on their own ad account. Our management fee is charged separately.
- Managed mode uses partner sharing (client shares ad account and Page with our Business Manager) and a non-expiring system user token, not the client's personal token.

## Key facts and risks

- **App Review is the long pole.** With Standard Access the app only works for people who hold a role on it (that is the "development mode" error seen earlier). For arbitrary customers we need Advanced Access to: `ads_management`, `ads_read`, `pages_show_list`, `pages_read_engagement`, `business_management`, `instagram_basic`, and for the WhatsApp agent `whatsapp_business_management` and `whatsapp_business_messaging`.
- Requirements for review: Business Verification of our company, privacy policy URL, terms URL, data-deletion callback, app icon, and a screencast per permission. Expect weeks.
- **User tokens expire** (about 60 days). Store `expires_at`, warn before it, offer one-click Reconnect. Evaluate Facebook Login for Business for longer-lived tokens.
- The token pasted in chat during development is a personal user token: remove it once multi-user works and rotate it.

## Phases

### Phase 0: Meta side, from scratch (no app code; owner does the Meta steps, starts now, runs in parallel)

Decision (2026-11-18): the production Meta setup is created from zero, with a new Business portfolio, a new developer account and a new app. The existing test app `YourAdss - Jhon Test` (id 2558874451528128), its Live status and the personal user token become development leftovers: do not build on them, and retire them once the new app works (revoke the token, delete or archive the app).

Owner steps, in order:

1. **Decide the legal identity first.** The name and address used for the Business portfolio, Business Verification and the legal pages must be the same real company data. Business Verification checks them against a registration document (in Colombia: RUT or Cámara de Comercio certificate). Also decide a company email on the `youradds.co` domain for Meta correspondence.
2. **New Facebook profile ownership.** Use a real, long-standing personal Facebook account of a company owner or administrator as the admin of the new portfolio (Meta ties business tools to it). Turn on two-factor authentication. Do not use a throwaway or brand-new profile: it is likely to be restricted.
3. **Create the Business portfolio** at business.facebook.com. Use the legal company name, address, phone, website `https://youradds.co` and the company email. Add a second admin person so one lost login does not lock the business out.
4. **Verify the domain** `youradds.co` in the portfolio (Brand safety, Domains) with a DNS TXT record or meta-tag. Required before some permissions and for verification.
5. **Start Business Verification** (Security Center). Have the registration document ready. Approval takes from minutes to several days.
6. **Create the developer account** at developers.facebook.com with the same admin profile, then **create the app from scratch**: use case "Other", app type "Business", name `youradds`, contact email on the company domain, and connect it to the new portfolio. Note the new App ID and App Secret and keep the secret only in server secrets.
7. **Fill app settings** (Settings, Basic): app icon (512 px PNG from the favicon pack), category "Business and pages", privacy policy URL `https://youradds.co/en/privacy/`, terms URL `https://youradds.co/en/terms/`, and **User data deletion** instructions URL `https://youradds.co/en/data-deletion/`. Keep the app in Development mode until review.
8. **Add products:** Facebook Login for Business (create a configuration with the permissions listed above, to be exact once the review screens show them), Marketing API, and later WhatsApp.
9. **Add test people** under App roles (Testers) so Phase 2 can be tried without review. Each tester needs access to a Page and an ad account to be useful.
10. **Bring the test assets into the new portfolio** (or keep using the old ad account as a "client" via partner sharing, which also rehearses managed mode): Page "RUTA Digital 5.O", the Instagram account, an ad account with a payment method, and the WhatsApp Business account if the WhatsApp destination is needed.
11. **Do not request Advanced Access yet.** Submit only after Phase 2 works, because Meta requires working screencasts for each permission.

Our side, done before submission:

- Static pages `/en|es/privacy/`, `/en|es/terms/` and `/en|es/data-deletion/` exist.
- **Replace the placeholders** `[Operator legal name]`, `[Operator business address]` and `[Jurisdiction]` (and the Spanish equivalents) in the privacy and terms pages with the real company details. Meta reviewers reject pages with placeholders.
- Decide whether the data-deletion page should promise a deletion time frame (it currently says we will confirm by email, with no number of days).
- Deauthorize and data-deletion **callbacks** are built in Phase 2; the instructions URL is enough to submit the settings now.

### Phase 1: Multi-tenant foundation

- Confirm each Clerk user maps to an account; scope every query and endpoint by account (today it is mostly "account 5").
- Add an account `mode` field: `self` | `managed`.
- Per-account connection storage: encrypted token, `expires_at`, Meta user id, granted scopes, selected ad account / Page / Instagram / WhatsApp number.
- Unique constraint enforcing one youradds account per Meta user and per ad account.
- Alembic migration; update the asserted head in `tests/test_database.py`.

### Phase 2: Self-managed Meta connect

- `/connect/meta` redirect with a CSRF `state`; callback exchanges code for a long-lived token and stores it encrypted.
- Account chooser (ad account, Page, Instagram, WhatsApp); reuse `detect` and `use-page` with the user's own token.
- Import existing Meta campaigns (reuse `list_campaigns` and `insights`), match by `external_id`, mark as imported, never duplicate.
- Reconnect UX when a token expires; per-connection health check (already exists).
- Deauthorize and data-deletion callbacks (Meta requires both).
- Per-ad-account rate-limit handling.
- Starting point: the parked Phase 3 OAuth work in `stash@{0}`.

### Phase 3: Managed mode

- Staff role that can open a customer's account, with a visible "managed by youradds" banner.
- Audit log of every staff action (who, which account, what changed, when).
- Spend approval: staff prepare campaigns paused; the client confirms before Activate unless they opted into staff activation. The publish pipeline already creates things paused.
- Onboarding: instructions for the client to share their ad account and Page with our Business Manager; system user token stored in server secrets, not per account.
- Managed-service terms (budgets, permissions, how to end it); have a lawyer review.
- Develop and test with our own Business Manager acting as the client.

## Order of work

1. Phase 0 starts immediately and continues in parallel.
2. Phases 1 and 2 are built and tested with test users while the review is pending.
3. Phase 3 starts in development once Phase 1 is done; it goes live after Advanced Access is approved.

## Open questions

- Policy for a user who connects a Meta account already linked elsewhere (block, or support a transfer flow).
- Whether Clerk should also offer a Facebook button as a convenience (not required).
- Fee model for managed mode and where it is invoiced.
