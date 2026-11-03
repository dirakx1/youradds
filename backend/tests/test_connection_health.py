from unittest.mock import patch, MagicMock

from tests import test_api_database as _m


def _resp(status, body=None, text=""):
    r = MagicMock(status_code=status, text=text)
    r.json.return_value = body or {}
    return r


class ConnectionHealthTests(_m.ApiDatabaseTests):
    def setUp(self):
        super().setUp()
        from app import health
        health._cache.clear()

    def test_higgsfield_states(self):
        self.client.put("/api/connections/higgsfield", json={"values": {"api_key": "id:secret"}})
        for status, text, want in ((200, "", "ok"), (401, "", "invalid"), (402, "", "no_balance"),
                                   (400, "Insufficient balance", "no_balance"), (503, "", "error")):
            with self.subTest(status=status), patch("app.health.httpx.post", return_value=_resp(status, {"usd": 1}, text)):
                body = self.client.get("/api/connections/status?force=true").json()
                self.assertEqual(body["higgsfield"]["state"], want)
                self.assertNotIn("id:secret", str(body))
        self.assertEqual(body["meta"]["state"], "not_connected")

    def test_meta_invalid_and_unreachable(self):
        import httpx
        self.client.put("/api/connections/meta", json={"values": {"access_token": "tok", "ad_account_id": "1"}})
        with patch("app.health.httpx.get", return_value=_resp(400, {"error": {"message": "Invalid OAuth access token"}})):
            self.assertEqual(self.client.get("/api/connections/status?force=true").json()["meta"]["state"], "invalid")
        with patch("app.health.httpx.get", side_effect=httpx.ConnectError("x")):
            self.assertEqual(self.client.get("/api/connections/status?force=true").json()["meta"]["state"], "error")

    def test_result_is_cached_until_forced(self):
        self.client.put("/api/connections/higgsfield", json={"values": {"api_key": "id:secret"}})
        with patch("app.health.httpx.post", return_value=_resp(200)) as post:
            self.client.get("/api/connections/status"); self.client.get("/api/connections/status")
            self.assertEqual(post.call_count, 1)
            self.client.get("/api/connections/status?force=true")
            self.assertEqual(post.call_count, 2)

    def test_server_key_with_sharing_off_is_reported(self):
        with patch("app.creds.env", side_effect=lambda n: "a:b" if n == "HIGGSFIELD_API_KEY" else ""):
            self.assertEqual(self.client.get("/api/connections/status").json()["higgsfield"]["state"], "server_key")

    def test_requires_login(self):
        del self.client.headers["Authorization"]
        self.assertEqual(self.client.get("/api/connections/status").status_code, 401)


