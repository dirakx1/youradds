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
