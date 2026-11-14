import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vskr.api import app
from vskr.auth import hash_password

from tests.asgi_client import ASGITestClient

TEST_HASH = hash_password(secrets.token_urlsafe(24))


class TestUserBundle(unittest.TestCase):
    def test_public_bundle_has_no_admin_or_management_routes(self):
        web_root = Path(__file__).parents[1] / "web" / "user"
        forbidden = ("/api/ingest", "/api/eval", "/api/graph", "/api/reload", "/admin")
        for path in web_root.rglob("*"):
            if path.is_file() and path.suffix in {".html", ".js", ".css", ".webmanifest"}:
                contents = path.read_text(encoding="utf-8")
                for value in forbidden:
                    self.assertNotIn(value, contents, f"{value} leaked into {path.relative_to(web_root)}")

    def test_user_pwa_shell_is_public_and_does_not_cache_api_or_video(self):
        data_dir = tempfile.TemporaryDirectory()
        try:
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": data_dir.name}):
                with ASGITestClient(app) as client:
                    page = client.get("/")
                    manifest = client.get("/manifest.webmanifest")
                    worker = client.get("/sw.js")
                    shared = client.get("/shared/tokens.css")
                    icon = client.get("/user/assets/icons/icon.svg")
            self.assertEqual(page.status_code, 200)
            self.assertEqual(manifest.status_code, 200)
            self.assertIn("application/manifest+json", manifest.headers["content-type"])
            self.assertEqual(worker.status_code, 200)
            self.assertEqual(worker.headers["service-worker-allowed"], "/")
            self.assertEqual(shared.status_code, 200)
            self.assertEqual(icon.status_code, 200)
            shell = worker.text.split("self.addEventListener(\"install\"", 1)[0]
            self.assertIn('"/"', shell)
            self.assertNotIn("/api/", shell)
            self.assertNotIn("/api/videos/", shell)
            self.assertIn('url.pathname.startsWith("/api/")', worker.text)
            self.assertNotIn("/admin", worker.text)
        finally:
            data_dir.cleanup()

    def test_unauthenticated_admin_page_and_bundle_do_not_return_admin_code(self):
        data_dir = tempfile.TemporaryDirectory()
        env = {
            "VSKR__PATHS__DATA_DIR": data_dir.name,
            "VSKR_ADMIN_USER": "test-admin",
            "VSKR_ADMIN_PASSWORD_HASH": TEST_HASH,
            "VSKR_COOKIE_SECURE": "false",
        }
        try:
            with mock.patch.dict(os.environ, env):
                with ASGITestClient(app) as client:
                    for path in ("/admin", "/admin/assets/admin.js"):
                        response = client.get(path)
                        expected = 303 if path == "/admin" else 401
                        self.assertEqual(response.status_code, expected)
                        self.assertNotIn("<html", response.text.lower())
                        self.assertNotIn("function init", response.text)
        finally:
            data_dir.cleanup()
