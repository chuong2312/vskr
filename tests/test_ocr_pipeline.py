import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vskr.pipeline import steps
from vskr.config import ROOT
from vskr.pipeline.ocr import merge_runs


def _frame(t):
    return {"t": t, "path": f"frames/f_{t:09.2f}.jpg"}


def _line(text, score=0.9):
    return {"text": text, "score": score, "box": None}


class TestStepDelegation(unittest.TestCase):
    def test_delegates_only_when_python_configured_and_not_already_in_child(self):
        cfg = {"ocr": {"python": ".venv-vlm/bin/python"}}
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(steps.IN_STEP_ENV, None)
            self.assertTrue(steps.should_delegate(cfg, "ocr"))
            self.assertFalse(steps.should_delegate({"ocr": {}}, "ocr"))
        with mock.patch.dict(os.environ, {steps.IN_STEP_ENV: "1"}):
            self.assertFalse(steps.should_delegate(cfg, "ocr"))

    def test_relative_python_path_resolves_against_repo_root(self):
        self.assertEqual(steps.step_python({"ocr": {"python": ".venv-vlm/bin/python"}}, "ocr"),
                         ROOT / ".venv-vlm" / "bin" / "python")

    def test_missing_venv_raises_step_unavailable(self):
        cfg = {"ocr": {"python": "/nonexistent/venv/bin/python"}}
        with self.assertRaises(steps.StepUnavailable):
            steps.run_external(cfg, "ocr", ["vid1"])

    def test_child_runs_cli_with_isolated_cuda_libraries(self):
        core_lib = str(ROOT / ".venv" / "lib" / "python3.12" / "site-packages" / "nvidia" / "cudnn" / "lib")
        cfg = {"ocr": {"python": sys.executable, "timeout_s": 120}}
        env = {"LD_LIBRARY_PATH": os.pathsep.join([core_lib, "/usr/lib/wsl/lib"]), "PYTHONPATH": "/extra"}
        with mock.patch.dict(os.environ, env), mock.patch("vskr.pipeline.steps.subprocess.run") as run:
            steps.run_external(cfg, "ocr", ["vid1", "vid2"], force=True)
        cmd = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(cmd, [sys.executable, "-m", "vskr.cli", "ocr", "--video", "vid1", "--video", "vid2", "--force"])
        self.assertEqual(kwargs["timeout"], 120)
        self.assertTrue(kwargs["check"])
        self.assertEqual(kwargs["env"][steps.IN_STEP_ENV], "1")
        self.assertEqual(kwargs["env"]["PYTHONPATH"], str(ROOT / "backend" / "src") + os.pathsep + "/extra")
        self.assertEqual(kwargs["env"]["LD_LIBRARY_PATH"], "/usr/lib/wsl/lib")

    def test_without_venv_the_step_runs_in_process(self):
        with mock.patch("vskr.pipeline.ocr.run") as run, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(steps.IN_STEP_ENV, None)
            steps.run_step({"ocr": {}}, "ocr", ["vid1"])
        run.assert_called_once_with({"ocr": {}}, ["vid1"], force=False)


class TestOcrMerge(unittest.TestCase):
    def test_static_text_across_consecutive_frames_becomes_one_span(self):
        rows = merge_runs([
            (_frame(0.0), [_line("SELECT * FROM users", 0.8)]),
            (_frame(8.0), [_line("SELECT * FROM users", 0.95)]),
            (_frame(16.0), [_line("SELECT * FROM users", 0.9)]),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["start"], rows[0]["end"]), (0.0, 16.0))
        self.assertEqual(rows[0]["score"], 0.95)
        self.assertEqual(rows[0]["type"], "ocr")

    def test_text_that_disappears_and_returns_starts_a_new_span(self):
        rows = merge_runs([
            (_frame(0.0), [_line("JOIN")]),
            (_frame(8.0), [_line("GROUP BY")]),
            (_frame(16.0), [_line("JOIN")]),
        ])
        self.assertEqual([(r["text"], r["start"], r["end"]) for r in rows],
                         [("JOIN", 0.0, 0.0), ("GROUP BY", 8.0, 8.0), ("JOIN", 16.0, 16.0)])

    def test_duplicate_line_inside_one_frame_is_kept_once(self):
        rows = merge_runs([(_frame(4.0), [_line("users"), _line("users")])])
        self.assertEqual(len(rows), 1)


class TestFramesReuseScenes(unittest.TestCase):
    def _video(self, temp_dir, scenes):
        vdir = Path(temp_dir) / "videos" / "vid1"
        vdir.mkdir(parents=True)
        (vdir / "source.mp4").write_bytes(b"video")
        (vdir / "meta.json").write_text(json.dumps(
            {"source_file": "source.mp4", "has_video_file": True, "duration": 40}), encoding="utf-8")
        if scenes is not None:
            (vdir / "scenes.json").write_text(json.dumps(scenes), encoding="utf-8")
        return vdir

    def test_cached_scene_starts_require_complete_run_with_same_params(self):
        from vskr.pipeline.frames import _cached_scene_starts

        fc = {"scene_threshold": 27.0, "frame_skip": 4}
        with tempfile.TemporaryDirectory() as temp_dir:
            vdir = self._video(temp_dir, {"params": dict(fc), "detection_status": "complete", "scene_starts": [0.0, 12.5]})
            self.assertEqual(_cached_scene_starts(vdir, fc), [0.0, 12.5])
            self.assertIsNone(_cached_scene_starts(vdir, {**fc, "frame_skip": 0}))
            (vdir / "scenes.json").write_text(json.dumps(
                {"params": dict(fc), "detection_status": "fallback", "scene_starts": []}), encoding="utf-8")
            self.assertIsNone(_cached_scene_starts(vdir, fc))

    def test_run_reuses_scenes_and_keeps_frames_in_time_order_with_workers(self):
        from vskr.pipeline import frames

        fc = {"scene_threshold": 27.0, "frame_skip": 4, "interval_s": 8, "workers": 4, "hash_dist": 0}
        with tempfile.TemporaryDirectory() as temp_dir:
            vdir = self._video(temp_dir, {"params": {"scene_threshold": 27.0, "frame_skip": 4},
                                          "detection_status": "complete", "scene_starts": [0.0, 20.0]})
            cfg = {"paths": {"data_dir": temp_dir}, "frames": fc}
            hashes = iter(range(1, 10_000, 997))
            with mock.patch("vskr.pipeline.frames.detect_scene_starts") as detect, \
                 mock.patch("vskr.pipeline.frames.grab_frame"), \
                 mock.patch("vskr.pipeline.frames.dhash", side_effect=lambda _p: next(hashes) << 20):
                frames.run(cfg, "vid1")
            detect.assert_not_called()
            saved = json.loads((vdir / "frames.json").read_text(encoding="utf-8"))
            times = [row["t"] for row in saved["frames"]]
            self.assertEqual(times, sorted(times))
            self.assertEqual(saved["scene_starts"], [0.0, 20.0])


class TestIngestVisualSteps(unittest.TestCase):
    def _cfg(self, temp_dir, ocr=True, vlm=False, has_video=True):
        vdir = Path(temp_dir) / "videos" / "vid1"
        vdir.mkdir(parents=True)
        (vdir / "meta.json").write_text(json.dumps({"has_video_file": has_video}), encoding="utf-8")
        return {"paths": {"data_dir": temp_dir}, "ocr": {"enabled": ocr}, "vlm": {"enabled": vlm}}

    def test_disabled_steps_do_nothing(self):
        from vskr.api import handlers

        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = self._cfg(temp_dir, ocr=False, vlm=False)
            warnings = []
            with mock.patch("vskr.pipeline.frames.run") as extract, mock.patch("vskr.pipeline.steps.run_step") as run_step:
                handlers._run_visual_steps(cfg, "job1", "vid1", warnings)
        extract.assert_not_called()
        run_step.assert_not_called()
        self.assertEqual(warnings, [])

    def test_audio_only_video_skips_visual_steps(self):
        from vskr.api import handlers

        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = self._cfg(temp_dir, vlm=True, has_video=False)
            with mock.patch("vskr.pipeline.frames.run") as extract:
                handlers._run_visual_steps(cfg, "job1", "vid1", [])
        extract.assert_not_called()

    def test_frames_then_ocr_then_vlm_each_under_gpu_lock(self):
        from vskr.api import handlers

        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = self._cfg(temp_dir, vlm=True)
            calls = []
            with mock.patch("vskr.pipeline.frames.run") as extract, \
                 mock.patch("vskr.pipeline.steps.run_step",
                            side_effect=lambda _cfg, step, ids: calls.append((step, ids, handlers._gpu_model_lock.locked()))):
                handlers._run_visual_steps(cfg, "job1", "vid1", [])
        extract.assert_called_once_with(cfg, "vid1")
        self.assertEqual(calls, [("ocr", ["vid1"], True), ("vlm", ["vid1"], True)])

    def test_ocr_failure_warns_and_vlm_still_runs(self):
        from vskr.api import handlers

        def fake(_cfg, step, _ids):
            if step == "ocr":
                raise steps.StepUnavailable("no venv")

        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = self._cfg(temp_dir, vlm=True)
            warnings = []
            with mock.patch("vskr.pipeline.frames.run"), \
                 mock.patch("vskr.pipeline.steps.run_step", side_effect=fake) as run_step, \
                 self.assertLogs(level="WARNING"):
                handlers._run_visual_steps(cfg, "job1", "vid1", warnings)
        self.assertEqual([c.args[1] for c in run_step.call_args_list], ["ocr", "vlm"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("OCR", warnings[0])
        self.assertFalse(handlers._gpu_model_lock.locked())

    def test_frame_extraction_failure_skips_ocr_and_vlm(self):
        from vskr.api import handlers

        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = self._cfg(temp_dir, vlm=True)
            warnings = []
            with mock.patch("vskr.pipeline.frames.run", side_effect=RuntimeError("ffmpeg")), \
                 mock.patch("vskr.pipeline.steps.run_step") as run_step, \
                 self.assertLogs(level="WARNING"):
                handlers._run_visual_steps(cfg, "job1", "vid1", warnings)
        run_step.assert_not_called()
        self.assertEqual(len(warnings), 1)


class TestOcrFrameStats(unittest.TestCase):
    def test_polygon_area_and_text_coverage(self):
        from vskr.pipeline.ocr import frame_stats, polygon_area

        self.assertEqual(polygon_area([[0, 0], [100, 0], [100, 20], [0, 20]]), 2000.0)
        self.assertEqual(polygon_area(None), 0.0)
        stats = frame_stats(_frame(8.0), [
            {"text": "SELECT", "score": 0.9, "box": [[0, 0], [100, 0], [100, 20], [0, 20]]},
            {"text": "FROM", "score": 0.9, "box": [[0, 40], [100, 40], [100, 60], [0, 60]]},
        ], (200, 100))
        self.assertEqual(stats["text_area"], 0.2)
        self.assertEqual(stats["text"], "SELECT FROM")
        self.assertEqual(stats["n_lines"], 2)


class TestVlmSelection(unittest.TestCase):
    def _frames(self, n):
        # Hashes far apart in Hamming distance so dedupe keeps them unless told otherwise.
        return [{"t": float(i * 8), "path": f"frames/{i}.jpg", "hash": format((1 << (i + 1) * 3) - 1, "016x")}
                for i in range(n)]

    def test_skips_text_heavy_frames_and_keeps_figures(self):
        from vskr.pipeline.vlm import select_frames

        frames = self._frames(3)
        ocr = [{"path": frames[0]["path"], "text_area": 0.4},
               {"path": frames[1]["path"], "text_area": 0.02},
               {"path": frames[2]["path"], "text_area": 0.15}]
        picked = select_frames(frames, ocr, {"max_text_area": 0.15, "min_hash_dist": 1})
        self.assertEqual([f["path"] for f in picked], [frames[1]["path"]])

    def test_without_ocr_every_distinct_frame_is_a_candidate(self):
        from vskr.pipeline.vlm import select_frames

        frames = self._frames(4)
        self.assertEqual(len(select_frames(frames, None, {"min_hash_dist": 1})), 4)

    def test_near_duplicate_frames_are_captioned_once(self):
        from vskr.pipeline.vlm import select_frames

        frames = [{"t": 0.0, "path": "a.jpg", "hash": "00000000000000ff"},
                  {"t": 8.0, "path": "b.jpg", "hash": "00000000000000fe"},
                  {"t": 16.0, "path": "c.jpg", "hash": "ffffffff00000000"}]
        picked = select_frames(frames, None, {"min_hash_dist": 10})
        self.assertEqual([f["path"] for f in picked], ["a.jpg", "c.jpg"])

    def test_cap_spreads_over_the_whole_lecture(self):
        from vskr.pipeline.vlm import select_frames

        frames = [{"t": float(i), "path": f"{i}.jpg"} for i in range(100)]
        picked = select_frames(frames, None, {"max_frames": 5})
        self.assertEqual(len(picked), 5)
        # The old behaviour kept frames[:5] (t=0..4); an even spread reaches the end.
        self.assertGreaterEqual(picked[-1]["t"], 80)

    def test_prompt_includes_ocr_context_and_empty_answer_is_detected(self):
        from vskr.pipeline.vlm import build_prompt, is_empty_caption

        self.assertIn("users, orders", build_prompt("users, orders"))
        self.assertNotIn("for reference", build_prompt(""))
        self.assertTrue(is_empty_caption("NONE"))
        self.assertTrue(is_empty_caption("KHÔNG."))
        self.assertTrue(is_empty_caption("Bản đồ này không có bất kỳ biểu đồ, bảng, hình ảnh hay giao diện phần mềm nào."))
        self.assertFalse(is_empty_caption("Không gian làm việc gồm hai bảng users và orders."))
        self.assertFalse(is_empty_caption("Đồ thị này hiển thị số lượng truy vấn mỗi giây của MySQL, PostgreSQL và SQLite."))

    def test_clean_caption_drops_paragraphs_copied_from_ocr(self):
        from vskr.pipeline.vlm import clean_caption

        ocr = "Bài 3: Truy vấn dữ liệu với JOIN SELECT u.name FROM users"
        caption = "Bài 3: Truy vấn dữ liệu với JOIN\n\nCâu truy vấn nối bảng users với orders."
        self.assertEqual(clean_caption(caption, ocr), "Câu truy vấn nối bảng users với orders.")
        self.assertEqual(clean_caption("Sơ đồ ba bảng.", ""), "Sơ đồ ba bảng.")

    def test_fit_pixels_downscales_large_frames_only(self):
        from PIL import Image
        from vskr.pipeline.vlm import fit_pixels

        big = fit_pixels(Image.new("RGB", (1280, 720)), 602112)
        self.assertLessEqual(big.size[0] * big.size[1], 602112)
        small = Image.new("RGB", (640, 360))
        self.assertIs(fit_pixels(small, 602112), small)


class TestOcrGroupLines(unittest.TestCase):
    @staticmethod
    def _word(text, x0, y0, x1, y1, score=0.9):
        return {"text": text, "score": score, "box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}

    def test_words_on_one_row_join_left_to_right_and_rows_stay_separate(self):
        from vskr.pipeline.ocr import group_lines

        words = [
            self._word("FROM", 300, 102, 380, 128, 0.8),
            self._word("SELECT", 60, 100, 180, 130, 1.0),
            self._word("name", 190, 101, 280, 129, 0.9),
            self._word("Truy vấn dữ liệu", 60, 20, 400, 60, 0.6),
        ]
        lines = group_lines(words)
        self.assertEqual([line["text"] for line in lines], ["Truy vấn dữ liệu", "SELECT name FROM"])
        self.assertEqual(lines[1]["score"], 0.9)
        self.assertEqual(lines[1]["box"][0], [60.0, 100.0])

    def test_no_words_gives_no_lines(self):
        from vskr.pipeline.ocr import group_lines

        self.assertEqual(group_lines([]), [])


if __name__ == "__main__":
    unittest.main()
