export class ApiError extends Error {
  constructor(message, status, retryAfter = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

const nativeFetch = window.fetch.bind(window);
let adminCsrf = "";
let adminAuth = null;

async function ensureAdminSession() {
  if (adminCsrf) return adminCsrf;
  if (!adminAuth) {
    adminAuth = nativeFetch("/api/auth/me", { credentials: "same-origin" })
      .then(async (response) => {
        const session = response.ok ? await response.json() : null;
        if (!session?.admin || !session.csrf_token) throw new Error("expired");
        adminCsrf = session.csrf_token;
        return adminCsrf;
      })
      .catch((error) => {
        location.replace("/admin/login?expired=1");
        throw error;
      });
  }
  return adminAuth;
}

export async function adminFetch(input, init = {}) {
  const url = new URL(typeof input === "string" ? input : input.url, location.href);
  const method = String(init.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
  const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", await ensureAdminSession());
  } else if (url.pathname.startsWith("/api/") && url.pathname !== "/api/auth/me") {
    await ensureAdminSession();
  }
  const response = await nativeFetch(input, { ...init, headers, credentials: "same-origin" });
  if (response.status === 401) location.replace("/admin/login?expired=1");
  return response;
}

export async function request(path, options = {}) {
  const response = await nativeFetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
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
