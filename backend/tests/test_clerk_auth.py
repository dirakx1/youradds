import base64
import json
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from tests.support import configure_test_environment


class ClerkVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = configure_test_environment()
        from app import config
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import hashes
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        from cryptography.hazmat.primitives import serialization
        config.CLERK_JWT_KEY = cls.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def token(self, **overrides):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        now = int(time.time())
        payload = {"iss": "https://example.clerk.accounts.dev", "sub": "user_test", "sid": "sess_test",
                   "sts": "active", "azp": "http://localhost:8000", "iat": now, "exp": now + 60, "v": 2}
        payload.update(overrides)
        encode = lambda value: base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=")
        head = encode({"alg": "RS256", "typ": "JWT", "kid": "test"})
        body = head + b"." + encode(payload)
        signature = self.private_key.sign(body, padding.PKCS1v15(), hashes.SHA256())
        return (body + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()

    @staticmethod
    def request(method="GET", headers=(), cookies=()):
        scope = {"type": "http", "http_version": "1.1", "method": method, "scheme": "http",
                 "path": "/api/test", "raw_path": b"/api/test", "query_string": b"", "root_path": "",
                 "headers": list(headers), "client": ("127.0.0.1", 1), "server": ("localhost", 8000)}
        if cookies:
            scope["headers"].append((b"cookie", b"; ".join(k + b"=" + v for k, v in cookies)))
        return Request(scope)

    def test_sdk_verifies_signed_session_and_rejects_invalid_claims(self):
        from app.auth import verify_clerk_request
        good = self.token()
        self.assertEqual(verify_clerk_request(self.request(headers=[(b"authorization", f"Bearer {good}".encode())]))["sub"], "user_test")
        for claims in ({"iss": "https://wrong.example"}, {"azp": "https://wrong.example"},
                       {"sub": ""}, {"sid": ""}, {"sts": "pending"}, {"exp": int(time.time()) - 10}):
            with self.subTest(claims=claims), self.assertRaises(HTTPException) as raised:
                verify_clerk_request(self.request(headers=[(b"authorization", f"Bearer {self.token(**claims)}".encode())]))
            self.assertEqual(raised.exception.status_code, 401)

    def test_cookie_is_read_only_and_invalid_bearer_never_falls_back(self):
        from app.auth import verify_clerk_request
        good = self.token()
        self.assertEqual(verify_clerk_request(self.request("GET", cookies=[(b"__session", good.encode())]))["sid"], "sess_test")
        for method in ("POST", "PUT", "DELETE"):
            with self.subTest(method=method), self.assertRaises(HTTPException) as raised:
                verify_clerk_request(self.request(method, cookies=[(b"__session", good.encode())]))
            self.assertEqual(raised.exception.status_code, 401)
        with self.assertRaises(HTTPException) as raised:
            verify_clerk_request(self.request("GET", headers=[(b"authorization", b"Bearer invalid")],
                                              cookies=[(b"__session", good.encode())]))
        self.assertEqual(raised.exception.status_code, 401)

    def test_old_local_cookie_is_not_an_authentication_credential(self):
        from app.auth import verify_clerk_request
        with self.assertRaises(HTTPException) as raised:
            verify_clerk_request(self.request("GET", cookies=[(b"yourads_session", b"legacy-token")]))
        self.assertEqual(raised.exception.status_code, 401)

