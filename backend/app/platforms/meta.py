import json

import httpx
from .. import fx
from ..config import DEFAULT_CURRENCY, env
from .base import AdPlatform

# Graph API v21.0 is removed by Meta on 2027-01-21, so the version is configurable (META_GRAPH_VERSION).
GRAPH_VERSION = "v26.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"


def base_url() -> str:
    """META_GRAPH_URL points the connector at a local fake or sandbox (see backend/devtools/)."""
    if env("META_GRAPH_URL"):
        return env("META_GRAPH_URL").rstrip("/")
    return f"https://graph.facebook.com/{env('META_GRAPH_VERSION') or GRAPH_VERSION}"

# Meta takes budgets as integers in the account currency's smallest unit. Most currencies have 100 per unit (cents);
# these have 1 (Meta's "currency offset" table), so 20000 COP is sent as 20000, never 2000000.
OFFSET_ONE = {"CLP", "COP", "CRC", "HUF", "ISK", "IDR", "JPY", "KRW", "PYG", "TWD", "VND"}


def minor_per_unit(currency: str) -> int:
    return 1 if currency.upper() in OFFSET_ONE else 100


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

    def _call(self, method, path, _files=None, **data):
        data["access_token"] = self.c["access_token"]
        r = httpx.request(method, f"{base_url()}/{path}", data=data if method == "POST" else None, files=_files,
                          params=data if method == "GET" else None, timeout=60 if _files else 30)
        if r.is_error:                       # Meta says why in the body; the raw httpx error says nothing useful
            try:
                e = r.json()["error"]
                msg = e.get("error_user_msg") or e.get("error_user_title") or e.get("message") or ""
            except (ValueError, KeyError, TypeError, AttributeError):
                msg = ""
            code = ""
            try:
                code = f" [{r.json()['error'].get('error_subcode') or r.json()['error'].get('code')}]"
            except (ValueError, KeyError, TypeError, AttributeError):
                pass
            raise RuntimeError(f"Meta: {msg or 'HTTP ' + str(r.status_code)}{code}"[:360])
        return r.json()

    def account_currency(self) -> str:
        """The currency this ad account bills in (what every Meta amount is expressed in)."""
        if not getattr(self, "_cur", None):
            r = httpx.get(f"{base_url()}/{self.acct}", params={"fields": "currency", "access_token": self.c["access_token"]},
                          timeout=30)
            cur = (r.json().get("currency") if r.status_code < 300 else "") or ""
            if not cur:
                raise RuntimeError("Could not read the ad account's currency from Meta")
            self._cur = cur.upper()
        return self._cur

    def create_campaign(self, c):
        res = self._call("POST", f"{self.acct}/campaigns",
                         name=c["name"], objective=OBJECTIVES.get(c["objective"], "OUTCOME_SALES"),
                         status="PAUSED", special_ad_categories="[]",
                         is_adset_budget_sharing_enabled="false")   # required by Meta when the budget is on ad sets
        return res["id"]  # ad sets / creatives are created in a follow-up step (targeting + page id)

    def set_status(self, external_id, status):
        self._call("POST", external_id, status="ACTIVE" if status == "active" else "PAUSED")

    def to_minor(self, daily_budget: float) -> int:
        """A budget in the currency youradds shows -> Meta's integer in the account's currency and smallest unit."""
        cur = self.account_currency()
        minor = round(fx.convert(daily_budget, DEFAULT_CURRENCY, cur) * minor_per_unit(cur))
        if minor < 1:
            raise RuntimeError(f"That budget is below Meta's minimum for a {cur} account")
        return minor

    def set_budget(self, external_id, daily_budget):
        """daily_budget is in the currency youradds shows; Meta gets it in the account's currency and minor unit.
        (A budget lives on ad sets unless CBO is used; this is the campaign-level one.)"""
        self._call("POST", external_id, daily_budget=self.to_minor(daily_budget))

    def min_daily_budget(self) -> int | None:
        """Meta's smallest daily budget for this account, in the account currency's minor unit (None if unknown)."""
        cur = self.account_currency()
        try:
            rows = self._call("GET", f"{self.acct}/minimum_budgets").get("data", [])
        except Exception:
            return None
        return next((int(r["min_daily_budget_imp"]) for r in rows if r.get("currency") == cur), None)

    # ---- publishing: images, ad set, creative, ad (always created PAUSED) ----
    def pages(self) -> list[dict]:
        """Facebook Pages this token can use, each with its linked Instagram business account when there is one."""
        res = self._call("GET", "me/accounts", fields="id,name,instagram_business_account{id,username}", limit=50)
        return [{"id": x["id"], "name": x.get("name", ""),
                 "instagram": ({"id": x["instagram_business_account"]["id"],
                                "username": x["instagram_business_account"].get("username", "")}
                               if x.get("instagram_business_account") else None)} for x in res.get("data", [])]

    def upload_image(self, filename: str, data: bytes) -> str:
        res = self._call("POST", f"{self.acct}/adimages", _files={"filename": (filename, data, "image/png")})
        try:
            return next(iter(res["images"].values()))["hash"]
        except (KeyError, StopIteration, TypeError):
            raise RuntimeError("Meta did not return an image hash")

    def create_adset(self, campaign_id: str, spec: dict, validate: bool = False) -> str:
        """spec: name, daily_budget (app currency), destination ('website'|'whatsapp'), targeting (dict),
        page_id, whatsapp_number."""
        body = dict(name=spec["name"], campaign_id=campaign_id, daily_budget=self.to_minor(spec["daily_budget"]),
                    billing_event="IMPRESSIONS", bid_strategy="LOWEST_COST_WITHOUT_CAP", status="PAUSED",
                    targeting=json.dumps(spec["targeting"]))
        if spec["destination"] == "whatsapp":
            body.update(optimization_goal="CONVERSATIONS", destination_type="WHATSAPP",
                        promoted_object=json.dumps({"page_id": spec["page_id"],
                                                    "whatsapp_phone_number": spec["whatsapp_number"]}))
        else:
            body.update(optimization_goal="LINK_CLICKS", destination_type="WEBSITE")
        if validate:
            body["execution_options"] = json.dumps(["validate_only"])
        return self._call("POST", f"{self.acct}/adsets", **body).get("id", "")

    def create_creative(self, spec: dict, validate: bool = False) -> str:
        """spec: name, page_id, instagram_id (optional), images {label: hash}, rules [{label, spec}], body, title,
        link, cta (Meta enum), destination."""
        feed = {"images": [{"hash": h, "adlabels": [{"name": label}]} for label, h in spec["images"].items()],
                "bodies": [{"text": spec["body"]}], "titles": [{"text": spec["title"]}],
                "link_urls": [{"website_url": spec["link"]}], "call_to_action_types": [spec["cta"]],
                "ad_formats": ["SINGLE_IMAGE"], "optimization_type": "PLACEMENT",
                "asset_customization_rules": [{"customization_spec": r["spec"], "image_label": {"name": r["label"]}}
                                              for r in spec["rules"]]}
        story = {"page_id": spec["page_id"]}
        if spec.get("instagram_id"):
            story["instagram_user_id"] = spec["instagram_id"]
        body = dict(name=spec["name"], object_story_spec=json.dumps(story), asset_feed_spec=json.dumps(feed))
        if validate:
            body["execution_options"] = json.dumps(["validate_only"])
        return self._call("POST", f"{self.acct}/adcreatives", **body).get("id", "")

    def create_ad(self, name: str, adset_id: str, creative_id: str) -> str:
        return self._call("POST", f"{self.acct}/ads", name=name, adset_id=adset_id, status="PAUSED",
                          creative=json.dumps({"creative_id": creative_id}))["id"]

    def list_campaigns(self):
        res = self._call("GET", f"{self.acct}/campaigns",
                         fields="id,name,status,objective,daily_budget", limit=100)
        data = res.get("data", [])
        cur = self.account_currency() if any(x.get("daily_budget") for x in data) else DEFAULT_CURRENCY
        rate = fx.rate(cur, DEFAULT_CURRENCY)          # budgets come back in the account's currency; show ours
        return [{"external_id": x["id"], "name": x["name"],
                 "status": "active" if x["status"] == "ACTIVE" else "paused",
                 "daily_budget": round(int(x.get("daily_budget", 0)) / minor_per_unit(cur) * rate, 2)} for x in data]

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
