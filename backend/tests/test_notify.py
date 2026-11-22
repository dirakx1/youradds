"""Managed-mode emails: who gets told what, once, and never at the cost of a request."""
import os
from unittest.mock import patch

from tests.test_managed import ManagedBase


class NotifyTests(ManagedBase):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"MAIL_SYNC": "1", "SES_SMTP_USER": "u", "SES_SMTP_PASSWORD": "p",
                                      "MAIL_FROM": "youradds <no-reply@youradds.co>", "PUBLIC_BASE_URL": "https://youradds.co"})
        env.start()
        self.addCleanup(env.stop)
        self.sent = []
        smtp = patch("app.mailer._smtp_send", side_effect=self.sent.append)
        smtp.start()
        self.addCleanup(smtp.stop)

    def mails(self, to=None):
        return [m for m in self.sent if to is None or to in m["To"]]

    def test_nothing_is_sent_and_nothing_breaks_when_email_is_not_configured(self):
        with patch.dict(os.environ, {"SES_SMTP_USER": ""}):
            self.use("user_two")
            self.assertEqual(self.client.post("/api/managed/request").status_code, 200)
        self.assertEqual(self.sent, [])

    def test_a_failing_mail_server_never_fails_the_request(self):
        with patch("app.mailer._smtp_send", side_effect=OSError("down")):
            self.use("user_two")
            self.assertEqual(self.client.post("/api/managed/request").status_code, 200)

    def test_request_tells_staff_and_marking_managed_tells_the_client(self):
        self.use("user_two")
        self.client.post("/api/managed/request")
        self.client.post("/api/managed/request")                                       # asking again is not news
        self.assertEqual(len(self.sent), 1)
        self.assertIn("two@example.test", self.sent[0].get_content())
        self.assertIn("/#staff", self.sent[0].get_content())
        self.use("user_one")
        self.manage()
        client = self.mails("two@example.test")
        self.assertEqual(len(client), 1)
        self.assertIn("ya administra", client[0]["Subject"])
        self.assertIn("now manages", client[0]["Subject"])
        self.assertEqual(client[0]["From"], "youradds <no-reply@youradds.co>")
        self.manage()                                                                  # already managed: no second email
        self.assertEqual(len(self.mails("two@example.test")), 1)

    def test_an_approval_request_emails_the_client_once(self):
        self.manage()
        self.use("user_two")
        cid = self.client.post("/api/campaigns", json={"platform": "meta", "name": "Cafe promo", "daily_budget": 20000}).json()["id"]
        self.client.post(f"/api/campaigns/{cid}/status", params={"status": "paused"})
        self.sent.clear()
        self.use("user_one"); self.act()
        self.client.post(f"/api/campaigns/{cid}/status", params={"status": "active"})
        self.client.post(f"/api/campaigns/{cid}/status", params={"status": "active"})   # same open request
        mails = self.mails("two@example.test")
        self.assertEqual(len(mails), 1)
        self.assertIn("Cafe promo", mails[0].get_content())
        self.assertIn("aprobación", mails[0]["Subject"])

    def test_ending_management_emails_the_instructions(self):
        self.manage(); self.sent.clear()
        self.use("user_two")
        self.client.post("/api/managed/end")
        client = self.mails("two@example.test")
        self.assertEqual(len(client), 1)
        self.assertIn("Partners", client[0].get_content())
        self.assertIn("Socios", client[0].get_content())
        self.assertEqual(len(self.mails("one@example.test")), 1)                        # staff are told a client left
        self.sent.clear()
        self.use("user_one"); self.manage(); self.sent.clear()
        self.client.post(f"/api/staff/accounts/{self.customer}/mode", json={"mode": "self"})
        self.assertIn("terminó la administración", self.mails("two@example.test")[0].get_content())

    def test_staff_activity_reaches_the_client_as_one_digest(self):
        from app import notify
        self.manage(); self.sent.clear()
        self.use("user_one"); self.act()
        self.client.post("/api/campaigns", json={"platform": "meta", "name": "By staff", "daily_budget": 1000})
        self.assertEqual(notify.send_digests(), 1)
        mail = self.mails("two@example.test")[0]
        self.assertIn("Creó una campaña", mail.get_content())
        self.assertIn("Created a campaign", mail.get_content())
        self.assertNotIn("/api/", mail.get_content())                                   # plain words, not requests
        self.assertEqual(notify.send_digests(), 0)                                      # nothing new: nothing sent
        self.client.post("/api/campaigns", json={"platform": "meta", "name": "Another", "daily_budget": 1000})
        self.assertEqual(notify.send_digests(), 1)
        self.assertEqual(len(self.mails("two@example.test")), 2)

    def test_a_digest_that_fails_to_send_is_tried_again_and_the_clients_own_actions_are_not_reported(self):
        from app import notify
        self.manage(); self.sent.clear()
        self.use("user_one"); self.act()
        self.client.post("/api/campaigns", json={"platform": "meta", "name": "By staff", "daily_budget": 1000})
        with patch("app.mailer._smtp_send", side_effect=OSError("down")):
            self.assertEqual(notify.send_digests(), 0)
        self.assertEqual(notify.send_digests(), 1)
        self.client.headers.pop("X-Act-As")
        self.use("user_two")
        self.client.post("/api/managed/end")                                            # the client's own entry
        self.sent.clear()
        self.assertEqual(notify.send_digests(), 0)
