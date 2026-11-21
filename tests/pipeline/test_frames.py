import tempfile
import unittest
import json
from unittest import mock
from pathlib import Path

from vskr.pipeline.frames import candidate_times, cap_evenly, dedupe_hashes, dhash, hamming


class TestFrames(unittest.TestCase):
    def test_scene_detection_uses_pyav_backend(self):
        scene = (mock.Mock(get_seconds=mock.Mock(return_value=0.0)), mock.Mock())
        manager = mock.Mock()
        manager.get_scene_list.return_value = [scene]
        with mock.patch("scenedetect.open_video", return_value=object()) as open_video, \
             mock.patch("scenedetect.SceneManager", return_value=manager), \
             mock.patch("scenedetect.ContentDetector"):
            from vskr.pipeline.frames import detect_scene_starts

            self.assertEqual(detect_scene_starts("lecture.mp4"), [0.0])
        open_video.assert_called_once_with("lecture.mp4", backend="pyav")

    def test_ingest_scene_detection_failure_falls_back_to_transcript_indexing(self):
        from vskr.pipeline.frames import detect_scenes_only

        with tempfile.TemporaryDirectory() as temp_dir:
            video_dir = Path(temp_dir) / "videos" / "vid1"
            video_dir.mkdir(parents=True)
            (video_dir / "source.mp4").write_bytes(b"video")
            (video_dir / "meta.json").write_text(json.dumps({"source_file": "source.mp4"}), encoding="utf-8")
            cfg = {"paths": {"data_dir": temp_dir}, "frames": {"frame_skip": 4}}
            with mock.patch("vskr.pipeline.frames.detect_scene_starts", side_effect=RuntimeError("decoder failed")):
                result = detect_scenes_only(cfg, "vid1")

            self.assertEqual(result["scene_starts"], [])
            self.assertEqual(result["scene_count"], 0)
            self.assertEqual(result["detection_status"], "fallback")
            persisted = json.loads((video_dir / "scenes.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["error_type"], "RuntimeError")

    def test_interval_only(self):
        c = candidate_times(30, [], interval_s=10)
        self.assertEqual([t for t, _ in c], [0.0, 10.0, 20.0])
        self.assertTrue(all(s == "interval" for _, s in c))

    def test_scene_replaces_close_interval(self):
        c = candidate_times(30, [9.5], interval_s=10, offset_s=1.0, min_gap_s=2.0)
        self.assertIn((10.5, "scene"), c)
        self.assertNotIn((10.0, "interval"), c)

    def test_sorted_and_within_duration(self):
        c = candidate_times(25, [3, 3.2, 24.9], interval_s=5)
        ts = [t for t, _ in c]
        self.assertEqual(ts, sorted(ts))
        self.assertTrue(all(0 <= t <= 24.5 for t in ts))
        self.assertTrue(all(b - a >= 2.0 for a, b in zip(ts, ts[1:])))

    def test_cap(self):
        items = list(range(100))
        out = cap_evenly(items, 10)
        self.assertEqual(len(out), 10)
        self.assertEqual(out[0], 0)
        self.assertEqual(cap_evenly(items, None), items)
        self.assertEqual(cap_evenly(items, 200), items)

    def test_dedupe_compares_to_last_kept(self):
        base = 0
        hs = [base, base ^ 0b1, base ^ 0b11, base ^ 0b111, base ^ 0b1111, 0xFFFF]
        # khác frame đã giữ (0) tối đa 2 bit thì bỏ; tích lũy 3 bit trở lên thì giữ
        # 0b1, 0b11: <=2 bit so với frame 0 -> bỏ; 0b111: 3 bit -> giữ; 0b1111: 1 bit so với 0b111 -> bỏ; 0xFFFF: xa hẳn -> giữ
        self.assertEqual(dedupe_hashes(hs, 2), [0, 3, 5])
        self.assertEqual(hamming(0b1010, 0b0101), 4)

    def test_dhash_similar_vs_different(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as d:
            def img(name, text_pos):
                im = Image.new("RGB", (320, 180), "white")
                dr = ImageDraw.Draw(im)
                for k in range(text_pos):
                    dr.rectangle([10, 10 + k * 20, 200, 20 + k * 20], fill="black")
                p = Path(d) / name
                im.save(p)
                return dhash(p)

            a, a2, b = img("a.png", 3), img("a2.png", 3), img("b.png", 8)
            self.assertEqual(hamming(a, a2), 0)
            self.assertGreater(hamming(a, b), 0)


if __name__ == "__main__":
    unittest.main()
