const notice = document.querySelector("#notice");
const error = document.querySelector("#error");
if (new URLSearchParams(location.search).has("expired")) {
  notice.textContent = "Phiên quản trị đã hết hạn. Vui lòng đăng nhập lại.";
}
document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  error.textContent = "";
  const form = new FormData(event.currentTarget);
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
    });
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 429) {
        const seconds = Number(response.headers.get("Retry-After") || 60);
        error.textContent = `Đăng nhập bị giới hạn. Thử lại sau ${seconds} giây.`;
      } else {
        error.textContent = data.detail || "Sai tên đăng nhập hoặc mật khẩu";
      }
      return;
    }
    location.assign("/admin");
  } catch {
    error.textContent = "Không thể kết nối tới máy chủ.";
  }
});
