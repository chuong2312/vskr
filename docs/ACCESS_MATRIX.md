# Ma trận quyền truy cập VSKR

Ma trận này là danh sách tường minh để đối chiếu quyền ở server. Route `/api/*`
mới phải được phân loại trước khi được thêm vào ứng dụng; không suy luận quyền
từ việc ẩn nút hoặc đường dẫn trong giao diện.

## Route giao diện

| Method | Route | Quyền mục tiêu | Dữ liệu cần bảo vệ |
|---|---|---|---|
| `GET` | `/` | Public | Chỉ trả app người dùng; không chứa HTML/JS của quản trị. |
| `GET` | `/login` | Public | Form đăng nhập/đăng ký người học; không chứa dữ liệu thư viện. |
| `GET` | `/user/assets/*` | Public | Chỉ bundle người dùng; không chứa endpoint quản trị. |
| `GET` | `/admin/login` | Public | Chỉ trang đăng nhập; không tải bundle quản trị trước xác thực. |
| `GET` | `/admin/login/assets/*` | Public | Chỉ CSS/JS tối thiểu của form đăng nhập; không chứa logic quản trị. |
| `GET` | `/admin`, `/admin/` | Admin session | HTML quản trị; không được phục vụ nếu chưa xác thực. |
| `GET` | `/admin/assets/*` | Admin session | JS/CSS/assets quản trị; bảo vệ tại route, không mount static công khai. |

## API

| Method | Route | Quyền mục tiêu | Trường nhạy cảm / quy tắc response |
|---|---|---|---|
| `GET` | `/api/videos` | Learner session (hoặc admin session) | Chỉ video dùng chung và học liệu thuộc tài khoản đang đăng nhập, đã index sẵn sàng. Bỏ job, lỗi, đường dẫn và metadata nội bộ. |
| `GET` | `/api/catalog` *(mới)* | Learner session (hoặc admin session) | Chỉ video/tài liệu dùng chung và thuộc tài khoản hiện tại. Không trả filename nội bộ, đường dẫn, trạng thái hay job. |
| `POST` | `/api/ingest` | Learner session + CSRF (hoặc admin session + CSRF) | Người học thêm video cá nhân; nội dung gắn `owner_id` và không hiện với tài khoản khác. Video admin/legacy không có chủ sở hữu vẫn dùng chung. URL được kiểm tra an toàn, upload tối đa 1 GB, video tối đa 2 giờ. |
| `POST` | `/api/documents` | Learner session + CSRF (hoặc admin session + CSRF) | Tài liệu người học được gắn `owner_id`; admin/legacy không có chủ sở hữu dùng chung. Tối đa 100 MB. |
| `GET` | `/api/jobs/{job_id}` | Chủ job (admin session xem đầy đủ) | Người học chỉ xem job do tài khoản mình tạo, response tối thiểu; admin dùng session quản trị để xem diagnostic. |
| `GET` | `/api/videos/{video_id}/file` | Learner session (hoặc admin session) | Chỉ trả file thuộc thư viện dùng chung hoặc tài khoản hiện tại; kiểm tra trạng thái index và vị trí file. |
| `GET` | `/api/documents/{document_id}/sections/{section}` | Learner session (hoặc admin session) | Chỉ trả mục thuộc thư viện dùng chung hoặc tài khoản hiện tại. |
| `POST` | `/api/ask` | Learner session + CSRF | Tìm trên học liệu dùng chung và học liệu của đúng tài khoản; không gửi chế độ truy xuất từ client. Người học dùng hybrid vector + BM25 để tránh graph của tài khoản khác ảnh hưởng kết quả. Response chỉ chứa câu trả lời và nguồn được phép xem. |
| `POST` | `/api/admin/ask` *(mới)* | Admin session + CSRF | Cho phép chọn retrieval mode và trả timing/diagnostic cho màn thử hỏi. Không trả path hoặc exception thô. |
| `GET` | `/api/status` | Learner session (hoặc admin session) | Chỉ `{"ready": bool}`. Không lộ `error`, `phase`, `gpu_busy` hay chi tiết kết nối. |
| `GET` | `/api/auth/me` | Public | Không có phiên: `{"admin": false}`; có phiên hợp lệ: `{"admin": true, "csrf_token": ..., "admin_url": "/admin"}` để UI chỉ hiện liên kết quản trị sau khi xác thực. Không trả session token. |
| `POST` | `/api/auth/login` | Public | Throttle theo IP và username. Thông báo lỗi chung; không trả hash hoặc chi tiết xác thực. Cookie session chỉ qua `Set-Cookie`, không trả token trong JSON. |
| `POST` | `/api/auth/logout` | Public auth route; yêu cầu phiên hợp lệ và CSRF | Thu hồi session phía server và xóa cookie; không nhận session token trong body. |
| `POST` | `/api/user/auth/register` | Public, invite-only + rate limit | Tạo learner account; code mời không lưu. Mật khẩu được băm Argon2id; không trả hash/session token. |
| `POST` | `/api/user/auth/login` | Public + rate limit | Thông báo lỗi chung, cookie HttpOnly và CSRF token riêng; không trả session token. |
| `GET` | `/api/user/auth/config` | Public | Chỉ cho biết đăng ký có mở hay không; không trả mã mời. |
| `GET` | `/api/user/auth/me` | Learner session | Tên tài khoản và CSRF token; không trả session token. |
| `POST` | `/api/user/auth/logout` | Learner session + CSRF | Thu hồi phiên server-side và xóa cookie. |
| `GET` | `/api/library` | Admin session | Có job, trạng thái, URL nguồn, filename và thông tin quản lý; chỉ hiển thị cho admin. Loại đường dẫn filesystem, exception/stack trace và chuỗi lỗi thô. |
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
| `GET` | `/api/admin/users` | Admin session | Tên tài khoản, ngày tạo và trạng thái; không trả hash hay session. |
| `POST` | `/api/admin/users/{user_id}/disable`, `/enable` | Admin session + CSRF | Thu hồi phiên tài khoản bị tắt; không xóa dữ liệu học tập. |

## Quy tắc áp dụng

- Các route quản trị ghi dữ liệu cần cookie session hợp lệ, `Origin` cùng host và
  `X-CSRF-Token` khớp session.
- Mọi route `/api/*` phải được phân loại là public auth, learner session hoặc
  admin session; route chưa phân loại phải làm kiểm thử đỏ.
- Các route mới `/api/catalog`, `/api/admin/status` và `/api/audit` được thêm để
  giữ response public tối thiểu trong khi vẫn đáp ứng dashboard quản trị.
- `/api/admin/ask` là route thử hỏi của quản trị; nó không mở rộng quyền của
  endpoint public `/api/ask`.
