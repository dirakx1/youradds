"""Publishing a campaign to Meta as a real ad (ad set + creative + ad), against the local fake Meta API."""
import os
import unittest
from unittest.mock import patch

import httpx

from devtools.fake_ads import env_for, serve_in_thread
from tests.support import ApiTestCase


class PublishTests(ApiTestCase):
    PINS = {"FX_RATE_COP_USD": "0.00025", "FX_RATE_USD_COP": "4000"}

    def setUp(self):
        super().setUp()
        self.base, stop = serve_in_thread()
        self.addCleanup(stop)
        p = patch.dict(os.environ, {**env_for(self.base), **self.PINS})
        p.start()
        self.addCleanup(p.stop)
        self.meta = f"{self.base}/meta/v26.0"
        self.client.put("/api/connections/meta", json={"values": {"access_token": "tok", "ad_account_id": "act_900"}})
        self.camp = self.client.post("/api/campaigns", json={"platform": "meta", "name": "Mine", "objective": "traffic",
                                                             "daily_budget": 50000}).json()
        self.cid = self.camp["id"]
        r = self.client.post("/api/creatives/generate", json={"product": "Cafe", "platforms": ["meta"],
                                                              "link": "https://mitienda.co"})
        self.creative_ids = [c["id"] for c in r.json()["creatives"]]
        for i in self.creative_ids:
            self.client.post(f"/api/creatives/{i}/attach", json={"campaign_id": self.cid})

    def objs(self):
        return httpx.get(f"{self.base}/__state").json().get("meta_objs", {})

    def flags(self, **kw):
        httpx.post(f"{self.base}/__flags", json=kw)

    # ---- connection helpers ----
    def test_detect_saves_the_single_page_and_its_instagram_and_rejects_links(self):
        r = self.client.post("/api/connections/meta/detect").json()
        self.assertEqual((r["saved"], r["pages"][0]["id"], r["pages"][0]["instagram"]["id"]), (True, "5551", "7771"))
        bad = self.client.put("/api/connections/meta", json={"values": {"page_id": "https://facebook.com/mypage"}})
        self.assertEqual(bad.status_code, 400)                                  # a profile link is not a Page ID

    # ---- check ----
    def test_check_reports_money_in_both_currencies_and_blocks_what_is_missing(self):
        self.client.post("/api/connections/meta/detect")
        r = self.client.post(f"/api/campaigns/{self.cid}/publish/check", json={}).json()
        self.assertTrue(r["ok"], r["checks"])
        self.assertEqual((r["money"]["account_currency"], r["money"]["account_amount"]), ("USD", 12.5))   # 50,000 COP
        self.assertEqual(r["images"], {"feed": "feed_portrait", "story": "story_reel"})
        missing = self.client.post(f"/api/campaigns/{self.cid}/publish/check", json={"placements": []}).json()
        self.assertFalse(missing["ok"])

    def test_check_blocks_without_a_page_and_without_a_story_image(self):
        r = self.client.post(f"/api/campaigns/{self.cid}/publish/check", json={}).json()
        self.assertFalse(r["ok"])
        self.assertTrue(any(c["key"] == "page" and c["level"] == "block" for c in r["checks"]))
        self.client.post("/api/connections/meta/detect")
        self.client.delete(f"/api/creatives/{self.creative_ids[2]}")             # the 9:16 story image
        r = self.client.post(f"/api/campaigns/{self.cid}/publish/check", json={}).json()
        self.assertTrue(any(c["key"] == "image_story" for c in r["checks"]))
        feed_only = self.client.post(f"/api/campaigns/{self.cid}/publish/check",
                                     json={"placements": ["facebook_feed", "instagram_feed"]}).json()
        self.assertTrue(feed_only["ok"])

    # ---- publish ----
    def test_publish_creates_a_paused_ad_set_creative_and_ad_once_then_activate(self):
        self.client.post("/api/connections/meta/detect")
        r = self.client.post(f"/api/campaigns/{self.cid}/publish", json={})
        self.assertEqual(r.status_code, 200, r.text)
        state = r.json()["state"]
        objs = self.objs()
        self.assertEqual({objs[state[k]]["kind"] for k in ("adset_id", "creative_id", "ad_id")}, {"adsets", "adcreatives", "ads"})
        self.assertTrue(all(objs[state[k]]["status"] == "PAUSED" for k in ("adset_id", "ad_id")))
        adset = objs[state["adset_id"]]["params"]
        self.assertEqual(adset["daily_budget"], "1250")                           # 12.50 USD in cents
        import json
        tg = json.loads(adset["targeting"])
        self.assertEqual((tg["geo_locations"], tg["age_min"], tg["age_max"]), ({"countries": ["CO"]}, 18, 65))
        self.assertEqual(sorted(tg["publisher_platforms"]), ["facebook", "instagram"])
        feed = json.loads(objs[state["creative_id"]]["params"]["asset_feed_spec"])
        self.assertEqual({r["image_label"]["name"] for r in feed["asset_customization_rules"]}, {"feed", "story"})
        self.assertEqual(feed["call_to_action_types"], ["SHOP_NOW"])        # "Compra ya"
        before = len(self.objs())
        again = self.client.post(f"/api/campaigns/{self.cid}/publish", json={})            # a retry creates nothing new
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(self.objs()), before)
        # activation turns on the ad, the ad set and the campaign
        self.assertEqual(self.client.post(f"/api/campaigns/{self.cid}/activate").status_code, 200)
        for oid in (state["ad_id"], state["adset_id"]):
            self.assertEqual(httpx.get(f"{self.meta}/{oid}", params={"access_token": "tok"}).json()["status"], "ACTIVE")
        self.assertEqual(httpx.get(f"{self.meta}/{self.camp['external_id']}", params={"access_token": "tok"}).json()["status"], "ACTIVE")

    def test_a_budget_typed_in_the_panel_is_used_and_saved_when_the_campaign_has_none(self):
        from app.db import session_scope
        from app.models import Campaign
        from sqlalchemy import update, select
        self.client.post("/api/connections/meta/detect")
        with session_scope() as s:
            s.execute(update(Campaign).where(Campaign.id == self.cid).values(daily_budget=0))   # imported from Meta: no budget
        blocked = self.client.post(f"/api/campaigns/{self.cid}/publish/check", json={}).json()
        self.assertFalse(blocked["ok"])
        ok = self.client.post(f"/api/campaigns/{self.cid}/publish/check", json={"daily_budget": 40000}).json()
        self.assertTrue(ok["ok"], ok["checks"])
        self.assertEqual(ok["money"]["account_amount"], 10.0)                                   # 40,000 COP at 0.00025
        r = self.client.post(f"/api/campaigns/{self.cid}/publish", json={"daily_budget": 40000})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.objs()[r.json()["state"]["adset_id"]]["params"]["daily_budget"], "1000")
        with session_scope() as s:
            self.assertEqual(s.scalar(select(Campaign.daily_budget).where(Campaign.id == self.cid)), 40000)

    def test_validate_only_creates_nothing(self):
        self.client.post("/api/connections/meta/detect")
        r = self.client.post(f"/api/campaigns/{self.cid}/publish", json={"validate_only": True})
        self.assertEqual((r.status_code, r.json()["validated"]), (200, True))
        self.assertEqual(self.objs(), {})

    def test_development_mode_app_gets_a_plain_explanation_and_a_retry_resumes(self):
        self.client.post("/api/connections/meta/detect")
        self.flags(dev_mode=True)
        r = self.client.post(f"/api/campaigns/{self.cid}/publish", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("switch the app to Live", r.json()["detail"])
        self.assertEqual([o["kind"] for o in self.objs().values()], ["adsets"])           # the ad set already exists
        self.flags(dev_mode=False)
        ok = self.client.post(f"/api/campaigns/{self.cid}/publish", json={})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(sorted(o["kind"] for o in self.objs().values()), ["adcreatives", "ads", "adsets"])   # no second ad set

    def test_whatsapp_destination_needs_the_page_linked_to_whatsapp(self):
        self.client.post("/api/connections/meta/detect")
        from app.db import session_scope
        from app.models import Campaign
        from sqlalchemy import update
        with session_scope() as s:
            s.execute(update(Campaign).where(Campaign.id == self.cid).values(landing_url="https://wa.me/573001112233"))
        self.flags(wa_unlinked=True)
        r = self.client.post(f"/api/campaigns/{self.cid}/publish", json={"destination": "whatsapp"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not linked to a WhatsApp Business account", r.json()["detail"])
        self.flags(wa_unlinked=False)
        ok = self.client.post(f"/api/campaigns/{self.cid}/publish", json={"destination": "whatsapp"})
        self.assertEqual(ok.status_code, 200, ok.text)
        import json
        creative = json.loads(self.objs()[ok.json()["state"]["creative_id"]]["params"]["asset_feed_spec"])
        self.assertEqual(creative["call_to_action_types"], ["WHATSAPP_MESSAGE"])

    def test_resuming_a_published_campaign_through_the_status_button_activates_the_ad_too(self):
        self.client.post("/api/connections/meta/detect")
        state = self.client.post(f"/api/campaigns/{self.cid}/publish", json={}).json()["state"]
        self.client.post(f"/api/campaigns/{self.cid}/status?status=active")
        self.assertEqual(httpx.get(f"{self.meta}/{state['ad_id']}", params={"access_token": "tok"}).json()["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
