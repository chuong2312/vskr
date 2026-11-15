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
        forbidden = ("/api/eval", "/api/graph", "/api/reload", "/admin")
        for path in web_root.rglob("*"):
            if path.is_file() and path.suffix in {".html", ".js", ".css", ".webmanifest"}:
                contents = path.read_text(encoding="utf-8")
                for value in forbidden:
                    self.assertNotIn(value, contents, f"{value} leaked into {path.relative_to(web_root)}")

    def test_chat_scroll_stays_in_thread_and_does_not_force_follow_manual_scroll(self):
        web_root = Path(__file__).parents[1] / "web" / "user"
        styles = (web_root / "styles.css").read_text(encoding="utf-8")
        app = (web_root / "app.js").read_text(encoding="utf-8")

        self.assertIn("grid-template-rows:minmax(0,1fr)", styles)
        self.assertIn("min-height:0; max-width:none", styles)
        self.assertIn("overflow-y:auto; overscroll-behavior-y:contain", styles)
        self.assertIn("position:relative; bottom:auto; flex:none", styles)
        self.assertIn('studyPanel.addEventListener("wheel"', app)
        self.assertIn("thread.contains(target)", app)
        self.assertIn("if (scroll && followThread) scrollThreadToEnd()", app)
        self.assertIn("if (followThread) scrollThreadToEnd()", app)

    def test_conversation_history_has_search_grouping_rename_and_deep_link_controls(self):
        web_root = Path(__file__).parents[1] / "web" / "user"
        page = (web_root / "index.html").read_text(encoding="utf-8")
        app = (web_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="new-conversation"', page)
        self.assertIn('id="conversation-search"', page)
        self.assertIn('id="rename-conversation-dialog"', page)
        self.assertIn('method: "PATCH"', app)
        self.assertIn("function conversationGroupTitle(timestamp)", app)
        self.assertIn('searchParams.set("conversation", conversationId)', app)
        self.assertIn('window.addEventListener("popstate"', app)

    def test_user_pwa_shell_is_public_and_does_not_cache_api_or_video(self):
        data_dir = tempfile.TemporaryDirectory()
        try:
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": data_dir.name}):
                with ASGITestClient(app) as client:
                    page = client.get("/")
                    login = client.get("/login")
                    register = client.get("/register")
                    favicon = client.get("/favicon.ico")
                    learner_session = client.get("/api/user/auth/me")
                    manifest = client.get("/manifest.webmanifest")
                    worker = client.get("/sw.js")
                    shared = client.get("/shared/tokens.css")
                    icon = client.get("/user/assets/icons/icon.svg")
                    api_client = client.get("/shared/api.js")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Hỏi điều bạn muốn hiểu", page.text)
            self.assertIn('id="user-login" class="quiet-button" href="/login"', page.text)
            self.assertIn('id="user-register" class="primary-button account-register" href="/register"', page.text)
            self.assertIn("Bạn có thể hỏi bài ngay mà không cần tài khoản", page.text)
            self.assertEqual(login.status_code, 200)
            self.assertEqual(login.headers.get("cache-control"), "no-store")
            self.assertIn('id="login-tab"', login.text)
            self.assertIn('id="register-tab"', login.text)
            self.assertNotIn("Học cùng bài giảng của bạn", login.text)
            self.assertEqual(register.status_code, 200)
            self.assertEqual(register.headers.get("cache-control"), "no-store")
            self.assertIn("Tạo tài khoản", register.text)
            self.assertIn('id="register-tab"', register.text)
            self.assertNotIn("invite-code", register.text)
            self.assertEqual(favicon.status_code, 200)
            self.assertEqual(favicon.headers.get("content-type"), "image/svg+xml")
            self.assertEqual(learner_session.status_code, 401)
            self.assertEqual(manifest.status_code, 200)
            self.assertIn("application/manifest+json", manifest.headers["content-type"])
            self.assertEqual(worker.status_code, 200)
            self.assertEqual(worker.headers["service-worker-allowed"], "/")
            self.assertEqual(shared.status_code, 200)
            self.assertEqual(icon.status_code, 200)
            self.assertEqual(api_client.status_code, 200)
            self.assertNotIn('location.assign("/login")', api_client.text)
            shell = worker.text.split("self.addEventListener(\"install\"", 1)[0]
            self.assertIn('const CACHE_NAME = "vskr-user-shell-v12"', shell)
            self.assertIn('"/register"', shell)
            self.assertIn('"/login"', shell)
            self.assertIn('const AUTH_PAGES = new Set(["/login", "/register"])', shell)
            self.assertIn('if (AUTH_PAGES.has(url.pathname))', worker.text)
            self.assertIn('fetch(url.href, { cache: "no-store", credentials: "same-origin" })', worker.text)
            self.assertNotIn('"/"', shell)
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
                        self.assertEqual(response.status_code, 303 if path == "/admin" else 401)
                        if path == "/admin":
                            self.assertEqual(response.headers.get("location"), "/admin/login")
                        self.assertNotIn("<html", response.text.lower())
                        self.assertNotIn("function init", response.text)
        finally:
            data_dir.cleanup()
