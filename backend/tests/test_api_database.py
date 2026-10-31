from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.support import DatabaseTestCase


class ApiDatabaseTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        from app.main import app
        from app.models import User
        from app.db import session_scope
        with session_scope() as session:
            self.user_ids = {}
            for subject, email in (("user_one", "one@example.test"), ("user_two", "two@example.test")):
                row = User(email=email, password_hash="dormant", clerk_user_id=subject,
                           webhook_key=f"hook-{subject}")
                session.add(row)
                session.flush()
                self.user_ids[subject] = row.id
        self.verifier = patch("app.auth.verify_clerk_request", side_effect=self._claims)
        self.verifier.start()
        self.addCleanup(self.verifier.stop)
        self.recover_patch = patch("app.main.videos.recover")
        self.loop_patch = patch("app.main.whatsapp.followup_loop", new=AsyncMock())
        self.recover_patch.start(); self.loop_patch.start()
        self.addCleanup(self.recover_patch.stop); self.addCleanup(self.loop_patch.stop)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.use("user_one")

    def _claims(self, request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not token and request.method in ("GET", "HEAD"):
            token = request.cookies.get("__session", "")
        return {"sub": token, "sid": "test-session", "iss": "https://example.clerk.accounts.dev", "sts": "active"}

    def use(self, subject):
        self.client.headers["Authorization"] = f"Bearer {subject}"

    def test_existing_identity_mapping_preserves_tenant_isolation_and_demo_seed(self):
        from app.models import Campaign, Metric
        first, second = self.user_ids.values()
        with self.engine.connect() as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(Campaign).where(Campaign.user_id == first)), 0)
        # Existing linked users do not get demo data implicitly; owner filters remain local integer IDs.
        with self.engine.begin() as c:
            c.execute(Campaign.__table__.insert().values(user_id=first, platform="meta", name="one"))
            c.execute(Campaign.__table__.insert().values(user_id=second, platform="meta", name="two"))
        self.assertEqual([x["name"] for x in self.client.get("/api/campaigns").json()], ["one"])
        cid = self.client.get("/api/campaigns").json()[0]["id"]
        self.use("user_two")
        self.assertEqual(self.client.get("/api/campaigns").json()[0]["name"], "two")
        self.assertEqual(self.client.post(f"/api/campaigns/{cid}/status?status=paused").status_code, 404)
        dashboard = self.client.get("/api/dashboard").json()
        self.assertEqual(set(dashboard), {"totals", "daily", "by_platform", "has_demo"})

    def test_credentials_mask_settings_and_whatsapp_attribution(self):
        from app import whatsapp
        user_id = self.user_ids["user_one"]
        save = self.client.put("/api/connections/meta", json={"values": {"access_token": "secret-token-value", "ad_account_id": "123"}})
        self.assertEqual(save.status_code, 200)
        connections = self.client.get("/api/connections").json()
        self.assertNotIn("secret-token-value", str(connections))
        self.client.put("/api/whatsapp/settings", json={"knowledge": "Test shop facts"})
        self.assertEqual(self.client.get("/api/whatsapp/settings").json()["knowledge"], "Test shop facts")
        campaign_id = self.client.post("/api/campaigns", json={"platform": "meta", "name": "Test"}).json()["id"]
        result = self.client.post("/api/whatsapp/simulate", json={"phone": "+15555550111", "text": "I want to buy",
                                                                     "name": "Test", "campaign_id": campaign_id})
        self.assertEqual(result.status_code, 200)
        lead_id = result.json()["lead_id"]
        self.assertEqual(self.client.post(f"/api/leads/{lead_id}/won", json={"value": 89}).status_code, 200)
        self.assertEqual(whatsapp.handle_inbound(user_id, "+15555550112", "hello", wa_id="provider-message-1") > 0, True)
        self.assertEqual(whatsapp.handle_inbound(user_id, "+15555550112", "again", wa_id="provider-message-1"), -1)

    def test_custom_auth_routes_and_old_auth_endpoints_are_absent(self):
        for path in ("/sign-in", "/sign-up", "/forgot-password", "/sso-callback", "/account"):
            result = self.client.get(path)
            self.assertEqual(result.status_code, 200)
            self.assertIn(b"/app.js", result.content)
        self.assertIn(self.client.post("/api/auth/login", json={}).status_code, (404, 405))
        self.assertIn(self.client.post("/api/auth/register", json={}).status_code, (404, 405))

