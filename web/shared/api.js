export class ApiError extends Error {
  constructor(message, status, retryAfter = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

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
