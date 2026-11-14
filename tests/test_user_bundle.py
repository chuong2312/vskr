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
                        self.assertEqual(response.status_code, 401)
                        self.assertNotIn("<html", response.text.lower())
                        self.assertNotIn("function init", response.text)
        finally:
            data_dir.cleanup()
