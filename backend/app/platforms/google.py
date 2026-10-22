import httpx
from .base import AdPlatform

API = "https://googleads.googleapis.com/v18"


class Google(AdPlatform):
    name = "google"

    def configured(self):
        return bool(self.c.get("access_token") and self.c.get("developer_token")
                    and self.c.get("customer_id"))

    @property
    def _cid(self):
        return self.c["customer_id"].replace("-", "")

    def _call(self, path, body):
        h = {"Authorization": f"Bearer {self.c['access_token']}",
             "developer-token": self.c["developer_token"]}
        if self.c.get("login_customer_id"):
            h["login-customer-id"] = self.c["login_customer_id"].replace("-", "")
        r = httpx.post(f"{API}/customers/{self._cid}/{path}", headers=h, json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def create_campaign(self, c):
        budget = self._call("campaignBudgets:mutate", {"operations": [{"create": {
            "name": f"{c['name']} budget", "amountMicros": int(c["daily_budget"] * 1_000_000),
            "deliveryMethod": "STANDARD"}}]})["results"][0]["resourceName"]
        res = self._call("campaigns:mutate", {"operations": [{"create": {
            "name": c["name"], "status": "PAUSED", "advertisingChannelType": "SEARCH",
            "campaignBudget": budget, "manualCpc": {}}}]})
        return res["results"][0]["resourceName"]

    def set_status(self, external_id, status):
        self._call("campaigns:mutate", {"operations": [{
            "update": {"resourceName": external_id, "status": "ENABLED" if status == "active" else "PAUSED"},
            "updateMask": "status"}]})

    def set_budget(self, external_id, daily_budget):
        q = self._call("googleAds:search", {"query": f"SELECT campaign.campaign_budget FROM campaign "
                                                     f"WHERE campaign.resource_name = '{external_id}'"})
        bres = q["results"][0]["campaign"]["campaignBudget"]
        self._call("campaignBudgets:mutate", {"operations": [{
            "update": {"resourceName": bres, "amountMicros": int(daily_budget * 1_000_000)},
            "updateMask": "amountMicros"}]})

    def list_campaigns(self):
        d = self._call("googleAds:search", {"query": "SELECT campaign.resource_name, campaign.name, "
                       "campaign.status, campaign_budget.amount_micros FROM campaign "
                       "WHERE campaign.status != 'REMOVED'"})
        return [{"external_id": r["campaign"]["resourceName"], "name": r["campaign"]["name"],
                 "status": "active" if r["campaign"]["status"] == "ENABLED" else "paused",
                 "daily_budget": int(r.get("campaignBudget", {}).get("amountMicros", 0)) / 1e6}
                for r in d.get("results", [])]
