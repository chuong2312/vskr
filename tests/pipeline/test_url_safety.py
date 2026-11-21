import asyncio
import socket
import unittest
from pathlib import Path
from unittest import mock


from vskr.pipeline import safe_ytdlp, url_safety
from vskr.pipeline.url_safety import UnsafeUrlError, validate_ingest_url, validate_learner_ingest_url


class TestIngestUrlValidation(unittest.TestCase):
    def test_learner_url_ingest_is_limited_to_youtube_hosts(self):
        youtube = "https://youtu.be/abc123"
        with mock.patch("vskr.pipeline.url_safety.validate_ingest_url", return_value=youtube) as validate:
            self.assertEqual(validate_learner_ingest_url(youtube), youtube)
            validate.assert_called_once_with(youtube)

        for url in (
            "https://example.com/video",
            "https://youtube.com.example.org/video",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                validate_learner_ingest_url(url)

    def test_learner_url_ingest_uses_configured_domains(self):
        lecture = "https://lectures.example/video"
        with mock.patch("vskr.pipeline.url_safety.validate_ingest_url", return_value=lecture):
            self.assertEqual(
                validate_learner_ingest_url(lecture, allowed_domains=["lectures.example"]),
                lecture,
            )

    def test_rejects_non_http_scheme_and_embedded_credentials(self):
        for url in ("file:///etc/passwd", "ftp://example.com/video", "http://user:pass@example.com/video"):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                validate_ingest_url(url)

    def test_rejects_loopback_private_link_local_and_metadata_addresses(self):
        for url in (
            "http://127.0.0.1/video",
            "http://10.2.3.4/video",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/video",
            "http://[fe80::1]/video",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                validate_ingest_url(url)

    def test_resolves_hostname_and_rejects_any_private_answer(self):
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.10.4", 80))
        with mock.patch("vskr.pipeline.url_safety.socket.getaddrinfo", return_value=[private]):
            with self.assertRaises(UnsafeUrlError):
                validate_ingest_url("https://lecture.example/video")

        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        with mock.patch("vskr.pipeline.url_safety.socket.getaddrinfo", return_value=[public]):
            self.assertEqual(validate_ingest_url("https://lecture.example/video"), "https://lecture.example/video")

        mixed = [public, private]
        with mock.patch("vskr.pipeline.url_safety.socket.getaddrinfo", return_value=mixed):
            with self.assertRaises(UnsafeUrlError):
                validate_ingest_url("https://lecture.example/video")

    def test_dns_failure_is_rejected(self):
        with mock.patch("vskr.pipeline.url_safety.socket.getaddrinfo", side_effect=socket.gaierror("unresolvable")):
            with self.assertRaises(UnsafeUrlError):
                validate_ingest_url("https://missing.example/video")

    def test_malformed_ipv6_authority_is_rejected(self):
        with self.assertRaises(UnsafeUrlError) as raised:
            validate_ingest_url("http://[::1/video")
        self.assertEqual(str(raised.exception), "URL video không hợp lệ.")

    def test_url_checks_do_not_depend_on_fastapi(self):
        self.assertTrue(issubclass(UnsafeUrlError, ValueError))
        self.assertNotIn("fastapi", Path(url_safety.__file__).read_text(encoding="utf-8"))
        self.assertNotIn("fastapi", Path(safe_ytdlp.__file__).read_text(encoding="utf-8"))

    def test_admin_ingest_maps_unsafe_url_to_http_400(self):
        from fastapi import HTTPException

        from vskr.api import handlers

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(handlers.jobs.start_ingest(
                url="http://127.0.0.1/video", file=None, owner_id="admin", project_id="project",
            ))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "URL video trỏ tới địa chỉ mạng nội bộ hoặc không công khai.")
