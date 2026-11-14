import { ApiError, request } from "/shared/api.js";

const $ = (selector) => document.querySelector(selector);
const state = {
  videos: [], documents: [], currentVideo: null, sources: [], player: null,
  youtubePromise: null, pendingJump: null, retryUntil: 0, retryQuestion: "", retryInterval: null,
  installPrompt: null, asking: false, requestId: null,
};

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

function setStatus(message = "", kind = "") {
  const status = $("#request-status");
  status.textContent = message;
  status.hidden = !message;
  status.classList.toggle("error", kind === "error");
}

function clearRetry() {
  if (state.retryInterval) clearInterval(state.retryInterval);
  state.retryInterval = null;
  state.retryUntil = 0;
  $("#retry-panel").hidden = true;
}

function waitBeforeRetry(seconds, question, status) {
  clearRetry();
  state.retryQuestion = question;
  state.retryUntil = Date.now() + Math.max(1, seconds) * 1000;
  $("#retry-panel").hidden = false;
  $("#retry-button").disabled = true;
  setStatus(status, "error");
  const update = () => {
    const left = Math.max(0, Math.ceil((state.retryUntil - Date.now()) / 1000));
    $("#retry-countdown").textContent = left
      ? `Bạn có thể gửi lại sau ${left} giây.`
      : "Đã có thể thử lại.";
    $("#retry-button").disabled = left > 0 || state.asking;
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
    button.addEventListener("click", () => selectVideo(video));
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
  setupPlayer(video);
  renderTimeline();
}

function renderTimeline() {
  const timeline = $("#timeline");
  timeline.replaceChildren();
  const duration = Number(state.currentVideo?.duration || 0);
  const sources = state.sources.filter((source) => source.video_id === state.currentVideo?.video_id && Number.isFinite(Number(source.start)));
  $("#timeline-range").textContent = duration ? `0:00 – ${formatTime(duration)}` : "";
  $("#timeline-empty").hidden = Boolean(duration && sources.length);
  for (const source of sources) {
    const mark = make("button", "timeline-mark");
    mark.type = "button";
    mark.style.left = `${Math.max(0, Math.min(100, Number(source.start) / duration * 100))}%`;
    const width = Number(source.end) > Number(source.start) ? Number(source.end) - Number(source.start) : 1;
    mark.style.width = `${Math.max(.8, Math.min(30, width / duration * 100))}%`;
    mark.title = `Nguồn ${source.n} · ${source.label || formatTime(source.start)}`;
    mark.setAttribute("aria-label", `Tua đến nguồn ${source.n}, ${source.label || formatTime(source.start)}`);
    mark.addEventListener("click", () => openSource(source));
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

async function openSource(source) {
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

function appendCitedAnswer(container, answer, sources) {
  const citationPattern = /\[\s*(?:Nguồn\s*)?(\d+(?:\s*[,;]\s*(?:Nguồn\s*)?\d+)*)\s*\]/gi;
  let cursor = 0;
  for (const match of answer.matchAll(citationPattern)) {
    container.append(document.createTextNode(answer.slice(cursor, match.index)));
    const numbers = match[1].match(/\d+/g).map(Number);
    if (numbers.every((number) => sources.some((source) => source.n === number))) {
      numbers.forEach((number, index) => {
        if (index) container.append(document.createTextNode(", "));
        const cite = make("button", "citation", String(number));
        cite.type = "button";
        cite.setAttribute("aria-label", `Mở nguồn ${number}`);
        cite.addEventListener("click", () => {
          const source = sources.find((item) => item.n === number);
          if (source) openSource(source);
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

function renderResult(result) {
  state.sources = Array.isArray(result.sources) ? result.sources : [];
  state.requestId = result.request_id || null;
  const root = $("#result");
  root.replaceChildren();
  root.hidden = false;
  const answer = make("div", `answer${result.found ? "" : " not-found"}`);
  appendCitedAnswer(answer, result.answer || "Chưa có câu trả lời.", state.sources);
  root.append(answer);
  if (state.sources.length) {
    const cards = make("div", "source-list");
    for (const source of state.sources) {
      const card = make("article", "source-card");
      const heading = make("div", "source-heading");
      heading.append(make("span", "source-number", `Nguồn ${source.n}`));
      heading.append(make("span", "source-title", source.title || (source.source_type === "document" ? "Tài liệu" : "Bài giảng")));
      heading.append(make("span", "source-time", source.label || (source.source_type === "document" ? `Mục ${source.section}` : formatTime(source.start))));
      card.append(heading);
      card.append(make("p", "source-text", source.text || ""));
      const actions = make("div", "source-actions");
      const open = make("button", "small-button", source.source_type === "document" ? "Mở mục tài liệu" : `Phát từ ${formatTime(source.start)}`);
      open.type = "button";
      open.addEventListener("click", () => openSource(source));
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
    root.append(cards);
  }
  if (state.requestId) {
    const feedback = make("div", "answer-feedback");
    const prompt = make("span", "hint", "Câu trả lời này có hữu ích không?");
    feedback.append(prompt);
    for (const [value, label] of [["up", "👍 Hữu ích"], ["down", "👎 Chưa hữu ích"]]) {
      const button = make("button", "small-button", label);
      button.type = "button";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await request("/api/feedback", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ request_id: state.requestId, feedback: value }),
          });
          feedback.replaceChildren(make("span", "hint", "Cảm ơn phản hồi của bạn."));
        } catch {
          button.disabled = false;
          setStatus("Chưa gửi được phản hồi. Bạn có thể thử lại.", "error");
        }
      });
      feedback.append(button);
    }
    root.append(feedback);
  }
  renderTimeline();
  root.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function submitQuestion(question = $("#question-input").value.trim()) {
  if (!question || state.asking) return;
  state.asking = true;
  $("#ask-button").disabled = true;
  $("#retry-button").disabled = true;
  clearRetry();
  setStatus("Đang tìm trong bài giảng và tài liệu…");
  try {
    const result = await request("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    $("#question-input").value = "";
    setStatus("");
    renderResult(result);
  } catch (error) {
    if (!navigator.onLine) {
      setStatus("Bạn đang ngoại tuyến. Kết nối lại internet rồi thử gửi câu hỏi.", "error");
    } else if (error instanceof ApiError && error.status === 429) {
      waitBeforeRetry(error.retryAfter || 60, question, "Bạn đã gửi nhiều câu hỏi trong thời gian ngắn.");
    } else if (error instanceof ApiError && error.status === 503) {
      waitBeforeRetry(error.retryAfter || 10, question, "Hệ thống đang bận xử lý học liệu. Câu hỏi của bạn vẫn được giữ lại.");
    } else if (error instanceof ApiError && error.status >= 500) {
      setStatus("Hiện chưa thể trả lời câu hỏi. Vui lòng thử lại sau.", "error");
    } else if (error instanceof ApiError) {
      setStatus(error.message || "Không gửi được câu hỏi. Hãy thử lại.", "error");
    } else {
      setStatus("Không kết nối được máy chủ. Kiểm tra kết nối rồi thử lại.", "error");
    }
  } finally {
    state.asking = false;
    $("#ask-button").disabled = false;
    if (state.retryUntil > Date.now()) $("#retry-button").disabled = true;
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
  submitQuestion();
});
$("#retry-button").addEventListener("click", () => submitQuestion(state.retryQuestion));
$("#close-document").addEventListener("click", () => $("#document-dialog").close());
$("#document-dialog").addEventListener("click", (event) => {
  if (event.target === $("#document-dialog")) $("#document-dialog").close();
});
$("#question-input").addEventListener("input", (event) => {
  $("#question-hint").textContent = `${event.target.value.length}/500 ký tự`;
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

setNetworkStatus();
loadLibrary();
showAdminLink();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
