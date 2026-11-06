import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from vskr import api


class TestIngestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post_upload(self, content, env=None, duration=20):
        env = {"VSKR__PATHS__DATA_DIR": str(self.tmp), **(env or {})}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(api._job_executor, "submit") as submit:
                with mock.patch("vskr.api.probe_duration", return_value=duration):
                    with TestClient(api.app) as client:
                        response = client.post(
                            "/api/ingest",
                            files={"file": ("lecture.mp4", content, "video/mp4")},
                        )
                if response.status_code == 202:
                    api._jobs.pop(response.json()["job_id"], None)
                return response, submit

    def test_video_list_is_empty_before_first_index(self):
        with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}):
            with TestClient(api.app) as client:
                response = client.get("/api/videos")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_upload_queues_pipeline_job_and_saves_file(self):
        response, submit = self._post_upload(b"fake video bytes")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        source = submit.call_args.args[2]
        self.assertEqual(source["kind"], "upload")
        self.assertTrue(Path(source["path"]).is_file())

    def test_upload_rejects_file_over_configured_size(self):
        response, submit = self._post_upload(
            b"0123456789", env={"VSKR__INGEST__MAX_UPLOAD_BYTES": "4"}
        )
        self.assertEqual(response.status_code, 413)
        submit.assert_not_called()
        self.assertEqual(list((self.tmp / "videos").iterdir()), [])

    def test_upload_rejects_video_over_duration_limit(self):
        response, submit = self._post_upload(b"fake video bytes", duration=1801)
        self.assertEqual(response.status_code, 400)
        self.assertIn("giới hạn hiện tại là 30 phút", response.json()["detail"])
        submit.assert_not_called()
        self.assertEqual(list((self.tmp / "videos").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
