import json
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vskr import auth
from vskr.api import app
from vskr.auth import hash_password

from tests.asgi_client import ASGITestClient

TEST_PASSWORD = secrets.token_urlsafe(24)
TEST_HASH = hash_password(TEST_PASSWORD)


class TestAdminAuth(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.TemporaryDirectory()
        with auth._sessions_lock:
            auth._sessions.clear()
        with auth._failures_lock:
            auth._failures.clear()
        self.env = {
            "VSKR__PATHS__DATA_DIR": self.data_dir.name,
            "VSKR_ADMIN_USER": "test-admin",
            "VSKR_ADMIN_PASSWORD_HASH": TEST_HASH,
            "VSKR_COOKIE_SECURE": "false",
        }

    def tearDown(self):
        self.data_dir.cleanup()

    def test_startup_warns_when_egress_firewall_is_not_confirmed(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertLogs("root", level="WARNING") as logs:
            auth.warn_if_unconfigured()
        self.assertTrue(any("VSKR_EGRESS_FIREWALL_CONFIRMED" in line for line in logs.output))

    def test_startup_does_not_warn_about_egress_when_operator_confirmed(self):
        with mock.patch.dict(os.environ, {"VSKR_EGRESS_FIREWALL_CONFIRMED": "true"}, clear=True), \
                self.assertLogs("root", level="WARNING") as logs:
            auth.warn_if_unconfigured()
        self.assertFalse(any("Egress firewall is not confirmed" in line for line in logs.output))

    def test_secure_cookie_trusts_forwarded_https_only_from_configured_proxy(self):
        from starlette.requests import Request

        def request_from(peer, forwarded_headers):
            headers = [(b"host", b"vskr-internal:8000"), *forwarded_headers]
            return Request({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": "http", "path": "/api/user/me",
                "raw_path": b"/api/user/me", "query_string": b"", "root_path": "",
                "headers": headers, "server": ("vskr", 8000), "client": (peer, 1234),
            })

        forwarded = [(b"x-forwarded-proto", b"https"), (b"x-forwarded-host", b"learn.example.test")]
        with mock.patch.dict(os.environ, {
            "VSKR_COOKIE_SECURE": "true", "VSKR_TRUSTED_PROXIES": "10.0.0.0/8",
        }):
            trusted = auth._secure_cookie_request(request_from("10.2.3.4", forwarded))
            untrusted = auth._secure_cookie_request(request_from("192.0.2.5", forwarded))
        self.assertTrue(trusted)
        self.assertFalse(untrusted)

    def test_secure_cookie_accepts_trusted_ipv6_proxy_and_rejects_bad_forwarded_proto(self):
        from starlette.requests import Request

        def request_with(proto):
            return Request({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": "http", "path": "/", "raw_path": b"/",
                "query_string": b"", "root_path": "",
                "headers": [
                    (b"host", b"internal:8000"), (b"x-forwarded-host", b"app.example.test"),
                    (b"x-forwarded-proto", proto),
                ],
                "server": ("app", 8000), "client": ("2001:db8::1", 443),
            })

        with mock.patch.dict(os.environ, {
            "VSKR_COOKIE_SECURE": "true", "VSKR_TRUSTED_PROXIES": "2001:db8::/32",
        }):
            trusted = auth._secure_cookie_request(request_with(b"https"))
            malformed = auth._secure_cookie_request(request_with(b"https,http"))
        self.assertTrue(trusted)
        self.assertFalse(malformed)

    def test_login_sets_http_only_same_site_cookie_and_returns_only_csrf(self):
        with mock.patch.dict(os.environ, self.env):
            with ASGITestClient(app) as client:
                response = client.post(
                    "/api/auth/login",
                    json={"username": "test-admin", "password": TEST_PASSWORD},
                    headers={"Origin": "http://localhost"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["admin"], True)
                self.assertIn("csrf_token", response.json())
                self.assertEqual(client.get("/api/auth/me").json()["admin_url"], "/admin")
                self.assertNotIn(TEST_PASSWORD, response.text)
                cookie = response.headers["set-cookie"].lower()
                self.assertIn("vskr_admin=", cookie)
                self.assertIn("httponly", cookie)
                self.assertIn("samesite=strict", cookie)
                self.assertIn("path=/", cookie)
                self.assertNotIn("secure", cookie)
                self.assertEqual(client.get("/api/auth/me").json()["admin"], True)
                admin_page = client.get("/admin")
                self.assertEqual(admin_page.status_code, 200)
                self.assertIn("VSKR · Quản trị", admin_page.text)

    def test_cookie_secure_defaults_to_true(self):
        env = {key: value for key, value in self.env.items() if key != "VSKR_COOKIE_SECURE"}
        env["VSKR_COOKIE_SECURE"] = "true"
        with mock.patch.dict(os.environ, env):
            with ASGITestClient(app) as client:
                response = client.post(
                    "/api/auth/login",
                    json={"username": "test-admin", "password": TEST_PASSWORD},
                    headers={"Origin": "http://localhost"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("secure", response.headers["set-cookie"].lower())

    def test_wrong_credentials_share_a_generic_message_and_throttle_at_five_failures(self):
        with mock.patch.dict(os.environ, self.env):
            with ASGITestClient(app) as client:
                for _ in range(5):
                    response = client.post(
                        "/api/auth/login",
                        json={"username": "test-admin", "password": "wrong-password"},
                        headers={"Origin": "http://localhost"},
                    )
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()["detail"], "Sai tên đăng nhập hoặc mật khẩu")
                limited = client.post(
                    "/api/auth/login",
                    json={"username": "test-admin", "password": "wrong-password"},
                    headers={"Origin": "http://localhost"},
                )
                self.assertEqual(limited.status_code, 429)
                self.assertGreater(int(limited.headers["retry-after"]), 0)

    def test_login_failures_are_scoped_to_ip_and_username_pair(self):
        for attempt in range(auth.LOGIN_FAILURES):
            auth._record_login_failure("192.0.2.10", "test-admin", now=100 + attempt)

        self.assertIsNotNone(auth.login_retry_after("192.0.2.10", "test-admin", now=105))
        self.assertIsNone(auth.login_retry_after("192.0.2.11", "test-admin", now=105))
        self.assertIsNone(auth.login_retry_after("192.0.2.10", "another-user", now=105))

    def test_ip_wide_limit_still_catches_password_spraying(self):
        for attempt in range(auth.LOGIN_IP_FAILURES):
            auth._record_login_failure("192.0.2.10", f"user-{attempt}", now=100 + attempt)

        self.assertIsNotNone(auth.login_retry_after("192.0.2.10", "new-user", now=130))

    def test_learner_failures_do_not_throttle_admin_login(self):
        with mock.patch.dict(os.environ, self.env), \
                mock.patch("vskr.auth.verify_login", return_value=True):
            with ASGITestClient(app) as client:
                headers = {"Origin": "http://localhost"}
                for attempt in range(auth.LOGIN_IP_FAILURES):
                    response = client.post(
                        "/api/user/auth/login",
                        json={"username": f"learner-{attempt}", "password": "wrong"},
                        headers=headers,
                    )
                    self.assertEqual(response.status_code, 401, f"attempt={attempt}: {response.text}")

                response = client.post(
                    "/api/auth/login",
                    json={"username": "test-admin", "password": "valid"},
                    headers=headers,
                )
        self.assertEqual(response.status_code, 200, response.text)

    def test_login_rotates_session_and_logout_revokes_it(self):
        with mock.patch.dict(os.environ, self.env):
            with ASGITestClient(app) as client:
                headers = {"Origin": "http://localhost"}
                first = client.post("/api/auth/login", json={"username": "test-admin", "password": TEST_PASSWORD}, headers=headers)
                self.assertEqual(first.status_code, 200)
                first_token = client.cookies.get(auth.COOKIE_NAME)
                self.assertIsNotNone(auth.get_session(first_token, touch=False))
                second = client.post("/api/auth/login", json={"username": "test-admin", "password": TEST_PASSWORD}, headers=headers)
                self.assertEqual(second.status_code, 200)
                second_token = client.cookies.get(auth.COOKIE_NAME)
                self.assertNotEqual(first_token, second_token)
                self.assertIsNone(auth.get_session(first_token, touch=False))
                csrf = second.json()["csrf_token"]
                logout = client.post(
                    "/api/auth/logout",
                    headers={"Origin": "http://localhost", "X-CSRF-Token": csrf},
                )
                self.assertEqual(logout.status_code, 200)
                self.assertIsNone(auth.get_session(second_token, touch=False))
                self.assertFalse(client.get("/api/auth/me").json()["admin"])

    def test_absolute_and_idle_session_expiry(self):
        now = [100_000.0]
        with mock.patch("vskr.auth.time.time", side_effect=lambda: now[0]):
            idle_token, _ = auth.create_session()
            now[0] += auth.IDLE_TTL
            self.assertIsNone(auth.get_session(idle_token))

            now[0] = 200_000.0
            absolute_token, _ = auth.create_session()
            for _ in range(8):
                now[0] += 55 * 60
                self.assertIsNotNone(auth.get_session(absolute_token))
            now[0] = 200_000.0 + auth.ABSOLUTE_TTL
            self.assertIsNone(auth.get_session(absolute_token))

    def test_forwarded_ip_is_used_only_for_trusted_proxy_chain(self):
        request = SimpleNamespace(
            client=SimpleNamespace(host="10.0.0.2"),
            headers={"x-forwarded-for": "203.0.113.99, 198.51.100.8, 10.0.0.1"},
        )
        with mock.patch.dict(os.environ, {"VSKR_TRUSTED_PROXIES": "10.0.0.0/24, 198.51.100.0/24"}):
            self.assertEqual(auth.client_ip(request), "203.0.113.99")

        request.client.host = "192.0.2.10"
        with mock.patch.dict(os.environ, {"VSKR_TRUSTED_PROXIES": "10.0.0.0/24"}):
            self.assertEqual(auth.client_ip(request), "192.0.2.10")

    def test_login_audit_does_not_record_password_cookie_or_ip(self):
        with mock.patch.dict(os.environ, self.env):
            with ASGITestClient(app) as client:
                response = client.post(
                    "/api/auth/login",
                    json={"username": "test-admin", "password": "bad-secret"},
                    headers={"Origin": "http://localhost"},
                )
                self.assertEqual(response.status_code, 401)
        audit_path = Path(self.data_dir.name) / "audit.jsonl"
        raw = audit_path.read_text(encoding="utf-8")
        self.assertIn("auth.login.failure", raw)
        self.assertNotIn("bad-secret", raw)
        for line in raw.splitlines():
            record = json.loads(line)
            self.assertNotIn("ip", record)
            self.assertNotIn("cookie", record)
            self.assertNotIn("token", record)
