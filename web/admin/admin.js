const nativeFetch = window.fetch.bind(window);
let adminCsrf = "";
let authReady = nativeFetch("/api/auth/me", { credentials: "same-origin" })
  .then((response) => response.ok ? response.json() : Promise.reject(new Error("auth")))
  .then((data) => {
    if (!data.admin || !data.csrf_token) {
      location.replace("/admin/login?expired=1");
      throw new Error("Admin session expired");
    }
    adminCsrf = data.csrf_token;
    return true;
  })
  .catch((error) => {
    if (!location.pathname.endsWith("/admin/login")) location.replace("/admin/login?expired=1");
    throw error;
  });

window.fetch = (input, init = {}) => {
  const url = new URL(typeof input === "string" ? input : input.url, location.href);
  if (!url.pathname.startsWith("/api/") || url.pathname === "/api/auth/me" || url.pathname === "/api/auth/login") {
    return nativeFetch(input, init);
  }
  return authReady.then(() => {
    const method = String(init.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", adminCsrf);
    return nativeFetch(input, { ...init, headers, credentials: "same-origin" }).then((response) => {
      if (response.status === 401) location.replace("/admin/login?expired=1");
      return response;
    });
  });
};

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (t) => { t = Math.max(0, Math.floor(t)); const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60; const p = (n) => String(n).padStart(2, "0"); return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`; };

let player = null, videos = [], current = null, lastSources = [], lastQuestion = "", lastGraphData = null, pendingJump = null, ytApiReady = false, qaInitialized = false;

const adminNav = document.querySelector(".app-nav");
const adminMenuToggle = $("#admin-menu-toggle");
const adminNavBackdrop = $("#admin-nav-backdrop");
const mobileAdminNav = window.matchMedia("(max-width: 900px)");
function setAdminNavOpen(open, { restoreFocus = false } = {}) {
  adminNav.classList.toggle("open", open);
  adminMenuToggle.setAttribute("aria-expanded", String(open));
  adminMenuToggle.setAttribute("aria-label", open ? "Đóng menu" : "Mở menu");
  adminNavBackdrop.hidden = !open || !mobileAdminNav.matches;
  adminNav.inert = mobileAdminNav.matches && !open;
  if (open && mobileAdminNav.matches) adminNav.querySelector("button")?.focus();
  else if (restoreFocus) adminMenuToggle.focus();
}
setAdminNavOpen(false);
adminMenuToggle.addEventListener("click", () => setAdminNavOpen(!adminNav.classList.contains("open")));
adminNavBackdrop.addEventListener("click", () => setAdminNavOpen(false, { restoreFocus: true }));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && adminNav.classList.contains("open") && mobileAdminNav.matches) {
    setAdminNavOpen(false, { restoreFocus: true });
  }
});
document.addEventListener("click", (event) => {
  if (mobileAdminNav.matches && adminNav.classList.contains("open")
      && event.target instanceof Node && !adminNav.contains(event.target)
      && !adminMenuToggle.contains(event.target) && !adminNavBackdrop.contains(event.target)) {
    setAdminNavOpen(false);
  }
});
mobileAdminNav.addEventListener("change", () => setAdminNavOpen(false));
document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => {
  const wasOpen = adminNav.classList.contains("open") && mobileAdminNav.matches;
  setAdminNavOpen(false, { restoreFocus: wasOpen });
  const target = `${button.dataset.page}-screen`;
  document.querySelectorAll("main").forEach((el) => { el.hidden = el.id !== target; });
  document.querySelectorAll("[data-page]").forEach((el) => el.setAttribute("aria-current", el === button ? "page" : "false"));
  if (button.dataset.page === "overview") loadDashboard();
  if (button.dataset.page === "library") loadLibrary();
  if (button.dataset.page === "users") loadUsers();
  if (button.dataset.page === "tasks") loadTasks();
  if (button.dataset.page === "qa" && !qaInitialized) init();
  if (button.dataset.page === "evaluation") loadEvaluation();
  if (button.dataset.page === "audit") loadAudit();
}));

function displayTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "medium" }).format(date);
}

async function loadDashboard() {
  const status = $("#dashboard-status");
  status.textContent = "Đang tải trạng thái hệ thống…";
  try {
    const [statusResponse, libraryResponse] = await Promise.all([fetch("/api/admin/status"), fetch("/api/library")]);
    const system = await statusResponse.json();
    const library = await libraryResponse.json();
    if (!statusResponse.ok) throw new Error(system.detail || statusResponse.statusText);
    if (!libraryResponse.ok) throw new Error(library.detail || libraryResponse.statusText);
    const ready = library.filter((item) => item.status?.state === "ready" || item.status?.label === "Đã chia mục");
    $("#system-ready").textContent = system.ready ? "Sẵn sàng" : "Chưa sẵn sàng";
    $("#system-phase").textContent = system.phase ? `Đang chạy: ${system.phase}` : "Không có pipeline đang chạy";
    $("#system-gpu").textContent = system.gpu_busy ? "Đang bận" : "Sẵn sàng";
    $("#system-videos").textContent = String(ready.filter((item) => item.kind === "video").length);
    $("#system-documents").textContent = String(ready.filter((item) => item.kind === "document").length);
    $("#system-ollama").textContent = system.ollama_connected ? "Đã kết nối" : "Chưa kết nối";
    $("#system-index").textContent = String(system.index?.chunk_count ?? "—");
    const details = $("#index-details");
    details.innerHTML = `<dt>Ngày tạo</dt><dd>${esc(displayTime(system.index?.created_at))}</dd><dt>Phiên bản</dt><dd>${esc(system.index?.version ?? "—")}</dd>`;
    $("#system-error").textContent = system.error?.detail ? `Lỗi dịch vụ: ${system.error.detail}` : "";
    status.textContent = `Cập nhật lúc ${displayTime(new Date().toISOString())}.`;
  } catch (error) {
    status.textContent = `Không tải được tổng quan: ${error.message}`;
  }
}

async function loadTasks() {
  const list = $("#tasks-list");
  const status = $("#tasks-status");
  status.textContent = "Đang tải tác vụ…";
  try {
    const response = await fetch("/api/admin/jobs");
    const jobs = await response.json();
    if (!response.ok) throw new Error(jobs.detail || response.statusText);
    if (!jobs.length) {
      list.innerHTML = `<p class="hint">Chưa có tác vụ trong phiên server này.</p>`;
      status.textContent = "";
      return;
    }
    const stateLabels = { queued: "Đang chờ", running: "Đang chạy", done: "Hoàn tất", error: "Lỗi" };
    const phaseLabels = { queued: "Hàng đợi", ingest: "Tải/đọc nguồn", scenes: "Phát hiện phân cảnh", asr: "Nhận dạng lời nói (ASR)", index: "Lập chỉ mục", reload: "Nạp chỉ mục", done: "Hoàn tất", error: "Lỗi", document: "Đọc tài liệu" };
    list.innerHTML = jobs.map((job) => {
      const target = job.title || job.video_id || job.document_id || "Nội dung";
      const retry = job.status === "error" ? `<button class="small-action" type="button" data-task-retry="${esc(job.job_id)}">Thử lại</button>` : "";
      const error = job.status === "error" && job.error_detail ? `<p class="task-diagnostic">${esc(job.error_type || "Lỗi")}: ${esc(job.error_detail)}</p>` : "";
      return `<article class="task-card"><div class="task-heading"><div><span class="state ${job.status === "done" ? "ready" : job.status === "error" ? "error" : ""}">${esc(stateLabels[job.status] || job.status)}</span><strong>${esc(target)}</strong></div><time>${esc(displayTime(job.created_at))}</time></div><p class="task-phase">${esc(phaseLabels[job.phase] || job.phase || "Đang xử lý")}</p><p class="hint">${esc(job.message || "")}</p>${error}<div class="task-actions">${retry}<code>${esc(job.job_id)}</code></div></article>`;
    }).join("");
    status.textContent = `Hiển thị ${jobs.length} tác vụ gần nhất. Tác vụ mất khỏi danh sách sau khi server khởi động lại.`;
  } catch (error) {
    status.textContent = `Không tải được tác vụ: ${error.message}`;
  }
}

async function loadAudit() {
  const list = $("#audit-list");
  const status = $("#audit-status");
  status.textContent = "Đang tải nhật ký…";
  try {
    const response = await fetch("/api/audit");
    const events = await response.json();
    if (!response.ok) throw new Error(events.detail || response.statusText);
    if (!events.length) {
      list.innerHTML = `<tr><td colspan="4">Chưa có sự kiện nào.</td></tr>`;
    } else {
      list.innerHTML = events.slice().reverse().map((event) => `<tr><td><time>${esc(displayTime(event.at))}</time></td><td>${esc(event.event)}</td><td>${esc(event.target_id || event.kind || "—")}</td><td>${esc(event.result || "success")}</td></tr>`).join("");
    }
    status.textContent = `Hiển thị ${events.length} sự kiện gần nhất.`;
  } catch (error) {
    status.textContent = `Không tải được nhật ký: ${error.message}`;
  }
}

$("#refresh-dashboard").addEventListener("click", loadDashboard);
$("#refresh-tasks").addEventListener("click", loadTasks);
$("#refresh-audit").addEventListener("click", loadAudit);

$("#tasks-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-task-retry]");
  if (!button || button.disabled) return;
  button.disabled = true;
  $("#tasks-status").textContent = "Đang đưa lại tác vụ vào hàng đợi…";
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(button.dataset.taskRetry)}/retry`, { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || response.statusText);
    await loadTasks();
  } catch (error) {
    $("#tasks-status").textContent = `Không thể thử lại: ${error.message}`;
    button.disabled = false;
  }
});

async function loadUsers() {
  const status = $("#users-status"), tbody = $("#users-list");
  status.textContent = "Đang tải tài khoản…";
  try {
    const users = await fetch("/api/admin/users").then(async (r) => { if (!r.ok) throw new Error("Không tải được tài khoản."); return r.json(); });
    tbody.replaceChildren();
    for (const user of users) {
      const row = document.createElement("tr");
      row.innerHTML = `<td>${esc(user.username)}</td><td>${new Date(user.created_at * 1000).toLocaleDateString("vi-VN")}</td><td>${user.disabled ? "Đã tắt" : "Đang hoạt động"}</td><td><button class="small-action" data-user-action="${user.disabled ? "enable" : "disable"}" data-user-id="${esc(user.user_id)}">${user.disabled ? "Mở lại" : "Tắt tài khoản"}</button></td>`;
      tbody.append(row);
    }
    if (!users.length) tbody.innerHTML = '<tr><td colspan="4">Chưa có tài khoản nào.</td></tr>';
    status.textContent = `${users.length} tài khoản`;
  } catch (error) { status.textContent = error.message; }
}

$("#refresh-users")?.addEventListener("click", loadUsers);
$("#users-list")?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-user-action]");
  if (!button) return;
  button.disabled = true;
  try {
    const response = await fetch(`/api/admin/users/${encodeURIComponent(button.dataset.userId)}/${button.dataset.userAction}`, { method: "POST" });
    if (!response.ok) throw new Error("Không cập nhật được tài khoản.");
    await loadUsers();
  } catch (error) { $("#users-status").textContent = error.message; button.disabled = false; }
});

$("#logout-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await fetch("/api/auth/logout", { method: "POST" });
    if (!response.ok) throw new Error("Phiên không hợp lệ hoặc đã hết hạn.");
    location.replace("/admin/login?logged_out=1");
  } catch (error) {
    $("#session-status").textContent = `Không đăng xuất được: ${error.message}`;
    button.disabled = false;
  }
});

setInterval(() => {
  const activePage = document.querySelector("[data-page][aria-current='page']")?.dataset.page;
  if (activePage === "overview") loadDashboard();
  if (activePage === "tasks") loadTasks();
  if (activePage === "audit") loadAudit();
}, 10_000);

function isYouTube(v) {
  try { const h = new URL(v.url).hostname.toLowerCase(); return h === "youtu.be" || h.endsWith("youtube.com") || h.endsWith("youtube-nocookie.com"); }
  catch { return false; }
}

function isUploaded(v) { return Boolean(v && v.source_type === "upload" && v.file_url); }

function ensureYouTubeApi() {
  if (ytApiReady || document.querySelector('script[data-youtube-api="true"]')) return;
  const s = document.createElement("script");
  s.dataset.youtubeApi = "true";
  s.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(s);
}

function setupPlayer() {
  if (!current) return;
  if (isUploaded(current)) {
    if (player && player.destroy) player.destroy();
    $("#player").innerHTML = `<video id="local-player" controls playsinline preload="metadata" src="${esc(current.file_url)}"></video>`;
    player = $("#local-player");
    player.addEventListener("loadedmetadata", () => {
      if (pendingJump && pendingJump.vid === current.video_id) {
        player.currentTime = pendingJump.start;
        player.play().catch(() => {});
        pendingJump = null;
      }
    });
    $("#tl-title").textContent = "Vị trí các nguồn trong video đã tải lên";
    return;
  }
  if (!isYouTube(current)) {
    if (player && player.destroy) player.destroy();
    player = null;
    $("#player").innerHTML = `<div class="player-msg">Nền tảng này chưa hỗ trợ phát/nhảy thời điểm ngay trong trang.<br><a href="${esc(current.url)}" target="_blank" rel="noopener">Mở video trên nền tảng gốc</a></div>`;
    $("#tl-title").textContent = "Thời điểm nguồn (mở video gốc để đối chiếu)";
    return;
  }
  if (!ytApiReady) { ensureYouTubeApi(); return; }
  if (player && player.destroy) player.destroy();
  $("#player").innerHTML = '<div id="yt-player"></div>';
  player = new YT.Player("yt-player", {
    videoId: current.video_id,
    playerVars: { rel: 0, modestbranding: 1, origin: location.origin },
    events: {
      onReady: () => { if (pendingJump) { jump(pendingJump.vid, pendingJump.start); pendingJump = null; } },
      onError: () => { $("#tl-title").textContent = "Video không cho nhúng; hãy mở liên kết nguồn."; },
    },
  });
}

async function init() {
  if (qaInitialized) return;
  qaInitialized = true;
  try {
    const r = await fetch("/api/library");
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    videos = (await r.json()).filter((item) => item.kind === "video" && item.status?.state === "ready").map((item) => ({
      video_id: item.id, title: item.title, duration: item.duration,
      source_type: item.source_type, file_url: item.file_url, url: item.url,
    }));
  } catch (e) {
    $("#player").innerHTML = `<div class="player-msg">Chưa tải được danh sách video: ${esc(e.message)}<br>Hãy kiểm tra máy chủ đang chạy rồi tải lại trang.</div>`;
    return;
  }
  if (!videos.length) { $("#player").innerHTML = `<div class="player-msg">Chưa có video nào.</div>`; return; }
  current = videos[0];
  renderTabs();
  drawTimeline();
  setupPlayer();
  if (videos.some(isYouTube)) ensureYouTubeApi();
}

window.onYouTubeIframeAPIReady = () => {
  ytApiReady = true;
  if (isYouTube(current)) setupPlayer();
};

function renderTabs() {
  const box = $("#tabs");
  box.innerHTML = "";
  if (videos.length < 2) return;
  videos.forEach((v, i) => {
    const b = document.createElement("button");
    b.className = "tab"; b.type = "button";
    b.textContent = `Video ${i + 1}: ${v.title || v.video_id}`;
    b.setAttribute("aria-pressed", v.video_id === current.video_id);
    b.onclick = () => { switchVideo(v.video_id, 0); };
    box.appendChild(b);
  });
}

function switchVideo(vid, start) {
  current = videos.find((v) => v.video_id === vid) || current;
  if (isYouTube(current) && player && player.loadVideoById) {
    player.loadVideoById({ videoId: vid, startSeconds: start });
  } else {
    setupPlayer();
  }
  renderTabs();
  drawTimeline();
}

function jump(vid, start) {
  const target = videos.find((v) => v.video_id === vid);
  if (target && isUploaded(target)) {
    if (current.video_id !== vid) {
      pendingJump = { vid, start };
      switchVideo(vid, 0);
    } else if (player && player.tagName === "VIDEO") {
      if (player.readyState >= 1) {
        player.currentTime = start;
        player.play().catch(() => {});
      } else {
        pendingJump = { vid, start };
      }
    }
    return;
  }
  if (target && !isYouTube(target)) {
    const source = lastSources.find((s) => s.video_id === vid && s.start === start);
    if (source) window.open(source.link, "_blank", "noopener");
    return;
  }
  if (!player || !player.seekTo) { pendingJump = { vid, start }; return; }
  if (current.video_id !== vid) switchVideo(vid, start);
  else { player.seekTo(start, true); player.playVideo(); }
  document.querySelectorAll(".src").forEach((el) => el.classList.toggle("active", el.dataset.vid === vid && Number(el.dataset.start) === start));
}

function drawTimeline() {
  const tl = $("#timeline");
  if (!current) { tl.innerHTML = `<div class="empty">Nguồn tài liệu không có mốc video</div>`; $("#tl-dur").textContent = "—"; return; }
  const dur = current && current.duration;
  $("#tl-dur").textContent = dur ? `0:00 – ${fmt(dur)}` : "—";
  const mine = lastSources.filter((s) => s.video_id === current.video_id);
  if (!lastSources.length) { tl.innerHTML = `<div class="empty">Chưa có câu hỏi</div>`; return; }
  if (!mine.length || !dur) { tl.innerHTML = `<div class="empty">Không có nguồn nào ở video này</div>`; return; }
  tl.innerHTML = "";
  mine.forEach((s) => {
    const b = document.createElement("button");
    b.className = "mk"; b.type = "button";
    b.style.left = (s.start / dur) * 100 + "%";
    b.style.width = Math.max(((s.end - s.start) / dur) * 100, 0.8) + "%";
    b.title = `Nguồn ${s.n}: ${s.label}`;
    b.setAttribute("aria-label", `Nguồn ${s.n}, ${s.label}`);
    b.onclick = () => jump(s.video_id, s.start);
    tl.appendChild(b);
  });
}

function linkify(text, sources) {
  const have = new Set(sources.map((s) => s.n));
  const re = /\[\s*(?:Nguồn\s*)?(\d+(?:\s*[,;]\s*(?:Nguồn\s*)?\d+)*)\s*\]/gi;
  return esc(text).replace(re, (m, inner) => {
    const nums = inner.match(/\d+/g).map(Number);
    if (!nums.every((n) => have.has(n))) return m;
    return nums.map((n) => `<button type="button" class="cite" data-n="${n}">${n}</button>`).join("");
  });
}

function renderResult(d) {
  lastSources = d.sources;
  const box = $("#result");
  box.style.display = "block";
  let html = `<div class="answer ${d.found ? "" : "nf"}">${linkify(d.answer, d.sources)}</div>`;
  if (d.matched_concepts && d.matched_concepts.length) html += `<div class="why"><strong>Chủ đề khớp trong câu hỏi:</strong> ${d.matched_concepts.map(esc).join(", ")}</div>`;
  if (d.found && !d.cited) html += `<div class="warn">Mô hình không ghi rõ nguồn cho câu trả lời này. Dưới đây là các đoạn gần nhất tìm được, hãy tự đối chiếu.</div>`;
  if (d.sources.length) {
    html += `<div class="sources">`;
    d.sources.forEach((s) => {
      const isDoc = s.source_type === "document";
      html += `<div class="src" data-vid="${esc(s.video_id || "")}" data-start="${s.start ?? ""}" id="src-${s.n}">
        <div class="src-head">
          <strong>Nguồn ${s.n}</strong><span>${isDoc ? "Tài liệu · " : "Video · "}${esc(s.title || s.video_id || "")}</span><span class="t">${esc(s.label)}</span>
          ${s.cited ? "" : `<span class="tag">chưa được dẫn</span>`}
          <span class="src-actions">
            ${isDoc ? `<button class="play" type="button" data-open-section="${s.n}">Xem mục</button>` : `<button class="play" type="button" data-jump="${s.n}">Phát từ ${fmt(s.start)}</button><a href="${esc(s.link)}" target="_blank" rel="noopener">Mở video nguồn</a>`}
          </span>
        </div>
        <p>${esc(s.text)}</p><button class="more" type="button">Xem đầy đủ</button>
      </div>`;
    });
    html += `</div>`;
    html += `<button class="small-action" type="button" data-eval-from-qa>Dùng câu hỏi này để kiểm tra tìm nguồn</button><p class="hint">Nguồn tìm được sẽ điền sẵn. Hãy đối chiếu và sửa cho đúng trước khi lưu.</p>`;
  }
  const modeLabels = { hybrid_graph: "Kết hợp ý nghĩa + từ khóa + liên kết", hybrid: "Kết hợp ý nghĩa + từ khóa", vector: "Tìm theo ý nghĩa", bm25: "Tìm từ khóa", graph: "Tìm qua sơ đồ liên kết" };
  html += `<div class="timing">Thời gian tìm nguồn: ${d.timing.retrieve_s}s · Soạn câu trả lời: ${d.timing.llm_s}s · Cách tìm: ${modeLabels[d.mode] || esc(d.mode)}</div>`;
  box.innerHTML = html;
  drawTimeline();
}

$("#result").addEventListener("click", (e) => {
  const toEvaluation = e.target.closest("[data-eval-from-qa]");
  if (toEvaluation) {
    const references = [];
    const seen = new Set();
    for (const source of lastSources) {
      const ref = source.source_type === "document"
        ? { document_id: source.document_id, section: source.section }
        : { video_id: source.video_id, start: source.start, end: source.end };
      if (source.source_type !== "document" && (!ref.video_id || ref.start == null || ref.end == null)) continue;
      const key = JSON.stringify(ref);
      if (!seen.has(key)) { seen.add(key); references.push(ref); }
    }
    $("#eval-question").value = lastQuestion;
    $("#eval-relevant").value = JSON.stringify(references, null, 2);
    $("[data-page='evaluation']").click();
    $("#eval-form-status").textContent = "Câu hỏi và các nguồn tìm được đã được điền sẵn. Hãy xác nhận hoặc sửa nguồn đúng rồi bấm Thêm câu hỏi.";
    $("#eval-relevant").focus();
    return;
  }
  const cite = e.target.closest("button.cite");
  const play = e.target.closest("button.play");
  const more = e.target.closest("button.more");
  const section = e.target.closest("button[data-open-section]");
  const n = cite ? Number(cite.dataset.n) : play ? Number(play.dataset.jump) : null;
  if (n) { const s = lastSources.find((x) => x.n === n); if (s) s.source_type === "document" ? showDocumentSection(s) : jump(s.video_id, s.start); }
  if (section) { const s = lastSources.find((x) => x.n === Number(section.dataset.openSection)); if (s) showDocumentSection(s); }
  if (more) { const p = more.previousElementSibling; p.classList.toggle("open"); more.textContent = p.classList.contains("open") ? "Thu gọn" : "Xem đầy đủ"; }
});

async function refreshVideos(selectVideoId = null) {
  const r = await fetch("/api/library");
  if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
  videos = (await r.json()).filter((item) => item.kind === "video" && item.status?.state === "ready").map((item) => ({
    video_id: item.id, title: item.title, duration: item.duration,
    source_type: item.source_type, file_url: item.file_url, url: item.url,
  }));
  if (!videos.length) {
    current = null;
    $("#player").innerHTML = `<div class="player-msg">Chưa có video trong thư viện.</div>`;
    $("#tabs").innerHTML = "";
    player = null;
    drawTimeline();
    return;
  }
  current = videos.find((v) => v.video_id === selectVideoId) || current || videos[0];
  renderTabs();
  drawTimeline();
  setupPlayer();
}

async function waitForIngest(jobId) {
  while (true) {
    const r = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    const job = await r.json();
    if (!r.ok) throw new Error(job.detail || r.statusText);
    $("#pipeline-status").textContent = `${job.phase || ""}: ${job.message || "Đang xử lý…"}`;
    await loadLibrary(false);
    if (job.status === "error") throw new Error(job.message || "Xử lý nội dung thất bại.");
    if (job.status === "done") {
      await refreshVideos(job.video_id);
      $("#pipeline-status").textContent = `Đã thêm “${job.title || job.video_id || "nội dung"}”. Có thể hỏi đáp ngay.`;
      await loadLibrary(false);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}

async function showDocumentSection(source) {
  try {
    const r = await fetch(source.link);
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    $("#doc-viewer-title").textContent = `${data.title} · Mục ${data.section}`;
    $("#doc-viewer-text").textContent = data.text;
    $("#doc-viewer").showModal();
  } catch (err) { $("#pipeline-status").textContent = `Không mở được mục tài liệu: ${err.message}`; }
}

let libraryItems = [];
let libraryKindFilter = "all";
function renderLibraryTable() {
  const title = $("#library-filter-title")?.value.trim().toLocaleLowerCase("vi") || "";
  const statusFilter = $("#library-filter-status")?.value || "all";
  const counts = { all: libraryItems.length, video: libraryItems.filter((x) => x.kind === "video").length, document: libraryItems.filter((x) => x.kind === "document").length };
  Object.entries(counts).forEach(([key, value]) => { const node = $(`#count-${key}`); if (node) node.textContent = String(value); });
  const filtered = libraryItems.filter((item) => {
    const state = item.status?.state || "processing";
    return (libraryKindFilter === "all" || item.kind === libraryKindFilter)
      && (statusFilter === "all" || state === statusFilter)
      && (!title || String(item.title || "").toLocaleLowerCase("vi").includes(title));
  });
  const list = $("#library-list");
  if (!filtered.length) { list.innerHTML = `<p class="library-empty">${libraryItems.length ? "Không có học liệu phù hợp với bộ lọc." : "Chưa có video hoặc tài liệu. Hãy chọn Thêm học liệu để bắt đầu."}</p>`; return; }
  const rows = filtered.map((item) => {
    const jobId = item.job_id || (String(item.id).startsWith("job:") ? item.id.slice(4) : "");
    const status = item.status || {};
    const extra = item.kind === "document" ? (item.sections ? `${item.sections} mục` : "—") : item.duration ? fmt(item.duration) : "—";
    const rawStatus = status.label || status.state || "Chưa rõ";
    const statusText = status.state === "ready" || rawStatus === "Đã chia mục" ? "Sẵn sàng để hỏi" : rawStatus.includes("lập chỉ mục") ? rawStatus.replace("lập chỉ mục", "chuẩn bị nội dung để tìm") : rawStatus;
    const stateClass = status.state === "ready" || rawStatus === "Đã chia mục" ? "ready" : status.state === "error" ? "error" : "processing";
    const actions = [];
    const isJobOnly = String(item.id).startsWith("job:");
    if (status.state === "error" && jobId) actions.push(`<button class="small-action" data-retry="${esc(jobId)}" type="button">Thử lại</button>`);
    if (isJobOnly) {
      if (status.state === "error") actions.push(`<button class="small-action danger" data-delete-job="${esc(jobId)}" type="button">Xóa tác vụ</button>`);
      else actions.push(`<span class="hint">Đang xử lý</span>`);
    } else {
      actions.push(`<button class="small-action danger" data-delete="${esc(item.kind)}:${esc(item.id)}" type="button">Xóa</button>`);
    }
    return `<article class="library-item"><div class="library-title-cell"><span class="library-thumb" aria-hidden="true">${item.kind === "document" ? "▤" : "▶"}</span><span class="item-title">${esc(item.title || "Học liệu chưa có tên")}</span></div><span>${item.kind === "document" ? "Tài liệu" : "Video"}</span><span class="state ${stateClass}">${esc(statusText)}</span><span>${esc(extra)}</span><span class="library-actions">${actions.join("")}</span></article>`;
  }).join("");
  list.innerHTML = `<div class="library-table-head" aria-hidden="true"><span>Tiêu đề</span><span>Loại</span><span>Trạng thái</span><span>Thời lượng / Số mục</span><span>Thao tác</span></div>${rows}`;
}
async function loadLibrary(showLoading = true) {
  const list = $("#library-list");
  if (showLoading) list.innerHTML = `<p class="hint">Đang tải thư viện…</p>`;
  try {
    const r = await fetch("/api/library");
    const items = await r.json();
    if (!r.ok) throw new Error(items.detail || r.statusText);
    libraryItems = Array.isArray(items) ? items : [];
    $("#welcome-guide").hidden = libraryItems.some((item) => item.status?.state === "ready" || item.status?.label === "Đã chia mục");
    renderLibraryTable();
  } catch (err) { list.innerHTML = `<p class="err">Không tải được thư viện: ${esc(err.message)}</p>`; }
}

$("#library-list").addEventListener("click", async (e) => {
  const retry = e.target.closest("[data-retry]");
  const removeJob = e.target.closest("[data-delete-job]");
  const remove = e.target.closest("[data-delete]");
  const action = retry || removeJob || remove;
  if (action?.disabled) return;
  if (remove) {
    const [kind] = remove.dataset.delete.split(":");
    const title = remove.closest(".library-item")?.querySelector(".item-title")?.textContent || "nội dung này";
    const kindLabel = kind === "document" ? "tài liệu" : "video";
    if (!window.confirm(`Xóa ${kindLabel} “${title}” khỏi thư viện và chỉ mục?`)) return;
  }
  if (removeJob && !window.confirm("Xóa tác vụ lỗi cùng file nguồn liên quan? Hành động này không thể hoàn tác.")) return;
  if (action) action.disabled = true;
  try {
    let r;
    if (retry) r = await fetch(`/api/jobs/${encodeURIComponent(retry.dataset.retry)}/retry`, { method: "POST" });
    else if (removeJob) r = await fetch(`/api/jobs/${encodeURIComponent(removeJob.dataset.deleteJob)}`, { method: "DELETE" });
    else if (remove) {
      const [kind, id] = remove.dataset.delete.split(":");
      r = await fetch(`/api/${kind === "document" ? "documents" : "videos"}/${encodeURIComponent(id)}`, { method: "DELETE" });
    } else return;
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    if (retry) await waitForIngest(data.job_id);
    else $("#pipeline-status").textContent = "Đã xóa nội dung.";
    await loadLibrary();
    await refreshVideos();
  } catch (err) {
    $("#pipeline-status").textContent = `Thao tác thất bại: ${err.message}`;
    await loadLibrary();
    await refreshVideos();
  } finally {
    if (action?.isConnected) action.disabled = false;
  }
});

$("#refresh-library").addEventListener("click", () => loadLibrary());
$("#apply-library-filter").addEventListener("click", renderLibraryTable);
$("#library-filter-title").addEventListener("input", renderLibraryTable);
$("#library-filter-status").addEventListener("change", renderLibraryTable);
document.querySelectorAll("[data-kind]").forEach((button) => button.addEventListener("click", () => {
  libraryKindFilter = button.dataset.kind;
  document.querySelectorAll("[data-kind]").forEach((tab) => {
    const selected = tab === button;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  $("#library-list").setAttribute("aria-labelledby", button.id);
  renderLibraryTable();
}));
document.querySelector(".library-tabs").addEventListener("keydown", (event) => {
  const tabs = [...document.querySelectorAll("[role=tab][data-kind]")];
  const currentIndex = tabs.indexOf(document.activeElement);
  if (currentIndex < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
    : (currentIndex + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
  tabs[nextIndex].focus();
  tabs[nextIndex].click();
});
const addLibraryDialog = $("#add-library-dialog");
$("#open-add-library").addEventListener("click", () => addLibraryDialog.showModal());
$("#close-add-library").addEventListener("click", () => addLibraryDialog.close());
addLibraryDialog.addEventListener("click", (event) => { if (event.target === addLibraryDialog) addLibraryDialog.close(); });

$("#library-video-url").addEventListener("input", () => { if ($("#library-video-url").value.trim()) $("#library-video-file").value = ""; });
$("#library-video-file").addEventListener("change", () => { if ($("#library-video-file").files.length) $("#library-video-url").value = ""; });

$("#library-video-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("#library-video-url").value.trim();
  const file = $("#library-video-file").files[0];
  if (Boolean(url) === Boolean(file)) {
    $("#library-video-status").textContent = "Hãy nhập một link hoặc chọn một file video.";
    return;
  }
  if (file && file.size > 1024 ** 3) {
    $("#library-video-status").textContent = "File vượt giới hạn 1 GB.";
    return;
  }
  const button = e.submitter;
  button.disabled = true;
  $("#library-video-status").textContent = "Đang gửi video vào hàng đợi…";
  const payload = new FormData();
  if (url) payload.append("url", url);
  else payload.append("file", file);
  try {
    const r = await fetch("/api/ingest", { method: "POST", body: payload });
    const result = await r.json();
    if (!r.ok) throw new Error(result.detail || r.statusText);
    await waitForIngest(result.job_id);
    $("#library-video-status").textContent = "Đã xử lý video.";
    $("#library-video-form").reset();
    addLibraryDialog.close();
  } catch (error) {
    $("#library-video-status").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("#library-doc-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("#document-file").files[0];
  if (!file) return;
  const button = e.submitter;
  button.disabled = true;
  $("#library-doc-status").textContent = "Đang tải tài liệu…";
  try {
    const payload = new FormData(); payload.append("file", file);
    const r = await fetch("/api/documents", { method: "POST", body: payload });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    await waitForIngest(data.job_id);
    $("#library-doc-status").textContent = "Tài liệu đã sẵn sàng để hỏi đáp.";
    $("#library-doc-form").reset();
    addLibraryDialog.close();
  } catch (err) { $("#library-doc-status").textContent = err.message; }
  finally { button.disabled = false; }
});

$("#form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  lastQuestion = q;
  const btn = $("#go");
  btn.disabled = true; btn.textContent = "Đang tìm nguồn và soạn câu trả lời…";
  const box = $("#result");
  box.style.display = "block";
  box.innerHTML = `<p class="hint">Đang tìm trong video và soạn câu trả lời…</p>`;
  const busyWatch = setInterval(async () => {
    try {
      const status = await (await fetch("/api/admin/status")).json();
      if (status.gpu_busy) box.innerHTML = `<p class="hint">${status.phase === "asr" ? "Nhận dạng lời nói (ASR) đang dùng bộ xử lý đồ họa (GPU); câu hỏi đang chờ tới lượt tạo câu trả lời. Bước tìm nguồn vẫn chạy bình thường." : "Mô hình ngôn ngữ (LLM) đang bận; đang chờ để tạo câu trả lời…"}</p>`;
    } catch { /* Giữ trạng thái chờ hiện tại nếu tạm thời không đọc được trạng thái. */ }
  }, 900);
  try {
    const r = await fetch("/api/admin/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, mode: $("#mode").value || null }) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    renderResult(d);
  } catch (err) {
    const detail = String(err.message || "Lỗi không xác định");
    const graphMissing = detail.includes("Chỉ mục chưa có Đồ thị");
    const message = graphMissing ? "Chưa có sơ đồ liên kết trong dữ liệu đã chuẩn bị." : detail;
    const hint = graphMissing ? "Hãy chạy lại lệnh vskr index để chuẩn bị dữ liệu, rồi thử lại." : "";
    box.innerHTML = `<p class="err">Không trả lời được: ${esc(message)}</p>${hint ? `<p class="hint">${esc(hint)}</p>` : ""}`;
  } finally {
    clearInterval(busyWatch);
    btn.disabled = false; btn.textContent = "Hỏi bài";
  }
});

$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#form").requestSubmit(); });

function fmtMetric(value, percent = false) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return percent ? `${(value * 100).toFixed(1)}%` : value.toFixed(3);
}

function renderEvalResults(result) {
  const body = $("#eval-results");
  if (!result || !result.summary) {
    body.innerHTML = `<tr><td>Chưa có dữ liệu</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>`;
    $("#eval-results-meta").textContent = "Chưa có kết quả đánh giá.";
    return;
  }
  const rawLabels = result.method_labels || {
    hybrid_graph: "Kết hợp ý nghĩa + từ khóa + liên kết (mặc định)", hybrid: "Kết hợp ý nghĩa + từ khóa",
    vector: "Tìm theo ý nghĩa", bm25: "Tìm từ khóa", graph: "Tìm qua sơ đồ liên kết",
  };
  const plainLabels = { hybrid_graph: "Kết hợp ý nghĩa + từ khóa + liên kết (mặc định)", hybrid: "Kết hợp ý nghĩa + từ khóa", vector: "Tìm theo ý nghĩa", bm25: "Tìm từ khóa", graph: "Tìm qua sơ đồ liên kết" };
  const labels = Object.fromEntries(Object.entries(rawLabels).map(([mode, label]) => [mode, plainLabels[mode] || String(label)
    .replace("Graph tăng trọng số từ hiếm (IDF)", "Kết hợp · ưu tiên chủ đề ít gặp")
    .replace("Graph mở rộng khái niệm bằng embedding E5", "Kết hợp · mở rộng chủ đề gần nghĩa (E5)")
    .replace("Graph lan truyền điểm (PPR)", "Kết hợp · lan điểm qua liên kết (PPR)")]));
  body.innerHTML = Object.entries(labels).filter(([mode]) => result.summary[mode]).map(([mode, label]) => {
    const fact = result.summary[mode]?.fact;
    const multi = result.summary[mode]?.multihop;
    return `<tr><td>${esc(label)}</td><td>${fmtMetric(fact?.recall_at_5, true)}</td><td>${fmtMetric(fact?.mrr)}</td><td>${fmtMetric(multi?.recall_at_5, true)}</td><td>${fmtMetric(multi?.mrr)}</td></tr>`;
  }).join("");
  const exp = result.retrieval_config?.experiments;
  const experimentLabel = exp
    ? ` · Tuỳ chọn thử nghiệm: ${[
      exp.idf_weighting && "ưu tiên chủ đề ít gặp", exp.semantic_expansion && "mở rộng chủ đề gần nghĩa (E5)", exp.ppr && `lan điểm qua liên kết (PPR, mức ${exp.damping})`,
    ].filter(Boolean).join(", ") || "đã tắt"}`
    : "";
  const count = Number(result.question_count || 0);
  const sampleNote = count < 20 ? " Chạy thử: nên có ít nhất 20 câu để so sánh đáng tin hơn." : "";
  $("#eval-results-meta").textContent = `Số câu: ${result.question_count ?? "—"} · Thời điểm: ${result.created_at ? new Date(result.created_at).toLocaleString("vi-VN") : "—"}${experimentLabel} · Chỉ đo tìm nguồn, không chấm câu trả lời.${sampleNote}`;
}

function evalExperimentOptions() {
  const ppr = $("#eval-exp-ppr").checked;
  return {
    idf_weighting: $("#eval-exp-idf").checked,
    semantic_expansion: $("#eval-exp-semantic").checked,
    ppr,
    damping: ppr ? Number($("#eval-exp-damping").value) : 0.5,
    seed_weight: ppr ? Number($("#eval-exp-seed").value) : 0.05,
  };
}

function validateExperimentOptions(options) {
  if (options.ppr && (!Number.isFinite(options.damping) || options.damping < 0 || options.damping > 0.99 ||
      !Number.isFinite(options.seed_weight) || options.seed_weight < 0 || options.seed_weight > 1)) {
    throw new Error("Hệ số PPR phải từ 0 đến 0,99 và trọng số nguồn từ 0 đến 1.");
  }
}

function syncExperimentControl(leftSelector, rightSelector) {
  const left = $(leftSelector), right = $(rightSelector);
  const copy = (from, to) => {
    if (from.type === "checkbox") to.checked = from.checked;
    else to.value = from.value;
  };
  left.addEventListener(left.type === "checkbox" ? "change" : "input", () => copy(left, right));
  right.addEventListener(right.type === "checkbox" ? "change" : "input", () => copy(right, left));
}

syncExperimentControl("#graph-idf", "#eval-exp-idf");
syncExperimentControl("#graph-semantic", "#eval-exp-semantic");
syncExperimentControl("#graph-ppr", "#eval-exp-ppr");
syncExperimentControl("#graph-damping", "#eval-exp-damping");
syncExperimentControl("#graph-seed", "#eval-exp-seed");

function updatePprControls() {
  const enabled = $("#eval-exp-ppr").checked;
  $("#eval-exp-damping").disabled = !enabled;
  $("#eval-exp-seed").disabled = !enabled;
  $("#graph-damping").disabled = !enabled;
  $("#graph-seed").disabled = !enabled;
}

$("#eval-exp-ppr").addEventListener("change", updatePprControls);
$("#graph-ppr").addEventListener("change", updatePprControls);

function renderEvalQuestions(items) {
  $("#eval-question-count").textContent = String(items.length);
  $("#eval-run").disabled = items.length === 0;
  $("#eval-empty-hint").hidden = items.length > 0;
  const list = $("#eval-question-list");
  if (!items.length) {
    list.innerHTML = `<p class="hint">Chưa có câu hỏi đánh giá. Hãy thêm câu hỏi thật hoặc nhập JSONL.</p>`;
    return;
  }
  list.innerHTML = items.map((item) => `<div class="question-row"><p>${esc(item.question)}</p><span class="tag">${item.type === "fact" ? "Tra dữ kiện" : "Nhiều bước"} · ${item.relevant.length} mục đúng</span><button class="small-action danger" type="button" data-eval-delete="${esc(item.id)}" aria-label="Xóa câu hỏi">Xóa</button></div>`).join("");
}

async function loadEvaluation() {
  try {
    const [questionsResponse, resultsResponse] = await Promise.all([fetch("/api/eval/questions"), fetch("/api/eval/results")]);
    const questions = await questionsResponse.json();
    const results = await resultsResponse.json();
    if (!questionsResponse.ok) throw new Error(questions.detail || questionsResponse.statusText);
    if (!resultsResponse.ok) throw new Error(results.detail || resultsResponse.statusText);
    renderEvalQuestions(questions);
    renderEvalResults(results);
  } catch (err) {
    $("#eval-question-list").innerHTML = `<p class="err">Không tải được bộ đánh giá: ${esc(err.message)}</p>`;
  }
}

$("#eval-question-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = $("#eval-form-status");
  try {
    const relevant = JSON.parse($("#eval-relevant").value);
    if (!Array.isArray(relevant)) throw new Error("Các mục đúng cần là một mảng JSON.");
    const payload = { question: $("#eval-question").value, type: $("#eval-type").value, relevant };
    const response = await fetch("/api/eval/questions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.statusText);
    $("#eval-question-form").reset();
    status.textContent = "Đã thêm câu hỏi.";
    renderEvalQuestions(data);
    renderEvalResults(null);
  } catch (err) { status.textContent = `Không lưu được: ${err.message}`; }
});

$("#eval-import-button").addEventListener("click", async () => {
  const file = $("#eval-import").files[0];
  const status = $("#eval-form-status");
  if (!file) { status.textContent = "Chọn tệp JSONL trước."; return; }
  const payload = new FormData(); payload.append("file", file);
  try {
    const response = await fetch("/api/eval/questions/import", { method: "POST", body: payload });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.statusText);
    renderEvalQuestions(data);
    renderEvalResults(null);
    status.textContent = `Đã nhập ${data.length} câu hỏi.`;
    $("#eval-import").value = "";
  } catch (err) { status.textContent = `Không nhập được: ${err.message}`; }
});

$("#eval-question-list").addEventListener("click", async (e) => {
  const button = e.target.closest("[data-eval-delete]");
  if (!button) return;
  try {
    const response = await fetch(`/api/eval/questions/${encodeURIComponent(button.dataset.evalDelete)}`, { method: "DELETE" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.statusText);
    await loadEvaluation();
  } catch (err) { $("#eval-form-status").textContent = `Không xóa được: ${err.message}`; }
});

$("#eval-run").addEventListener("click", async (e) => {
  const button = e.currentTarget;
  if (button.disabled) return;
  button.disabled = true;
  $("#eval-run-status").textContent = "Đang so sánh các cách tìm trên bộ câu hỏi đã nhập…";
  try {
    const options = evalExperimentOptions();
    validateExperimentOptions(options);
    const response = await fetch("/api/eval/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || response.statusText);
    renderEvalResults(result);
    $("#eval-run-status").textContent = "Đã lưu kết quả vào data/eval/results.json.";
  } catch (err) {
    const detail = String(err.message || "Lỗi không xác định");
    $("#eval-run-status").textContent = detail.includes("Chưa có câu hỏi đánh giá")
      ? "Chưa có câu hỏi đã lưu. Hãy thêm câu hỏi kèm nguồn đúng ở biểu mẫu bên trái trước."
      : `Không chạy được: ${detail}`;
  } finally { button.disabled = Number($("#eval-question-count").textContent) === 0; }
});

function svgElement(name, attrs = {}) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}

function renderGraph(data) {
  lastGraphData = data;
  const canvas = $("#graph-canvas");
  const list = $("#graph-path-list");
  canvas.innerHTML = "";
  list.innerHTML = "";
  if (!data.nodes.length) {
    canvas.innerHTML = `<div class="empty" style="padding:40px">Chưa tìm thấy khái niệm hoặc nguồn liên quan đến câu hỏi này.</div>`;
    $("#graph-status").textContent = "Không tìm thấy mối liên hệ phù hợp với câu hỏi này.";
    return;
  }
  const allConcepts = data.nodes.filter((n) => n.kind === "concept");
  const videoScenes = new Map(data.nodes.filter((n) => n.kind === "video_scene").map((n) => [`${n.video_id}:${n.scene_id}`, n]));
  const sourceNodes = data.nodes.filter((n) => n.kind !== "concept" && n.kind !== "video_scene");
  const mentionCounts = new Map();
  for (const edge of data.edges) {
    if (edge.type === "MENTIONS") mentionCounts.set(edge.from, (mentionCounts.get(edge.from) || 0) + (edge.weight || 1));
  }
  const queryTokens = new Set(($("#graph-query").value.toLocaleLowerCase("vi").match(/[\p{L}\p{N}]+/gu) || []));
  const directConcepts = allConcepts.filter((node) => queryTokens.has(node.label.toLocaleLowerCase("vi")));
  const otherConcepts = allConcepts.filter((node) => !directConcepts.includes(node))
    .sort((a, b) => (mentionCounts.get(b.id) || 0) - (mentionCounts.get(a.id) || 0) || a.label.localeCompare(b.label, "vi"));
  const orderedConcepts = [...directConcepts, ...otherConcepts];
  const concepts = $("#graph-all-concepts").checked ? orderedConcepts : orderedConcepts.slice(0, 8);
  const allConceptIds = new Set(allConcepts.map((node) => node.id));
  const visibleConceptIds = new Set(concepts.map((node) => node.id));
  const groupMap = new Map(), nodeToGroup = new Map();
  for (const node of sourceNodes) {
    const isDocument = node.kind === "document_section";
    const groupId = isDocument
      ? `document:${node.document_id || node.id}`
      : node.scene_id ? `scene:${node.video_id}:${node.scene_id}` : `video:${node.video_id || node.id}`;
    let group = groupMap.get(groupId);
    if (!group) {
      const scene = node.scene_id ? videoScenes.get(`${node.video_id}:${node.scene_id}`) : null;
      group = {
        id: groupId, kind: isDocument ? "document" : "video", title: node.title || scene?.title || "",
        video_id: node.video_id, document_id: node.document_id, scene_id: node.scene_id,
        start: scene?.start ?? node.start, end: scene?.end ?? node.end, members: [],
      };
      groupMap.set(groupId, group);
    }
    group.members.push(node);
    if (node.start != null) group.start = group.start == null ? node.start : Math.min(group.start, node.start);
    if (node.end != null) group.end = group.end == null ? node.end : Math.max(group.end, node.end);
    nodeToGroup.set(node.id, groupId);
  }
  for (const scene of videoScenes.values()) {
    const groupId = `scene:${scene.video_id}:${scene.scene_id}`;
    if (groupMap.has(groupId)) nodeToGroup.set(scene.id, groupId);
  }
  const groups = [...groupMap.values()].sort((a, b) =>
    `${a.title}:${a.start ?? 0}`.localeCompare(`${b.title}:${b.start ?? 0}`, "vi")
  );
  const graphEdges = new Map();
  let hiddenCooccurrences = 0;
  for (const edge of data.edges) {
    if (allConceptIds.has(edge.from) && !visibleConceptIds.has(edge.from)) continue;
    if (allConceptIds.has(edge.to) && !visibleConceptIds.has(edge.to)) continue;
    if (edge.type === "CO_OCCURS" && !$("#graph-cooccurs").checked) {
      hiddenCooccurrences += 1;
      continue;
    }
    const from = nodeToGroup.get(edge.from) || edge.from;
    const to = nodeToGroup.get(edge.to) || edge.to;
    if (from === to || edge.type === "PART_OF_SCENE") continue;
    const key = `${from}|${to}|${edge.type}`;
    const existing = graphEdges.get(key) || { ...edge, from, to, weight: 0 };
    existing.weight += edge.weight || 1;
    graphEdges.set(key, existing);
  }
  const edges = [...graphEdges.values()];
  let edgeCount = 0;
  const width = 900, height = Math.max(420, Math.max(concepts.length, groups.length) * 96 + 40);
  const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Sơ đồ nối các chủ đề với nguồn bài giảng" });
  const positions = new Map();
  concepts.forEach((n, i) => positions.set(n.id, { x: 170, y: 42 + i * (height - 84) / Math.max(concepts.length - 1, 1) }));
  groups.forEach((group, i) => positions.set(group.id, { x: 700, y: 42 + i * (height - 84) / Math.max(groups.length - 1, 1) }));
  const relationNames = {
    MENTIONS: "nhắc đến", CO_OCCURS: "cùng xuất hiện", VIDEO_DOCUMENT_MATCH: "video khớp tài liệu",
    DEPENDS_ON: "cần trước", PART_OF: "thuộc", CAUSES: "gây ra", USES: "dùng",
    DEFINES: "định nghĩa", IS_A: "là một", HAS_PROPERTY: "có đặc tính", COMPARES_WITH: "so sánh",
    NEXT_SECTION: "mục kế tiếp", NEXT_SEGMENT: "đoạn kế tiếp", RELATED_TO: "liên quan",
  };
  for (const edge of edges) {
    const a = positions.get(edge.from), b = positions.get(edge.to);
    if (!a || !b) continue;
    const line = svgElement("line", {
      x1: a.x, y1: a.y, x2: b.x, y2: b.y,
      stroke: edge.type === "NEXT_SEGMENT" || edge.type === "CO_OCCURS" ? "#9a6a20" : edge.type === "VIDEO_DOCUMENT_MATCH" ? "#35b7ae" : "#82909c",
      "stroke-width": edge.type === "NEXT_SEGMENT" ? 2.5 : edge.type === "CO_OCCURS" ? 1 : 1.5,
      opacity: edge.type === "CO_OCCURS" ? .2 : edge.type === "MENTIONS" ? .55 : .82,
    });
    if (edge.type === "NEXT_SEGMENT") line.setAttribute("stroke-dasharray", "7 6");
    if (edge.type === "CO_OCCURS") line.setAttribute("stroke-dasharray", "3 5");
    svg.appendChild(line);
    edgeCount += 1;
    if (edge.type === "MENTIONS" || edge.type === "CO_OCCURS") continue;
    const label = svgElement("text", {
      x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 5, fill: "currentColor", "font-size": 11,
      "text-anchor": "middle", "paint-order": "stroke", stroke: "var(--canvas)", "stroke-width": 4,
    });
    label.textContent = relationNames[edge.type] || edge.type.toLowerCase().replaceAll("_", " ");
    svg.appendChild(label);
  }
  for (const node of concepts) {
    const { x, y } = positions.get(node.id);
    svg.appendChild(svgElement("circle", { cx: x, cy: y, r: 24, fill: "#0b6e6e" }));
    const label = svgElement("text", { x: x + 36, y: y + 5, fill: "currentColor", "font-size": 15 });
    label.textContent = node.label; svg.appendChild(label);
  }
  for (const group of groups) {
    const { x, y } = positions.get(group.id);
    svg.appendChild(svgElement("rect", { x: x - 16, y: y - 16, width: 32, height: 32, rx: 3, fill: "#e7a33e" }));
    const label = svgElement("text", { x: x - 30, y: y + 35, fill: "currentColor", "font-size": 12, "text-anchor": "middle" });
    const sourceLabel = group.kind === "document"
      ? `${group.title || "Tài liệu"} · ${group.members.length} mục`
      : group.scene_id
        ? `${group.title || "Video"} · cảnh ${(Number(group.scene_id.split(":").at(-1)) || 0) + 1}`
        : `${group.title || "Video"} · ${group.members.length} đoạn`;
    label.textContent = sourceLabel.length > 43 ? `${sourceLabel.slice(0, 40)}…` : sourceLabel;
    svg.appendChild(label);
  }
  canvas.appendChild(svg);
  const activeExperiments = data.experiments ? [
    data.experiments.idf_weighting && "IDF", data.experiments.semantic_expansion && "E5", data.experiments.ppr && "PPR",
  ].filter(Boolean) : [];
  const experimentNames = { IDF: "ưu tiên chủ đề ít gặp", E5: "mở rộng chủ đề gần nghĩa", PPR: "lan điểm qua liên kết" };
  const experimentStatus = activeExperiments.length ? ` · Tuỳ chọn đang bật: ${activeExperiments.map((name) => experimentNames[name] || name).join(" + ")}` : " · Tuỳ chọn thử nghiệm đang tắt";
  const hiddenNote = hiddenCooccurrences ? ` · đang ẩn ${hiddenCooccurrences} cạnh “cùng xuất hiện”` : "";
  $("#graph-status").textContent = `Sơ đồ có ${concepts.length}/${allConcepts.length} chủ đề và ${groups.length} nhóm nguồn liên quan (${sourceNodes.length} đoạn hoặc mục, ${edgeCount} đường nối)${hiddenNote}${experimentStatus}.`;
  const renderMember = (node) => {
    const isDocument = node.kind === "document_section";
    const label = isDocument ? `Mục ${node.section ?? "—"}` : `${fmt(node.start || 0)}${node.end != null ? `–${fmt(node.end)}` : ""}`;
    return `<article class="src"><div class="src-head"><strong>${isDocument ? "Tài liệu" : "Video"}</strong><span>${esc(node.title || node.video_id || "")}</span><span class="t">${label}</span><span class="src-actions"><button class="play" type="button" data-graph-node="${esc(node.id)}">${isDocument ? "Xem mục" : "Phát đoạn"}</button></span></div><p>${esc(node.text || "")}</p></article>`;
  };
  list.innerHTML = groups.map((group) => {
    const title = group.kind === "document"
      ? `${group.title || "Tài liệu"} · ${group.members.length} mục liên quan`
      : group.scene_id
        ? `${group.title || "Video"} · cảnh ${(Number(group.scene_id.split(":").at(-1)) || 0) + 1} · ${group.members.length} đoạn`
        : `${group.title || "Video"} · ${group.members.length} đoạn liên quan`;
    const members = group.members.map(renderMember).join("");
    return group.members.length > 1
      ? `<details class="graph-group"><summary>${esc(title)}</summary>${members}</details>`
      : `<section class="graph-group"><strong>${esc(title)}</strong>${members}</section>`;
  }).join("");
  list.dataset.nodes = JSON.stringify(sourceNodes);
}

["#graph-all-concepts", "#graph-cooccurs"].forEach((selector) => {
  $(selector).addEventListener("change", () => {
    if (lastGraphData) renderGraph(lastGraphData);
  });
});

$("#graph-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = $("#graph-query").value.trim();
  if (!query) return;
  $("#graph-status").textContent = "Đang tìm các khái niệm và nguồn có liên quan…";
  try {
    const ppr = $("#graph-ppr").checked;
    const options = {
      ppr,
      damping: ppr ? Number($("#graph-damping").value) : 0.5,
      seed_weight: ppr ? Number($("#graph-seed").value) : 0.05,
    };
    validateExperimentOptions(options);
    const params = new URLSearchParams({
      query, adjacent: String($("#graph-adjacent").checked),
      idf_weighting: String($("#graph-idf").checked),
      semantic_expansion: String($("#graph-semantic").checked),
      ppr: String(options.ppr), damping: String(options.damping), seed_weight: String(options.seed_weight),
    });
    const response = await fetch(`/api/graph?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.statusText);
    renderGraph(data);
  } catch (err) { $("#graph-status").textContent = `Không tải được đồ thị: ${err.message}`; }
});

$("#graph-path-list").addEventListener("click", (e) => {
  const button = e.target.closest("[data-graph-node]");
  if (!button) return;
  const node = JSON.parse($("#graph-path-list").dataset.nodes || "[]").find((n) => n.id === button.dataset.graphNode);
  if (!node) return;
  if (node.kind === "document_section") {
    showDocumentSection({ link: `/api/documents/${encodeURIComponent(node.document_id)}/sections/${node.section}` });
  } else jump(node.video_id, node.start || 0);
});

loadDashboard();
