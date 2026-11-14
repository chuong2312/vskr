# Ma trận quyền truy cập VSKR

Ma trận này là danh sách tường minh để đối chiếu quyền ở server. Route `/api/*`
mới phải được phân loại trước khi được thêm vào ứng dụng; không suy luận quyền
từ việc ẩn nút hoặc đường dẫn trong giao diện.

## Route giao diện

| Method | Route | Quyền mục tiêu | Dữ liệu cần bảo vệ |
|---|---|---|---|
| `GET` | `/` | Public | Chỉ trả app người dùng; không chứa HTML/JS của quản trị. |
| `GET` | `/user/assets/*` | Public | Chỉ bundle người dùng; không chứa endpoint quản trị. |
| `GET` | `/admin/login` | Public | Chỉ trang đăng nhập; không tải bundle quản trị trước xác thực. |
| `GET` | `/admin/login/assets/*` | Public | Chỉ CSS/JS tối thiểu của form đăng nhập; không chứa logic quản trị. |
| `GET` | `/admin`, `/admin/` | Admin session | Chưa đăng nhập được chuyển đến `/admin/login`; HTML quản trị chỉ được phục vụ sau xác thực. |
| `GET` | `/admin/assets/*` | Admin session | JS/CSS/assets quản trị; bảo vệ tại route, không mount static công khai. |

## API

| Method | Route | Quyền mục tiêu | Trường nhạy cảm / quy tắc response |
|---|---|---|---|
| `GET` | `/api/videos` | Public | Chỉ video đã index và được đánh dấu công khai. Trả tiêu đề, thời lượng và playback link an toàn; bỏ job, lỗi, filesystem path và URL query nhạy cảm. |
| `GET` | `/api/catalog` | Public | Chỉ video/tài liệu đã index và được đánh dấu công khai; chỉ trả ID, tiêu đề và số mục. |
| `GET` | `/api/videos/{video_id}/file` | Public | Chỉ trả file của video đã index và công khai; xác minh đường dẫn nằm trong thư mục video. |
| `GET` | `/api/documents/{document_id}/sections/{section}` | Public | Chỉ trả mục của tài liệu đã index và công khai; không trả đường dẫn hay metadata nội bộ. |
| `POST` | `/api/ask` | Public | Không nhận `mode`; retrieval chỉ dùng chunk của học liệu công khai. Bỏ điểm retrieval, chunk ID, matched concepts và timing. Giới hạn độ dài, rate limit và số request đang chờ. |
| `POST` | `/api/feedback` | Public, same-origin | Chỉ nhận 👍/👎 cho request ID đã ghi; không lưu IP. |
| `POST` | `/api/admin/ask` *(mới)* | Admin session + CSRF | Cho phép chọn retrieval mode và trả timing/diagnostic cho màn thử hỏi. Không trả path hoặc exception thô. |
| `GET` | `/api/status` | Public | Chỉ `{"ready": bool}`. Không lộ `error`, `phase`, `gpu_busy` hay chi tiết kết nối. |
| `GET` | `/api/auth/me` | Public | Không có phiên: `{"admin": false}`; có phiên hợp lệ: `{"admin": true, "csrf_token": ..., "admin_url": "/admin"}` để UI chỉ hiện liên kết quản trị sau khi xác thực. Không trả session token. |
| `POST` | `/api/auth/login` | Public | Throttle theo IP và username. Thông báo lỗi chung; không trả hash hoặc chi tiết xác thực. Cookie session chỉ qua `Set-Cookie`, không trả token trong JSON. |
| `POST` | `/api/auth/logout` | Public auth route; yêu cầu phiên hợp lệ và CSRF | Thu hồi session phía server và xóa cookie; không nhận session token trong body. |
| `GET` | `/api/library` | Admin session | Có job, trạng thái, URL nguồn, filename và thông tin quản lý; chỉ hiển thị cho admin. Loại đường dẫn filesystem, exception/stack trace và chuỗi lỗi thô. |
| `PATCH` | `/api/library/{kind}/{item_id}/visibility` | Admin session + CSRF | Cập nhật cờ hiển thị công khai theo từng video/tài liệu. Học liệu mới công khai sau khi index; học liệu cũ mặc định công khai. |
| `POST` | `/api/ingest` | Admin session + CSRF | URL/file nguồn và thông tin job chỉ dành cho admin. URL phải qua kiểm tra scheme, DNS và địa chỉ IP trước khi tải; lỗi trả về không chứa exception thô. |
| `POST` | `/api/documents` | Admin session + CSRF | Metadata upload và job chỉ dành cho admin. Không trả đường dẫn file; lỗi lưu/parse phải được làm sạch. |
| `GET` | `/api/jobs/{job_id}` | Admin session | Job, phase, message, ID nội dung và lỗi đã làm sạch; tuyệt đối bỏ `_source`, path và dữ liệu nội bộ. |
| `POST` | `/api/jobs/{job_id}/retry` | Admin session + CSRF | Chỉ trả ID/trạng thái tác vụ mới; không trả nguồn hoặc đường dẫn trên đĩa. |
| `DELETE` | `/api/jobs/{job_id}` | Admin session + CSRF | Kết quả xóa tối thiểu; không phản chiếu `_source` hoặc path. |
| `DELETE` | `/api/videos/{video_id}` | Admin session + CSRF | Kết quả xóa tối thiểu; thông báo lỗi không chứa path hoặc stack trace. |
| `DELETE` | `/api/documents/{document_id}` | Admin session + CSRF | Kết quả xóa tối thiểu; thông báo lỗi không chứa path hoặc stack trace. |
| `POST` | `/api/reload` | Admin session + CSRF | Trả trạng thái đã làm sạch; chi tiết lỗi kỹ thuật chỉ ở log/admin diagnostic an toàn. |
| `GET` | `/api/admin/status` *(mới)* | Admin session | Trạng thái đầy đủ cho dashboard: `ready`, phase, GPU busy, số lượng nội dung, index và Ollama. Không trả stack trace, path hoặc exception thô. |
| `GET` | `/api/eval/questions` | Admin session | Bộ câu hỏi và nguồn tham chiếu; chỉ dành cho admin. |
| `GET` | `/api/eval/results` | Admin session | Kết quả và cấu hình evaluation; chỉ dành cho admin. |
| `POST` | `/api/eval/questions` | Admin session + CSRF | Ghi bộ câu hỏi; lỗi validation không tiết lộ file path. |
| `POST` | `/api/eval/questions/import` | Admin session + CSRF | Ghi file JSONL; giới hạn kích thước và làm sạch lỗi parse. |
| `DELETE` | `/api/eval/questions/{question_id}` | Admin session + CSRF | Xóa câu hỏi; response tối thiểu. |
| `POST` | `/api/eval/run` | Admin session + CSRF | Có thể chiếm tài nguyên retrieval; chỉ admin. Kết quả evaluation không chứa path nội bộ. |
| `GET` | `/api/graph` | Admin session | Truy vấn, nội dung node/edge, IDs và điểm retrieval chỉ dành cho admin. |
| `GET` | `/api/audit` *(mới)* | Admin session | Tối đa 200 sự kiện gần nhất; không bao giờ chứa mật khẩu, cookie, token hoặc IP thô. |
| `GET` | `/api/admin/questions` | Admin session | Tối đa 200 câu hỏi gần nhất, trạng thái tìm thấy và đánh giá; không có địa chỉ IP. |

## Quy tắc áp dụng

- Các route quản trị ghi dữ liệu cần cookie session hợp lệ, `Origin` cùng host và
  `X-CSRF-Token` khớp session. Upload xác thực trước khi parser đọc body và có giới
  hạn kích thước ở middleware; reverse proxy cũng cần giới hạn request body.
- Mọi route `/api/*` phải xuất hiện trong danh sách public tường minh hoặc được
  gắn dependency `require_admin`; route chưa phân loại phải làm kiểm thử đỏ.
- Các route mới `/api/catalog`, `/api/admin/status`, `/api/audit`,
  `/api/admin/questions` và `/api/feedback` được thêm để
  giữ response public tối thiểu trong khi vẫn đáp ứng dashboard quản trị.
- `/api/admin/ask` là route thử hỏi của quản trị; nó không mở rộng quyền của
  endpoint public `/api/ask`.
