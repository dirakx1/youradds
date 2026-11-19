"""Multi-user foundations: account mode, one youradds account per connected ad account, and tenant isolation."""
import json

from sqlalchemy import text

from tests.support import ApiTestCase


class MultiUserTests(ApiTestCase):
    def meta(self, ad_account, token="tok-1234567890"):
        return self.client.put("/api/connections/meta", json={"values": {"access_token": token, "ad_account_id": ad_account}})

    def test_accounts_start_self_managed_and_the_database_rejects_other_modes(self):
        self.assertEqual(self.client.get("/api/auth/me").json()["mode"], "self")
        with self.assertRaises(Exception):
            with self.engine.begin() as c:
                c.execute(text("UPDATE yourads.users SET mode='agency' WHERE id=:i"), {"i": self.user_ids["user_one"]})
        with self.engine.begin() as c:
            c.execute(text("UPDATE yourads.users SET mode='managed' WHERE id=:i"), {"i": self.user_ids["user_one"]})
        self.assertEqual(self.client.get("/api/auth/me").json()["mode"], "managed")

    def test_an_ad_account_can_belong_to_only_one_youradds_account(self):
        self.assertEqual(self.meta("act_555").status_code, 200)
        self.use("user_two")
        for spelling in ("act_555", "555", " ACT_555 "):
            with self.subTest(spelling=spelling):
                r = self.meta(spelling)
                self.assertEqual(r.status_code, 409)
                self.assertIn("already connected to another youradds account", r.json()["detail"])
        self.assertFalse([c for c in self.client.get("/api/connections").json() if c["service"] == "meta"][0]["connected"])
        self.assertEqual(self.meta("act_777").status_code, 200)          # a different ad account is fine

    def test_disconnecting_or_switching_accounts_frees_the_ad_account(self):
        self.meta("act_555")
        self.assertEqual(self.meta("act_556").status_code, 200)           # user one switches to another account
        self.use("user_two")
        self.assertEqual(self.meta("act_555").status_code, 200)           # 555 is free again
        self.use("user_one")
        self.assertEqual(self.client.delete("/api/connections/meta").status_code, 200)
        self.use("user_two")
        self.assertEqual(self.client.put("/api/connections/meta", json={"values": {"ad_account_id": "act_556"}}).status_code, 200)

    def test_saving_the_same_account_again_keeps_it(self):
        self.meta("act_555")
        self.assertEqual(self.meta("act_555", token="another-token-12345").status_code, 200)

    def test_each_user_sees_only_their_own_connections_and_campaigns(self):
        self.meta("act_555")
        mine = self.client.post("/api/campaigns", json={"platform": "meta", "name": "Mine"}).json()["id"]
        self.use("user_two")
        self.assertFalse([c for c in self.client.get("/api/connections").json() if c["service"] == "meta"][0]["connected"])
        self.assertNotIn(mine, [c["id"] for c in self.client.get("/api/campaigns").json()])
        self.assertEqual(self.client.post(f"/api/campaigns/{mine}/status", params={"status": "paused"}).status_code, 404)
        self.client.delete(f"/api/campaigns/{mine}")                     # answers 204 either way but must delete nothing of theirs
        self.use("user_one")
        self.assertIn(mine, [c["id"] for c in self.client.get("/api/campaigns").json()])

    def test_accounts_saved_before_ownership_was_tracked_are_linked_once_at_startup(self):
        from app import creds
        from app.db import session_scope
        from app.models import Credential
        blob = creds._fernet().encrypt(json.dumps({"access_token": "t" * 12, "ad_account_id": "act_900"}).encode()).decode()
        with session_scope() as session:            # rows as they were stored before ownership was tracked: no links
            for subject in ("user_one", "user_two"):
                session.add(Credential(user_id=self.user_ids[subject], service="meta", data=blob))
        self.assertEqual(creds.backfill_links(), 1)                       # the first holder keeps it; the second is only logged
        with self.engine.connect() as c:
            owner = c.execute(text("SELECT user_id FROM yourads.connection_links WHERE external_id='900'")).scalar_one()
        self.assertEqual(owner, self.user_ids["user_one"])
        self.assertEqual(creds.backfill_links(), 1)                       # safe to repeat


class FacebookConnectTests(ApiTestCase):
    """Phase 2: the Facebook login stores who connected, enforces one owner, and honours Meta's removal callbacks."""
    def setUp(self):
        import os
        from unittest.mock import patch
        from devtools.fake_ads import env_for, serve_in_thread
        super().setUp()
        base, stop = serve_in_thread()
        self.addCleanup(stop)
        env = patch.dict(os.environ, {**env_for(base), "META_APP_ID": "APP1", "META_APP_SECRET": "SECRET1",
                                      "PUBLIC_BASE_URL": "https://example.test"})
        env.start()
        self.addCleanup(env.stop)

    def login(self, code, subject="user_one"):
        from urllib.parse import parse_qs, urlparse
        self.use(subject)
        state = parse_qs(urlparse(self.client.post("/api/oauth/meta/start").json()["url"]).query)["state"][0]
        r = self.client.get("/api/oauth/meta/callback", params={"code": code, "state": state},
                            headers={"Authorization": ""}, follow_redirects=False)
        return r.headers["location"]

    def signed(self, user_id, secret="SECRET1"):
        import base64, hashlib, hmac
        payload = base64.urlsafe_b64encode(json.dumps({"user_id": user_id, "algorithm": "HMAC-SHA256"}).encode()).decode().rstrip("=")
        sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        return f"{sig}.{payload}"

    def test_the_login_stores_who_connected_and_what_they_allowed(self):
        from app import creds
        self.assertTrue(self.login("ONE1").endswith("meta=connected#connections"))
        saved = creds.get(self.user_ids["user_one"], "meta")
        self.assertEqual((saved["meta_user_id"], saved["meta_user_name"]), ("fbuser-ONE1", "Fake Owner"))
        self.assertEqual(saved["scopes"], "ads_management,ads_read,pages_show_list")      # only what was granted
        state = self.client.get("/api/oauth/meta/state").json()
        self.assertEqual(state["scopes"], ["ads_management", "ads_read", "pages_show_list"])
        with self.engine.connect() as c:
            row = c.execute(text("SELECT source, expires_at FROM yourads.credentials WHERE service='meta'")).one()
        self.assertEqual(row[0], "oauth")
        self.assertTrue(row[1])

    def test_a_facebook_login_or_ad_account_already_used_elsewhere_is_refused(self):
        from app import creds
        self.assertTrue(self.login("ONE1").endswith("meta=connected#connections"))
        self.assertTrue(self.login("ONE1", "user_two").endswith("meta=taken#connections"))        # same Facebook person
        self.assertFalse(creds.get(self.user_ids["user_two"], "meta").get("access_token"))
        self.assertTrue(self.login("ONE2", "user_two").endswith("meta=taken#connections"))        # other person, same ad account
        kept = creds.get(self.user_ids["user_two"], "meta")
        self.assertEqual((bool(kept.get("access_token")), kept.get("ad_account_id")), (True, None))   # login kept, picker decides
        picked = self.client.post("/api/oauth/meta/select", json={"ad_account_id": "act_1001"})
        self.assertEqual(picked.status_code, 409)

    def test_meta_removal_callbacks_need_a_valid_signature_and_delete_the_connection(self):
        from app import creds
        self.login("ONE1")
        uid = self.user_ids["user_one"]
        hdr = {"Authorization": "", "Content-Type": "application/x-www-form-urlencoded"}
        for bad in (self.signed("fbuser-ONE1", secret="WRONG"), "garbage", ""):
            r = self.client.post("/api/oauth/meta/deauthorize", content=f"signed_request={bad}", headers=hdr)
            self.assertEqual(r.status_code, 400)
        self.assertTrue(creds.get(uid, "meta").get("access_token"))
        r = self.client.post("/api/oauth/meta/data-deletion", content=f"signed_request={self.signed('fbuser-ONE1')}", headers=hdr)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["url"].startswith("https://example.test/api/oauth/meta/deletion-status?code="))
        self.assertEqual(creds.get(uid, "meta"), {})
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT count(*) FROM yourads.connection_links")).scalar_one(), 0)
        self.assertEqual(self.client.get(r.json()["url"].replace("https://example.test", ""), headers={"Authorization": ""}).status_code, 200)
        self.login("ONE1")                                                                       # reconnecting later works
        r = self.client.post("/api/oauth/meta/deauthorize", content=f"signed_request={self.signed('fbuser-ONE1')}", headers=hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(creds.get(uid, "meta"), {})

    def test_facebook_login_for_business_sends_the_configuration_instead_of_scopes(self):
        import os
        from unittest.mock import patch
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.client.post("/api/oauth/meta/start").json()["url"]).query)
        self.assertIn("scope", q)
        self.assertNotIn("config_id", q)
        with patch.dict(os.environ, {"META_LOGIN_CONFIG_ID": "CFG123"}):
            q = parse_qs(urlparse(self.client.post("/api/oauth/meta/start").json()["url"]).query)
        self.assertEqual((q["config_id"], q["override_default_response_type"], q["response_type"]), (["CFG123"], ["true"], ["code"]))
        self.assertNotIn("scope", q)
