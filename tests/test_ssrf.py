import socket
import unittest
from unittest import mock

from fastapi import HTTPException

from vskr.api.ssrf import validate_ingest_url


class TestIngestUrlValidation(unittest.TestCase):
    def test_rejects_non_http_scheme_and_embedded_credentials(self):
        for url in ("file:///etc/passwd", "ftp://example.com/video", "http://user:pass@example.com/video"):
            with self.subTest(url=url), self.assertRaises(HTTPException):
                validate_ingest_url(url)

    def test_rejects_loopback_private_link_local_and_metadata_addresses(self):
        for url in (
            "http://127.0.0.1/video",
            "http://10.2.3.4/video",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/video",
            "http://[fe80::1]/video",
        ):
            with self.subTest(url=url), self.assertRaises(HTTPException):
                validate_ingest_url(url)

    def test_resolves_hostname_and_rejects_any_private_answer(self):
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.10.4", 80))
        with mock.patch("vskr.api.ssrf.socket.getaddrinfo", return_value=[private]):
            with self.assertRaises(HTTPException):
                validate_ingest_url("https://lecture.example/video")

        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        with mock.patch("vskr.api.ssrf.socket.getaddrinfo", return_value=[public]):
            self.assertEqual(validate_ingest_url("https://lecture.example/video"), "https://lecture.example/video")

        mixed = [public, private]
        with mock.patch("vskr.api.ssrf.socket.getaddrinfo", return_value=mixed):
            with self.assertRaises(HTTPException):
                validate_ingest_url("https://lecture.example/video")

    def test_dns_failure_is_rejected(self):
        with mock.patch("vskr.api.ssrf.socket.getaddrinfo", side_effect=socket.gaierror("unresolvable")):
            with self.assertRaises(HTTPException):
                validate_ingest_url("https://missing.example/video")
