"""Hàm dùng chung trong vskr.util: định dạng thời gian, xuất SRT và link nguồn."""
import unittest

from vskr.util import fmt_srt_time, fmt_time, is_youtube_host, source_link, to_srt


class TestUtil(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(fmt_time(32), "00:32")
        self.assertEqual(fmt_time(3725), "1:02:05")

    def test_youtube_host_matches_whole_domain_labels(self):
        for host in ("youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com",
                     "youtube-nocookie.com", "www.youtube-nocookie.com", "WWW.YouTube.com"):
            self.assertTrue(is_youtube_host(host), host)
        for host in ("evilyoutube.com", "youtube.com.evil.example", "notyoutube-nocookie.com",
                     "youtu.be.evil.example", "", None):
            self.assertFalse(is_youtube_host(host), host)

    def test_link_adds_timestamp_only_for_real_youtube_hosts(self):
        self.assertEqual(source_link("https://www.youtube.com/watch?v=abc", "abc", 65.4),
                         "https://www.youtube.com/watch?v=abc&t=65s")
        self.assertEqual(source_link("https://evilyoutube.com/watch?v=abc", "abc", 65.4),
                         "https://evilyoutube.com/watch?v=abc")

    def test_link(self):
        self.assertEqual(source_link(None, "ID", 32.9), "https://www.youtube.com/watch?v=ID&t=32s")


class TestSrt(unittest.TestCase):
    def test_time(self):
        self.assertEqual(fmt_srt_time(3661.5), "01:01:01,500")
        self.assertEqual(fmt_srt_time(0), "00:00:00,000")
        self.assertEqual(fmt_srt_time(32.499), "00:00:32,499")

    def test_blocks_skip_empty_and_renumber(self):
        segs = [
            {"start": 0, "end": 2.5, "text": " Xin chào "},
            {"start": 2.5, "end": 3, "text": "   "},
            {"start": 3, "end": 5, "text": "Biến trong Python"},
        ]
        self.assertEqual(
            to_srt(segs),
            "1\n00:00:00,000 --> 00:00:02,500\nXin chào\n\n2\n00:00:03,000 --> 00:00:05,000\nBiến trong Python\n",
        )


class TestSourceLink(unittest.TestCase):
    def test_youtube_gets_timestamp_without_losing_other_query(self):
        self.assertEqual(
            source_link("https://www.youtube.com/watch?v=abc&list=xyz", "abc", 42.8),
            "https://www.youtube.com/watch?v=abc&list=xyz&t=42s",
        )

    def test_other_platform_keeps_original_url(self):
        url = "https://example.com/watch/abc?token=private"
        self.assertEqual(source_link(url, "abc", 42), url)


if __name__ == "__main__":
    unittest.main()
