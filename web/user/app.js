import { ApiError, request } from "/shared/api.js";

const $ = (selector) => document.querySelector(selector);
const state = {
  videos: [], documents: [], conversations: [], activeConversationId: null, currentVideo: null, sources: [], player: null,
  signedIn: false,
  sourcesByTurn: new Map(), activeTurnId: null, activeSourceKey: null, turnCounter: 0,
  youtubePromise: null, pendingJump: null, retryUntil: 0, retryInterval: null,
  retryPanel: null, retryButton: null, retryTurn: null,
  installPrompt: null, asking: false,
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

async function waitForPublicJob(jobId, statusNode) {
  while (true) {
    const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    statusNode.textContent = `${job.phase || "Đang xử lý"}: ${job.message || "Vui lòng chờ…"}`;
    if (job.status === "error") throw new Error(job.error_detail || job.message || "Xử lý thất bại.");
    if (job.status === "done") return job;
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

$("#add-video-toggle").addEventListener("click", () => {
  const panel = $("#add-video-panel");
  panel.hidden = !panel.hidden;
  $("#add-video-toggle").setAttribute("aria-expanded", String(!panel.hidden));
});
$("#user-video-url").addEventListener("input", () => { if ($("#user-video-url").value.trim()) $("#user-video-file").value = ""; });
$("#user-video-file").addEventListener("change", () => { if ($("#user-video-file").files.length) $("#user-video-url").value = ""; });
$("#user-video-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = $("#user-video-status"), url = $("#user-video-url").value.trim(), file = $("#user-video-file").files[0];
  if (Boolean(url) === Boolean(file)) { status.textContent = "Dán một link hoặc chọn một video."; return; }
  const payload = new FormData();
  if (url) payload.append("url", url); else payload.append("file", file);
  const button = event.submitter; button.disabled = true; status.textContent = "Đang tiếp nhận video…";
  try {
    const queued = await request("/api/ingest", { method: "POST", body: payload });
    await waitForPublicJob(queued.job_id, status);
    await loadLibrary();
    status.textContent = "Video đã sẵn sàng. Bạn có thể hỏi ngay.";
  } catch (error) { status.textContent = error.message || "Không thêm được video."; }
  finally { button.disabled = false; }
});
$("#user-document-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = $("#user-document-status"), file = $("#user-document-file").files[0];
  if (!file) { status.textContent = "Chọn một tài liệu trước."; return; }
  const payload = new FormData(); payload.append("file", file);
  const button = event.submitter; button.disabled = true; status.textContent = "Đang tiếp nhận tài liệu…";
  try {
    const queued = await request("/api/documents", { method: "POST", body: payload });
    await waitForPublicJob(queued.job_id, status);
    await loadLibrary(); status.textContent = "Tài liệu đã sẵn sàng để hỏi.";
  } catch (error) { status.textContent = error.message || "Không thêm được tài liệu."; }
  finally { button.disabled = false; }
});

function clearRetry() {
  if (state.retryInterval) clearInterval(state.retryInterval);
  state.retryInterval = null;
  state.retryUntil = 0;
  state.retryPanel?.remove();
  state.retryPanel = null;
  state.retryButton = null;
  state.retryTurn = null;
}

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

function selectVideo(video) {
  state.currentVideo = video;
  $("#welcome").hidden = true;
  $("#lecture-card").hidden = false;
  $("#lecture-title").textContent = video.title || "Bài giảng";
  $("#lecture-duration").textContent = video.duration ? formatTime(video.duration) : "";
  renderVideos();
  const index = state.videos.findIndex((item) => item.video_id === video.video_id);
  $("#video-position").textContent = `${Math.max(0, index) + 1}/${state.videos.length} bài giảng`;
  $("#prev-video").disabled = index <= 0;
  $("#next-video").disabled = index < 0 || index >= state.videos.length - 1;
  setupPlayer(video);
  renderTimeline();
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

function appendCitedAnswer(container, answer, sources, turnId) {
  const citationPattern = /\[\s*(?:Nguồn\s*)?(\d+(?:\s*[,;]\s*(?:Nguồn\s*)?\d+)*)\s*\]/gi;
  let cursor = 0;
  for (const match of answer.matchAll(citationPattern)) {
    container.append(document.createTextNode(answer.slice(cursor, match.index)));
    const numbers = match[1].match(/\d+/g).map(Number);
    if (numbers.every((number) => sources.some((source) => source.n === number))) {
      numbers.forEach((number, index) => {
        if (index) container.append(document.createTextNode(", "));
        const sourceItem = sources.find((item) => item.n === number);
        const cite = make("button", "citation", `[${number}] ${sourceItem?.source_type === "document" ? "▤" : "▶"}`);
        cite.type = "button";
        cite.setAttribute("aria-label", `Mở nguồn ${number}`);
        cite.addEventListener("click", () => {
          const source = sources.find((item) => item.n === number);
          if (source) openSource(source, turnId);
        });
        container.append(cite);
      });
    } else {
      container.append(document.createTextNode(match[0]));
    }
    cursor = match.index + match[0].length;
  }
  container.append(document.createTextNode(answer.slice(cursor)));
}

function renderResult(result, root, turnId, { scroll = true } = {}) {
  const followThread = isThreadNearEnd();
  state.sources = Array.isArray(result.sources) ? result.sources : [];
  state.activeTurnId = String(turnId);
  state.activeSourceKey = null;
  state.sourcesByTurn.set(state.activeTurnId, state.sources);
  root.replaceChildren();
  const answer = make("div", `answer${result.found ? "" : " not-found"}`);
  appendCitedAnswer(answer, result.answer || "Chưa có câu trả lời.", state.sources, turnId);
  root.append(answer);
  if (result.found && state.sources.length) {
    root.append(make("p", "coverage-note", "Câu trả lời dựa trên các đoạn gần nhất tìm được trong thư viện. Hãy mở nguồn để đối chiếu."));
  }
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

function addTurn(role, content, pending = false, { scroll = true, forceScroll = false } = {}) {
  const row = make("div", role === "user" ? "user-turn" : "assistant-turn");
  const turnId = `turn-${++state.turnCounter}`;
  row.id = turnId;
  row.dataset.turnId = turnId;
  const avatar = make("span", "turn-avatar", role === "user" ? "B" : "V");
  avatar.setAttribute("aria-hidden", "true");
  const body = make("div", "turn-content", content);
  row.append(avatar, body);
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

async function submitQuestion(question = $("#question-input").value.trim(), options = {}) {
  if (!question || state.asking) return;
  const retryTurn = options.retryTurn || null;
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
  const pending = make("p", "request-status", retryTurn ? "Đang thử gửi lại câu hỏi…" : "Đang tìm trong bài giảng và tài liệu…");
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
      body: JSON.stringify({ question, conversation_id: state.activeConversationId }),
    });
    if (result.conversation_id) {
      state.activeConversationId = result.conversation_id;
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
      state.sourcesByTurn.set(String(turn.dataset.turnId), state.sources);
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
    $("#ask-button").disabled = false;
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

async function loadLearnerSession() {
  try {
    const session = await request("/api/user/auth/me");
    setAccountView(session);
    $("#user-name").textContent = session.username;
    if (session.csrf_token) sessionStorage.setItem("vskr-user-csrf", session.csrf_token);
    await loadConversations();
  } catch {
    setAccountView(null);
    sessionStorage.removeItem("vskr-user-csrf");
  }
}

function setAccountView(session, { preserveCurrentThread = false } = {}) {
  const signedIn = Boolean(session?.user_id);
  const lostSession = state.signedIn && !signedIn;
  state.signedIn = signedIn;
  $("#user-login").hidden = signedIn;
  $("#user-register").hidden = signedIn;
  $("#user-name").hidden = !signedIn;
  $("#user-logout").hidden = !signedIn;
  $("#history-section").hidden = !signedIn;
  $("#guest-history-cta").hidden = signedIn;
  $("#guest-add-notice").hidden = signedIn;
  $("#user-video-form").hidden = !signedIn;
  $("#user-document-form").hidden = !signedIn;
  if (!signedIn) {
    state.conversations = [];
    state.activeConversationId = null;
    renderConversations();
    if (lostSession && !preserveCurrentThread) {
      state.sources = [];
      state.sourcesByTurn.clear();
      state.activeTurnId = null;
      state.activeSourceKey = null;
      $("#conversation").replaceChildren();
      $("#welcome").hidden = false;
      renderTimeline();
    }
  }
}

function renderConversations() {
  const list = $("#conversation-list");
  list.replaceChildren();
  $("#conversation-count").textContent = String(state.conversations.length);
  $("#conversation-empty").hidden = state.conversations.length > 0;
  for (const conversation of state.conversations) {
    const row = make("div", `conversation-row${conversation.conversation_id === state.activeConversationId ? " is-active" : ""}`);
    const open = make("button", "conversation-choice", conversation.title || "Cuộc trò chuyện mới");
    open.type = "button";
    open.setAttribute("aria-current", String(conversation.conversation_id === state.activeConversationId));
    open.addEventListener("click", () => openConversation(conversation.conversation_id));
    const remove = make("button", "conversation-delete", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Xóa cuộc trò chuyện: ${conversation.title}`);
    remove.title = "Xóa cuộc trò chuyện";
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Xóa cuộc trò chuyện “${conversation.title}”?`)) return;
      try {
        await request(`/api/conversations/${encodeURIComponent(conversation.conversation_id)}`, { method: "DELETE" });
        if (state.activeConversationId === conversation.conversation_id) startNewConversation();
        await loadConversations();
      } catch (error) { setStatus(error.message, "error"); }
    });
    row.append(open, remove);
    list.append(row);
  }
}

async function loadConversations() {
  try {
    state.conversations = await request("/api/conversations");
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

async function openConversation(conversationId) {
  try {
    const conversation = await request(`/api/conversations/${encodeURIComponent(conversationId)}`);
    clearRetry();
    state.activeConversationId = conversation.conversation_id;
    state.sources = [];
    state.sourcesByTurn.clear();
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
    scrollThreadToEnd();
    if (!conversation.turns.length) $("#welcome").hidden = false;
    renderConversations();
    closeDrawer({ restoreFocus: false });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      sessionStorage.removeItem("vskr-user-csrf");
      setAccountView(null);
      setStatus("Phiên đăng nhập đã hết hạn. Bạn vẫn có thể tiếp tục hỏi bài với tư cách khách.", "error");
    } else {
      setStatus(error.message || "Không mở được cuộc trò chuyện.", "error");
    }
  }
}

function startNewConversation() {
  clearRetry();
  state.activeConversationId = null;
  $("#conversation").replaceChildren();
  $("#welcome").hidden = false;
  questionInput.value = "";
  questionInput.style.height = "auto";
  updateQuestionInput();
  setStatus("");
  state.sources = [];
  state.sourcesByTurn.clear();
  state.activeTurnId = null;
  state.activeSourceKey = null;
  renderTimeline();
  renderConversations();
  $("#thread").scrollTop = 0;
  questionInput.focus();
}

$("#user-logout").addEventListener("click", async () => {
  const button = $("#user-logout"); button.disabled = true;
  try { await request("/api/user/auth/logout", { method: "POST" }); }
  finally { sessionStorage.removeItem("vskr-user-csrf"); location.assign("/"); }
});

async function loadLibrary() {
  try {
    const [catalog, status] = await Promise.all([request("/api/catalog"), request("/api/status")]);
    state.videos = Array.isArray(catalog.videos) ? catalog.videos : [];
    state.documents = Array.isArray(catalog.documents) ? catalog.documents : [];
    $("#library-loading").hidden = true;
    renderDocuments();
    renderVideos();
    if (state.videos.length) selectVideo(state.videos[0]);
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
$("#close-document").addEventListener("click", () => $("#document-dialog").close());
$("#document-dialog").addEventListener("click", (event) => {
  if (event.target === $("#document-dialog")) $("#document-dialog").close();
});
const questionInput = $("#question-input");
function updateQuestionInput() {
  questionInput.style.height = "auto";
  questionInput.style.height = `${Math.min(questionInput.scrollHeight, 144)}px`;
  const count = questionInput.value.length;
  $("#question-hint").textContent = `${count}/500 ký tự${count >= 500 ? " · Đã đạt giới hạn" : ""}`;
}
questionInput.addEventListener("input", updateQuestionInput);
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
        selectVideo(entry.item);
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
      if (window.matchMedia("(max-width: 899px)").matches) closeSearch();
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
  if (event.target instanceof Element && !event.target.closest(".global-search")) {
    searchResults.hidden = true;
    searchInput.setAttribute("aria-expanded", "false");
    searchInput.removeAttribute("aria-activedescendant");
    if (window.matchMedia("(max-width: 899px)").matches) closeSearch();
  }
});
searchToggle.addEventListener("click", () => {
  const open = topbar.classList.toggle("search-open");
  searchToggle.setAttribute("aria-expanded", String(open));
  if (open) searchInput.focus();
  else closeSearch();
});
const drawer = $("#library-drawer"), drawerToggle = $("#drawer-toggle"), drawerBackdrop = $("#drawer-backdrop");
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
$("#video-position").addEventListener("click", () => drawerToggle.click());
$("#prev-video").addEventListener("click", () => { const i = state.videos.findIndex((v) => v.video_id === state.currentVideo?.video_id); if (i > 0) selectVideo(state.videos[i - 1]); });
$("#next-video").addEventListener("click", () => { const i = state.videos.findIndex((v) => v.video_id === state.currentVideo?.video_id); if (i >= 0 && i < state.videos.length - 1) selectVideo(state.videos[i + 1]); });
$("#new-thread").addEventListener("click", () => {
  if (state.asking) return;
  startNewConversation();
});
$("#suggest-toggle").addEventListener("click", () => { const box = $("#suggestions"), open = box.hidden; box.hidden = !open; $("#suggest-toggle").setAttribute("aria-expanded", String(open)); });
$("#suggestions").addEventListener("click", (event) => { const button = event.target.closest("button"); if (!button) return; $("#question-input").value = button.textContent; $("#question-input").dispatchEvent(new Event("input")); $("#question-input").focus(); $("#suggestions").hidden = true; $("#suggest-toggle").setAttribute("aria-expanded", "false"); });
questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("#question-form").requestSubmit();
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

setNetworkStatus();
loadLearnerSession();
loadLibrary();
showAdminLink();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
