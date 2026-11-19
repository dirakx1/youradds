"""Managed mode: staff act inside a managed customer's account, everything they change is audited."""
import os
from unittest.mock import patch

from sqlalchemy import text

from tests.support import ApiTestCase


class ManagedBase(ApiTestCase):
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



class ManagedModeTests(ManagedBase):
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

    def test_staff_find_accounts_by_email(self):
        self.use("user_two")
        self.assertEqual(self.client.get("/api/staff/lookup", params={"email": "one"}).status_code, 403)
        self.use("user_one")
        self.assertEqual(self.client.get("/api/staff/lookup", params={"email": "tw"}).status_code, 400)
        found = self.client.get("/api/staff/lookup", params={"email": "TWO@Example"}).json()
        self.assertEqual([(a["id"], a["email"], a["mode"]) for a in found], [(self.customer, "two@example.test", "self")])
        self.assertEqual(len(self.client.get("/api/staff/lookup", params={"email": "@example.test"}).json()), 2)
        self.assertEqual(self.client.get("/api/staff/lookup", params={"email": "%%%"}).json(), [])        # wildcards are literal

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


class SpendApprovalTests(ManagedBase):
    """Staff prepare; the client confirms before money is spent."""
    def setUp(self):
        super().setUp()
        self.manage()
        self.use("user_two")
        self.cid = self.client.post("/api/campaigns", json={"platform": "meta", "name": "Client campaign", "daily_budget": 20000}).json()["id"]
        self.client.post(f"/api/campaigns/{self.cid}/status", params={"status": "paused"})
        self.use("user_one")

    def status_of(self, subject="user_two"):
        self.use(subject)
        return [c["status"] for c in self.client.get("/api/campaigns").json() if c["id"] == self.cid][0]

    def test_staff_activation_waits_for_the_client_who_then_approves_or_declines(self):
        self.act()
        r = self.client.post(f"/api/campaigns/{self.cid}/status", params={"status": "active"}).json()
        self.assertEqual((r["ok"], r["approval"]), (False, "pending"))
        again = self.client.post(f"/api/campaigns/{self.cid}/status", params={"status": "active"}).json()
        self.assertEqual(again["approval_id"], r["approval_id"])                   # one open request per campaign
        self.assertEqual(self.client.post(f"/api/campaigns/{self.cid}/status", params={"status": "paused"}).json()["ok"], True)   # pausing never needs approval
        self.assertEqual(len(self.client.get("/api/approvals").json()), 1)          # staff can see it is waiting
        self.assertEqual(self.client.post(f"/api/approvals/{r['approval_id']}/approve").status_code, 403)   # but not decide
        self.client.headers.pop("X-Act-As")
        self.assertEqual(self.status_of(), "paused")
        waiting = self.client.get("/api/approvals").json()
        self.assertEqual((len(waiting), waiting[0]["name"], waiting[0]["requested_by"]), (1, "Client campaign", "one@example.test"))
        self.assertEqual(self.client.post(f"/api/approvals/{r['approval_id']}/approve").json()["ok"], True)
        self.assertEqual(self.status_of("user_two"), "active")
        self.assertEqual(self.client.get("/api/approvals").json(), [])
        self.assertEqual(self.client.post(f"/api/approvals/{r['approval_id']}/approve").status_code, 404)   # decided once

    def test_declining_keeps_the_campaign_paused(self):
        self.act()
        aid = self.client.post(f"/api/campaigns/{self.cid}/status", params={"status": "active"}).json()["approval_id"]
        self.client.headers.pop("X-Act-As")
        self.use("user_two")
        self.assertEqual(self.client.post(f"/api/approvals/{aid}/decline").json()["ok"], True)
        self.assertEqual(self.status_of("user_two"), "paused")

    def test_only_the_owner_decides_their_own_requests(self):
        self.act()
        aid = self.client.post(f"/api/campaigns/{self.cid}/status", params={"status": "active"}).json()["approval_id"]
        self.client.headers.pop("X-Act-As")
        self.use("user_one")                                                         # staff as themselves: another account's request is not theirs
        self.assertEqual(self.client.post(f"/api/approvals/{aid}/approve").status_code, 404)
        self.assertEqual(self.status_of("user_two"), "paused")

    def test_a_client_who_allows_staff_activation_skips_the_wait(self):
        self.use("user_one")
        self.assertEqual(self.client.put("/api/approvals/settings", json={"staff_activation": True}).status_code, 403)   # not a managed account
        self.use("user_two")
        self.assertEqual(self.client.put("/api/approvals/settings", json={"staff_activation": True}).json()["staff_activation"], True)
        self.use("user_one"); self.act()
        self.assertEqual(self.client.put("/api/approvals/settings", json={"staff_activation": False}).status_code, 403)   # staff cannot change it
        self.assertEqual(self.client.post(f"/api/campaigns/{self.cid}/status", params={"status": "active"}).json()["ok"], True)
        self.client.headers.pop("X-Act-As")
        self.assertEqual(self.status_of("user_two"), "active")

    def test_a_client_activates_their_own_campaign_without_approval(self):
        self.use("user_two")
        self.assertEqual(self.client.post(f"/api/campaigns/{self.cid}/status", params={"status": "active"}).json()["ok"], True)
        self.assertEqual(self.client.get("/api/approvals").json(), [])
