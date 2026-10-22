# yourads

Open-source AI ads manager inspired by saleads.ai. Create and manage **Meta**, **TikTok** and **Google** ad
campaigns, generate ad copy with AI, and get optimization recommendations you can apply in one click.

- **Backend**: Python / FastAPI / SQLite (`backend/`)
- **Frontend**: dependency-free JS SPA (`frontend/`), served by the backend

## Run
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional
uvicorn app.main:app --reload
```
Open http://localhost:8000. API docs at `/docs`.

## Accounts (multi-user)

- Sign up / log in with email + password (PBKDF2-SHA256, server-side sessions in an HttpOnly cookie, login throttling).
- Everything is per account: campaigns and metrics, creatives, videos, WhatsApp leads/chats and knowledge base.
  Every API query is filtered by the logged-in user; another user's ids return 404.
- **Connections page** (per user): Meta (+ Page and Instagram account IDs), TikTok, Google Ads, WhatsApp, Higgsfield.
  Stored encrypted (Fernet); the API never returns secrets, only "saved" and the last 4 characters.
- New accounts get sample campaigns (flagged as sample data, removable from the dashboard). The first account created
  also inherits any data from before accounts existed.
- Operator settings live in `backend/.env` (see `.env.example`): `ALLOW_SIGNUP`, `YOURADS_SECRET_KEY` (back it up),
  `SHARED_HIGGSFIELD`. The Anthropic key is server-wide, so AI copy/chat/WhatsApp-agent usage is billed to the operator.

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
- **Demo** (default): no credentials needed. Seeded campaigns with synthetic metrics; everything is stored locally.
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
Accounts: no email verification or password reset yet, login throttling is per-process, and credentials are pasted
tokens (no OAuth connect flow or token refresh - Google/TikTok access tokens expire and must be re-pasted). Run behind
HTTPS in production. Not yet audited for production use.
