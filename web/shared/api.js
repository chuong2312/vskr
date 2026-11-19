/**
 * Gọi API cho app người học: gửi cookie cùng origin, tự gắn token CSRF (lưu trong sessionStorage)
 * vào request ghi, và đổi lỗi HTTP thành ApiError kèm mã trạng thái và Retry-After.
 */
/** Lỗi từ API: `status` là mã HTTP, `retryAfter` là số giây nên chờ (0 nếu server không gửi). */
export class ApiError extends Error {
  constructor(message, status, retryAfter = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/**
 * Gửi request tới API và trả về JSON; lỗi HTTP ném ApiError với thông báo `detail` của server.
 * @param {string} path Đường dẫn API, ví dụ "/api/ask".
 * @param {RequestInit} [options] Tuỳ chọn của fetch (method, body, headers...).
 */
export async function request(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const csrf = sessionStorage.getItem("vskr-user-csrf");
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (csrf && !["GET", "HEAD", "OPTIONS"].includes(method)) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  let body = null;
  try { body = await response.json(); } catch { /* Some downloads and server errors have no JSON body. */ }
  if (!response.ok) {
    const message = typeof body?.detail === "string" ? body.detail : `Yêu cầu thất bại (${response.status}).`;
    throw new ApiError(message, response.status, Number(response.headers.get("Retry-After") || 0));
  }
  return body;
}
