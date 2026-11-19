"""Chốt chặn: bộ test không bao giờ được đọc/ghi data/ thật của dự án.

accounts.py, audit.py... tự gọi load_config(), nên một test quên đổi thư mục dữ liệu sẽ ghi thẳng vào
data/ thật (đã xảy ra: migration viết lại meta.json, tạo .guest-owner-key). tests/__init__.py đặt
VSKR__PATHS__DATA_DIR sang thư mục tạm cho cả bộ test; các test dưới đây hỏng nếu ai đó bỏ cơ chế đó
hoặc thêm code tự ghép đường dẫn data/ không qua cấu hình.
"""
import re
import unittest
from pathlib import Path

from tests import TEST_DATA_DIR
from vskr.access import accounts
from vskr.config import ROOT, load_config


class TestDataIsolation(unittest.TestCase):
    def test_suite_runs_on_a_temporary_data_dir(self):
        data = Path(load_config()["paths"]["data_dir"]).resolve()
        self.assertEqual(data, Path(TEST_DATA_DIR).resolve())
        self.assertNotEqual(data, (ROOT / "data").resolve())

    def test_modules_that_read_global_config_stay_in_the_temp_dir(self):
        # Đường lỗi cũ: test_public_api / test_ask_limits đi qua accounts mà không truyền cfg tạm.
        self.assertTrue(accounts._database_path().resolve().is_relative_to(Path(TEST_DATA_DIR).resolve()))

    def test_no_module_builds_the_data_path_without_config(self):
        pattern = re.compile(r"ROOT\s*/\s*['\"]data['\"]")
        offenders = [str(path.relative_to(ROOT)) for path in (ROOT / "backend" / "src" / "vskr").rglob("*.py")
                     if pattern.search(path.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [], "Dùng data_dir(cfg) thay vì ROOT / 'data' để test đổi được thư mục")


if __name__ == "__main__":
    unittest.main()
