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

    def raise_budget(self, value, cid=None):
        return self.client.post("/api/ai/apply", json={"campaign_id": cid or self.cid, "action": "budget", "value": value})

    def budget_of(self, subject="user_two"):
        self.use(subject)
        return [c["daily_budget"] for c in self.client.get("/api/campaigns").json() if c["id"] == self.cid][0]

    def test_staff_budget_increases_wait_for_the_client_but_decreases_do_not(self):
        self.act()
        self.assertEqual(self.raise_budget(10000).json()["ok"], True)                 # lower: goes through
        self.assertEqual(self.budget_of("user_one"), 10000)
        self.act()
        first = self.raise_budget(30000).json()
        self.assertEqual((first["ok"], first["approval"]), (False, "pending"))
        second = self.raise_budget(40000).json()
        self.assertEqual(second["approval_id"], first["approval_id"])                 # one open raise; the latest amount wins
        self.assertEqual(self.budget_of("user_one"), 10000)                           # nothing changed yet
        self.client.headers.pop("X-Act-As")
        self.use("user_two")
        waiting = self.client.get("/api/approvals").json()
        self.assertEqual((waiting[0]["kind"], waiting[0]["value"]), ("budget", 40000))
        self.assertEqual(self.client.post(f"/api/approvals/{first['approval_id']}/approve").json()["ok"], True)
        self.assertEqual(self.budget_of(), 40000)

    def test_a_raise_after_activation_was_requested_voids_that_activation(self):
        self.act()
        activate = self.client.post(f"/api/campaigns/{self.cid}/status", params={"status": "active"}).json()["approval_id"]
        self.raise_budget(10000)                                                      # lowering is fine and keeps the request valid
        self.client.headers.pop("X-Act-As")
        self.use("user_two")
        self.assertEqual(self.raise_budget(90000).json()["ok"], True)                 # the client raising it themselves
        r = self.client.post(f"/api/approvals/{activate}/approve")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.status_of("user_two"), "paused")
        self.assertEqual(self.client.get("/api/approvals").json(), [])


class PartnerConnectionTests(ManagedBase):
    """A client who shared their ad account with youradds is connected through the server's system token, which nobody sees."""
    TOKEN = "SYSTEM-TOKEN-ZQXJ-QQKV"

    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"META_SYSTEM_TOKEN": self.TOKEN})
        env.start()
        self.addCleanup(env.stop)
        from app import creds
        creds.save_partner(self.customer, "act_777", "100", "200")

    def meta_card(self, subject="user_two"):
        self.use(subject)
        return [c for c in self.client.get("/api/connections").json() if c["service"] == "meta"][0]

    def test_the_token_is_added_when_read_and_never_stored_or_shown(self):
        from app import creds
        self.assertEqual(creds.get(self.customer, "meta")["access_token"], self.TOKEN)
        self.assertNotIn("access_token", creds.get(self.customer, "meta", raw=True))
        with self.engine.connect() as c:
            row = c.execute(text("SELECT source, expires_at, data FROM yourads.credentials WHERE service='meta'")).one()
        self.assertEqual((row[0], row[1]), ("partner", None))
        self.assertNotIn(self.TOKEN, creds._fernet().decrypt(row[2].encode()).decode())
        card = self.meta_card()
        self.assertEqual((card["connected"], card["partner"]), (True, True))
        self.assertNotIn(self.TOKEN, str(self.client.get("/api/connections").json()))
        self.assertNotIn(self.TOKEN[-4:], str(card))
        self.assertTrue(self.client.get("/api/oauth/meta/state").json()["partner"])

    def test_without_the_server_token_the_connection_does_not_work(self):
        from app import creds
        with patch.dict(os.environ, {"META_SYSTEM_TOKEN": ""}):
            self.assertFalse(creds.connected(self.customer, "meta"))

    def test_the_ad_account_is_owned_once_and_cannot_be_moved_by_the_client(self):
        self.use("user_one")
        r = self.client.put("/api/connections/meta", json={"values": {"access_token": "tok-1234567890", "ad_account_id": "act_777"}})
        self.assertEqual(r.status_code, 409)
        self.use("user_two")
        for call in (lambda: self.client.put("/api/connections/meta", json={"values": {"ad_account_id": "act_888"}}),
                     lambda: self.client.get("/api/oauth/meta/accounts"),
                     lambda: self.client.post("/api/oauth/meta/select", json={"ad_account_id": "act_888"})):
            self.assertEqual(call().status_code, 403)
        from app import creds
        self.assertEqual(creds.get(self.customer, "meta", raw=True)["ad_account_id"], "act_777")

    def test_a_client_can_disconnect_or_replace_it_with_their_own_token(self):
        from app import creds
        self.use("user_two")
        r = self.client.put("/api/connections/meta", json={"values": {"access_token": "own-token-1234567", "ad_account_id": "act_999"}})
        self.assertEqual(r.status_code, 200)
        stored = creds.get(self.customer, "meta", raw=True)
        self.assertEqual((stored.get("partner"), stored["access_token"]), (None, "own-token-1234567"))
        creds.save_partner(self.customer, "act_777")
        self.assertEqual(self.client.delete("/api/connections/meta").status_code, 200)
        self.assertEqual(creds.get(self.customer, "meta"), {})


class PartnerOnboardingTests(ManagedBase):
    """Staff check what a client shared with youradds' business before connecting it."""
    def setUp(self):
        super().setUp()
        import httpx
        from devtools.fake_ads import env_for, serve_in_thread
        base, stop = serve_in_thread()
        self.addCleanup(stop)
        env = patch.dict(os.environ, {**env_for(base), "META_SYSTEM_TOKEN": "SYSTEM-TOKEN-ZQXJ"})
        env.start()
        self.addCleanup(env.stop)
        self.flags = lambda **f: httpx.post(base + "/__flags", json=f)
        self.flags(partner_accounts={"act_777": {"name": "Cafe Client", "currency": "COP", "status": 1}},
                   partner_pages={"100": {"name": "Cafe Page", "instagram": "200"}})
        self.manage()
        self.url = f"/api/staff/accounts/{self.customer}/partner"
        self.body = {"ad_account_id": "act_777", "page_id": "100"}

    def test_only_staff_and_only_for_managed_accounts(self):
        self.use("user_two")
        self.assertEqual(self.client.post(self.url + "/verify", json=self.body).status_code, 403)
        self.use("user_one")
        self.client.post(f"/api/staff/accounts/{self.customer}/mode", json={"mode": "self"})
        self.assertEqual(self.client.post(self.url + "/verify", json=self.body).status_code, 403)
        self.manage()
        self.assertEqual(self.client.post(self.url + "/verify", json={"ad_account_id": "abc", "page_id": "100"}).status_code, 400)

    def test_verify_reports_what_is_missing(self):
        ok = self.client.post(self.url + "/verify", json=self.body).json()
        self.assertEqual((ok["ready"], ok["problems"], ok["account"]["name"], ok["page"]["instagram_id"]), (True, [], "Cafe Client", "200"))
        nothing = self.client.post(self.url + "/verify", json={"ad_account_id": "999", "page_id": "888"}).json()
        self.assertEqual((nothing["ready"], nothing["problems"]), (False, ["not_shared", "page_not_shared"]))
        self.flags(partner_accounts={"act_777": {"name": "Cafe Client", "currency": "COP", "status": 2}})
        self.assertEqual(self.client.post(self.url + "/verify", json=self.body).json()["problems"], ["restricted"])
        self.flags(partner_accounts={"act_777": {"name": "Cafe Client", "currency": "COP", "status": 1, "payment": False}})
        self.assertEqual(self.client.post(self.url + "/verify", json=self.body).json()["problems"], ["no_payment"])
        with patch.dict(os.environ, {"META_SYSTEM_TOKEN": ""}):
            self.assertEqual(self.client.post(self.url + "/verify", json=self.body).json()["problems"], ["no_token"])

    def test_connect_saves_a_partner_connection_and_is_audited(self):
        from app import creds
        r = self.client.post(self.url + "/connect", json=self.body)
        self.assertEqual((r.status_code, r.json()["ok"]), (200, True))
        stored = creds.get(self.customer, "meta", raw=True)
        self.assertEqual((stored["partner"], stored["ad_account_id"], stored["page_id"], stored["instagram_account_id"]), ("1", "act_777", "100", "200"))
        self.assertTrue(creds.connected(self.customer, "meta"))
        self.assertEqual(self.client.get(self.url).json()["ad_account_id"], "act_777")
        self.use("user_two")
        self.assertTrue(self.client.get("/api/oauth/meta/state").json()["partner"])
        self.use("user_one")
        actions = [a["action"] for a in self.client.get("/api/staff/audit", params={"account_id": self.customer}).json()]
        self.assertIn("connected partner ad account act_777", actions)

    def test_connect_refuses_when_not_ready_or_already_owned_or_the_client_has_their_own(self):
        from app import creds
        self.flags(partner_accounts={"act_777": {"name": "Cafe Client", "currency": "COP", "status": 1, "payment": False}})
        r = self.client.post(self.url + "/connect", json=self.body)
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "not ready: no_payment"))
        self.assertEqual(creds.get(self.customer, "meta", raw=True), {})
        self.flags(partner_accounts={"act_777": {"name": "Cafe Client", "currency": "COP", "status": 1}})
        creds.save_partner(self.user_ids["user_one"], "act_777")                           # someone else already owns it
        self.assertEqual(self.client.post(self.url + "/connect", json=self.body).status_code, 409)
        creds._save(self.user_ids["user_one"], "meta", {})
        creds._save(self.customer, "meta", {"access_token": "own-token-1234567", "ad_account_id": "act_5"})
        self.assertEqual(self.client.post(self.url + "/connect", json=self.body).status_code, 409)

    def test_disconnecting_or_ending_management_removes_the_access(self):
        from app import creds
        self.client.post(self.url + "/connect", json=self.body)
        self.assertEqual(self.client.post(self.url + "/disconnect").status_code, 200)
        self.assertEqual(creds.get(self.customer, "meta", raw=True), {})
        self.assertEqual(self.client.post(self.url + "/disconnect").status_code, 409)
        self.client.post(self.url + "/connect", json=self.body)
        self.client.post(f"/api/staff/accounts/{self.customer}/mode", json={"mode": "self"})
        self.assertEqual(creds.get(self.customer, "meta", raw=True), {})


class ManagedRequestTests(ManagedBase):
    """A client can ask for managed mode; staff see the request and answer it by marking the account managed."""
    def test_a_client_asks_staff_see_it_and_marking_the_account_answers_it(self):
        self.use("user_two")
        self.assertEqual(self.client.get("/api/managed/state").json()["mode"], "self")
        first = self.client.post("/api/managed/request").json()
        self.assertTrue(first["requested_at"])
        self.assertEqual(self.client.post("/api/managed/request").json()["requested_at"], first["requested_at"])   # asking twice keeps the first time
        self.assertEqual(self.client.get("/api/staff/requests").status_code, 403)
        self.use("user_one")
        waiting = self.client.get("/api/staff/requests").json()
        self.assertEqual([(r["id"], r["email"]) for r in waiting], [(self.customer, "two@example.test")])
        self.manage()
        self.assertEqual(self.client.get("/api/staff/requests").json(), [])
        self.use("user_two")
        state = self.client.get("/api/managed/state").json()
        self.assertEqual((state["mode"], state["requested_at"]), ("managed", None))
        self.assertEqual(self.client.post("/api/managed/request").status_code, 409)

    def test_a_client_can_withdraw_and_staff_cannot_ask_on_their_behalf(self):
        self.use("user_two")
        self.client.post("/api/managed/request")
        self.assertIsNone(self.client.delete("/api/managed/request").json()["requested_at"])
        self.use("user_one")
        self.assertEqual(self.client.get("/api/staff/requests").json(), [])
        self.manage(); self.act()
        self.assertEqual(self.client.post("/api/managed/request").status_code, 403)

    def test_the_state_shows_what_to_share_and_whether_access_is_connected(self):
        from app import creds
        with patch.dict(os.environ, {"YOURADS_BUSINESS_ID": "1234567890", "YOURADS_BUSINESS_NAME": "KubeAI LLC"}):
            self.use("user_two")
            s = self.client.get("/api/managed/state").json()
            self.assertEqual((s["business_id"], s["business_name"], s["partner"]), ("1234567890", "KubeAI LLC", False))
            creds.save_partner(self.customer, "act_9", "5")
            self.assertTrue(self.client.get("/api/managed/state").json()["partner"])
