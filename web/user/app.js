/**
 * App người học (PWA): thư viện bài giảng, trình phát video, hỏi đáp có trích dẫn và lịch sử hội thoại.
 *
 * Toàn bộ trạng thái giao diện nằm trong `state`. Giao diện được dựng bằng DOM (make(), textContent),
 * không dùng innerHTML, nên nội dung từ server không thể chèn mã HTML/JS vào trang.
 */
import { ApiError, request } from "/shared/api.js";

const $ = (selector) => document.querySelector(selector);
/** Trạng thái duy nhất của giao diện: thư viện, video đang xem, nguồn của từng lượt, hội thoại, tác vụ. */
const state = {
  videos: [], documents: [], conversations: [], activeConversationId: null, currentVideo: null, sources: [], player: null,
  projects: [], currentProjectId: null,
  signedIn: false,
  uploadLimits: null, jobs: [], jobPollTimer: null, jobPollInFlight: false, toastTimer: null,
  sourcesByTurn: new Map(), answersByTurn: new Map(), activeTurnId: null, activeSourceKey: null, turnCounter: 0,
  youtubePromise: null, pendingJump: null, retryUntil: 0, retryInterval: null,
  retryPanel: null, retryButton: null, retryTurn: null,
  installPrompt: null, asking: false, conversationLoadToken: 0, renameConversationId: null,
  preferredConversationVideoId: null, conversationSource: { source_id: null, source_type: "library", title: "Cả thư viện" },
  drafts: new Map(), draftId: "draft-new", sourcePickerMode: "new",
  segmentContext: null, videoPanelWidth: null,
};

function sourceKey(source) {
  return JSON.stringify([
    String(source.n), source.source_type || "", source.video_id || "",
    source.document_id || "", String(source.section ?? ""),
  ]);
}

function formatTime(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  const pad = (number) => String(number).padStart(2, "0");
  return hours ? `${hours}:${pad(minutes)}:${pad(remainder)}` : `${minutes}:${pad(remainder)}`;
}

function currentPlaybackTime() {
  if (!state.player) return 0;
  if (state.player.tagName === "VIDEO") return Number(state.player.currentTime) || 0;
  if (typeof state.player.getCurrentTime === "function") return Number(state.player.getCurrentTime()) || 0;
  return 0;
}

function setConversationSource(source = { source_id: null, source_type: "library", title: "Cả thư viện" }) {
  state.conversationSource = source;
  const chip = $("#conversation-source-chip");
  chip.textContent = source.source_type === "library" ? "Cả thư viện ▾" : `📹 ${source.title} ▾`;
  chip.setAttribute("aria-label", `Chọn nguồn. Hiện tại: ${source.title}`);
  $("#privacy-note").textContent = source.source_type === "library"
    ? "Tìm trên toàn bộ thư viện"
    : `Chỉ tìm trong: ${source.title}`;
}

function currentDraftKey() {
  return state.draftId || state.activeConversationId || "draft-new";
}

function createDraftId() {
  return `draft-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function saveCurrentDraft() {
  if (typeof questionInput !== "undefined") state.drafts.set(currentDraftKey(), questionInput.value);
}

function restoreDraft(key) {
  questionInput.value = state.drafts.get(key) || "";
  updateQuestionInput();
}

function setSourceDeleted(deleted) {
  $("#source-deleted-banner").hidden = !deleted;
  $("#question-input").disabled = deleted;
  $("#ask-button").disabled = deleted;
}

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function isYouTube(video) {
  try {
    const host = new URL(video.playback_url).hostname.toLowerCase();
    return host === "youtu.be" || host === "youtube.com" || host.endsWith(".youtube.com")
      || host === "youtube-nocookie.com" || host.endsWith(".youtube-nocookie.com");
  } catch { return false; }
}

function setStatus(message = "", kind = "", status = $("#app-status")) {
  if (!status) return;
  status.textContent = message;
  status.hidden = !message;
  status.classList.toggle("error", kind === "error");
}

function setContentDialogStatus(message = "", kind = "") {
  setStatus(message, kind, $("#content-dialog-status"));
}

function updateProjectLabel() {
  const label = $("#content-project-label");
  if (!label) return;
  const limits = state.uploadLimits;
  if (!limits || !Number.isFinite(Number(limits.max_upload_mb))) {
    label.textContent = "Chưa tải được hạn mức tải lên từ tài khoản.";
    return;
  }
  const parts = [`Tối đa ${Number(limits.max_upload_mb)} MB mỗi tệp`];
  if (Number.isFinite(Number(limits.max_duration_min))) parts.push(`${Number(limits.max_duration_min)} phút mỗi video`);
  if (Number.isFinite(Number(limits.max_active_jobs))) parts.push(`${Number(limits.max_active_jobs)} tác vụ cùng lúc`);
  label.textContent = `Hạn mức tài khoản: ${parts.join(" · ")}.`;
}

function renderLibraryBadge() {
  const badge = $("#sidebar-library-badge");
  const libraryButton = $("#sidebar-library");
  if (!badge || !libraryButton) return;
  const total = state.videos.length + state.documents.length;
  badge.textContent = String(total);
  badge.hidden = total === 0;
  const hasActiveJobs = state.jobs.some((job) => job.status === "queued" || job.status === "running");
  libraryButton.classList.toggle("has-active-jobs", hasActiveJobs);
  libraryButton.setAttribute("aria-label", hasActiveJobs ? "Thư viện, có nội dung đang xử lý" : "Thư viện");
}

function showToast(message) {
  const toast = $("#app-toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { toast.hidden = true; }, 4000);
}

function renderContentJobs() {
  const list = $("#content-job-list");
  if (!list) return;
  list.replaceChildren();
  if (!state.jobs.length) {
    list.append(make("p", "muted", "Chưa có nội dung nào đang xử lý."));
    return;
  }
  for (const job of state.jobs.slice(0, 8)) {
    const card = make("article", "content-job");
    const heading = make("div", "content-job-heading");
    heading.append(
      make("strong", "content-job-title", job.title || "Nội dung mới"),
      make("span", `content-job-state ${job.status === "error" ? "is-error" : ""}`, job.status === "done" ? "Đã xong" : job.status === "error" ? "Lỗi" : job.status === "queued" ? "Đang chờ" : "Đang xử lý"),
    );
    const details = job.error_detail || job.message || (job.status === "done" ? "Nội dung đã sẵn sàng để hỏi đáp." : "Vui lòng chờ…");
    card.append(heading, make("p", `content-job-message ${job.status === "error" ? "is-error" : ""}`, `${job.phase || "Tác vụ"}: ${details}`));
    if (job.status === "queued" || job.status === "running") {
      const progress = make("progress", "content-job-progress");
      progress.max = 1;
      progress.setAttribute("aria-label", `Tiến độ xử lý ${job.title || "nội dung"}`);
      card.append(progress);
    }
    list.append(card);
  }
}

function stopJobPolling() {
  if (state.jobPollTimer) clearInterval(state.jobPollTimer);
  state.jobPollTimer = null;
}

/** Hỏi lại trạng thái tác vụ của mình khi hộp thoại thêm nội dung đang mở; tác vụ vừa xong thì tải lại thư viện. */
async function refreshMyJobs() {
  if (!contentDialog.open || state.jobPollInFlight) return;
  state.jobPollInFlight = true;
  try {
    const jobs = await request("/api/my/jobs");
    if (!Array.isArray(jobs)) throw new Error("Máy chủ trả về trạng thái tác vụ không hợp lệ.");
    if (!contentDialog.open) return;
    const previousStatuses = new Map(state.jobs.map((job) => [job.job_id, job.status]));
    state.jobs = jobs;
    renderContentJobs();
    renderLibraryBadge();
    const completed = state.jobs.find((job) =>
      job.status === "done" && ["queued", "running"].includes(previousStatuses.get(job.job_id)));
    if (completed) {
      showToast(`${completed.title || "Nội dung"} đã xử lý xong.`);
      await loadLibrary();
    }
    if (state.jobs.some((job) => job.status === "queued" || job.status === "running")) {
      if (!state.jobPollTimer) state.jobPollTimer = setInterval(refreshMyJobs, 2000);
    } else {
      stopJobPolling();
    }
  } catch (error) {
    if (contentDialog.open) setContentDialogStatus(error.message || "Không tải được trạng thái xử lý.", "error");
  } finally {
    state.jobPollInFlight = false;
  }
}

const contentDialog = $("#content-dialog");
const libraryDialog = $("#library-dialog");
let libraryDialogScope = "mine";

/** Kiểm tra định dạng và dung lượng (theo hạn mức của tài khoản) trước khi tải lên; trả về thông báo lỗi hoặc chuỗi rỗng. */
function validateUploadFile(file, type) {
  if (!file) return "Chọn một tệp trước.";
  const extension = file.name.match(/\.([^.]+)$/)?.[1]?.toLowerCase();
  const supported = type === "document"
    ? new Set(["txt", "md", "rst", "pdf", "docx"])
    : new Set(["mp4", "m4v", "mov", "mkv", "webm", "avi", "mpeg", "mpg"]);
  if (!extension || !supported.has(extension)) {
    return type === "document"
      ? "Định dạng tài liệu chưa được hỗ trợ. Hãy dùng .txt, .md, .rst, .pdf hoặc .docx."
      : "Định dạng video chưa được hỗ trợ. Hãy dùng .mp4, .m4v, .mov, .mkv, .webm, .avi, .mpeg hoặc .mpg.";
  }
  const maxUploadMb = Number(state.uploadLimits?.max_upload_mb);
  if (!Number.isFinite(maxUploadMb) || maxUploadMb <= 0) return "Chưa tải được hạn mức tệp từ tài khoản. Hãy thử tải lại trang.";
  if (file.size > maxUploadMb * 1024 * 1024) return `Tệp vượt quá giới hạn ${maxUploadMb} MB của tài khoản.`;
  return "";
}

/** Gửi video (URL hoặc tệp) hoặc tài liệu vào project hiện tại, rồi theo dõi tác vụ xử lý. */
async function enqueueContent({ type, url = "", file = null, statusNode, button, closeOnQueue = false }) {
  const error = type === "document" ? validateUploadFile(file, type)
    : file ? validateUploadFile(file, "video") : "";
  if (error) {
    setStatus(error, "error", statusNode);
    return;
  }
  if (!state.currentProjectId) {
    setStatus("Chưa tải được thư viện. Hãy thử lại sau.", "error", statusNode);
    return;
  }
  let parsedUrl;
  if (url) {
    try { parsedUrl = new URL(url); } catch { setStatus("Nhập một liên kết hợp lệ.", "error", statusNode); return; }
    if (!["http:", "https:"].includes(parsedUrl.protocol)) {
      setStatus("Liên kết phải bắt đầu bằng http:// hoặc https://.", "error", statusNode);
      return;
    }
  } else if (type === "video" && !file) {
    setStatus("Dán liên kết video hoặc chọn một tệp video.", "error", statusNode);
    return;
  }
  if (button) button.disabled = true;
  setStatus("Đang thêm vào hàng đợi xử lý…", "", statusNode);
  try {
    const payload = new FormData();
    if (type === "document") {
      payload.append("file", file);
    } else if (file) {
      payload.append("file", file);
    } else {
      payload.append("url", parsedUrl.href);
    }
    payload.append("project_id", state.currentProjectId);
    const queued = await request(type === "document" ? "/api/documents" : "/api/ingest", {
      method: "POST",
      body: payload,
    });
    state.jobs = [
      { job_id: queued.job_id, title: file?.name || parsedUrl?.hostname || "Nội dung mới", status: "queued", phase: "queued", message: "Đang chờ xử lý…" },
      ...state.jobs.filter((job) => job.job_id !== queued.job_id),
    ];
    renderLibraryBadge();
    if (closeOnQueue && contentDialog.open) contentDialog.close();
    showToast("Đã thêm vào hàng đợi xử lý.");
    setStatus("Đã thêm vào hàng đợi xử lý.", "", statusNode);
    if (contentDialog.open) {
      renderContentJobs();
      await refreshMyJobs();
    }
  } catch (error) {
    setStatus(error.message || "Không thể thêm nội dung vào hàng đợi.", "error", statusNode);
  } finally {
    if (button) button.disabled = false;
  }
}

$("#add-video-toggle").addEventListener("click", () => {
  const panel = $("#add-video-panel");
  panel.hidden = !panel.hidden;
  $("#add-video-toggle").setAttribute("aria-expanded", String(!panel.hidden));
});
$("#user-video-url").addEventListener("input", () => { if ($("#user-video-url").value.trim()) $("#user-video-file").value = ""; });
$("#user-video-file").addEventListener("change", () => { if ($("#user-video-file").files.length) $("#user-video-url").value = ""; });
$("#user-video-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
  const url = $("#user-video-url").value.trim();
  const file = $("#user-video-file").files[0] || null;
  if (Boolean(url) === Boolean(file)) {
    setStatus("Dán một liên kết hoặc chọn một video.", "error", $("#user-video-status"));
    return;
  }
  enqueueContent({ type: "video", url, file, statusNode: $("#user-video-status"), button });
});
$("#user-document-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
  enqueueContent({
    type: "document",
    file: $("#user-document-file").files[0] || null,
    statusNode: $("#user-document-status"),
    button,
  });
});

function openContentDialog() {
  if (!state.currentProjectId) {
    setStatus("Chưa tải được thư viện. Hãy thử lại sau.", "error");
    return;
  }
  updateProjectLabel();
  if (!contentDialog.open) contentDialog.showModal();
  setContentDialogStatus("");
  renderContentJobs();
  refreshMyJobs();
  const selected = document.querySelector(".content-tab[data-content-tab][aria-selected='true']")?.dataset.contentTab || "link";
  $(`#content-${selected === "link" ? "video-url" : selected === "upload" ? "video-file" : "document-file"}`).focus();
}
async function renderLibraryDialog(scope = libraryDialogScope) {
  libraryDialogScope = scope;
  const list = $("#library-dialog-list");
  list.replaceChildren();
  list.append(make("p", "muted", "Đang tải thư viện…"));
  try {
    const catalog = await request(`/api/catalog?scope=${encodeURIComponent(scope)}&project_id=${encodeURIComponent(state.currentProjectId || "")}`);
    list.replaceChildren();
    for (const video of catalog.videos || []) {
    const item = make("div", "library-item");
    const icon = make("span", "library-item-icon", "▶");
    icon.setAttribute("aria-hidden", "true");
    const details = make("span");
    details.append(
      make("strong", "library-item-title", video.title || "Bài giảng"),
      make("small", "library-item-meta", `${formatTime(video.duration || 0)}  ·  ${scope === "mine" ? "Riêng tư" : "Công khai"}`),
    );
    const button = make("button", "small-button", "Xem");
    button.type = "button";
    button.addEventListener("click", () => { libraryDialog.close(); beginConversationWithSource(video); });
    item.append(icon, details, button);
      list.append(item);
    }
    for (const documentItem of catalog.documents || []) {
    const item = make("div", "library-item");
    const icon = make("span", "library-item-icon", "▤");
    icon.setAttribute("aria-hidden", "true");
    const details = make("span");
    details.append(
      make("strong", "library-item-title", documentItem.title || "Tài liệu"),
      make("small", "library-item-meta", `Tài liệu  ·  ${scope === "mine" ? "Riêng tư" : "Công khai"}`),
    );
    item.append(icon, details);
      list.append(item);
    }
    if (!list.children.length) list.append(make("p", "muted", "Chưa có nội dung trong thư viện."));
  } catch (error) {
    list.replaceChildren(make("p", "request-status error", error.message || "Không tải được thư viện."));
  }
}

function clearRetry() {
  if (state.retryInterval) clearInterval(state.retryInterval);
  state.retryInterval = null;
  state.retryUntil = 0;
  state.retryPanel?.remove();
  state.retryPanel = null;
  state.retryButton = null;
  state.retryTurn = null;
}

/** Khi server báo quá tải (429/503): hiện đếm ngược theo Retry-After và nút gửi lại câu hỏi. */
function waitBeforeRetry(seconds, question, message, turn) {
  clearRetry();
  const body = turn.querySelector(".turn-content");
  const status = make("p", "request-status error");
  status.setAttribute("role", "status");
  setStatus(message, "error", status);
  const panel = make("div", "retry-panel");
  const countdown = make("span");
  const button = make("button", "small-button", "Thử lại");
  button.type = "button";
  button.disabled = true;
  panel.append(countdown, button);
  body.replaceChildren(status, panel);
  const waitSeconds = Math.max(1, Number(seconds) || 1);
  state.retryUntil = Date.now() + waitSeconds * 1000;
  state.retryPanel = panel;
  state.retryButton = button;
  state.retryTurn = turn;
  button.addEventListener("click", () => {
    questionInput.value = question;
    updateQuestionInput();
    submitQuestion(question, { retryTurn: turn });
  });
  const update = () => {
    const left = Math.max(0, Math.ceil((state.retryUntil - Date.now()) / 1000));
    countdown.textContent = left
      ? `Bạn có thể gửi lại sau ${left} giây.`
      : "Đã có thể thử lại.";
    button.disabled = left > 0 || state.asking;
    if (!left && state.retryInterval) {
      clearInterval(state.retryInterval);
      state.retryInterval = null;
    }
  };
  update();
  state.retryInterval = setInterval(update, 250);
}

function renderDocuments() {
  const list = $("#document-list");
  list.replaceChildren();
  $("#document-count").textContent = String(state.documents.length);
  $("#document-empty").hidden = state.documents.length > 0;
  for (const doc of state.documents) {
    const item = make("li");
    item.dataset.documentId = String(doc.document_id || doc.id || "");
    item.tabIndex = -1;
    item.append(make("span", "document-icon", "▤"));
    const details = make("span");
    details.append(make("span", "document-title", doc.title || "Tài liệu"));
    details.append(make("span", "document-meta", `${doc.sections || 0} mục · sẵn sàng để hỏi`));
    item.append(details);
    list.append(item);
  }
}

function renderVideos() {
  const list = $("#video-list");
  list.replaceChildren();
  $("#video-count").textContent = String(state.videos.length);
  $("#library-empty").hidden = state.videos.length > 0;
  for (const video of state.videos) {
    const button = make("button", "video-choice");
    button.type = "button";
    button.setAttribute("role", "listitem");
    button.setAttribute("aria-current", state.currentVideo?.video_id === video.video_id ? "true" : "false");
    button.setAttribute("aria-label", `${video.title || "Bài giảng"}${video.duration ? `, ${formatTime(video.duration)}` : ""}`);
    button.append(make("span", "video-icon", isYouTube(video) ? "▶" : "▣"));
    const text = make("span");
    text.append(make("span", "video-choice-title", video.title || "Bài giảng"));
    text.append(make("span", "video-choice-meta", video.duration ? formatTime(video.duration) : "Video bài giảng"));
    button.append(text);
    button.addEventListener("click", () => {
      selectVideo(video);
      closeDrawer({ restoreFocus: true });
    });
    list.append(button);
  }
}

function playerMessage(message) {
  const area = $("#player");
  area.replaceChildren(make("p", "player-message", message));
}

function loadYouTubeApi() {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (state.youtubePromise) return state.youtubePromise;
  state.youtubePromise = new Promise((resolve, reject) => {
    window.onYouTubeIframeAPIReady = () => resolve(window.YT);
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.async = true;
    script.onerror = () => reject(new Error("Không tải được trình phát video."));
    document.head.append(script);
  });
  return state.youtubePromise;
}

/** Dựng trình phát cho video đang chọn: YouTube nhúng, file đã tải lên, hoặc thông báo khi không phát được. */
function setupPlayer(video) {
  const area = $("#player");
  if (state.player?.destroy) state.player.destroy();
  state.player = null;
  area.replaceChildren();
  if (!video?.playback_url) {
    playerMessage("Video này chưa có trình phát nhúng. Bạn vẫn có thể đặt câu hỏi và mở các nguồn bên dưới.");
    return;
  }
  if (isYouTube(video)) {
    const mount = make("div");
    mount.id = "youtube-player";
    area.append(mount);
    loadYouTubeApi().then((yt) => {
      if (state.currentVideo?.video_id !== video.video_id || !mount.isConnected) return;
      state.player = new yt.Player(mount, {
        videoId: video.video_id,
        playerVars: { rel: 0, modestbranding: 1, origin: location.origin },
        events: {
          onReady: () => {
            if (state.pendingJump?.videoId === video.video_id) {
              const start = state.pendingJump.start;
              state.pendingJump = null;
              jumpCurrentVideo(start);
            }
          },
          onError: () => playerMessage("Video không cho nhúng. Hãy mở liên kết nguồn trong thẻ trích dẫn."),
        },
      });
    }).catch(() => playerMessage("Không tải được trình phát. Bạn vẫn có thể mở nguồn và đặt câu hỏi."));
    return;
  }
  if (video.playback_url.startsWith("/api/videos/")) {
    const element = make("video");
    element.controls = true;
    element.playsInline = true;
    element.preload = "metadata";
    element.src = video.playback_url;
    element.setAttribute("aria-label", video.title || "Video bài giảng");
    element.addEventListener("loadedmetadata", () => {
      if (state.pendingJump?.videoId === video.video_id) {
        element.currentTime = state.pendingJump.start;
        state.pendingJump = null;
      }
    });
    state.player = element;
    area.append(element);
    return;
  }
  const message = make("div", "player-message");
  message.append(document.createTextNode("Nền tảng này chưa hỗ trợ phát trực tiếp trong trang. "));
  const link = make("a", "source-link", "Mở video trên nền tảng gốc ↗");
  link.href = video.playback_url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  message.append(link);
  area.append(message);
}

/** Chọn một bài giảng: hiện thẻ bài giảng và dựng trình phát. */
function selectVideo(video, { open = true } = {}) {
  state.currentVideo = video;
  $("#welcome").hidden = open;
  $("#lecture-card").hidden = !open;
  $("#lecture-card").classList.remove("video-expanded");
  $("#expand-video").setAttribute("aria-label", "Phóng to video");
  $("#expand-video").textContent = "⤢";
  $("#lecture-title").textContent = video.title || "Bài giảng";
  $("#lecture-duration").textContent = video.duration ? formatTime(video.duration) : "";
  updateVideoStageButtons();
  renderVideos();
  setupPlayer(video);
  renderTimeline();
}

function updateVideoStageButtons() {
  $("#show-video-stage").hidden = !state.currentVideo || !$("#lecture-card").hidden;
}

function resizeVideoPanel(width) {
  const sidebarWidth = document.body.classList.contains("sidebar-collapsed") ? 72 : 260;
  const maxWidth = Math.max(280, window.innerWidth - sidebarWidth - 400);
  const boundedWidth = Math.max(280, Math.min(Math.min(720, maxWidth), width));
  state.videoPanelWidth = boundedWidth;
  document.documentElement.style.setProperty("--video-panel-width", `${boundedWidth}px`);
  const resizer = $("#video-resizer");
  resizer.setAttribute("aria-valuemin", "280");
  resizer.setAttribute("aria-valuemax", String(Math.floor(Math.min(720, maxWidth))));
  resizer.setAttribute("aria-valuenow", String(Math.round(boundedWidth)));
}

function setActiveSource(turnId, source) {
  state.activeTurnId = turnId == null ? null : String(turnId);
  state.activeSourceKey = source ? sourceKey(source) : null;
  for (const item of document.querySelectorAll(".source-card, .timeline-mark")) {
    const active = item.dataset.turnId === state.activeTurnId
      && item.dataset.sourceKey === state.activeSourceKey;
    item.classList.toggle("is-active", active);
    if (active) {
      const details = item.closest("details");
      if (details) details.open = true;
    }
    if (active) item.setAttribute("aria-current", "true");
    else item.removeAttribute("aria-current");
  }
}

function renderTimeline() {
  const timeline = $("#timeline");
  timeline.replaceChildren();
  const duration = Number(state.currentVideo?.duration || 0);
  const sources = duration ? state.sources.filter((source) => source.video_id === state.currentVideo?.video_id && Number.isFinite(Number(source.start))) : [];
  $("#timeline-range").textContent = duration ? `0:00 – ${formatTime(duration)}` : "";
  $("#timeline-empty").hidden = Boolean(duration && sources.length);
  for (const source of sources) {
    const mark = make("button", "timeline-mark");
    mark.type = "button";
    mark.dataset.turnId = String(state.activeTurnId || "");
    mark.dataset.sourceNumber = String(source.n);
    mark.dataset.sourceKey = sourceKey(source);
    mark.style.left = `${Math.max(0, Math.min(100, Number(source.start) / duration * 100))}%`;
    const width = Number(source.end) > Number(source.start) ? Number(source.end) - Number(source.start) : 1;
    mark.style.width = `${Math.max(.8, Math.min(30, width / duration * 100))}%`;
    mark.title = `Nguồn ${source.n} · ${source.label || formatTime(source.start)}`;
    mark.setAttribute("aria-label", `Tua đến nguồn ${source.n}, ${source.label || formatTime(source.start)}`);
    if (sourceKey(source) === state.activeSourceKey && String(state.activeTurnId) === String(mark.dataset.turnId)) {
      mark.classList.add("is-active");
      mark.setAttribute("aria-current", "true");
    }
    mark.addEventListener("click", () => openSource(source, state.activeTurnId));
    timeline.append(mark);
  }
}

function jumpCurrentVideo(start) {
  if (!state.player) return;
  if (state.player.tagName === "VIDEO") {
    const element = state.player;
    if (element.readyState >= 1) element.currentTime = start;
    else state.pendingJump = { videoId: state.currentVideo.video_id, start };
    element.play().catch(() => {});
  } else if (state.player.seekTo) {
    state.player.seekTo(start, true);
    state.player.playVideo();
  } else {
    state.pendingJump = { videoId: state.currentVideo.video_id, start };
  }
}

function closeLecture() {
  if (state.player?.pause) state.player.pause();
  if (state.player?.destroy) state.player.destroy();
  state.player = null;
  $("#lecture-card").hidden = true;
  $("#lecture-card").classList.remove("video-expanded");
  $("#welcome").hidden = Boolean($("#conversation").children.length);
  updateVideoStageButtons();
  renderTimeline();
}

/** Mở một nguồn được trích dẫn: tài liệu thì hiện mục tương ứng, video thì chuyển tới đúng mốc thời gian. */
async function openSource(source, turnId = state.activeTurnId) {
  const turnSources = state.sourcesByTurn.get(String(turnId));
  if (turnSources) {
    state.sources = turnSources;
    setActiveSource(turnId, source);
    renderTimeline();
  }
  if (source.source_type === "document") {
    try {
      const data = await request(source.link);
      $("#document-title").textContent = `${data.title || source.title || "Tài liệu"} · Mục ${data.section || source.section}`;
      $("#document-text").textContent = data.text || "Không có nội dung trong mục này.";
      $("#document-dialog").showModal();
    } catch {
      setStatus("Không mở được mục tài liệu. Hãy thử lại sau.", "error");
    }
    return;
  }
  const video = state.videos.find((item) => item.video_id === source.video_id);
  if (!video) return;
  if (state.currentVideo?.video_id !== video.video_id) {
    state.pendingJump = { videoId: video.video_id, start: Number(source.start) || 0 };
    selectVideo(video);
    return;
  }
  jumpCurrentVideo(Number(source.start) || 0);
}

const circledNumbers = [..."①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"];

function showCitationPreview(wrapper, visible) {
  wrapper.querySelector(".citation-popover").hidden = !visible;
}

/** Nút trích dẫn [n] trong câu trả lời, kèm khung xem trước nội dung nguồn. */
function createCitation(source, number, turnId, label) {
  const wrapper = make("span", "citation-wrap");
  const button = make("button", "citation", label);
  button.type = "button";
  button.setAttribute("aria-label", `Xem trước nguồn ${number}; bấm để mở nguồn`);
  button.setAttribute("aria-haspopup", "true");
  const location = source.source_type === "document"
    ? `Mục ${source.section ?? "tài liệu"}`
    : source.label || formatTime(source.start);
  const preview = make("span", "citation-popover");
  preview.setAttribute("role", "tooltip");
  preview.append(
    make("strong", "citation-preview-title", source.title || (source.source_type === "document" ? "Tài liệu" : "Bài giảng")),
    make("small", "citation-preview-location", location),
    make("span", "citation-preview-text", source.text ? source.text.slice(0, 220) : "Không có trích đoạn xem trước."),
  );
  preview.hidden = true;
  wrapper.append(button, preview);
  wrapper.addEventListener("mouseenter", () => showCitationPreview(wrapper, true));
  wrapper.addEventListener("mouseleave", () => showCitationPreview(wrapper, false));
  wrapper.addEventListener("focusin", () => showCitationPreview(wrapper, true));
  wrapper.addEventListener("focusout", (event) => {
    if (!wrapper.contains(event.relatedTarget)) showCitationPreview(wrapper, false);
  });
  let holdTimer = null;
  let held = false;
  const cancelHold = () => {
    if (holdTimer) clearTimeout(holdTimer);
    holdTimer = null;
  };
  button.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "touch") return;
    held = false;
    holdTimer = setTimeout(() => {
      held = true;
      showCitationPreview(wrapper, true);
    }, 450);
  });
  button.addEventListener("pointerup", cancelHold);
  button.addEventListener("pointercancel", cancelHold);
  button.addEventListener("click", (event) => {
    if (held) {
      event.preventDefault();
      held = false;
      return;
    }
    openSource(source, turnId);
  });
  return wrapper;
}

function appendInlineMarkdown(parent, text, sources, turnId) {
  const pattern = /\[\s*(?:Nguồn\s*)?(\d+(?:\s*[,;]\s*(?:Nguồn\s*)?\d+)*)\s*\]|([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|`([^`\n]+)`|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/gu;
  let cursor = 0;
  let renderedCitations = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(cursor, match.index)));
    if (match[1] || match[2]) {
      const numbers = match[1]
        ? match[1].match(/\d+/g).map(Number)
        : [circledNumbers.indexOf(match[2]) + 1];
      const valid = numbers.map((number) => ({
        number,
        source: sources.find((item) => Number(item.n) === number),
      }));
      if (valid.every((item) => item.source)) {
        renderedCitations += valid.length;
        valid.forEach((item, index) => {
          if (index) parent.append(document.createTextNode(", "));
          const label = circledNumbers[item.number - 1] || `[${item.number}]`;
          parent.append(createCitation(item.source, item.number, turnId, label));
        });
      } else {
        parent.append(document.createTextNode(match[0]));
      }
    } else if (match[3]) {
      parent.append(make("strong", "markdown-strong", match[3].slice(2, -2)));
    } else if (match[4]) {
      parent.append(make("em", "markdown-emphasis", match[4].slice(1, -1)));
    } else if (match[5]) {
      parent.append(make("code", "inline-code", match[5]));
    } else if (match[6] && match[7]) {
      const link = make("a", "markdown-link", match[6]);
      link.href = match[7];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      parent.append(link);
    }
    cursor = match.index + match[0].length;
  }
  parent.append(document.createTextNode(text.slice(cursor)));
  return renderedCitations;
}

function tableCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function isTableStart(lines, index) {
  if (!lines[index]?.includes("|") || !lines[index + 1]?.includes("|")) return false;
  return tableCells(lines[index + 1]).every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isMarkdownBlockStart(lines, index) {
  const line = lines[index] || "";
  return /^\s*(```|#{1,6}\s|[-*+]\s|\d+\.\s|>)/.test(line) || isTableStart(lines, index);
}

/** Hiển thị câu trả lời Markdown đơn giản (đoạn, danh sách, bảng, code) bằng DOM, gắn nút trích dẫn; trả về số trích dẫn. */
function appendMarkdownAnswer(container, answer, sources, turnId) {
  const lines = String(answer).replace(/\r\n?/g, "\n").split("\n");
  let citations = 0;
  const appendInline = (parent, text) => {
    citations += appendInlineMarkdown(parent, text, sources, turnId);
  };
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    if (/^\s*```/.test(line)) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) codeLines.push(lines[index++]);
      if (index < lines.length) index += 1;
      const block = make("div", "code-block");
      const pre = make("pre");
      pre.append(make("code", "", codeLines.join("\n")));
      const copy = make("button", "small-button code-copy", "Sao chép mã");
      copy.type = "button";
      copy.setAttribute("aria-label", "Sao chép đoạn mã");
      copy.addEventListener("click", () => copyText(codeLines.join("\n")));
      block.append(copy, pre);
      container.append(block);
      continue;
    }
    const heading = line.match(/^\s*(#{1,6})\s+(.+)$/);
    if (heading) {
      const node = make(`h${heading[1].length}`, "markdown-heading");
      appendInline(node, heading[2]);
      container.append(node);
      index += 1;
      continue;
    }
    if (isTableStart(lines, index)) {
      const tableWrap = make("div", "markdown-table-wrap");
      const table = make("table", "markdown-table");
      const header = make("thead");
      const headerRow = make("tr");
      for (const cell of tableCells(line)) {
        const th = make("th");
        appendInline(th, cell);
        headerRow.append(th);
      }
      header.append(headerRow);
      table.append(header);
      index += 2;
      const body = make("tbody");
      while (index < lines.length && lines[index].includes("|")) {
        const row = make("tr");
        for (const cell of tableCells(lines[index++])) {
          const td = make("td");
          appendInline(td, cell);
          row.append(td);
        }
        body.append(row);
      }
      table.append(body);
      tableWrap.append(table);
      container.append(tableWrap);
      continue;
    }
    const listMatch = line.match(/^\s*([-*+])\s+|^\s*(\d+)\.\s+/);
    if (listMatch) {
      const ordered = Boolean(listMatch[2]);
      const list = make(ordered ? "ol" : "ul", "markdown-list");
      const itemPattern = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*+]\s+/;
      while (index < lines.length && itemPattern.test(lines[index])) {
        const item = make("li");
        appendInline(item, lines[index++].replace(itemPattern, ""));
        list.append(item);
      }
      container.append(list);
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = make("blockquote", "markdown-quote");
      while (index < lines.length && /^\s*>/.test(lines[index])) {
        const paragraph = make("p");
        appendInline(paragraph, lines[index++].replace(/^\s*>\s?/, ""));
        quote.append(paragraph);
      }
      container.append(quote);
      continue;
    }
    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isMarkdownBlockStart(lines, index)) {
      paragraphLines.push(lines[index++]);
    }
    const paragraph = make("p", "markdown-paragraph");
    appendInline(paragraph, paragraphLines.join("\n"));
    container.append(paragraph);
  }
  return citations;
}

async function copyText(text) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Bộ nhớ tạm không khả dụng.");
    await navigator.clipboard.writeText(text);
    setStatus("Đã sao chép vào bộ nhớ tạm.");
  } catch (error) {
    setStatus(error.message || "Không sao chép được nội dung.", "error");
  }
}

/** Hiển thị kết quả hỏi đáp của một lượt: câu trả lời, danh sách nguồn và mốc thời gian trên video. */
function renderResult(result, root, turnId, { scroll = true } = {}) {
  const followThread = isThreadNearEnd();
  state.sources = Array.isArray(result.sources) ? result.sources : [];
  state.answersByTurn.set(String(turnId), result.answer || "Chưa có câu trả lời.");
  state.activeTurnId = String(turnId);
  state.activeSourceKey = null;
  state.sourcesByTurn.set(state.activeTurnId, state.sources);
  root.replaceChildren();
  const answer = make("div", `answer${result.found ? "" : " not-found"}`);
  const citationCount = appendMarkdownAnswer(answer, state.answersByTurn.get(String(turnId)), state.sources, turnId);
  root.append(answer);
  if (result.no_sources === true || citationCount === 0) {
    root.append(make("p", "coverage-note", "Không tìm thấy nội dung này trong nguồn đang chọn"));
  } else if (result.found && state.sources.length) {
    root.append(make("p", "coverage-note", "Câu trả lời dựa trên các đoạn gần nhất tìm được trong thư viện. Hãy mở nguồn để đối chiếu."));
  }
  const actions = make("div", "answer-actions");
  const copy = make("button", "answer-action", "Sao chép");
  copy.type = "button";
  copy.setAttribute("aria-label", "Sao chép câu trả lời");
  copy.addEventListener("click", () => copyText(state.answersByTurn.get(String(turnId)) || ""));
  const regenerate = make("button", "answer-action", "Tạo lại");
  regenerate.type = "button";
  regenerate.setAttribute("aria-label", "Tạo lại câu trả lời");
  regenerate.addEventListener("click", () => {
    const userTurn = root.closest(".assistant-turn")?.previousElementSibling;
    const question = userTurn?.dataset.question;
    if (question) submitQuestion(question, { retryTurn: root.closest(".assistant-turn") });
  });
  actions.append(copy, regenerate);
  root.append(actions);
  if (state.sources.length) {
    const cards = make("div", "source-list");
    for (const source of state.sources) {
      const card = make("article", "source-card");
      card.dataset.turnId = String(turnId);
      card.dataset.sourceNumber = String(source.n);
      card.dataset.sourceKey = sourceKey(source);
      const heading = make("div", "source-heading");
      heading.append(make("span", "source-number", `Nguồn ${source.n}`));
      heading.append(make("span", "source-title", source.title || (source.source_type === "document" ? "Tài liệu" : "Bài giảng")));
      heading.append(make("span", "source-time", source.label || (source.source_type === "document" ? `Mục ${source.section}` : formatTime(source.start))));
      card.append(heading);
      appendSourceText(card, source.text || "");
      const actions = make("div", "source-actions");
      const open = make("button", "small-button", source.source_type === "document" ? "Mở mục tài liệu" : `Phát từ ${formatTime(source.start)}`);
      open.type = "button";
      open.addEventListener("click", () => openSource(source, turnId));
      actions.append(open);
      if (source.source_type !== "document" && source.link && /^https:\/\//i.test(source.link)) {
        const link = make("a", "source-link", "Mở video nguồn ↗");
        link.href = source.link;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        actions.append(link);
      }
      card.append(actions);
      cards.append(card);
    }
    const details = make("details", "source-details");
    details.append(make("summary", "source-summary", `Nguồn (${state.sources.length})`), cards);
    root.append(details);
  }
  renderTimeline();
  if (scroll && followThread) scrollThreadToEnd();
}

function sourcePreview(text, limit = 280) {
  if (text.length <= limit) return null;
  let end = text.lastIndexOf(" ", limit);
  if (end < Math.floor(limit * .75)) end = text.indexOf(" ", limit);
  if (end < 0) end = limit;
  return `${text.slice(0, end).trimEnd()}…`;
}

function appendSourceText(card, text) {
  const preview = sourcePreview(text);
  const paragraph = make("p", "source-text", preview || text);
  card.append(paragraph);
  if (!preview) return;
  const toggle = make("button", "small-button source-toggle", "Xem thêm");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    paragraph.textContent = expanded ? preview : text;
    toggle.setAttribute("aria-expanded", String(!expanded));
    toggle.textContent = expanded ? "Xem thêm" : "Thu gọn";
  });
  card.append(toggle);
}

function isThreadNearEnd(thread = $("#thread")) {
  return thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 96;
}

function scrollThreadToEnd({ smooth = false, onlyIfNearEnd = false } = {}) {
  const thread = $("#thread");
  if (onlyIfNearEnd && !isThreadNearEnd(thread)) return false;
  if (smooth) thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" });
  else thread.scrollTop = thread.scrollHeight;
  return true;
}

/** Thêm một lượt (câu hỏi hoặc câu trả lời) vào khung hội thoại và trả về phần tử của lượt đó. */
function addTurn(role, content, pending = false, { scroll = true, forceScroll = false } = {}) {
  const row = make("div", role === "user" ? "user-turn" : "assistant-turn");
  const turnId = `turn-${++state.turnCounter}`;
  row.id = turnId;
  row.dataset.turnId = turnId;
  const body = make("div", role === "user" ? "turn-content turn-bubble" : "turn-content", content);
  if (role === "user") {
    row.dataset.question = content;
    const actions = make("div", "question-actions");
    const edit = make("button", "answer-action", "Sửa");
    edit.type = "button";
    edit.setAttribute("aria-label", "Sửa câu hỏi");
    const editor = make("div", "edit-question-panel");
    editor.hidden = true;
    const textarea = make("textarea", "edit-question-input", content);
    textarea.rows = 3;
    textarea.setAttribute("aria-label", "Chỉnh sửa câu hỏi");
    const save = make("button", "small-button", "Gửi lại");
    save.type = "button";
    const cancel = make("button", "answer-action", "Hủy");
    cancel.type = "button";
    const closeEditor = () => {
      editor.hidden = true;
      body.hidden = false;
      edit.focus();
    };
    edit.addEventListener("click", () => {
      editor.hidden = false;
      body.hidden = true;
      textarea.value = row.dataset.question || "";
      textarea.focus();
    });
    save.addEventListener("click", () => {
      if (!textarea.value.trim()) {
        textarea.focus();
        return;
      }
      if (state.asking) {
        setStatus("Đợi câu trả lời hiện tại xong rồi gửi lại câu hỏi.", "error");
        return;
      }
      if (!$("#source-deleted-banner").hidden) {
        setStatus("Nguồn của cuộc trò chuyện đã bị xoá; không thể gửi câu hỏi mới.", "error");
        return;
      }
      editor.hidden = true;
      body.hidden = false;
      submitQuestion(textarea.value.trim());
    });
    cancel.addEventListener("click", closeEditor);
    editor.append(textarea, save, cancel);
    actions.append(edit);
    row.append(body, actions, editor);
  } else {
    row.append(body);
  }
  if (pending) row.dataset.pending = "true";
  $("#conversation").append(row);
  if (scroll) scrollThreadToEnd({ onlyIfNearEnd: !forceScroll });
  return row;
}

function setTurnStatus(turn, message, kind = "") {
  const status = make("p", "request-status");
  status.setAttribute("role", "status");
  setStatus(message, kind, status);
  turn.querySelector(".turn-content").replaceChildren(status);
  turn.removeAttribute("data-pending");
}

/** Gửi câu hỏi: hiện lượt chờ, gọi API hỏi đáp, hiển thị kết quả và nhớ id cuộc trò chuyện; quá tải thì chờ rồi cho gửi lại. */
async function submitQuestion(question = $("#question-input").value.trim(), options = {}) {
  if (!question || state.asking || $("#source-deleted-banner").hidden === false) return;
  const previousDraftKey = currentDraftKey();
  const retryTurn = options.retryTurn || null;
  if (state.segmentContext && question && !question.startsWith("Tại ")) {
    question = `Tại ${formatTime(state.segmentContext)}: ${question}`;
  }
  state.segmentContext = null;
  clearRetry();
  let turn;
  if (retryTurn?.isConnected) {
    turn = retryTurn;
    turn.querySelector(".turn-content").replaceChildren();
  } else {
    $("#welcome").hidden = true;
    addTurn("user", question, false, { scroll: false });
    turn = addTurn("assistant", "", true, { scroll: false });
    scrollThreadToEnd();
  }
  const pending = make("p", "request-status", retryTurn ? "Đang thử gửi lại câu hỏi…" : "Đang trả lời…");
  pending.setAttribute("role", "status");
  turn.querySelector(".turn-content").replaceChildren(pending);
  state.asking = true;
  $("#ask-button").disabled = true;
  $("#new-thread").disabled = true;
  setStatus("");
  try {
    const result = await request("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        conversation_id: state.activeConversationId,
        source_id: state.conversationSource.source_id,
        source_type: state.conversationSource.source_type,
        project_id: state.currentProjectId,
      }),
    });
    if (result.conversation_id) {
      state.activeConversationId = result.conversation_id;
      state.draftId = result.conversation_id;
      state.drafts.delete(previousDraftKey);
      $("#conversation-heading").textContent = result.conversation_title || question.slice(0, 60);
      updateConversationUrl(result.conversation_id);
      loadConversations();
    }
    $("#question-input").value = "";
    updateQuestionInput();
    const resultNode = make("div", "result");
    turn.querySelector(".turn-content").replaceChildren(resultNode);
    turn.removeAttribute("data-pending");
    renderResult(result, resultNode, turn.dataset.turnId);
    if (result.history_saved === false) {
      setStatus(result.history_message || "Câu trả lời đã có, nhưng chưa lưu được lịch sử. Hãy thử lại sau.", "error");
    }
    if (state.signedIn && !result.conversation_id && result.history_saved !== false) {
      const currentUserTurn = turn.previousElementSibling;
      const conversation = $("#conversation");
      for (const child of [...conversation.children]) {
        if (child !== currentUserTurn && child !== turn) child.remove();
      }
      state.sourcesByTurn.clear();
      state.answersByTurn.clear();
      state.sourcesByTurn.set(String(turn.dataset.turnId), state.sources);
      state.answersByTurn.set(String(turn.dataset.turnId), result.answer || "Chưa có câu trả lời.");
      sessionStorage.removeItem("vskr-user-csrf");
      setAccountView(null, { preserveCurrentThread: true });
      setStatus("Phiên đăng nhập đã hết hạn. Câu trả lời này chưa được lưu; bạn vẫn có thể tiếp tục hỏi bài với tư cách khách.", "error");
    }
  } catch (error) {
    const followThread = isThreadNearEnd();
    if (!navigator.onLine) {
      setTurnStatus(turn, "Bạn đang ngoại tuyến. Kết nối lại internet rồi thử gửi lại câu hỏi.", "error");
    } else if (error instanceof ApiError && error.status === 429) {
      waitBeforeRetry(error.retryAfter || 60, question, "Bạn đã gửi nhiều câu hỏi trong thời gian ngắn.", turn);
    } else if (error instanceof ApiError && error.status === 503) {
      waitBeforeRetry(error.retryAfter || 10, question, error.message || "Hệ thống đang xử lý nhiều câu hỏi cùng lúc, bạn thử lại sau vài giây.", turn);
    } else if (error instanceof ApiError && error.status >= 500) {
      setTurnStatus(turn, "Hiện chưa thể trả lời câu hỏi. Vui lòng thử lại sau.", "error");
    } else if (error instanceof ApiError) {
      setTurnStatus(turn, error.message || "Không gửi được câu hỏi. Hãy thử lại.", "error");
    } else {
      setTurnStatus(turn, "Không kết nối được máy chủ. Kiểm tra kết nối rồi thử lại.", "error");
    }
    if (followThread) scrollThreadToEnd();
  } finally {
    state.asking = false;
    updateQuestionInput();
    $("#new-thread").disabled = false;
    if (state.retryButton) state.retryButton.disabled = state.retryUntil > Date.now();
  }
}

function setNetworkStatus() {
  const status = $("#network-status");
  status.textContent = navigator.onLine ? "" : "Ngoại tuyến";
  status.hidden = navigator.onLine;
}

async function showAdminLink() {
  try {
    const session = await request("/api/auth/me");
    if (session?.admin === true && typeof session.admin_url === "string" && session.admin_url.startsWith("/")) {
      const link = $("#admin-link");
      link.href = session.admin_url;
      link.textContent = "Quản trị";
      link.hidden = false;
    }
  } catch { /* Public reading and questions do not depend on admin session lookup. */ }
}

/** Đọc phiên hiện tại (người học hoặc khách) rồi cập nhật giao diện tài khoản. */
async function loadLearnerSession() {
  try {
    const session = await request("/api/user/me");
    if (session.user_id && !state.signedIn) {
      state.projects = [];
      state.currentProjectId = null;
    }
    setAccountView(session);
    $("#user-name").textContent = session.username || "";
    if (session.user_id) {
      if (session.csrf_token) sessionStorage.setItem("vskr-user-csrf", session.csrf_token);
      await loadProjects();
      const requestedConversation = new URL(location.href).searchParams.get("conversation");
      if (requestedConversation && state.conversations.some((item) => item.conversation_id === requestedConversation)) {
        await openConversation(requestedConversation, { updateUrl: false });
      } else if (requestedConversation) {
        updateConversationUrl(null, { replace: true });
      }
    } else {
      sessionStorage.removeItem("vskr-user-csrf");
      updateConversationUrl(null, { replace: true });
    }
  } catch {
    setAccountView(null);
    sessionStorage.removeItem("vskr-user-csrf");
    updateConversationUrl(null, { replace: true });
  }
}

/** Cập nhật giao diện theo trạng thái đăng nhập; mất phiên thì xoá dữ liệu riêng đang hiển thị. */
function setAccountView(session, { preserveCurrentThread = false } = {}) {
  const signedIn = Boolean(session?.user_id);
  const lostSession = state.signedIn && !signedIn;
  state.signedIn = signedIn;
  state.uploadLimits = session?.limits || null;
  updateProjectLabel();
  if (lostSession) {
    state.videos = [];
    state.documents = [];
    renderVideos();
    renderDocuments();
    loadLibrary();
  }
  $("#user-login").hidden = signedIn;
  $("#user-register").hidden = signedIn;
  $("#user-name").hidden = !signedIn;
  $("#user-logout").hidden = !signedIn;
  $("#history-section").hidden = !signedIn;
  $("#guest-history-cta").hidden = signedIn;
  $("#guest-add-notice").hidden = signedIn;
  const projectSection = $("#project-section");
  if (projectSection) projectSection.hidden = true;
  $("#user-video-form").hidden = !signedIn;
  $("#user-document-form").hidden = !signedIn;
  $("#signed-in-account").hidden = !signedIn;
  if (signedIn) {
    const email = session.email || session.username || "";
    const displayName = session.display_name || email.split("@", 1)[0] || "Người dùng";
    $("#account-display-name").textContent = displayName;
    $("#account-email").textContent = email;
    $("#account-avatar").textContent = displayName.trim().charAt(0).toUpperCase() || "N";
  }
  if (!signedIn) {
    state.projects = [];
    state.currentProjectId = null;
    $("#project-select").replaceChildren();
    state.conversations = [];
    state.activeConversationId = null;
    state.preferredConversationVideoId = null;
    $("#conversation-search").value = "";
    updateConversationUrl(null, { replace: true });
    renderConversations();
    if (lostSession && !preserveCurrentThread) {
      state.sources = [];
      state.sourcesByTurn.clear();
      state.answersByTurn.clear();
      state.activeTurnId = null;
      state.activeSourceKey = null;
      $("#conversation").replaceChildren();
      $("#welcome").hidden = false;
      renderTimeline();
    }
  }
}

function conversationGroupTitle(timestamp) {
  const value = Number(timestamp);
  const date = new Date(value < 1e12 ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return "Cũ hơn";

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const weekAgo = new Date(today);
  weekAgo.setDate(weekAgo.getDate() - 6);
  const monthAgo = new Date(today);
  monthAgo.setDate(monthAgo.getDate() - 29);

  if (date >= today) return "Hôm nay";
  if (date >= yesterday) return "Hôm qua";
  if (date >= weekAgo) return "7 ngày qua";
  if (date >= monthAgo) return "30 ngày qua";
  return new Intl.DateTimeFormat("vi-VN", { month: "long", year: "numeric" }).format(date);
}

function updateConversationUrl(conversationId, { replace = false } = {}) {
  const url = new URL(location.href);
  if (conversationId) url.searchParams.set("conversation", conversationId);
  else url.searchParams.delete("conversation");
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${location.pathname}${location.search}${location.hash}`;
  if (next === current) return;
  history[replace ? "replaceState" : "pushState"]({ conversationId: conversationId || null }, "", next);
}

function openRenameDialog(conversation) {
  if (state.asking) return setStatus("Đợi câu trả lời xong rồi hãy đổi tên cuộc trò chuyện.", "error");
  state.renameConversationId = conversation.conversation_id;
  $("#rename-conversation-title").value = conversation.title || "";
  $("#rename-conversation-status").textContent = "";
  $("#rename-conversation-dialog").showModal();
  $("#rename-conversation-title").focus();
  $("#rename-conversation-title").select();
}

function conversationVideoId(conversation) {
  for (const turn of [...(conversation.turns || [])].reverse()) {
    const source = (Array.isArray(turn.sources) ? turn.sources : [])
      .find((item) => item?.source_type === "video" && item.video_id);
    if (source) return String(source.video_id);
  }
  return null;
}

function renderConversations() {
  const list = $("#conversation-list");
  list.replaceChildren();
  const conversationCount = $("#conversation-count");
  if (conversationCount) conversationCount.textContent = String(state.conversations.length);
  $("#conversation-empty").hidden = state.conversations.length > 0;
  const query = $("#conversation-search").value.trim().toLocaleLowerCase("vi");
  const visible = state.conversations.filter((conversation) =>
    (conversation.title || "Cuộc trò chuyện mới").toLocaleLowerCase("vi").includes(query));
  $("#conversation-no-match").hidden = !state.conversations.length || visible.length > 0;

  const groups = new Map();
  for (const conversation of visible) {
    const title = conversationGroupTitle(conversation.updated_at || conversation.created_at);
    if (!groups.has(title)) groups.set(title, []);
    groups.get(title).push(conversation);
  }

  for (const [groupTitle, conversations] of groups) {
    const group = make("section", "conversation-group");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", groupTitle);
    group.append(make("h3", "conversation-group-title", groupTitle));
    for (const conversation of conversations) {
      const row = make("div", `conversation-row${conversation.conversation_id === state.activeConversationId ? " is-active" : ""}`);
      row.setAttribute("role", "listitem");
      const open = make("button", "conversation-choice", conversation.title || "Cuộc trò chuyện mới");
      open.type = "button";
      open.setAttribute("aria-current", String(conversation.conversation_id === state.activeConversationId));
      open.addEventListener("click", () => openConversation(conversation.conversation_id));
      const actions = make("div", "conversation-actions");
      const rename = make("button", "conversation-action conversation-rename", "✎");
      rename.type = "button";
      rename.setAttribute("aria-label", `Đổi tên cuộc trò chuyện: ${conversation.title}`);
      rename.title = "Đổi tên";
      rename.addEventListener("click", () => openRenameDialog(conversation));
      const remove = make("button", "conversation-delete", "×");
      remove.classList.add("conversation-action");
      remove.type = "button";
      remove.setAttribute("aria-label", `Xóa cuộc trò chuyện: ${conversation.title}`);
      remove.title = "Xóa cuộc trò chuyện";
      remove.addEventListener("click", async () => {
        if (state.asking) return setStatus("Đợi câu trả lời xong rồi hãy xóa cuộc trò chuyện.", "error");
        if (!window.confirm(`Xóa cuộc trò chuyện “${conversation.title}”?`)) return;
        try {
          await request(`/api/conversations/${encodeURIComponent(conversation.conversation_id)}`, { method: "DELETE" });
          if (state.activeConversationId === conversation.conversation_id) startNewConversation({ replaceUrl: true });
          await loadConversations();
        } catch (error) { setStatus(error.message, "error"); }
      });
      actions.append(rename, remove);
      row.append(open, actions);
      group.append(row);
    }
    list.append(group);
  }
}

async function loadProjects() {
  try {
    const projects = await request("/api/projects");
    state.projects = Array.isArray(projects) ? projects : [];
    if (!state.projects.length) {
      state.currentProjectId = null;
    } else if (!state.projects.some((project) => project.project_id === state.currentProjectId)) {
      state.currentProjectId = state.projects[0].project_id;
    }
    const select = $("#project-select");
    if (!select) return;
    select.replaceChildren();
    for (const project of state.projects) {
      const option = new Option(project.name, project.project_id);
      option.selected = project.project_id === state.currentProjectId;
      select.append(option);
    }
    updateProjectLabel();
    if (state.signedIn) await loadConversations();
  } catch (error) {
    setStatus(error.message || "Không tải được danh sách dự án.", "error");
  }
}

async function loadConversations() {
  try {
    const query = state.currentProjectId
      ? `?project_id=${encodeURIComponent(state.currentProjectId)}`
      : "";
    state.conversations = await request(`/api/conversations${query}`);
    renderConversations();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      sessionStorage.removeItem("vskr-user-csrf");
      setAccountView(null);
    } else {
      setStatus("Chưa tải được lịch sử trò chuyện.", "error");
    }
  }
}

/** Mở một cuộc trò chuyện đã lưu: tải các lượt hỏi đáp và hiển thị lại. */
async function openConversation(conversationId, { updateUrl = true } = {}) {
  if (state.asking) {
    setStatus("Đợi câu trả lời xong rồi hãy chuyển cuộc trò chuyện.", "error");
    closeDrawer();
    return;
  }
  saveCurrentDraft();
  const loadToken = ++state.conversationLoadToken;
  try {
    setStatus("Đang mở cuộc trò chuyện…");
    const conversation = await request(`/api/conversations/${encodeURIComponent(conversationId)}`);
    if (loadToken !== state.conversationLoadToken) return;
    clearRetry();
    state.activeConversationId = conversation.conversation_id;
    state.draftId = conversation.conversation_id;
    $("#conversation-heading").textContent = conversation.title || "Cuộc trò chuyện mới";
    setConversationSource({
      source_id: conversation.source_id || null,
      source_type: conversation.source_type || "library",
      title: conversation.source_title || conversation.title || "Cả thư viện",
    });
    if (conversation.project_id && state.projects.some((project) => project.project_id === conversation.project_id)) {
      state.currentProjectId = conversation.project_id;
      $("#project-select").value = conversation.project_id;
      await loadLibrary();
    }
    setSourceDeleted(Boolean(conversation.source_deleted));
    state.preferredConversationVideoId = conversationVideoId(conversation);
    if (updateUrl) updateConversationUrl(conversation.conversation_id);
    state.sources = [];
    state.sourcesByTurn.clear();
    state.answersByTurn.clear();
    state.activeTurnId = null;
    state.activeSourceKey = null;
    $("#conversation").replaceChildren();
    $("#welcome").hidden = true;
    setStatus("");
    for (const savedTurn of conversation.turns) {
      addTurn("user", savedTurn.question, false, { scroll: false });
      const answerTurn = addTurn("assistant", "", false, { scroll: false });
      const resultNode = make("div", "result");
      answerTurn.querySelector(".turn-content").replaceChildren(resultNode);
      renderResult(savedTurn, resultNode, answerTurn.dataset.turnId, { scroll: false });
    }
    restoreDraft(state.draftId);
    const sourceVideo = state.videos.find((video) => video.video_id === conversation.source_id)
      || state.videos.find((video) => video.video_id === state.preferredConversationVideoId);
    if (sourceVideo) selectVideo(sourceVideo);
    scrollThreadToEnd();
    if (!conversation.turns.length) $("#welcome").hidden = false;
    renderConversations();
    closeDrawer({ restoreFocus: false });
  } catch (error) {
    if (loadToken !== state.conversationLoadToken) return;
    if (error instanceof ApiError && error.status === 401) {
      sessionStorage.removeItem("vskr-user-csrf");
      setAccountView(null);
      setStatus("Phiên đăng nhập đã hết hạn. Bạn vẫn có thể tiếp tục hỏi bài với tư cách khách.", "error");
    } else {
      if (error instanceof ApiError && error.status === 404) updateConversationUrl(null, { replace: true });
      setStatus(error.message || "Không mở được cuộc trò chuyện.", "error");
    }
  }
}

function resetConversationView({ updateUrl = true, replaceUrl = false, focus = true } = {}) {
  if (state.asking) {
    setStatus("Đợi câu trả lời xong rồi hãy bắt đầu cuộc trò chuyện mới.", "error");
    return;
  }
  saveCurrentDraft();
  state.conversationLoadToken += 1;
  clearRetry();
  state.activeConversationId = null;
  state.draftId = createDraftId();
  setConversationSource();
  setSourceDeleted(false);
  $("#conversation-heading").textContent = "Cuộc trò chuyện mới";
  state.preferredConversationVideoId = null;
  $("#conversation").replaceChildren();
  $("#welcome").hidden = false;
  questionInput.value = "";
  questionInput.style.height = "auto";
  updateQuestionInput();
  setStatus("");
  state.sources = [];
  state.sourcesByTurn.clear();
  state.answersByTurn.clear();
  state.activeTurnId = null;
  state.activeSourceKey = null;
  renderTimeline();
  renderConversations();
  $("#thread").scrollTop = 0;
  if (updateUrl) updateConversationUrl(null, { replace: replaceUrl });
  closeDrawer({ restoreFocus: false });
  if (focus) questionInput.focus();
}

function renderConversationSourcePicker() {
  const list = $("#conversation-source-list");
  const query = $("#conversation-source-search").value.trim().toLocaleLowerCase("vi");
  list.replaceChildren();
  const items = [
    ...state.videos.map((item) => ({ ...item, source_id: item.video_id, source_type: "video", meta: formatTime(item.duration || 0) })),
    ...state.documents.map((item) => ({ ...item, source_id: item.document_id, source_type: "document", meta: `${item.sections || 0} mục` })),
  ].filter((item) => (item.title || "").toLocaleLowerCase("vi").includes(query));
  for (const item of items) {
    const row = make("button", "library-item source-picker-item");
    row.type = "button";
    row.append(
      make("span", "library-item-icon", item.source_type === "video" ? "▶" : "▤"),
      (() => {
        const info = make("span");
        info.append(make("strong", "library-item-title", item.title || "Nguồn"));
        info.append(make("small", "library-item-meta", item.meta));
        return info;
      })(),
    );
    row.addEventListener("click", () => {
      if (state.sourcePickerMode === "select") {
        setConversationSource(item);
        $("#conversation-source-dialog").close();
        return;
      }
      beginConversationWithSource(item);
    });
    list.append(row);
  }
  if (!items.length) list.append(make("p", "muted", "Không tìm thấy nguồn phù hợp."));
}

/** Bắt đầu cuộc trò chuyện mới gắn với một nguồn: một video, một tài liệu hoặc cả thư viện. */
async function beginConversationWithSource(source) {
  saveCurrentDraft();
  const normalized = {
    source_id: source.source_type === "library" ? null : source.source_id,
    source_type: source.source_type,
    title: source.title || "Toàn bộ thư viện",
  };
  if (state.signedIn) {
    try {
      const created = await request("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: "Cuộc trò chuyện mới",
          source_id: normalized.source_id,
          source_type: normalized.source_type,
          project_id: state.currentProjectId,
        }),
      });
      state.activeConversationId = created.conversation_id;
      state.draftId = created.conversation_id;
      updateConversationUrl(created.conversation_id);
      await loadConversations();
    } catch (error) {
      setStatus(error.message || "Không tạo được cuộc trò chuyện.", "error");
      return;
    }
  } else {
    state.activeConversationId = null;
    state.draftId = createDraftId();
    updateConversationUrl(null, { replace: true });
  }
  state.conversationLoadToken += 1;
  clearRetry();
  state.sources = [];
  state.sourcesByTurn.clear();
  state.answersByTurn.clear();
  state.activeTurnId = null;
  state.activeSourceKey = null;
  state.preferredConversationVideoId = normalized.source_type === "video" ? normalized.source_id : null;
  setConversationSource(normalized);
  setSourceDeleted(false);
  $("#conversation-heading").textContent = "Cuộc trò chuyện mới";
  $("#conversation").replaceChildren();
  $("#welcome").hidden = false;
  restoreDraft(state.draftId);
  if (normalized.source_type === "video") {
    const video = state.videos.find((item) => item.video_id === normalized.source_id);
    if (video) selectVideo(video);
  } else {
    closeLecture();
  }
  renderTimeline();
  renderConversations();
  $("#conversation-source-dialog").close();
  questionInput.focus();
}

function openConversationSourcePicker({ mode = "new" } = {}) {
  if (state.asking) {
    setStatus("Đợi câu trả lời xong rồi hãy bắt đầu cuộc trò chuyện mới.", "error");
    return;
  }
  state.sourcePickerMode = mode;
  $("#conversation-source-search").value = "";
  renderConversationSourcePicker();
  $("#conversation-source-dialog").showModal();
  $("#conversation-source-chip").setAttribute("aria-expanded", String(mode === "select"));
}

function startNewConversation(options = {}) {
  if (options.resetOnly) return resetConversationView(options);
  openConversationSourcePicker();
}

$("#user-logout").addEventListener("click", async () => {
  const button = $("#user-logout"); button.disabled = true;
  try { await request("/api/user/auth/logout", { method: "POST" }); }
  finally { sessionStorage.removeItem("vskr-user-csrf"); location.assign("/"); }
});

/** Tải thư viện của project hiện tại (video, tài liệu) và trạng thái chỉ mục. */
async function loadLibrary() {
  try {
    if (!state.projects.length) await loadProjects();
    const projectQuery = state.currentProjectId
      ? `?project_id=${encodeURIComponent(state.currentProjectId)}`
      : "";
    const [catalog, status] = await Promise.all([request(`/api/catalog${projectQuery}`), request("/api/status")]);
    state.videos = Array.isArray(catalog.videos) ? catalog.videos : [];
    state.documents = Array.isArray(catalog.documents) ? catalog.documents : [];
    $("#library-loading").hidden = true;
    renderDocuments();
    renderVideos();
    renderLibraryBadge();
    const preferredVideoId = state.currentVideo?.video_id || state.preferredConversationVideoId;
    const initialVideo = state.videos.find((video) => video.video_id === preferredVideoId);
    if (initialVideo) selectVideo(initialVideo, { open: false });
    else {
      $("#welcome").hidden = false;
      if (!status.ready) setStatus("Thư viện đang được khởi tạo. Hãy tải lại trang sau ít phút.");
    }
  } catch (error) {
    $("#library-loading").hidden = true;
    $("#library-empty").hidden = false;
    $("#library-empty").textContent = navigator.onLine
      ? "Chưa tải được danh sách học liệu. Hãy thử tải lại trang."
      : "Bạn đang ngoại tuyến. Kết nối lại internet để tải học liệu.";
  }
}

$("#question-form").addEventListener("submit", (event) => {
  event.preventDefault();
  $("#suggestions").hidden = true;
  $("#suggest-toggle").setAttribute("aria-expanded", "false");
  submitQuestion();
});
document.querySelectorAll(".welcome-suggestions button").forEach((button) => {
  button.addEventListener("click", () => {
    questionInput.value = button.textContent;
    updateQuestionInput();
    submitQuestion();
  });
});
$("#new-conversation").addEventListener("click", () => startNewConversation());
$("#new-conversation-sidebar").addEventListener("click", () => startNewConversation());
$("#sidebar-search").addEventListener("click", () => {
  openLibrarySearch();
});
$("#project-select").addEventListener("change", async (event) => {
  state.currentProjectId = event.target.value || null;
  updateProjectLabel();
  if (state.signedIn) await loadConversations();
  await loadLibrary();
  resetConversationView({ focus: false });
});
const projectDialog = $("#project-dialog");
$("#new-project").addEventListener("click", () => {
  $("#project-name-input").value = "";
  $("#project-form-status").textContent = "";
  projectDialog.showModal();
  $("#project-name-input").focus();
});
$("#close-project-dialog").addEventListener("click", () => projectDialog.close());
$("#project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = $("#project-form-status");
  const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  status.textContent = "Đang tạo dự án…";
  try {
    const project = await request("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("#project-name-input").value.trim() }),
    });
    state.projects.push(project);
    state.currentProjectId = project.project_id;
    await loadProjects();
    await loadLibrary();
    projectDialog.close();
  } catch (error) {
    status.textContent = error.message || "Không tạo được dự án.";
  } finally {
    button.disabled = false;
  }
});
$("#sidebar-add-content").addEventListener("click", () => {
  openContentDialog();
  closeDrawer({ restoreFocus: false });
});
$("#sidebar-account-menu").addEventListener("click", () => {
  const menu = $("#sidebar-account-actions");
  menu.hidden = !menu.hidden;
  $("#sidebar-account-menu").setAttribute("aria-expanded", String(!menu.hidden));
});
$("#sidebar-logout").addEventListener("click", () => {
  $("#sidebar-account-actions").hidden = true;
  $("#sidebar-account-menu").setAttribute("aria-expanded", "false");
  $("#user-logout").click();
});
$("#sidebar-library").addEventListener("click", () => {
  libraryDialog.showModal();
  renderLibraryDialog("mine");
  closeDrawer({ restoreFocus: false });
});
document.querySelectorAll(".library-tab[data-library-tab]").forEach((tab) => tab.addEventListener("click", () => {
  const scope = tab.dataset.libraryTab;
  document.querySelectorAll(".library-tab[data-library-tab]").forEach((item) =>
    item.setAttribute("aria-selected", String(item === tab)));
  renderLibraryDialog(scope);
}));
$("#close-content-dialog").addEventListener("click", () => contentDialog.close());
contentDialog.addEventListener("close", stopJobPolling);
projectDialog.addEventListener("click", (event) => { if (event.target === projectDialog) projectDialog.close(); });
$("#close-library-dialog").addEventListener("click", () => libraryDialog.close());
$("#close-conversation-source").addEventListener("click", () => $("#conversation-source-dialog").close());
for (const dialog of [contentDialog, libraryDialog]) {
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
}
$("#conversation-source-dialog").addEventListener("click", (event) => {
  if (event.target === $("#conversation-source-dialog")) $("#conversation-source-dialog").close();
});
$("#conversation-source-search").addEventListener("input", renderConversationSourcePicker);
$("#conversation-library-choice").addEventListener("click", () => {
  const librarySource = { source_type: "library", source_id: null, title: "Cả thư viện" };
  if (state.sourcePickerMode === "select") {
    setConversationSource(librarySource);
    $("#conversation-source-dialog").close();
    return;
  }
  beginConversationWithSource(librarySource);
});
$("#conversation-source-chip").addEventListener("click", () => openConversationSourcePicker({ mode: "select" }));
$("#conversation-source-dialog").addEventListener("close", () => {
  $("#conversation-source-chip").setAttribute("aria-expanded", "false");
});
document.querySelectorAll(".content-tab[data-content-tab]").forEach((tab) => tab.addEventListener("click", () => {
  const selected = tab.dataset.contentTab;
  document.querySelectorAll(".content-tab[data-content-tab]").forEach((item) => item.setAttribute("aria-selected", String(item === tab)));
  $("#content-link-form").hidden = selected !== "link";
  $("#content-upload-form").hidden = selected !== "upload";
  $("#content-document-form").hidden = selected !== "document";
  setContentDialogStatus("");
}));
$("#content-video-file").addEventListener("change", (event) => {
  const error = validateUploadFile(event.currentTarget.files[0], "video");
  if (error) setContentDialogStatus(error, "error");
});
$("#content-document-file").addEventListener("change", (event) => {
  const error = validateUploadFile(event.currentTarget.files[0], "document");
  if (error) setContentDialogStatus(error, "error");
});
function openContentWithDroppedFile(file) {
  const extension = file.name.match(/\.([^.]+)$/)?.[1]?.toLowerCase();
  const isDocument = ["txt", "md", "rst", "pdf", "docx"].includes(extension);
  const isVideo = ["mp4", "m4v", "mov", "mkv", "webm", "avi", "mpeg", "mpg"].includes(extension);
  if (!isDocument && !isVideo) {
    openContentDialog();
    setContentDialogStatus(`Định dạng ${extension ? `.${extension}` : "tệp"} chưa được hỗ trợ. Hãy chọn video hoặc tài liệu thuộc danh sách cho phép.`, "error");
    return;
  }
  const action = isDocument ? "document" : "upload";
  const input = $(isDocument ? "#content-document-file" : "#content-video-file");
  const transfer = new DataTransfer();
  transfer.items.add(file);
  input.files = transfer.files;
  openContentDialog();
  document.querySelector(`.content-tab[data-content-tab="${action}"]`).click();
  input.focus();
  const validation = validateUploadFile(file, isDocument ? "document" : "video");
  if (validation) setContentDialogStatus(validation, "error");
}
$("#content-drop-zone").addEventListener("click", () => $("#content-drop-file").click());
$("#content-drop-zone").addEventListener("dragover", (event) => {
  event.preventDefault();
  event.currentTarget.classList.add("is-dragging");
});
$("#content-drop-zone").addEventListener("dragleave", (event) => event.currentTarget.classList.remove("is-dragging"));
$("#content-drop-zone").addEventListener("drop", (event) => {
  event.preventDefault();
  event.stopPropagation();
  event.currentTarget.classList.remove("is-dragging");
  const file = event.dataTransfer?.files?.[0];
  if (file) openContentWithDroppedFile(file);
});
$("#content-drop-file").addEventListener("change", (event) => {
  const file = event.currentTarget.files[0];
  if (file) openContentWithDroppedFile(file);
  event.currentTarget.value = "";
});
document.addEventListener("dragover", (event) => {
  if ([...(event.dataTransfer?.types || [])].includes("Files")) event.preventDefault();
});
document.addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (!file) return;
  event.preventDefault();
  openContentWithDroppedFile(file);
});
$("#content-link-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
  enqueueContent({
    type: "video",
    url: $("#content-video-url").value.trim(),
    statusNode: $("#content-dialog-status"),
    button,
    closeOnQueue: true,
  });
});
$("#content-upload-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
  enqueueContent({
    type: "video",
    file: $("#content-video-file").files[0] || null,
    statusNode: $("#content-dialog-status"),
    button,
    closeOnQueue: true,
  });
});
$("#content-document-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
  enqueueContent({
    type: "document",
    file: $("#content-document-file").files[0] || null,
    statusNode: $("#content-dialog-status"),
    button,
    closeOnQueue: true,
  });
});
$("#close-lecture").addEventListener("click", closeLecture);
$("#collapse-lecture").addEventListener("click", () => {
  if (state.currentVideo && state.player) {
    state.pendingJump = { videoId: state.currentVideo.video_id, start: currentPlaybackTime() };
  }
  closeLecture();
});
$("#show-video-stage").addEventListener("click", () => {
  if (state.currentVideo) selectVideo(state.currentVideo);
});
$("#expand-video").addEventListener("click", () => {
  const expanded = $("#lecture-card").classList.toggle("video-expanded");
  $("#expand-video").setAttribute("aria-label", expanded ? "Thu nhỏ video" : "Phóng to video");
  $("#expand-video").textContent = expanded ? "⤡" : "⤢";
});
const videoResizer = $("#video-resizer");
let resizePointerStart = 0;
let resizeWidthStart = 0;
videoResizer.addEventListener("pointerdown", (event) => {
  if (!event.isPrimary || event.button !== 0 || window.matchMedia("(max-width: 899px)").matches) return;
  event.preventDefault();
  resizePointerStart = event.clientX;
  resizeWidthStart = state.videoPanelWidth || $("#lecture-card").getBoundingClientRect().width;
  videoResizer.setPointerCapture(event.pointerId);
  document.body.classList.add("resizing-video-panel");
});
videoResizer.addEventListener("pointermove", (event) => {
  if (!videoResizer.hasPointerCapture(event.pointerId)) return;
  resizeVideoPanel(resizeWidthStart + resizePointerStart - event.clientX);
});
function finishVideoResize(event) {
  if (videoResizer.hasPointerCapture(event.pointerId)) videoResizer.releasePointerCapture(event.pointerId);
  document.body.classList.remove("resizing-video-panel");
}
videoResizer.addEventListener("pointerup", finishVideoResize);
videoResizer.addEventListener("pointercancel", finishVideoResize);
videoResizer.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  const width = state.videoPanelWidth || $("#lecture-card").getBoundingClientRect().width;
  resizeVideoPanel(width + (event.key === "ArrowLeft" ? 24 : -24));
});
window.addEventListener("resize", () => {
  if (state.videoPanelWidth !== null && !window.matchMedia("(max-width: 899px)").matches) {
    resizeVideoPanel(state.videoPanelWidth);
  }
});
$("#ask-current-segment").addEventListener("click", () => {
  const time = currentPlaybackTime();
  state.segmentContext = time;
  questionInput.value = `Tại ${formatTime(time)}: `;
  updateQuestionInput();
  questionInput.focus();
});

const accountDialog = $("#account-dialog");
let accountMode = "login";
function openAccountDialog(mode = "login") {
  accountMode = mode;
  const registering = mode === "register";
  $("#account-dialog-title").textContent = registering ? "Tạo tài khoản" : "Đăng nhập";
  $("#account-login-tab").setAttribute("aria-selected", String(!registering));
  $("#account-register-tab").setAttribute("aria-selected", String(registering));
  $("#account-display-label").hidden = !registering;
  $("#account-display-input").hidden = !registering;
  $("#account-display-input").required = registering;
  $("#account-password-input").autocomplete = registering ? "new-password" : "current-password";
  $("#account-submit").textContent = registering ? "Đăng ký" : "Đăng nhập";
  $("#account-form-status").textContent = "";
  accountDialog.showModal();
  $("#account-email-input").focus();
}
$("#user-login").addEventListener("click", (event) => { event.preventDefault(); openAccountDialog("login"); });
$("#user-register").addEventListener("click", (event) => { event.preventDefault(); openAccountDialog("register"); });
document.querySelectorAll(".guest-account-panel a, .guest-add-notice a").forEach((link) => {
  link.addEventListener("click", (event) => { event.preventDefault(); openAccountDialog(link.textContent.includes("Đăng ký") || link.textContent.includes("Tạo") ? "register" : "login"); });
});
$("#account-login-tab").addEventListener("click", () => openAccountDialog("login"));
$("#account-register-tab").addEventListener("click", () => openAccountDialog("register"));
$("#close-account-dialog").addEventListener("click", () => accountDialog.close());
accountDialog.addEventListener("click", (event) => { if (event.target === accountDialog) accountDialog.close(); });
$("#account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = $("#account-submit");
  const status = $("#account-form-status");
  submit.disabled = true;
  status.textContent = accountMode === "register" ? "Đang tạo tài khoản…" : "Đang đăng nhập…";
  try {
    const body = {
      email: $("#account-email-input").value.trim(),
      password: $("#account-password-input").value,
    };
    if (accountMode === "register") body.display_name = $("#account-display-input").value.trim();
    const result = await request(accountMode === "register" ? "/api/user/register" : "/api/user/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (result.csrf_token) sessionStorage.setItem("vskr-user-csrf", result.csrf_token);
    accountDialog.close();
    await loadLearnerSession();
    await loadLibrary();
  } catch (error) {
    status.textContent = error.message || "Không thể hoàn tất yêu cầu.";
  } finally {
    submit.disabled = false;
  }
});
$("#conversation-search").addEventListener("input", renderConversations);
$("#close-rename-conversation").addEventListener("click", () => $("#rename-conversation-dialog").close());
$("#cancel-rename-conversation").addEventListener("click", () => $("#rename-conversation-dialog").close());
$("#rename-conversation-dialog").addEventListener("click", (event) => {
  if (event.target === $("#rename-conversation-dialog")) $("#rename-conversation-dialog").close();
});
$("#rename-conversation-dialog").addEventListener("close", () => { state.renameConversationId = null; });
$("#rename-conversation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const conversationId = state.renameConversationId;
  const title = $("#rename-conversation-title").value.trim();
  const status = $("#rename-conversation-status");
  const saveButton = $("#save-rename-conversation");
  if (!conversationId || !title) {
    status.textContent = "Nhập tên cho cuộc trò chuyện.";
    return;
  }
  saveButton.disabled = true;
  status.textContent = "Đang lưu tên…";
  try {
    const updated = await request(`/api/conversations/${encodeURIComponent(conversationId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    state.conversations = state.conversations.map((conversation) =>
      conversation.conversation_id === updated.conversation_id
        ? { ...conversation, title: updated.title, updated_at: Math.floor(Date.now() / 1000) }
        : conversation)
      .sort((left, right) => Number(right.updated_at) - Number(left.updated_at));
    renderConversations();
    $("#rename-conversation-dialog").close();
  } catch (error) {
    status.textContent = error.message || "Không đổi được tên cuộc trò chuyện.";
  } finally {
    saveButton.disabled = false;
  }
});
$("#close-document").addEventListener("click", () => $("#document-dialog").close());
$("#document-dialog").addEventListener("click", (event) => {
  if (event.target === $("#document-dialog")) $("#document-dialog").close();
});
const questionInput = $("#question-input");
function updateQuestionInput() {
  questionInput.style.height = "auto";
  const maxHeight = 144;
  questionInput.style.height = `${Math.min(questionInput.scrollHeight, maxHeight)}px`;
  questionInput.style.overflowY = questionInput.scrollHeight > maxHeight ? "auto" : "hidden";
  const count = questionInput.value.length;
  $("#question-hint").textContent = `${count}/500 ký tự${count >= 500 ? " · Đã đạt giới hạn" : ""}`;
  $("#ask-button").disabled = !count || state.asking;
  const url = questionInput.value.match(/https?:\/\/[^\s<>"']+/i)?.[0].replace(/[),.!?]+$/, "");
  $("#pasted-link-card").hidden = !url;
  saveCurrentDraft();
}
questionInput.addEventListener("input", updateQuestionInput);
$("#add-pasted-link").addEventListener("click", () => {
  const url = questionInput.value.match(/https?:\/\/[^\s<>"']+/i)?.[0].replace(/[),.!?]+$/, "");
  if (!url) return;
  $("#content-video-url").value = url;
  openContentDialog();
  document.querySelector('.content-tab[data-content-tab="link"]').click();
});
window.addEventListener("online", setNetworkStatus);
window.addEventListener("offline", setNetworkStatus);
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  state.installPrompt = event;
  $("#install-app").hidden = false;
});
$("#install-app").addEventListener("click", async () => {
  if (!state.installPrompt) return;
  state.installPrompt.prompt();
  await state.installPrompt.userChoice;
  state.installPrompt = null;
  $("#install-app").hidden = true;
});

// Search, drawer, fresh thread and composer interactions.
const searchInput = $("#library-search");
const searchResults = $("#search-results");
const searchToggle = $("#search-toggle");
const topbar = document.querySelector(".topbar");
let searchIndex = -1;
function closeSearch() {
  topbar.classList.remove("search-open");
  searchToggle.setAttribute("aria-expanded", "false");
  searchResults.hidden = true;
  searchResults.replaceChildren();
  searchInput.value = "";
  searchInput.setAttribute("aria-expanded", "false");
  searchInput.removeAttribute("aria-activedescendant");
}
function openLibrarySearch() {
  topbar.classList.add("search-open");
  searchInput.focus();
}
/** Tìm nhanh theo tên video/tài liệu trong thư viện đã tải (tối đa 8 kết quả). */
function updateSearch() {
  const query = searchInput.value.trim().toLocaleLowerCase("vi");
  searchResults.replaceChildren(); searchIndex = -1;
  searchInput.removeAttribute("aria-activedescendant");
  if (!query) { searchResults.hidden = true; searchInput.setAttribute("aria-expanded", "false"); return; }
  const matches = [
    ...state.videos.map((item) => ({ type: "video", title: item.title || "Bài giảng", item })),
    ...state.documents.map((item) => ({ type: "document", title: item.title || "Tài liệu", item })),
  ].filter((entry) => entry.title.toLocaleLowerCase("vi").includes(query)).slice(0, 8);
  for (const [index, entry] of matches.entries()) {
    const button = make("button", "search-result"); button.type = "button"; button.role = "option";
    button.id = `search-result-${index}`;
    button.setAttribute("aria-selected", "false");
    button.append(make("span", "video-icon", entry.type === "video" ? "▶" : "▤"), make("span", "", entry.title));
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      if (entry.type === "video") {
        beginConversationWithSource(entry.item);
        closeDrawer({ restoreFocus: false });
      } else {
        openDrawer();
        window.setTimeout(() => {
          const target = [...$("#document-list").children].find((node) => node.dataset.documentId === String(entry.item.document_id || entry.item.id || ""));
          target?.classList.add("search-target");
          target?.scrollIntoView({ block: "nearest" });
          target?.focus({ preventScroll: true });
          window.setTimeout(() => target?.classList.remove("search-target"), 1800);
        }, 240);
      }
      searchResults.hidden = true;
      searchInput.setAttribute("aria-expanded", "false");
      searchInput.value = "";
      searchInput.removeAttribute("aria-activedescendant");
      closeSearch();
    });
    searchResults.append(button);
  }
  if (!matches.length) searchResults.append(make("p", "muted", "Không tìm thấy học liệu phù hợp."));
  searchResults.hidden = false; searchInput.setAttribute("aria-expanded", "true");
}
searchInput.addEventListener("input", updateSearch);
searchInput.addEventListener("keydown", (event) => {
  const options = [...searchResults.querySelectorAll("button")];
  if (event.key === "Escape") {
    if (!searchResults.hidden) {
      event.preventDefault();
      event.stopPropagation();
      searchResults.hidden = true;
      searchInput.setAttribute("aria-expanded", "false");
      searchInput.removeAttribute("aria-activedescendant");
    } else if (window.matchMedia("(max-width: 899px)").matches && topbar.classList.contains("search-open")) {
      event.preventDefault();
      event.stopPropagation();
      closeSearch();
    }
  }
  if ((event.key === "ArrowDown" || event.key === "ArrowUp") && options.length) {
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    searchIndex = searchIndex < 0 ? (step > 0 ? 0 : options.length - 1) : (searchIndex + step + options.length) % options.length;
    options.forEach((el, i) => el.setAttribute("aria-selected", String(i === searchIndex)));
    searchInput.setAttribute("aria-activedescendant", options[searchIndex].id);
  }
  if (event.key === "Enter" && searchIndex >= 0) { event.preventDefault(); options[searchIndex]?.click(); }
});
document.addEventListener("click", (event) => {
  if (event.target instanceof Element && !event.target.closest(".global-search") && !event.target.closest("#sidebar-search")) {
    searchResults.hidden = true;
    searchInput.setAttribute("aria-expanded", "false");
    searchInput.removeAttribute("aria-activedescendant");
    closeSearch();
  }
});
searchToggle.addEventListener("click", () => {
  const open = topbar.classList.toggle("search-open");
  searchToggle.setAttribute("aria-expanded", String(open));
  if (open) searchInput.focus();
  else closeSearch();
});
const drawer = $("#library-drawer"), drawerToggle = $("#drawer-toggle"), drawerBackdrop = $("#drawer-backdrop");
$("#sidebar-collapse").addEventListener("click", () => {
  if (window.matchMedia("(max-width: 899px)").matches) {
    closeDrawer({ restoreFocus: false });
    return;
  }
  const collapsed = document.body.classList.toggle("sidebar-collapsed");
  $("#sidebar-collapse").setAttribute("aria-label", collapsed ? "Mở rộng thanh bên" : "Thu gọn thanh bên");
  if (state.videoPanelWidth !== null) resizeVideoPanel(state.videoPanelWidth);
});
if (!window.matchMedia("(max-width: 899px)").matches) {
  drawer.inert = false;
  drawer.setAttribute("aria-hidden", "false");
}
function openDrawer() {
  drawer.classList.add("open");
  drawer.inert = false;
  drawer.setAttribute("aria-hidden", "false");
  drawerBackdrop.hidden = false;
  drawerToggle.setAttribute("aria-expanded", "true");
  drawerToggle.setAttribute("aria-label", "Đóng thư viện");
  drawer.querySelector("button")?.focus();
}
function closeDrawer({ restoreFocus = false } = {}) {
  if (!drawer.classList.contains("open")) return;
  drawer.classList.remove("open");
  drawer.inert = true;
  drawer.setAttribute("aria-hidden", "true");
  drawerBackdrop.hidden = true;
  drawerToggle.setAttribute("aria-expanded", "false");
  drawerToggle.setAttribute("aria-label", "Mở thư viện");
  if (restoreFocus) drawerToggle.focus();
}
drawerToggle.addEventListener("click", () => {
  if (drawer.classList.contains("open")) closeDrawer();
  else openDrawer();
});
drawerBackdrop.addEventListener("click", () => closeDrawer({ restoreFocus: true }));
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if ($("#rename-conversation-dialog").open) return;
  const suggestions = $("#suggestions");
  if (!suggestions.hidden) {
    suggestions.hidden = true;
    $("#suggest-toggle").setAttribute("aria-expanded", "false");
    $("#suggest-toggle").focus();
    return;
  }
  if (drawer.classList.contains("open")) closeDrawer({ restoreFocus: true });
});
document.addEventListener("click", (event) => {
  if (event.target instanceof Node && drawer.classList.contains("open") && !drawer.contains(event.target) && !drawerToggle.contains(event.target) && !searchResults.contains(event.target)) {
    closeDrawer();
  }
});
document.addEventListener("click", (event) => {
  if (event.target instanceof Element && !event.target.closest(".composer-tools")) {
    $("#suggestions").hidden = true;
    $("#suggest-toggle").setAttribute("aria-expanded", "false");
  }
});
$("#new-thread").addEventListener("click", () => {
  if (state.asking) return;
  startNewConversation();
});
$("#suggest-toggle").addEventListener("click", () => { const box = $("#suggestions"), open = box.hidden; box.hidden = !open; $("#suggest-toggle").setAttribute("aria-expanded", String(open)); });
$("#suggestions").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-content-action]");
  if (!button) return;
  const action = button.dataset.contentAction;
  $("#suggestions").hidden = true;
  $("#suggest-toggle").setAttribute("aria-expanded", "false");
  openContentDialog();
  document.querySelector(`.content-tab[data-content-tab="${action}"]`)?.click();
  if (action === "link") $("#content-video-url").focus();
  else if (action === "upload") $("#content-video-file").focus();
  else $("#content-document-file").focus();
});
questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("#question-form").requestSubmit();
  }
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openLibrarySearch();
  }
});
const thread = $("#thread"), bottomButton = $("#thread-bottom");
const studyPanel = document.querySelector(".study-panel");
function canScrollVertically(element, deltaY) {
  if (!(element instanceof HTMLElement)) return false;
  const overflowY = getComputedStyle(element).overflowY;
  const maxScrollTop = element.scrollHeight - element.clientHeight;
  if (!(overflowY === "auto" || overflowY === "scroll" || overflowY === "overlay") || maxScrollTop <= 1) return false;
  return deltaY < 0 ? element.scrollTop > 1 : element.scrollTop < maxScrollTop - 1;
}
studyPanel.addEventListener("wheel", (event) => {
  if (event.defaultPrevented || event.ctrlKey || !event.deltaY) return;
  const target = event.target instanceof Element ? event.target : null;
  if (!target || thread.contains(target)) return;

  for (let element = target; element && element !== studyPanel; element = element.parentElement) {
    if (canScrollVertically(element, event.deltaY)) return;
  }
  if (!canScrollVertically(thread, event.deltaY)) return;

  const delta = event.deltaMode === WheelEvent.DOM_DELTA_LINE
    ? event.deltaY * 16
    : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
      ? event.deltaY * thread.clientHeight
      : event.deltaY;
  thread.scrollTop += delta;
  event.preventDefault();
}, { passive: false });
thread.addEventListener("scroll", () => { bottomButton.hidden = thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 100; });
bottomButton.addEventListener("click", () => scrollThreadToEnd({ smooth: true }));
window.addEventListener("popstate", () => {
  const conversationId = new URL(location.href).searchParams.get("conversation");
  if (conversationId) openConversation(conversationId, { updateUrl: false });
  else startNewConversation({ updateUrl: false, focus: false });
});

setNetworkStatus();
loadLearnerSession().then(async () => {
  await loadLibrary();
});
showAdminLink();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
