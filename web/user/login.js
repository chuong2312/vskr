const form = document.querySelector("#auth-form");
const status = document.querySelector("#auth-status");
let registering = false;
fetch("/api/user/auth/config", { credentials: "same-origin" }).then((response) => response.ok ? response.json() : null)
  .then((config) => {
    if (!config?.registration_enabled) {
      document.querySelector("#auth-switch").hidden = true;
      document.querySelector("#auth-description").textContent = "Đăng nhập để xem học liệu dùng chung và nội dung của tài khoản. Muốn tạo tài khoản, hãy xin mã mời từ quản trị viên.";
    }
  }).catch(() => {});
document.querySelector("#switch-mode").addEventListener("click", () => {
  registering = !registering;
  document.querySelector("#invite-wrap").hidden = !registering;
  document.querySelector("#invite-code").required = registering;
  document.querySelector("#auth-title").textContent = registering ? "Tạo tài khoản người học" : "Đăng nhập người học";
  document.querySelector("#submit-button").textContent = registering ? "Tạo tài khoản" : "Đăng nhập";
  document.querySelector("#switch-copy").textContent = registering ? "Đã có tài khoản?" : "Chưa có tài khoản?";
  document.querySelector("#switch-mode").textContent = registering ? "Đăng nhập" : "Đăng ký bằng mã mời";
  document.querySelector("#password").autocomplete = registering ? "new-password" : "current-password";
  status.textContent = "";
});
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const payload = { username: data.get("username"), password: data.get("password") };
  if (registering) payload.invite_code = data.get("invite_code");
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
