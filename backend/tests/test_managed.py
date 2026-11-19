"""Managed mode: staff act inside a managed customer's account, everything they change is audited."""
import os
from unittest.mock import patch

from sqlalchemy import text

from tests.support import ApiTestCase


class ManagedModeTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"STAFF_EMAILS": "ONE@example.test"})     # user_one is staff; case does not matter
        env.start()
        self.addCleanup(env.stop)
        self.customer = self.user_ids["user_two"]

    def act(self, account=None):
        self.client.headers["X-Act-As"] = str(account or self.customer)

    def tearDown(self):
        self.client.headers.pop("X-Act-As", None)

    def manage(self):
        r = self.client.post(f"/api/staff/accounts/{self.customer}/mode", json={"mode": "managed"})
        self.assertEqual(r.status_code, 200)

    def test_only_staff_see_or_change_managed_accounts(self):
        self.use("user_two")
        self.assertFalse(self.client.get("/api/auth/me").json()["is_staff"])
        for call in (lambda: self.client.get("/api/staff/accounts"),
                     lambda: self.client.post(f"/api/staff/accounts/{self.customer}/mode", json={"mode": "managed"})):
            self.assertEqual(call().status_code, 403)
        self.use("user_one")
        self.assertTrue(self.client.get("/api/auth/me").json()["is_staff"])
        self.assertEqual(self.client.post(f"/api/staff/accounts/{self.customer}/mode", json={"mode": "agency"}).status_code, 400)
        self.manage()
        self.assertEqual([a["id"] for a in self.client.get("/api/staff/accounts").json()], [self.customer])

    def test_act_as_needs_staff_and_a_managed_account(self):
        self.use("user_two")
        self.act(self.user_ids["user_one"])
        self.assertEqual(self.client.get("/api/campaigns").status_code, 403)         # not staff
        self.use("user_one"); self.act()
        self.assertEqual(self.client.get("/api/campaigns").status_code, 403)         # customer is still self-managed
        self.client.headers.pop("X-Act-As"); self.manage(); self.act()
        self.assertEqual(self.client.get("/api/campaigns").status_code, 200)
        self.client.headers["X-Act-As"] = "abc"
        self.assertEqual(self.client.get("/api/campaigns").status_code, 400)

    def test_staff_work_in_the_customers_account_and_it_is_audited(self):
        self.manage()
        self.use("user_two")
        mine = self.client.post("/api/campaigns", json={"platform": "meta", "name": "Client campaign"}).json()["id"]
        self.use("user_one")
        self.assertNotIn(mine, [c["id"] for c in self.client.get("/api/campaigns").json()])
        self.act()
        me = self.client.get("/api/auth/me").json()
        self.assertEqual((me["id"], me["mode"], me["is_staff"], me["acting_as"]["staff_email"]), (self.customer, "managed", False, "one@example.test"))
        self.assertIn(mine, [c["id"] for c in self.client.get("/api/campaigns").json()])
        new = self.client.post("/api/campaigns", json={"platform": "meta", "name": "By staff"}).json()["id"]
        self.assertEqual(self.client.delete(f"/api/campaigns/{new}").status_code, 204)
        self.client.headers.pop("X-Act-As")
        log = self.client.get("/api/staff/audit", params={"account_id": self.customer}).json()
        actions = [row["action"] for row in log]
        self.assertIn("POST /api/campaigns", actions)
        self.assertIn(f"DELETE /api/campaigns/{new}", actions)
        self.assertIn("set mode managed", actions)
        self.assertFalse([a for a in actions if a.startswith("GET")])                # reads are not logged
        self.assertEqual({row["staff_email"] for row in log}, {"one@example.test"})

    def test_staff_cannot_change_the_customers_connections(self):
        self.manage(); self.act()
        for r in (self.client.put("/api/connections/meta", json={"values": {"access_token": "tok-1234567890", "ad_account_id": "act_1"}}),
                  self.client.delete("/api/connections/meta"), self.client.post("/api/oauth/meta/start")):
            self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/api/connections").status_code, 200)

    def test_opening_an_account_is_audited_and_back_to_self_ends_access(self):
        self.manage()
        self.assertEqual(self.client.post(f"/api/staff/accounts/{self.customer}/open").status_code, 200)
        self.act()
        self.assertEqual(self.client.get("/api/campaigns").status_code, 200)
        self.client.headers.pop("X-Act-As")
        self.client.post(f"/api/staff/accounts/{self.customer}/mode", json={"mode": "self"})
        self.act()
        self.assertEqual(self.client.get("/api/campaigns").status_code, 403)
        self.client.headers.pop("X-Act-As")
        self.assertIn("opened the account", [r["action"] for r in self.client.get("/api/staff/audit", params={"account_id": self.customer}).json()])
