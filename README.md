# yourads

Open-source AI ads manager inspired by saleads.ai. Create and manage **Meta**, **TikTok** and **Google** ad
campaigns, generate ad copy with AI, and get optimization recommendations you can apply in one click.

- **Backend**: Python / FastAPI / PostgreSQL hosted by Supabase (`backend/`), using SQLAlchemy 2 and Alembic
- **Frontend**: dependency-free JS SPA (`frontend/`), served by the backend

## Run
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # set DATABASE_URL and MIGRATION_DATABASE_URL first
python -m alembic upgrade head
uvicorn app.main:app --reload
```
Open http://localhost:8000. API docs at `/docs`.

For the configured local development environment, run `./backend/run-local.sh` from the repository root.
It starts the local PostgreSQL instance if needed, applies migrations, and serves the app at http://localhost:8000.
Local credentials are in ignored `backend/.env`; database files persist in ignored `backend/data/postgres`.

The backend requires a migrated PostgreSQL database; startup never creates or changes tables. For a Supabase
project, keep the private `yourads` schema out of the Data API exposed-schema list and use a restricted runtime
database role. See [the database migration runbook](backend/docs/database-migration.md) and the
[EC2 + Nginx deployment guide](backend/docs/ec2-nginx-deployment.md). The current server state is recorded in the
[EC2 operations record](backend/docs/ec2-server-operations.md).

## Testing the ad-platform integrations (Meta / TikTok / Google)

Three layers, from fastest to most realistic. The connectors in `backend/app/platforms/` are the same code in all three.

1. **Local fake APIs (no accounts, no network).** `backend/devtools/fake_ads.py` imitates the Meta Graph, TikTok Business
   and Google Ads REST calls yourads makes (create / list / pause / budget + the credential probes), keeps state, and
   rejects malformed requests the way the real APIs do. Automated contract tests use it:
   `cd backend && python -m unittest tests.test_ad_platforms`.
2. **Click through the real app against the fake.** In one terminal `python -m devtools.fake_ads --port 9100` (it prints
   three `..._URL` variables); in another, export them and start the app (`META_GRAPH_URL=... TIKTOK_API_URL=...
   GOOGLE_ADS_API_URL=... ./run-local.sh`). On the Connections page enter any token (`test-token`), a Meta account
   such as `123`, a TikTok advertiser `7000`, a 10-digit Google customer id and any developer token. Then Create campaign,
   Pause, Optimizer "Apply" and "Test & import" all hit the fake (state at `http://127.0.0.1:9100/__state`). Tokens
   containing `EXPIRED`, `NOPERM` or `RATELIMIT` simulate those failures, so the red/amber badges can be tried.
3. **Real sandbox / test accounts (the only proof the upstream APIs still accept our requests).**
   `python -m devtools.smoke_ads --platform meta` runs a read-only credential probe + campaign list against the real API
   using `META_ACCESS_TOKEN`/`META_AD_ACCOUNT_ID` (TikTok: `TIKTOK_ACCESS_TOKEN`/`TIKTOK_ADVERTISER_ID`; Google:
   `GOOGLE_ADS_ACCESS_TOKEN`/`GOOGLE_ADS_DEVELOPER_TOKEN`/`GOOGLE_ADS_CUSTOMER_ID`). Add `--write` to also create one
   PAUSED campaign and change its budget (asks first; it can't spend and is never activated; delete it in the platform
   UI afterwards). Use a Meta test ad account, a TikTok sandbox advertiser and a Google Ads test account. Re-run this
   whenever you change `platforms/*.py` or bump an API version (`GRAPH`/`API` constants).
   `python -m devtools.smoke_ads --fake --write` runs the same script against the fake.

The fake proves our requests are well-formed and our parsing is right; it cannot know about upstream changes (API
versions, new required fields, policy rules). Treat layer 3 as the release check.

## Accounts (multi-user)

- Sign up / log in with email + password or Google through Clerk. The backend verifies Clerk session tokens.
  Existing local passwords and sessions are not migrated into Clerk; users must be linked explicitly as described in
  the [Clerk runbook](backend/docs/clerk-auth.md).
- Everything is per account: campaigns and metrics, creatives, videos, WhatsApp leads/chats and knowledge base.
  Every API query is filtered by the logged-in user; another user's ids return 404.
- **Connections page** (per user): Meta (+ Page and Instagram account IDs), TikTok, Google Ads, WhatsApp, Higgsfield.
  Stored encrypted (Fernet); the API never returns secrets, only "saved" and the last 4 characters.
- New accounts get sample campaigns (flagged as sample data, removable from the dashboard). The first account created
  also inherits any data from before accounts existed.
- Operator settings live in `backend/.env` (see `.env.example`): `ALLOW_SIGNUP`, `YOURADS_SECRET_KEY` (back it up),
  `SHARED_HIGGSFIELD`. The Anthropic key is server-wide, so AI copy/chat/WhatsApp-agent usage is billed to the operator.

## Languages (English / Spanish)

Every page lives under a language prefix: `/en/...` is English, `/es/...` is Spanish (app: `/es/#studio`, auth:
`/es/sign-in`, `/es/sso-callback`, static pages: `/es/about/`, `/es/contact/`, `/es/privacy/`, `/es/terms/`).
Old un-prefixed URLs (`/`, `/sign-in`, `/about/` ...) redirect (302, query string kept) to the visitor's language: the
`lang` cookie set by the footer switcher, else the browser's `Accept-Language`, else English (`backend/app/pages.py`).

- Static pages are one HTML file per language (`frontend/en/<page>/`, `frontend/es/<page>/`); edit both together.
- The app is written in English. `frontend/i18n.js` holds the Spanish dictionary (exact-text keys): on `/es/` it
  translates everything the app renders, and `t('Text {name}', {name})` is used for alerts/confirms/built strings.
  A string without an entry just stays English, so add the Spanish entry whenever you add UI text.
  `frontend/tests/i18n.test.mjs` checks that placeholders match between each key and its translation.
- Clerk redirect/sign-in URLs are language-prefixed (`/es/sso-callback`); the Clerk instance only needs the site
  origin allowed, not individual paths.
- Not translated: provider/Clerk error messages and AI replies (ad copy follows the Language dropdown instead).

## Creative Studio
Tab **Creatives**: generates image ads for Meta (1080×1080, 1080×1350, 9:16 Story/Reel), TikTok (9:16) and Google
Display (1200×628, 1200×1200, 300×250) from a product name + optional product photo. Each platform gets its own
copy style (LLM if `ANTHROPIC_API_KEY` is set, templates otherwise), in 6 languages, 5 color themes. 9:16 layouts keep
text out of the platform UI zones. Reviews/ratings/customer counts appear **only if you type them in** — nothing is
fabricated. Rendering is local (Pillow); the product photo is composited as-is (a transparent PNG looks best). Backgrounds are
gradients, or with your Higgsfield key (`ID:SECRET`, a separate prepaid API balance, added on the Connections page) AI scenes from Higgsfield: one
per aspect ratio, prompt written by Claude (if configured) without naming the product, optionally checked by Claude
vision, cost estimated first and refused above `SCENE_MAX_USD`. Only the background is generated; your real product
photo is never redrawn. Creatives can be attached to a campaign (copies its text);
uploading the image to the ad platform itself is not implemented yet.

## Remove background
The Creatives and Video forms have a **Remove background** button next to the product-image input. It uses `rembg`
(IS-Net, ~180MB model downloaded on first use) and, on white studio backdrops, GrabCut too so white parts (e.g. shoe
soles) stay solid while the floor shadow is dropped. The result is a cropped transparent PNG that replaces your file in
the form. Needs `rembg onnxruntime opencv-python-headless numpy` (in `requirements.txt`).

## Video Studio
Tab **Video**: Seedance 2.5 (via the same Higgsfield key) generates a background clip per aspect ratio (9:16 for
Meta Reels/TikTok, 1:1 for Meta feed, 16:9 for YouTube/Google); your product photo, headline, bullets, price and CTA
are composited on top with FFmpeg (bundled via `imageio-ffmpeg`, nothing to install). Runs as a background job.
**Video is expensive**: ~$0.46/s at 720p, ~$0.21/s at 480p *per clip* (a 5 s 720p clip ≈ $2.40). Cost is computed
from Higgsfield's published token formula (+5% margin), shown for confirmation, and refused above `VIDEO_MAX_USD`
(default $3). No audio is generated and no automatic quality retry is done (it would double the cost).

## WhatsApp sales automation
Tab **WhatsApp**: an AI agent answers leads (e.g. from Click-to-WhatsApp ads), qualifies them, hands off to a human
when asked or unsure, and nudges quiet leads (max 2 follow-ups, only inside WhatsApp's 24h window). Marking a lead as
sold credits the revenue to its campaign. The agent only uses the facts you enter under *Sales knowledge*; `STOP`
opts a customer out permanently.
- Demo: use *Simulate a customer* in the tab — no credentials needed.
- Live: add your WhatsApp credentials on the Connections page. Each account gets its own callback URL
  (`/api/whatsapp/webhook/<key>`) and verify token, shown there; expose the server publicly (e.g. ngrok) and register
  them in Meta's WhatsApp config. Add the app secret so webhook signatures are verified.

## Modes
- **Demo** (default): no ad-platform credentials needed. Seeded campaigns with synthetic metrics are stored in PostgreSQL.
- **Live**: add your platform credentials on the Connections page. Creating/pausing campaigns and budget changes are pushed to
  the platform; use *Connections → Import* to pull existing campaigns. Campaigns created live start **paused**.
- **AI**: set `ANTHROPIC_API_KEY` for LLM copy and Copilot answers; otherwise templates/rules are used.

## Status / limitations
Live connectors are written against the public Meta Graph, TikTok Business and Google Ads REST APIs but are
**untested against real accounts**. Campaign creation makes the campaign shell only (Meta ad sets/creatives, TikTok ad
groups and Google ad groups/ads are not yet created), and live metrics are not pulled — metrics are demo data.
WhatsApp live mode is untested against a real number; outside the 24h window only the single approved
the single approved follow-up template can be sent (human replies use it too). Ad-to-lead attribution relies on the
referral `source_id` matching a campaign's external id, so live Click-to-WhatsApp leads may arrive unattributed.
Clerk-backed custom email/password and Google flows are implemented. Production Clerk configuration and live Google
acceptance must be completed before launch. Ad-platform credentials are pasted tokens (no OAuth connect flow or token refresh -
Google/TikTok access tokens expire and must be re-pasted). Run behind
HTTPS in production. Not yet audited for production use.
# Production launch status

The authentication implementation is Clerk-backed. Configure a dedicated production Clerk instance, verify Google
OAuth in a normal browser, and complete account linking before switching existing users. See the
[Clerk runbook](backend/docs/clerk-auth.md) and [EC2 deployment guide](backend/docs/ec2-nginx-deployment.md).
