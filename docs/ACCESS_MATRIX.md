# Ma trận quyền truy cập VSKR

Ma trận này là danh sách tường minh để đối chiếu quyền ở server. Route `/api/*`
mới phải được phân loại trước khi được thêm vào ứng dụng; không suy luận quyền
từ việc ẩn nút hoặc đường dẫn trong giao diện.

## Route giao diện

| Method | Route | Quyền mục tiêu | Dữ liệu cần bảo vệ |
|---|---|---|---|
| `GET` | `/` | Public | Trả app người dùng; khách được hỏi trên thư viện chung, không có lịch sử đã lưu. Không chứa HTML/JS của quản trị. |
| `GET` | `/login`, `/register` | Public | Form đăng nhập hoặc tự đăng ký người học; phản hồi không được lưu trong browser HTTP cache. |
| `GET` | `/favicon.ico` | Public | Icon của ứng dụng người học. |
| `GET` | `/user/assets/*` | Public | Chỉ bundle người dùng; không chứa endpoint quản trị. |
| `GET` | `/admin/login` | Public | Chỉ trang đăng nhập; không tải bundle quản trị trước xác thực. |
| `GET` | `/admin/login/assets/*` | Public | Chỉ CSS/JS tối thiểu của form đăng nhập; không chứa logic quản trị. |
| `GET` | `/admin`, `/admin/` | Admin session | Phiên hợp lệ nhận HTML quản trị; chưa đăng nhập được chuyển hướng tới `/admin/login`. |
| `GET` | `/admin/assets/*` | Admin session | JS/CSS/assets quản trị; bảo vệ tại route, không mount static công khai. |

## API

| Method | Route | Quyền mục tiêu | Trường nhạy cảm / quy tắc response |
|---|---|---|---|
| `GET` | `/api/videos` | Public guest, learner session hoặc admin session | Khách chỉ thấy video dùng chung; learner thấy nội dung dùng chung và của mình; admin thấy toàn bộ. Chỉ nội dung đã index sẵn sàng; bỏ job, lỗi, đường dẫn và metadata nội bộ. |
| `GET` | `/api/catalog` *(mới)* | Public guest, learner session hoặc admin session | Khách chỉ thấy thư viện dùng chung; learner thấy nội dung dùng chung và của mình; admin thấy toàn bộ. Không trả filename nội bộ, đường dẫn, trạng thái hay job. |
| `POST` | `/api/ingest` | Guest hoặc learner session | Bắt buộc truyền `project_id` thuộc owner; nội dung mới mặc định `private`. Guest ghi dữ liệu bị giới hạn 10 request/IP/giờ, ngoài hạn mức theo vai trò. URL chỉ cho phép domain cấu hình và được kiểm tra scheme/DNS/IP. Admin không dùng route này. |
| `POST` | `/api/documents` | Guest hoặc learner session | Bắt buộc truyền `project_id` thuộc owner; tài liệu mới mặc định `private`. Guest ghi dữ liệu bị giới hạn 10 request/IP/giờ, ngoài hạn mức theo vai trò. Admin không dùng route này. |
| `GET` | `/api/jobs/{job_id}` | Chủ job (admin session xem đầy đủ) | Người học chỉ xem job do tài khoản mình tạo, response tối thiểu; admin dùng session quản trị để xem diagnostic. |
| `GET` | `/api/videos/{video_id}/file` | Public guest, learner session hoặc admin session | Khách chỉ mở file video dùng chung; learner mở nội dung dùng chung và của mình; admin mở toàn bộ. Kiểm tra trạng thái index và vị trí file. |
| `GET` | `/api/documents/{document_id}/sections/{section}` | Public guest, learner session hoặc admin session | Khách chỉ mở mục tài liệu dùng chung; learner mở nội dung dùng chung và của mình; admin mở toàn bộ. |
| `POST` | `/api/ask` | Public guest; learner session + CSRF; hoặc admin session + CSRF | Khách chỉ tìm thư viện dùng chung và không lưu lịch sử. Learner tìm nội dung dùng chung và của mình, lưu lượt hỏi theo tài khoản. Không nhận retrieval mode từ client; learner/guest dùng hybrid vector + BM25. Response chỉ chứa câu trả lời và nguồn được phép xem. |
| `GET` | `/api/conversations` | Learner session | Chỉ danh sách cuộc trò chuyện của tài khoản hiện tại. |
| `GET` | `/api/conversations/{conversation_id}` | Learner session | Chỉ nội dung hỏi đáp và nguồn của chủ cuộc trò chuyện; ID của tài khoản khác trả `404`. |
| `PATCH` | `/api/conversations/{conversation_id}` | Learner session + CSRF | Đổi tên cuộc trò chuyện của chính tài khoản hiện tại; tối đa 72 ký tự. |
| `DELETE` | `/api/conversations/{conversation_id}` | Learner session + CSRF | Chỉ chủ sở hữu có thể xóa cuộc trò chuyện; xóa theo cascade các lượt hỏi đáp đã lưu. |
| `POST` | `/api/admin/ask` *(mới)* | Admin session + CSRF | Cho phép chọn retrieval mode và trả timing/diagnostic cho màn thử hỏi. Không trả path hoặc exception thô. |
| `GET` | `/api/status` | Public guest, learner session hoặc admin session | Chỉ `{"ready": bool}`. Không lộ `error`, `phase`, `gpu_busy` hay chi tiết kết nối. |
| `GET` | `/api/auth/me` | Public | Không có phiên: `{"admin": false}`; có phiên hợp lệ: `{"admin": true, "csrf_token": ..., "admin_url": "/admin"}` để UI chỉ hiện liên kết quản trị sau khi xác thực. Không trả session token. |
| `POST` | `/api/auth/login` | Public | Throttle theo IP và username. Thông báo lỗi chung; không trả hash hoặc chi tiết xác thực. Cookie session chỉ qua `Set-Cookie`, không trả token trong JSON. |
| `POST` | `/api/auth/logout` | Public auth route; yêu cầu phiên hợp lệ và CSRF | Thu hồi session phía server và xóa cookie; không nhận session token trong body. |
| `POST` | `/api/user/auth/register` | Public + rate limit | Người học tự tạo tài khoản và đăng nhập ngay, không cần admin duyệt hoặc mã mời. Mật khẩu được băm Argon2id; không trả hash/session token. |
| `POST` | `/api/user/auth/login` | Public + rate limit | Thông báo lỗi chung, cookie HttpOnly và CSRF token riêng; không trả session token. |
| `GET` | `/api/user/auth/config` | Public | Cho biết đăng ký người học đang mở; không có quy trình cấp mã mời. |
| `GET` | `/api/user/me` | Public hoặc learner session | Trả tài khoản hiện tại hoặc `null`, CSRF token và hạn mức hiệu lực. |
| `GET` | `/api/user/auth/me` | Learner session | Tên tài khoản và CSRF token; không trả session token. |
| `POST` | `/api/user/auth/logout` | Learner session + CSRF | Thu hồi phiên server-side và xóa cookie. |
| `GET` | `/api/my/jobs`, `/api/jobs/{job_id}` | Chủ job hoặc guest cookie | Chỉ trả tác vụ do danh tính hiện tại tạo; admin dùng `/api/admin/jobs`. |
| `GET` | `/api/projects` | Guest hoặc learner session | Liệt kê project của owner hiện tại; GET không tạo project mặc định. Danh tính guest chỉ được lưu trong HttpOnly cookie, không trả owner ID. |
| `POST` | `/api/projects` | Guest hoặc learner session | Tạo project cho owner hiện tại; giới hạn 20 project mỗi owner. Guest bị giới hạn 10 lần tạo project/IP/giờ kể cả khi tự đổi cookie. |
| `POST` | `/api/my/content/{kind}/{id}/visibility` | Chủ nội dung | Chỉ chuyển `private` hoặc `pending`; admin duyệt sang `public`. |
| `DELETE` | `/api/my/content/{kind}/{id}` | Chủ nội dung | Chỉ xóa nội dung do danh tính hiện tại tạo. |
| `GET` | `/api/admin/content` | Admin session | Liệt kê nội dung theo visibility, ưu tiên nội dung chờ duyệt. |
| `POST` | `/api/admin/content/{kind}/{id}/visibility` | Admin session + CSRF | Duyệt, từ chối hoặc thay đổi visibility; ghi audit. |
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
| `POST` | `/api/eval/questions/import` | Admin session + CSRF | Xác thực trước khi parse multipart; body JSONL tối đa 5 MB và lỗi parse được làm sạch. |
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
