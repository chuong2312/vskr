"""VSKR test suite.

Cả bộ test chạy trên một thư mục dữ liệu tạm, không bao giờ trên data/ thật: nhiều module (accounts,
audit...) tự gọi load_config(), nên chỉ đổi cfg trong từng test là không đủ. File này được nạp trước
mọi module test (kể cả khi chạy một file lẻ bằng `python -m unittest tests.test_x`), nên biến môi
trường được đặt trước khi vskr đọc cấu hình. Ghi đè cả giá trị có sẵn trong shell, vì test không được
chạm vào bất kỳ thư mục dữ liệu thật nào. Test nào cần thư mục riêng vẫn có thể patch biến này.
"""
import atexit
import os
import shutil
import tempfile

TEST_DATA_DIR = tempfile.mkdtemp(prefix="vskr-test-data-")
os.environ["VSKR__PATHS__DATA_DIR"] = TEST_DATA_DIR
atexit.register(shutil.rmtree, TEST_DATA_DIR, ignore_errors=True)
