import http.client
import socket
import unittest
import urllib.request
from email.message import Message
from unittest import mock

from fastapi import HTTPException

from vskr.pipeline.url_safety import validate_ingest_url
from vskr.pipeline.safe_ytdlp import (
    create_pinned_connection,
    make_pinned_redirect_handler,
    make_safe_youtube_dl,
    resolve_download_target,
)
from yt_dlp.networking.exceptions import RequestError


_PUBLIC = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
_PRIVATE = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))


class TestSafeYtDlp(unittest.TestCase):
    def test_dns_rebinding_is_rejected_at_download_connection(self):
        with mock.patch("vskr.pipeline.url_safety.socket.getaddrinfo", side_effect=[[_PUBLIC], [_PRIVATE]]):
            self.assertEqual(validate_ingest_url("https://video.example/watch"),
                             "https://video.example/watch")
            with self.assertRaises(HTTPException):
                resolve_download_target("https://video.example/watch")

    def test_connection_uses_the_validated_numeric_ip_without_resolving_again(self):
        with mock.patch("vskr.pipeline.safe_ytdlp.socket.create_connection", return_value=mock.sentinel.socket) as connect:
            connection = create_pinned_connection(http.client.HTTPConnection, "8.8.8.8", "video.example", 443)
            sock = connection._create_connection(("video.example", 443), 4, None)

        self.assertIs(sock, mock.sentinel.socket)
        connect.assert_called_once_with(("8.8.8.8", 443), timeout=4, source_address=None)

    def test_redirect_hop_is_validated_before_following(self):
        handler = make_pinned_redirect_handler()()
        request = urllib.request.Request("https://video.example/watch")
        for target in (
            "http://127.0.0.1/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/admin",
            "http://[fe80::1]/admin",
            "http://[fc00::1]/admin",
            "http://[::ffff:169.254.169.254]/latest/meta-data/",
            "http://100.64.0.1/admin",
            "http://2130706433/admin",
            "http://0x7f000001/admin",
            "http://0177.0.0.1/admin",
            "http://user:pass@8.8.8.8/admin",
        ):
            with self.subTest(target=target), self.assertRaises(RequestError):
                handler.redirect_request(request, None, 302, "Found", Message(), target)

    def test_redirect_to_resolved_private_host_is_rejected_before_connection(self):
        handler = make_pinned_redirect_handler()()
        request = urllib.request.Request("https://video.example/watch")
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))
        with mock.patch("vskr.pipeline.url_safety.socket.getaddrinfo", return_value=[private]):
            with self.assertRaises(RequestError):
                handler.redirect_request(
                    request, None, 302, "Found", Message(), "http://metadata.example/latest"
                )

    def test_initial_private_target_fails_as_a_yt_dlp_request_error(self):
        import yt_dlp

        safe_downloader = make_safe_youtube_dl(yt_dlp)({"quiet": True})
        request_handler = safe_downloader._request_director.handlers["PinnedUrllib"]
        opener = request_handler._get_instance(
            proxies={}, cookiejar=safe_downloader.cookiejar,
        )
        http_handler = next(
            handler for handler in opener.handlers if type(handler).__name__ == "PinnedHTTPHandler"
        )
        with mock.patch(
            "vskr.pipeline.safe_ytdlp.resolve_download_target",
            side_effect=HTTPException(400, "private"),
        ), self.assertRaises(RequestError):
            http_handler.http_open(urllib.request.Request("http://private.example/path"))

    def test_yt_dlp_request_director_has_no_unpinned_http_fallback(self):
        import yt_dlp

        safe_downloader = make_safe_youtube_dl(yt_dlp)({"quiet": True})
        handlers = safe_downloader._request_director.handlers

        self.assertEqual(set(handlers), {"PinnedUrllib"})

    def test_downloader_policy_prefers_native_http_for_network_downloads(self):
        import yt_dlp
        from yt_dlp.downloader import get_suitable_downloader

        safe_downloader = make_safe_youtube_dl(yt_dlp)({"quiet": True})

        self.assertEqual(safe_downloader.params["external_downloader"], "native")
        self.assertTrue(safe_downloader.params["hls_prefer_native"])
        hls_downloader = get_suitable_downloader(
            {"protocol": "m3u8", "url": "https://video.example/playlist.m3u8"},
            safe_downloader.params,
        )
        self.assertEqual(hls_downloader.__name__, "HlsFD")

    def test_live_hls_selected_for_ffmpeg_is_rejected_before_network_download(self):
        import yt_dlp
        from yt_dlp.downloader import get_suitable_downloader
        from yt_dlp.downloader.external import FFmpegFD
        from vskr.pipeline.safe_ytdlp import UnsafeNetworkDownloaderError

        safe_downloader = make_safe_youtube_dl(yt_dlp)({"quiet": True})
        info = {
            "url": "https://video.example/live.m3u8",
            "protocol": "m3u8",
            "is_live": True,
        }
        selected = get_suitable_downloader(info.copy(), safe_downloader.params)
        self.assertIs(selected, FFmpegFD)

        with mock.patch.object(FFmpegFD, "download") as download:
            with self.assertRaisesRegex(UnsafeNetworkDownloaderError, "network downloader"):
                safe_downloader.dl("unused.ts", info)

        download.assert_not_called()

    def test_native_hls_and_external_protocol_downloaders_fail_closed(self):
        import yt_dlp
        from yt_dlp.downloader import get_suitable_downloader
        from yt_dlp.downloader.external import FFmpegFD
        from yt_dlp.downloader.rtmp import RtmpFD
        from vskr.pipeline.safe_ytdlp import UnsafeNetworkDownloaderError

        safe_downloader = make_safe_youtube_dl(yt_dlp)({"quiet": True})
        cases = (
            ({"url": "https://video.example/encrypted.m3u8", "protocol": "m3u8_native"}, "HlsFD"),
            ({"url": "rtmp://video.example/live", "protocol": "rtmp"}, "RtmpFD"),
        )
        for info, expected_downloader in cases:
            with self.subTest(protocol=info["protocol"]):
                selected = get_suitable_downloader(info.copy(), safe_downloader.params)
                self.assertEqual(selected.__name__, expected_downloader)
                with mock.patch.object(FFmpegFD, "download") as ffmpeg_download:
                    with self.assertRaises(UnsafeNetworkDownloaderError):
                        safe_downloader.dl("unused.bin", info.copy())
                    ffmpeg_download.assert_not_called()

    def test_native_dash_segments_are_allowed_through_yt_dlp(self):
        import yt_dlp
        from yt_dlp.downloader import get_suitable_downloader
        from yt_dlp.downloader.dash import DashSegmentsFD

        safe_downloader = make_safe_youtube_dl(yt_dlp)({"quiet": True})
        info = {
            "url": "https://video.example/manifest.mpd",
            "protocol": "http_dash_segments",
            "is_live": False,
        }
        self.assertIs(get_suitable_downloader(info.copy(), safe_downloader.params), DashSegmentsFD)
        with mock.patch.object(DashSegmentsFD, "download", return_value=True) as download:
            self.assertTrue(safe_downloader.dl("segments.bin", info))
        download.assert_called_once()

    def test_ffmpeg_remains_allowed_for_local_file_processing(self):
        import yt_dlp
        from yt_dlp.downloader.external import FFmpegFD

        safe_downloader = make_safe_youtube_dl(yt_dlp)({"quiet": True})
        local_info = {
            "url": "file:///tmp/source.mp4",
            "protocol": "https",
            "section_start": 1,
        }
        with mock.patch.object(FFmpegFD, "download", return_value=True) as download:
            self.assertTrue(safe_downloader.dl("local.mp4", local_info))
        download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
