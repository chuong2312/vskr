"""Dò trạng thái slide trên khung hình giả: đổi slide, thêm node, con trỏ chuột, webcam, animation, chớp sáng."""
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from vskr.config import load_config
from vskr.pipeline import slide_change
from vskr.pipeline.slide_change import (
    PARAM_KEYS, change_area, detect_states, ignore_mask, params_from, read_gray_frames, work_size,
)

H, W = 144, 256
PARAMS = {
    "sample_fps": 1.0, "work_width": 256, "pixel_threshold": 25, "min_blob_area": 0.0015,
    "min_change_area": 0.002, "stable_frames": 3, "stable_area": 0.001, "busy_ratio": 0.2,
    "slide_area": 0.3, "ignore_top": 0.0, "ignore_bottom": 0.0,
}


def slide(seed=0):
    """Một slide nền sáng có vài dòng "chữ" tối; seed khác cho bố cục khác."""
    img = np.full((H, W), 235, dtype=np.uint8)
    rng = np.random.default_rng(seed)
    for row in range(5):
        y = 15 + row * 22
        x = int(rng.integers(10, 40))
        length = int(rng.integers(80, 200))
        img[y:y + 6, x:x + length] = 40
    return img


def detect(frames, **overrides):
    return detect_states(frames, [float(i) for i in range(len(frames))], {**PARAMS, **overrides})


class TestDetectStates(unittest.TestCase):
    def test_static_video_is_one_state(self):
        result = detect([slide()] * 20)
        self.assertEqual(result["status"], "complete")
        self.assertEqual([s["kind"] for s in result["states"]], ["start"])
        self.assertEqual(result["states"][0]["end"], 19.0)
        self.assertEqual(result["rejected_count"], 0)

    def test_new_slide_starts_a_slide_state(self):
        result = detect([slide(0)] * 10 + [slide(1)] * 10)
        starts = [(s["start"], s["kind"]) for s in result["states"]]
        self.assertEqual(starts, [(0.0, "start"), (10.0, "slide")])
        self.assertEqual(result["states"][0]["end"], 10.0)

    def test_adding_one_small_node_is_a_partial_state(self):
        base = slide()
        with_node = base.copy()
        with_node[100:110, 200:214] = 0  # một node nhỏ, khoảng 0,4% khung hình
        result = detect([base] * 10 + [with_node] * 10)
        self.assertEqual([s["kind"] for s in result["states"]], ["start", "partial"])
        state = result["states"][1]
        self.assertEqual(state["start"], 10.0)
        x0, y0, x1, y1 = state["bbox"]
        self.assertTrue(0.75 < x0 < 0.79 and 0.82 < x1 < 0.86, state["bbox"])
        self.assertTrue(0.67 < y0 < 0.7 and 0.75 < y1 < 0.78, state["bbox"])

    def test_moving_mouse_cursor_is_ignored(self):
        frames = []
        for i in range(30):
            img = slide()
            x = 20 + 7 * i
            img[60:64, x:x + 3] = 0  # con trỏ chuột 3x4 điểm ảnh, chạy ngang màn hình
            frames.append(img)
        result = detect(frames)
        self.assertEqual(len(result["states"]), 1)

    def test_webcam_region_is_masked_and_slides_still_detected(self):
        rng = np.random.default_rng(7)
        frames = []
        for i in range(40):
            img = slide(0 if i < 20 else 1)
            img[100:140, 190:250] = rng.integers(0, 256, size=(40, 60), dtype=np.uint8)  # webcam nhiễu
            frames.append(img)
        result = detect(frames)
        self.assertGreater(result["ignored_ratio"], 0.05)
        self.assertEqual([(s["start"], s["kind"]) for s in result["states"]], [(0.0, "start"), (20.0, "slide")])

    def test_animation_waits_until_screen_settles(self):
        frames = [slide(0)] * 10
        for step in range(3):  # 3 mẫu đang chạy hiệu ứng, mỗi mẫu một khác
            img = slide(0)
            img[:, step * 60:step * 60 + 80] = 120
            frames.append(img)
        frames += [slide(1)] * 6
        result = detect(frames)
        self.assertEqual([s["start"] for s in result["states"]], [0.0, 13.0])

    def test_one_frame_flash_is_rejected_with_reason(self):
        black = np.zeros((H, W), dtype=np.uint8)
        result = detect([slide()] * 10 + [black] + [slide()] * 10)
        self.assertEqual(len(result["states"]), 1)
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(result["rejected"][0]["reason"], "thoáng qua rồi trở lại như cũ")

    def test_small_additions_accumulate_against_the_kept_state(self):
        frames = []
        for i in range(20):
            img = slide()
            img[120:123, 30:30 + 2 * min(i, 8)] = 0  # nét vẽ dài thêm 2 điểm ảnh mỗi giây, dừng ở giây 8
            frames.append(img)
        result = detect(frames)
        # Mỗi giây chỉ thêm một mẩu nhỏ hơn ngưỡng, nhưng cộng dồn so với trạng thái đã giữ thì vượt ngưỡng.
        self.assertEqual([s["kind"] for s in result["states"]], ["start", "partial"])
        self.assertTrue(4.0 <= result["states"][1]["start"] <= 8.0, result["states"][1])

    def test_unsettled_change_at_the_end_is_rejected(self):
        frames = [slide(0)] * 10 + [slide(1), slide(2)]
        result = detect(frames)
        self.assertEqual(len(result["states"]), 1)
        self.assertEqual(result["rejected"][-1]["reason"], "chưa đứng yên tới hết video")

    def test_camera_only_video_is_too_dynamic(self):
        rng = np.random.default_rng(3)
        frames = [rng.integers(0, 256, size=(H, W), dtype=np.uint8) for _ in range(15)]
        result = detect(frames)
        self.assertEqual(result["status"], "too_dynamic")
        self.assertEqual(result["states"], [])

    def test_blank_state_is_flagged(self):
        black = np.zeros((H, W), dtype=np.uint8)
        result = detect([black] * 5 + [slide()] * 10)
        self.assertEqual([s["blank"] for s in result["states"]], [True, False])

    def test_empty_input(self):
        self.assertEqual(detect([])["status"], "empty")

    def test_ignore_bands_and_change_area(self):
        a = slide()
        b = a.copy()
        b[0:10, 0:256] = 0  # đổi dải trên cùng
        mask = ignore_mask([a, a, a], 25, 0.2, ignore_top=0.1)
        self.assertEqual(change_area(a, b, mask, 25, 0.0015), (0.0, None))
        area, bbox = change_area(a, b, np.zeros_like(mask), 25, 0.0015)
        self.assertGreater(area, 0.05)
        self.assertEqual(bbox[1], 0.0)


class TestRunAndIO(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vdir = self.tmp / "videos" / "v1"
        self.vdir.mkdir(parents=True)
        (self.vdir / "source.mp4").write_bytes(b"video")
        self.meta = {"video_id": "v1", "source_file": "source.mp4", "has_video_file": True, "duration": 30.5}
        (self.vdir / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
        self.cfg = {"paths": {"data_dir": str(self.tmp)}, "slide_change": dict(PARAMS)}
        self.frames = [slide(0)] * 15 + [slide(1)] * 15

    def _run(self, **kwargs):
        with mock.patch.object(slide_change, "probe_size", return_value=(1280, 720)), \
             mock.patch.object(slide_change, "read_gray_frames", return_value=self.frames) as reader, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            result = slide_change.run(self.cfg, "v1", **kwargs)
        return result, reader

    def test_run_writes_states_and_reuses_them(self):
        result, reader = self._run()
        reader.assert_called_once()
        self.assertEqual(reader.call_args.args[2], (256, 144))
        saved = json.loads((self.vdir / "slide_states.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["params"], PARAMS)
        self.assertEqual(saved["state_count"], 2)
        self.assertEqual(saved["states"][-1]["end"], 30.5)
        self.assertEqual(result["sample_count"], 30)

        _again, reader = self._run()
        reader.assert_not_called()
        _forced, reader = self._run(force=True)
        reader.assert_called_once()

    def test_changed_params_trigger_a_new_run(self):
        self._run()
        self.cfg["slide_change"]["stable_frames"] = 2
        _result, reader = self._run()
        reader.assert_called_once()

    def test_audio_only_video_is_refused(self):
        self.meta["has_video_file"] = False
        (self.vdir / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            slide_change.run(self.cfg, "v1")

    def test_work_size_keeps_ratio_and_never_upscales(self):
        self.assertEqual(work_size(1280, 720, 256), (256, 144))
        self.assertEqual(work_size(640, 480, 256), (256, 192))
        self.assertEqual(work_size(200, 100, 256), (200, 100))

    def test_read_gray_frames_parses_raw_pipe(self):
        raw = bytes(range(8)) * 2 + b"\x01"  # 2 ảnh 4x2 và 1 byte thừa (ảnh cuối bị cắt)
        proc = mock.Mock(stdout=io.BytesIO(raw))
        proc.wait.return_value = 0
        with mock.patch("subprocess.Popen", return_value=proc) as popen:
            frames = read_gray_frames("lecture.mp4", 1.0, (4, 2))
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].shape, (2, 4))
        cmd = popen.call_args.args[0]
        self.assertLess(cmd.index("-protocol_whitelist"), cmd.index("-i"))
        self.assertIn("fps=1.0,scale=4:2:flags=area,format=gray", cmd)

    def test_read_gray_frames_reports_ffmpeg_errors(self):
        proc = mock.Mock(stdout=io.BytesIO(b""))
        proc.wait.return_value = 1
        with mock.patch("subprocess.Popen", return_value=proc):
            with self.assertRaises(RuntimeError):
                read_gray_frames("lecture.mp4", 1.0, (4, 2))

    def test_models_yaml_has_every_parameter(self):
        params = params_from(load_config())
        self.assertEqual(set(params), set(PARAM_KEYS))

    def test_cli_runs_each_video(self):
        from vskr import cli

        with mock.patch.object(sys, "argv", ["vskr", "slides", "--video", "v1", "--force"]), \
             mock.patch.object(cli, "load_config", return_value=self.cfg), \
             mock.patch.object(slide_change, "run") as run:
            cli.main()
        run.assert_called_once_with(self.cfg, "v1", force=True)


if __name__ == "__main__":
    unittest.main()
