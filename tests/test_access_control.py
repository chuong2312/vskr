import os
import re
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vskr.api import app
from vskr.auth import hash_password

from tests.asgi_client import ASGITestClient

TEST_PASSWORD = secrets.token_urlsafe(24)
TEST_HASH = hash_password(TEST_PASSWORD)
PUBLIC_API_ROUTES = {
    ("GET", "/api/videos"),
    ("GET", "/api/catalog"),
    ("POST", "/api/ingest"),
    ("POST", "/api/documents"),
    ("GET", "/api/jobs/{job_id}"),
    ("GET", "/api/documents/{document_id}/sections/{section}"),
    ("GET", "/api/videos/{video_id}/file"),
    ("GET", "/api/status"),
    ("POST", "/api/ask"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/user/auth/register"),
    ("POST", "/api/user/auth/login"),
    ("GET", "/api/user/auth/config"),
}
LEARNER_API_ROUTES = {
    ("GET", "/api/videos"), ("GET", "/api/catalog"), ("POST", "/api/ingest"),
    ("POST", "/api/documents"), ("GET", "/api/jobs/{job_id}"),
    ("GET", "/api/documents/{document_id}/sections/{section}"),
    ("GET", "/api/videos/{video_id}/file"), ("GET", "/api/status"), ("POST", "/api/ask"),
}


def _expanded_routes(routes):
    for route in routes:
        if hasattr(route, "path"):
            yield route
        elif hasattr(route, "original_router"):
            yield from _expanded_routes(route.original_router.routes)
        elif hasattr(route, "routes"):
            yield from _expanded_routes(route.routes)


def _has_admin_dependency(route):
    dependencies = getattr(getattr(route, "dependant", None), "dependencies", [])
    return any(getattr(dependency.call, "__name__", "") == "require_admin" for dependency in dependencies)


def _has_learner_dependency(route):
    dependencies = getattr(getattr(route, "dependant", None), "dependencies", [])
    return any(getattr(dependency.call, "__name__", "") in {"require_learner_or_admin", "require_user"} for dependency in dependencies)


def _concrete_path(path):
    return re.sub(r"\{[^}:]+(?::path)?\}", "test-id", path)


def _send_route(client, method, path, *, origin=None, csrf=None):
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    concrete = _concrete_path(path)
    options = {"headers": headers}
    if path == "/api/graph":
        options["params"] = {"query": "test"}
    if method == "GET":
        return client.get(concrete, **options)
    if method == "PATCH":
        options["json"] = {"title": "Đã đổi tên"}
        return client.patch(concrete, **options)
    if method == "DELETE":
        return client.delete(concrete, **options)
    if path == "/api/documents":
        options["files"] = {"file": ("notes.txt", b"notes", "text/plain")}
    elif path == "/api/eval/questions/import":
        options["files"] = {"file": ("questions.jsonl", b"", "application/json")}
    elif path == "/api/ingest":
        options["data"] = {}
    elif path in {"/api/eval/questions", "/api/admin/ask", "/api/eval/run"}:
        options["json"] = {"questions": []} if path.endswith("/questions") else {"question": "test"} if path.endswith("/ask") else {}
    return client.post(concrete, **options)


class TestAccessControl(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.TemporaryDirectory()
        self.env = {
            "VSKR__PATHS__DATA_DIR": self.data_dir.name,
            "VSKR_ADMIN_USER": "test-admin",
            "VSKR_ADMIN_PASSWORD_HASH": TEST_HASH,
            "VSKR_COOKIE_SECURE": "false",
        }

    def tearDown(self):
        self.data_dir.cleanup()

    def test_every_api_route_is_explicitly_public_or_requires_admin(self):
        api_routes = [route for route in _expanded_routes(app.routes) if route.path.startswith("/api/")]
        self.assertTrue(api_routes)
        for route in api_routes:
            for method in route.methods - {"HEAD", "OPTIONS"}:
                if (method, route.path) in PUBLIC_API_ROUTES or (method, route.path) in LEARNER_API_ROUTES:
                    continue
                self.assertTrue(_has_admin_dependency(route) or _has_learner_dependency(route),
                                f"Unclassified API route: {method} {route.path}")

    def test_unauthenticated_requests_are_denied_for_every_admin_route(self):
        with mock.patch.dict(os.environ, self.env):
            with ASGITestClient(app, authenticated_learner=False) as client:
                routes = [
                    route for route in _expanded_routes(app.routes)
                    if route.path.startswith("/api/") and (route.methods - {"HEAD", "OPTIONS"})
                    and not all((method, route.path) in PUBLIC_API_ROUTES for method in route.methods - {"HEAD", "OPTIONS"})
                ]
                routes += [route for route in _expanded_routes(app.routes) if route.path in {"/admin", "/admin/", "/admin/assets/{asset_path:path}"}]
                for route in routes:
                    for method in route.methods - {"HEAD", "OPTIONS"}:
                        response = _send_route(client, method, route.path)
                        if route.path in {"/admin", "/admin/"}:
                            self.assertEqual(response.status_code, 303, f"{method} {route.path}: {response.text}")
                            self.assertEqual(response.headers.get("location"), "/admin/login")
                        else:
                            self.assertEqual(response.status_code, 401, f"{method} {route.path}: {response.text}")

    def test_authenticated_admin_writes_require_origin_and_csrf_on_every_route(self):
        with mock.patch.dict(os.environ, self.env):
            with ASGITestClient(app) as client:
                login = client.post(
                    "/api/auth/login",
                    json={"username": "test-admin", "password": TEST_PASSWORD},
                    headers={"Origin": "http://localhost"},
                )
                self.assertEqual(login.status_code, 200)
                writes = [
                    route for route in _expanded_routes(app.routes)
                    if route.path.startswith("/api/") and route.path != "/api/auth/login"
                    and any(method not in {"GET", "HEAD", "OPTIONS"} for method in route.methods)
                    and not all((method, route.path) in PUBLIC_API_ROUTES for method in route.methods - {"GET", "HEAD", "OPTIONS"})
                    and _has_admin_dependency(route)
                ]
                for route in writes:
                    for method in route.methods - {"GET", "HEAD", "OPTIONS"}:
                        response = _send_route(client, method, route.path)
                        self.assertEqual(response.status_code, 403, f"{method} {route.path}: {response.text}")
                logout = client.post("/api/auth/logout")
                self.assertEqual(logout.status_code, 403)

    def test_admin_is_fail_closed_when_credentials_are_missing(self):
        env = {**self.env, "VSKR_ADMIN_USER": "", "VSKR_ADMIN_PASSWORD_HASH": ""}
        with mock.patch.dict(os.environ, env):
            with ASGITestClient(app) as client:
                admin_page = client.get("/admin")
                self.assertEqual(admin_page.status_code, 303)
                self.assertEqual(admin_page.headers["location"], "/admin/login")
                response = client.post("/api/auth/login", json={"username": "x", "password": "y"}, headers={"Origin": "http://localhost"})
                self.assertEqual(response.status_code, 503)
                self.assertIn("VSKR_ADMIN_USER", response.json()["detail"])
