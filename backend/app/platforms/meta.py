import json

import httpx
from ..config import env
from .base import AdPlatform

# Graph API v21.0 is removed by Meta on 2027-01-21, so the version is configurable (META_GRAPH_VERSION).
GRAPH_VERSION = "v26.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"


def base_url() -> str:
    """META_GRAPH_URL points the connector at a local fake or sandbox (see backend/devtools/)."""
    if env("META_GRAPH_URL"):
        return env("META_GRAPH_URL").rstrip("/")
    return f"https://graph.facebook.com/{env('META_GRAPH_VERSION') or GRAPH_VERSION}"

OBJECTIVES = {"sales": "OUTCOME_SALES", "traffic": "OUTCOME_TRAFFIC", "leads": "OUTCOME_LEADS",
              "awareness": "OUTCOME_AWARENESS", "whatsapp": "OUTCOME_ENGAGEMENT"}


# Insights report conversions as lists of {action_type, value}. The purchase types overlap (omni_purchase already
# includes the pixel and app ones), so the FIRST type present wins instead of summing them.
PURCHASE = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
LEAD = ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead")
CHAT = ("onsite_conversion.messaging_conversation_started_7d",)


def _first(rows, types):
    got = {r.get("action_type"): float(r.get("value") or 0) for r in rows or []}
    return next((got[t] for t in types if t in got), None)


def normalize_insight(row: dict) -> dict:
    """One Graph insights row (level=campaign, daily) -> the app's metric shape. Conversions are purchases when the
    campaign has any, else leads, else WhatsApp/Messenger conversations started; revenue is purchase value only."""
    conv = next((v for v in (_first(row.get("actions"), PURCHASE), _first(row.get("actions"), LEAD),
                             _first(row.get("actions"), CHAT)) if v is not None), 0)
    return {"external_id": str(row["campaign_id"]), "day": row["date_start"],
            "impressions": int(float(row.get("impressions") or 0)), "clicks": int(float(row.get("clicks") or 0)),
            "spend": float(row.get("spend") or 0), "conversions": int(conv),
            "revenue": float(_first(row.get("action_values"), PURCHASE) or 0),
            "currency": row.get("account_currency") or ""}


class Meta(AdPlatform):
    name = "meta"

    @property
    def acct(self):
        a = self.c.get("ad_account_id", "").strip()
        return a if a.startswith("act_") else f"act_{a}"

    def configured(self):
        return bool(self.c.get("access_token") and self.c.get("ad_account_id"))

    def _call(self, method, path, **data):
        data["access_token"] = self.c["access_token"]
        r = httpx.request(method, f"{base_url()}/{path}", data=data if method == "POST" else None,
                          params=data if method == "GET" else None, timeout=30)
        r.raise_for_status()
        return r.json()

    def create_campaign(self, c):
        res = self._call("POST", f"{self.acct}/campaigns",
                         name=c["name"], objective=OBJECTIVES.get(c["objective"], "OUTCOME_SALES"),
                         status="PAUSED", special_ad_categories="[]")
        return res["id"]  # ad sets / creatives are created in a follow-up step (targeting + page id)

    def set_status(self, external_id, status):
        self._call("POST", external_id, status="ACTIVE" if status == "active" else "PAUSED")

    def set_budget(self, external_id, daily_budget):
        # Budget lives on ad sets unless CBO is used; campaign-level budget in cents.
        self._call("POST", external_id, daily_budget=int(daily_budget * 100))

    def list_campaigns(self):
        res = self._call("GET", f"{self.acct}/campaigns",
                         fields="id,name,status,objective,daily_budget", limit=100)
        return [{"external_id": x["id"], "name": x["name"],
                 "status": "active" if x["status"] == "ACTIVE" else "paused",
                 "daily_budget": int(x.get("daily_budget", 0)) / 100} for x in res.get("data", [])]

    def insights(self, since: str, until: str) -> list[dict]:
        """Daily per-campaign results between two ISO dates (inclusive), following Graph pagination."""
        params = {"level": "campaign", "time_increment": 1, "limit": 500, "access_token": self.c["access_token"],
                  "fields": "campaign_id,impressions,clicks,spend,actions,action_values,account_currency",
                  "time_range": json.dumps({"since": since, "until": until})}
        url, rows = f"{base_url()}/{self.acct}/insights", []
        for _ in range(50):                      # hard stop: 50 pages x 500 rows
            r = httpx.get(url, params=params, timeout=30)
            if r.is_error:                       # never surface the raw httpx error: its URL carries the access token
                try:
                    msg = r.json()["error"]["message"]
                except (ValueError, KeyError, TypeError):
                    msg = f"HTTP {r.status_code}"
                raise RuntimeError(f"Meta: {msg}"[:300])
            body = r.json()
            rows += [normalize_insight(x) for x in body.get("data", [])]
            url = (body.get("paging") or {}).get("next")
            if not url:
                break
            params = None                        # the "next" URL already carries every parameter
        return rows
