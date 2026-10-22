import httpx
from .base import AdPlatform

GRAPH = "https://graph.facebook.com/v21.0"
OBJECTIVES = {"sales": "OUTCOME_SALES", "traffic": "OUTCOME_TRAFFIC", "leads": "OUTCOME_LEADS",
              "awareness": "OUTCOME_AWARENESS", "whatsapp": "OUTCOME_ENGAGEMENT"}


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
        r = httpx.request(method, f"{GRAPH}/{path}", data=data if method == "POST" else None,
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
