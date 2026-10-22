import httpx
from .base import AdPlatform

API = "https://business-api.tiktok.com/open_api/v1.3"
OBJECTIVES = {"sales": "WEB_CONVERSIONS", "traffic": "TRAFFIC", "leads": "LEAD_GENERATION",
              "awareness": "REACH", "whatsapp": "TRAFFIC"}


class TikTok(AdPlatform):
    name = "tiktok"

    def configured(self):
        return bool(self.c.get("access_token") and self.c.get("advertiser_id"))

    def _call(self, method, path, **kw):
        r = httpx.request(method, f"{API}/{path}", headers={"Access-Token": self.c["access_token"]},
                          timeout=30, **kw)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(f"TikTok API: {body.get('message')}")
        return body.get("data", {})

    def create_campaign(self, c):
        d = self._call("POST", "campaign/create/", json={
            "advertiser_id": self.c["advertiser_id"], "campaign_name": c["name"],
            "objective_type": OBJECTIVES.get(c["objective"], "WEB_CONVERSIONS"),
            "budget_mode": "BUDGET_MODE_DAY", "budget": c["daily_budget"],
            "operation_status": "DISABLE"})
        return str(d["campaign_id"])

    def set_status(self, external_id, status):
        self._call("POST", "campaign/status/update/", json={
            "advertiser_id": self.c["advertiser_id"], "campaign_ids": [external_id],
            "operation_status": "ENABLE" if status == "active" else "DISABLE"})

    def set_budget(self, external_id, daily_budget):
        self._call("POST", "campaign/update/", json={
            "advertiser_id": self.c["advertiser_id"], "campaign_id": external_id,
            "budget": daily_budget})

    def list_campaigns(self):
        d = self._call("GET", "campaign/get/", params={"advertiser_id": self.c["advertiser_id"],
                                                       "page_size": 100})
        return [{"external_id": str(x["campaign_id"]), "name": x["campaign_name"],
                 "status": "active" if x.get("operation_status") == "ENABLE" else "paused",
                 "daily_budget": x.get("budget", 0)} for x in d.get("list", [])]
