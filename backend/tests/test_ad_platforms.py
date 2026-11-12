"""Connector + connection-health contract tests against the local fake ad APIs (devtools/fake_ads.py). No database."""
import os
import unittest
from unittest.mock import patch

import httpx

from devtools.fake_ads import env_for, serve_in_thread


class FakeAdsCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.stop = serve_in_thread()
        cls.env = patch.dict(os.environ, env_for(cls.base))
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.stop()

    def setUp(self):
        from app import health
        health._cache.clear()
        httpx.post(f"{self.base}/__reset")


class ConnectorTests(FakeAdsCase):
    CAMPAIGN = {"name": "Spring sale", "objective": "sales", "daily_budget": 20}

    def lifecycle(self, platform, creds, budget_after):
        from app.platforms import make
        p = make(platform, creds)
        self.assertTrue(p.configured())
        ext = p.create_campaign(self.CAMPAIGN)
        self.assertTrue(ext)
        rows = p.list_campaigns()
        self.assertEqual([(r["external_id"], r["name"], r["status"]) for r in rows], [(ext, "Spring sale", "paused")])
        p.set_status(ext, "active")
        self.assertEqual(p.list_campaigns()[0]["status"], "active")
        p.set_budget(ext, budget_after)
        self.assertEqual(p.list_campaigns()[0]["daily_budget"], budget_after)
        p.set_status(ext, "paused")
        self.assertEqual(p.list_campaigns()[0]["status"], "paused")

    def test_meta_lifecycle(self):
        self.lifecycle("meta", {"access_token": "tok", "ad_account_id": "act_555"}, 35.5)

    def test_meta_account_id_with_and_without_prefix_is_the_same_account(self):
        from app.platforms import make
        make("meta", {"access_token": "t", "ad_account_id": "777"}).create_campaign(self.CAMPAIGN)
        self.assertEqual(len(make("meta", {"access_token": "t", "ad_account_id": "act_777"}).list_campaigns()), 1)

    def test_meta_insights_are_paged_normalized_and_pick_one_purchase_type(self):
        from app.platforms import make
        p = make("meta", {"access_token": "tok", "ad_account_id": "act_555"})
        a = p.create_campaign(self.CAMPAIGN)
        b = p.create_campaign({**self.CAMPAIGN, "name": "Contacts-leads"})
        rows = p.insights("2026-11-01", "2026-11-03")
        self.assertEqual(len(rows), 6)                                          # 2 campaigns x 3 days, fetched 2 rows per page
        sale = next(r for r in rows if r["external_id"] == a)
        n = int(a) % 5 + 1
        self.assertEqual((sale["impressions"], sale["clicks"], sale["spend"]), (1000 * n, 30 * n, 12.5 * n))
        self.assertEqual((sale["conversions"], sale["revenue"]), (n, 40.0 * n))   # omni_purchase only: not purchase + omni, not link_click
        lead = next(r for r in rows if r["external_id"] == b)
        self.assertEqual((lead["conversions"], lead["revenue"]), (int(b) % 5 + 1, 0.0))
        self.assertEqual({r["day"] for r in rows}, {"2026-11-01", "2026-11-02", "2026-11-03"})

    def test_meta_insights_errors_never_leak_the_token(self):
        from app.platforms import make
        p = make("meta", {"access_token": "EXPIRED-secret-token", "ad_account_id": "act_555"})
        with self.assertRaises(RuntimeError) as ctx:
            p.insights("2026-11-01", "2026-11-03")
        self.assertIn("Session has expired", str(ctx.exception))
        self.assertNotIn("secret-token", str(ctx.exception))

    def test_tiktok_and_google_report_no_results_connector_yet(self):
        from app.platforms import make
        with self.assertRaises(NotImplementedError):
            make("tiktok", {"access_token": "t", "advertiser_id": "1"}).insights("2026-11-01", "2026-11-02")

    def test_tiktok_lifecycle(self):
        self.lifecycle("tiktok", {"access_token": "tok", "advertiser_id": "7000"}, 45)

    def test_google_lifecycle_with_dashed_customer_ids_and_mcc(self):
        self.lifecycle("google", {"access_token": "tok", "developer_token": "dev", "customer_id": "123-456-7890",
                                  "login_customer_id": "098-765-4321"}, 12.5)

    def test_every_objective_the_app_offers_is_accepted_upstream(self):
        from app.platforms import make
        creds = {"meta": {"access_token": "t", "ad_account_id": "1"}, "tiktok": {"access_token": "t", "advertiser_id": "1"},
                 "google": {"access_token": "t", "developer_token": "d", "customer_id": "1234567890"}}
        for name, c in creds.items():
            for objective in ("sales", "traffic", "leads", "awareness", "whatsapp"):
                with self.subTest(platform=name, objective=objective):
                    make(name, c).create_campaign({**self.CAMPAIGN, "objective": objective})

    def test_rejected_credentials_raise_instead_of_returning_garbage(self):
        from app.platforms import make
        bad_meta = make("meta", {"access_token": "EXPIRED-token", "ad_account_id": "1"})
        with self.assertRaises(httpx.HTTPStatusError):
            bad_meta.list_campaigns()
        bad_tt = make("tiktok", {"access_token": "EXPIRED-token", "advertiser_id": "1"})
        with self.assertRaisesRegex(RuntimeError, "TikTok API: Access token is incorrect"):
            bad_tt.list_campaigns()
        bad_g = make("google", {"access_token": "EXPIRED-token", "developer_token": "d", "customer_id": "1234567890"})
        with self.assertRaises(httpx.HTTPStatusError):
            bad_g.list_campaigns()

    def test_fake_is_strict_like_the_real_apis(self):
        r = httpx.post(f"{self.base}/meta/v26.0/act_1/campaigns", data={"access_token": "t", "name": "x", "objective": "OUTCOME_SALES", "status": "PAUSED"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, 100))           # special_ad_categories is required
        r = httpx.post(f"{self.base}/meta/v26.0/act_1/campaigns", data={"access_token": "t", "name": "x", "objective": "SALES", "status": "PAUSED", "special_ad_categories": "[]"})
        self.assertEqual(r.status_code, 400)                                               # legacy objective names are rejected
        r = httpx.post(f"{self.base}/tiktok/open_api/v1.3/campaign/create/", headers={"Access-Token": "t"}, json={"advertiser_id": "1", "campaign_name": "x"})
        self.assertEqual((r.status_code, r.json()["code"]), (200, 40002))                  # TikTok errors are HTTP 200 + code
        r = httpx.post(f"{self.base}/google/v18/customers/12345/campaigns:mutate", headers={"Authorization": "Bearer t", "developer-token": "d"}, json={"operations": []})
        self.assertEqual(r.json()["error"]["status"], "INVALID_ARGUMENT")                  # customer id must be 10 digits

    def test_base_urls_default_to_production_without_overrides(self):
        from app.platforms import google, meta, tiktok
        with patch.dict(os.environ, {"META_GRAPH_URL": "", "TIKTOK_API_URL": "", "GOOGLE_ADS_API_URL": ""}):
            self.assertEqual(meta.base_url(), "https://graph.facebook.com/v26.0")
            self.assertEqual(tiktok.base_url(), "https://business-api.tiktok.com/open_api/v1.3")
            self.assertEqual(google.base_url(), "https://googleads.googleapis.com/v18")


class ConnectionHealthTests(FakeAdsCase):
    def check(self, service, values):
        from app import health
        return health.check(service, values, force=True)["state"]

    def test_valid_credentials_are_ok_on_every_platform(self):
        self.assertEqual(self.check("meta", {"access_token": "t", "ad_account_id": "123"}), "ok")
        self.assertEqual(self.check("whatsapp", {"token": "t", "phone_id": "109876"}), "ok")
        self.assertEqual(self.check("tiktok", {"access_token": "t", "advertiser_id": "77"}), "ok")
        self.assertEqual(self.check("google", {"access_token": "t", "developer_token": "d", "customer_id": "123-456-7890"}), "ok")

    def test_revoked_or_unauthorised_credentials_are_invalid(self):
        for token in ("EXPIRED-1", "NOPERM-1"):
            with self.subTest(token=token):
                self.assertEqual(self.check("meta", {"access_token": token, "ad_account_id": "123"}), "invalid")
                self.assertEqual(self.check("tiktok", {"access_token": token, "advertiser_id": "77"}), "invalid")
                self.assertEqual(self.check("google", {"access_token": token, "developer_token": "d", "customer_id": "1234567890"}), "invalid")

    def test_rate_limits_are_not_reported_as_bad_credentials(self):
        # A throttled probe says nothing about the key; it must not turn the card red.
        probes = {"meta": {"access_token": "RATELIMIT-1", "ad_account_id": "123"},
                  "whatsapp": {"token": "RATELIMIT-1", "phone_id": "109876"},
                  "tiktok": {"access_token": "RATELIMIT-1", "advertiser_id": "77"},
                  "google": {"access_token": "RATELIMIT-1", "developer_token": "d", "customer_id": "1234567890"}}
        for service, values in probes.items():
            with self.subTest(service=service):
                self.assertEqual(self.check(service, values), "error")

    def test_unreachable_upstream_is_an_error_not_a_crash(self):
        with patch.dict(os.environ, {"META_GRAPH_URL": "http://127.0.0.1:1/meta/v26.0"}):
            self.assertEqual(self.check("meta", {"access_token": "t", "ad_account_id": "123"}), "error")


if __name__ == "__main__":
    unittest.main()
