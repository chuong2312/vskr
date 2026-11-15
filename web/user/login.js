const form = document.querySelector("#auth-form");
const status = document.querySelector("#auth-status");
const loginTab = document.querySelector("#login-tab");
const registerTab = document.querySelector("#register-tab");
let registering = location.pathname === "/register" || new URLSearchParams(location.search).get("mode") === "register";

function setMode(register) {
  registering = register;
  document.querySelector("#auth-title").textContent = registering ? "Tạo tài khoản người học" : "Đăng nhập để xem lịch sử";
  document.querySelector("#auth-description").textContent = registering
    ? "Tạo tài khoản để lưu các câu hỏi và xem lại lịch sử học của bạn."
    : "Bạn vẫn có thể dùng VSKR mà không cần tài khoản. Đăng nhập để lưu và xem lại các cuộc trò chuyện.";
  document.querySelector("#submit-button").textContent = registering ? "Tạo tài khoản" : "Đăng nhập";
  document.querySelector("#password").autocomplete = registering ? "new-password" : "current-password";
  loginTab.setAttribute("aria-selected", String(!registering));
  registerTab.setAttribute("aria-selected", String(registering));
  status.textContent = "";
}

loginTab.addEventListener("click", () => setMode(false));
registerTab.addEventListener("click", () => setMode(true));
setMode(registering);
fetch("/api/user/auth/config", { credentials: "same-origin" })
  .then((response) => response.ok ? response.json() : null)
  .then((config) => {
    if (config?.registration_enabled === false) {
      registerTab.hidden = true;
      if (registering) setMode(false);
      return;
    }
    setMode(registering);
  })
  .catch(() => setMode(registering));
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const payload = { username: data.get("username"), password: data.get("password") };
  const button = document.querySelector("#submit-button");
  button.disabled = true;
  status.textContent = registering ? "Đang tạo tài khoản…" : "Đang đăng nhập…";
  try {
    const response = await fetch(`/api/user/auth/${registering ? "register" : "login"}`, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Yêu cầu thất bại (${response.status}).`);
    sessionStorage.setItem("vskr-user-csrf", body.csrf_token);
    location.assign("/");
  } catch (error) { status.textContent = error.message || "Không thể đăng nhập."; }
  finally { button.disabled = false; }
});
