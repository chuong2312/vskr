"""Bộ test của VSKR.

Cả bộ test chạy trên một thư mục dữ liệu tạm, không bao giờ trên data/ thật: nhiều module (accounts,
audit...) tự gọi load_config(), nên chỉ đổi cfg trong từng test là không đủ. File này được nạp trước
mọi module test (kể cả khi chạy một file lẻ bằng `python -m unittest tests.test_x`), nên biến môi
trường được đặt trước khi vskr đọc cấu hình. Ghi đè cả giá trị có sẵn trong shell, vì test không được
chạm vào bất kỳ thư mục dữ liệu thật nào. Test nào cần thư mục riêng vẫn có thể patch biến này.

Địa chỉ Ollama cũng bị trỏ tới một cổng không có gì chạy: models.yaml bật `extract_relations`, nên test
nào dựng chỉ mục sẽ gọi Ollama. Nếu Ollama thật đang chạy, bộ test sẽ dùng GPU của chủ dự án và chậm
đi hàng chục lần (đã xảy ra: khoảng 63 lượt /api/chat trong một lần chạy). Với cổng chết, lệnh gọi lỗi
ngay và code đi theo nhánh "không có Ollama" như khi Ollama tắt.
"""
import atexit
import os
import shutil
import tempfile

TEST_DATA_DIR = tempfile.mkdtemp(prefix="vskr-test-data-")
os.environ["VSKR__PATHS__DATA_DIR"] = TEST_DATA_DIR
atexit.register(shutil.rmtree, TEST_DATA_DIR, ignore_errors=True)

# Cổng 9 (discard) không có dịch vụ nào lắng nghe, nên kết nối bị từ chối ngay.
OFFLINE_OLLAMA_URL = "http://127.0.0.1:9"
os.environ["VSKR__LLM__BASE_URL"] = OFFLINE_OLLAMA_URL
