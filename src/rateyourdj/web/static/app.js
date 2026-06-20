// rateyourDJ — 纯对话式前端
// 对话: POST /api/v1/agent/recommend
// 反馈/学习: POST /api/feedback/<user_id>  (favorite/like/dislike/skip/play)
// 收藏列表: GET /api/collection/<user_id>
// 画像/反馈统计: GET /api/profile/<user_id>, GET /api/feedback/<user_id>

const state = {
  userId: "demo-user",
  sessionId: null,
  lastRunId: null,
  busy: false,
  // track_id -> { fav: bool, vote: "like"|"dislike"|null }
  trackState: {},
  // track_id -> track payload(给收藏/反馈带上下文，让发现的歌也能显示标题)
  trackCache: {},
};

const $ = (sel) => document.querySelector(sel);

const stream = $("#chat-stream");
const composer = $("#composer");
const composerInput = $("#composer-input");
const userIdInput = $("#user-id");
const statusPill = $("#status-pill");

let spotifyIframeApi = null;
let pendingPreview = null; // { container, trackId }

window.onSpotifyIframeApiReady = (IFrameAPI) => {
  spotifyIframeApi = IFrameAPI;
  if (pendingPreview) {
    const p = pendingPreview;
    pendingPreview = null;
    mountSpotify(p.container, p.trackId);
  }
};

// ---------- 启动 ----------
init();

function init() {
  userIdInput.addEventListener("change", () => {
    state.userId = userIdInput.value.trim() || "demo-user";
    state.sessionId = null;
    refreshCollection();
  });

  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = composerInput.value.trim();
    if (text) sendMessage(text);
  });

  $("#suggestion-row").addEventListener("click", (event) => {
    const button = event.target.closest(".suggestion");
    if (button) sendMessage(button.dataset.q);
  });

  $("#open-collection").addEventListener("click", openDrawer);
  $("#close-collection").addEventListener("click", closeDrawer);
  $("#drawer-backdrop").addEventListener("click", closeDrawer);

  state.userId = userIdInput.value.trim() || "demo-user";
  loadStatus();
  refreshCollection();
}

async function loadStatus() {
  try {
    const status = await getJSON("/api/agent-status");
    const model = status.model_enabled ? (status.provider || "model") : "rules";
    const music = status.music_provider_enabled ? " · Spotify on" : "";
    setStatus(`${model}${music}`, true);
  } catch (error) {
    setStatus("离线", false);
  }
}

function setStatus(text, online) {
  statusPill.innerHTML = `<span class="dot"></span> ${escapeHtml(text)}`;
  statusPill.classList.toggle("offline", !online);
}

// ---------- 发送消息 ----------
async function sendMessage(text) {
  if (state.busy) return;
  state.userId = userIdInput.value.trim() || "demo-user";
  dismissWelcome();
  appendUserBubble(text);
  composerInput.value = "";
  setBusy(true);
  const thinking = appendThinking();

  try {
    const result = await getJSON("/api/v1/agent/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: state.userId,
        message: text,
        session_id: state.sessionId,
        constraints: { limit: 8 },
        include_trace: true,
      }),
    });
    state.sessionId = result.session_id;
    state.lastRunId = result.run_id;
    thinking.remove();
    appendDJReply(result, text);
  } catch (error) {
    thinking.remove();
    appendErrorBubble(error.message);
  } finally {
    setBusy(false);
  }
}

// ---------- 渲染：消息气泡 ----------
function appendUserBubble(text) {
  const row = el("div", "msg msg-user");
  row.appendChild(el("div", "bubble bubble-user", text));
  stream.appendChild(row);
  scrollToBottom();
}

function appendThinking() {
  const row = el("div", "msg msg-dj");
  const bubble = el("div", "bubble bubble-dj thinking");
  bubble.innerHTML =
    '<span class="dj-avatar">DJ</span><span class="dots"><i></i><i></i><i></i></span>';
  row.appendChild(bubble);
  stream.appendChild(row);
  scrollToBottom();
  return row;
}

function appendErrorBubble(text) {
  const row = el("div", "msg msg-dj");
  const bubble = el("div", "bubble bubble-dj error");
  bubble.textContent = `出错了：${text}`;
  row.appendChild(bubble);
  stream.appendChild(row);
  scrollToBottom();
}

function appendDJReply(result, query) {
  const row = el("div", "msg msg-dj");
  const bubble = el("div", "bubble bubble-dj");

  const head = el("div", "dj-head");
  head.innerHTML = '<span class="dj-avatar">DJ</span>';
  const note = el("p", "dj-note", result.message || "为你挑了几首：");
  head.appendChild(note);
  bubble.appendChild(head);

  const recs = result.recommendations || [];
  if (recs.length === 0) {
    bubble.appendChild(el("p", "dj-empty", "这次没找到合适的歌，换个说法试试？"));
  } else {
    const cards = el("div", "track-cards");
    recs.forEach((rec) => cards.appendChild(buildTrackCard(rec)));
    bubble.appendChild(cards);

    const actions = el("div", "reply-actions");
    const more = el("button", "chip-button", "换一批");
    more.type = "button";
    more.addEventListener("click", () =>
      sendMessage("换一批，不要重复刚才推荐过的歌")
    );
    actions.appendChild(more);
    bubble.appendChild(actions);
  }

  row.appendChild(bubble);
  stream.appendChild(row);
  scrollToBottom();
}

// ---------- 渲染：歌曲卡片 ----------
function buildTrackCard(rec) {
  const track = rec.track || {};
  const trackId = track.track_id || `unknown-${Math.random().toString(36).slice(2)}`;
  state.trackCache[trackId] = track;
  if (!state.trackState[trackId]) state.trackState[trackId] = { fav: false, vote: null };

  const card = el("div", "track-card");
  card.dataset.trackId = trackId;

  // 头部：曲名 / 艺人 / 排名
  const main = el("div", "track-main");
  const titleWrap = el("div", "track-title-wrap");
  titleWrap.appendChild(el("div", "track-title", track.title || "未知曲目"));
  const sub = [track.artist, track.album].filter(Boolean).join(" · ");
  titleWrap.appendChild(el("div", "track-sub", sub || "未知艺人"));
  main.appendChild(titleWrap);
  if (rec.rank) main.appendChild(el("span", "track-rank", `#${rec.rank}`));
  card.appendChild(main);

  // 推荐理由
  const reasons = (rec.reasons || []).filter((r) => r && r.text).slice(0, 2);
  if (reasons.length) {
    const reasonWrap = el("div", "track-reasons");
    reasons.forEach((r) => reasonWrap.appendChild(el("p", "reason", r.text)));
    card.appendChild(reasonWrap);
  }

  // 试听
  const playRow = el("div", "track-play");
  if (track.preview_available && track.external_ids && track.external_ids.spotify_track_id) {
    const playBtn = el("button", "play-button", "▶ 试听");
    playBtn.type = "button";
    const slot = el("div", "spotify-slot");
    playBtn.addEventListener("click", () => {
      playBtn.style.display = "none";
      mountSpotify(slot, track.external_ids.spotify_track_id);
    });
    playRow.appendChild(playBtn);
    playRow.appendChild(slot);
  } else {
    const link = track.external_urls && track.external_urls.spotify;
    if (link) {
      const a = el("a", "play-link", "在 Spotify 打开 ↗");
      a.href = link;
      a.target = "_blank";
      a.rel = "noopener";
      playRow.appendChild(a);
    } else {
      playRow.appendChild(el("span", "no-preview", "暂无试听"));
    }
  }
  card.appendChild(playRow);

  // 反馈按钮
  const fb = el("div", "track-feedback");
  const ts = state.trackState[trackId];

  const fav = iconButton("♥", "收藏", ts.fav);
  fav.addEventListener("click", () => toggleFavorite(trackId, fav));

  const like = iconButton("👍", "喜欢", ts.vote === "like");
  const dislike = iconButton("👎", "不喜欢", ts.vote === "dislike");
  like.addEventListener("click", () => vote(trackId, "like", like, dislike));
  dislike.addEventListener("click", () => vote(trackId, "dislike", like, dislike));

  const skip = iconButton("⤳", "跳过", false);
  skip.addEventListener("click", () => {
    sendFeedback(trackId, "skip");
    card.classList.add("skipped");
    toast("已跳过，这类我会少推");
  });

  fb.append(fav, like, dislike, skip);
  card.appendChild(fb);
  return card;
}

function iconButton(glyph, label, active) {
  const b = el("button", "fb-button", glyph);
  b.type = "button";
  b.title = label;
  b.setAttribute("aria-label", label);
  if (active) b.classList.add("active");
  return b;
}

// ---------- Spotify 内嵌试听 ----------
function mountSpotify(container, spotifyTrackId) {
  if (!spotifyIframeApi) {
    pendingPreview = { container, trackId: spotifyTrackId };
    container.innerHTML = '<div class="spotify-loading">加载播放器…</div>';
    return;
  }
  container.innerHTML = "";
  spotifyIframeApi.createController(
    container,
    {
      uri: `spotify:track:${spotifyTrackId}`,
      width: "100%",
      height: 80,
    },
    () => {}
  );
}

// ---------- 反馈 / 学习 ----------
async function toggleFavorite(trackId, button) {
  const ts = state.trackState[trackId];
  ts.fav = !ts.fav;
  button.classList.toggle("active", ts.fav);
  if (ts.fav) {
    await sendFeedback(trackId, "favorite");
    toast("已收藏 ♥");
    refreshCollection();
  } else {
    toast("已取消收藏");
  }
}

async function vote(trackId, kind, likeBtn, dislikeBtn) {
  const ts = state.trackState[trackId];
  ts.vote = ts.vote === kind ? null : kind;
  likeBtn.classList.toggle("active", ts.vote === "like");
  dislikeBtn.classList.toggle("active", ts.vote === "dislike");
  if (ts.vote === kind) {
    await sendFeedback(trackId, kind === "like" ? "like" : "dislike");
    toast(kind === "like" ? "记下了，你喜欢这种 👍" : "记下了，少推这种 👎");
  }
}

async function sendFeedback(trackId, feedbackType) {
  const track = state.trackCache[trackId] || {};
  try {
    await getJSON(`/api/feedback/${encodeURIComponent(state.userId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        song_id: trackId,
        feedback_type: feedbackType,
        recommendation_context: {
          run_id: state.lastRunId,
          session_id: state.sessionId,
          track: {
            title: track.title,
            artist: track.artist,
            album: track.album,
          },
        },
      }),
    });
  } catch (error) {
    toast(`反馈失败：${error.message}`);
  }
}

// ---------- 收藏抽屉 ----------
function openDrawer() {
  $("#collection-drawer").classList.remove("hidden");
  $("#collection-drawer").setAttribute("aria-hidden", "false");
  $("#drawer-backdrop").classList.remove("hidden");
  refreshCollection();
}

function closeDrawer() {
  $("#collection-drawer").classList.add("hidden");
  $("#collection-drawer").setAttribute("aria-hidden", "true");
  $("#drawer-backdrop").classList.add("hidden");
}

async function refreshCollection() {
  try {
    const [collection, feedback] = await Promise.all([
      getJSON(`/api/collection/${encodeURIComponent(state.userId)}`),
      getJSON(`/api/feedback/${encodeURIComponent(state.userId)}`).catch(() => null),
    ]);
    renderCollection(collection);
    $("#collection-badge").textContent = collection.total || 0;
    $("#stat-collection").textContent = collection.total || 0;
    if (feedback) {
      $("#stat-feedback").textContent = feedback.total_events ?? 0;
      const reward = Number(feedback.average_reward || 0);
      $("#stat-reward").textContent = reward.toFixed(2);
    }
  } catch (error) {
    // 用户画像可能还不存在(新用户)，静默处理
    $("#collection-badge").textContent = "0";
  }
}

function renderCollection(collection) {
  const list = $("#collection-list");
  const songs = collection.songs || [];
  if (songs.length === 0) {
    list.innerHTML =
      '<p class="drawer-empty">还没有收藏。点歌曲卡片上的 ♥ 就会出现在这里。</p>';
    return;
  }
  list.replaceChildren();
  songs.forEach((song) => {
    const item = el("div", "collection-item");
    item.appendChild(el("div", "ci-title", song.title || "未知曲目"));
    const sub = [song.artist, song.album].filter(Boolean).join(" · ");
    item.appendChild(el("div", "ci-sub", sub || "未知艺人"));
    if (Array.isArray(song.genres) && song.genres.length) {
      const tags = el("div", "ci-tags");
      song.genres.slice(0, 3).forEach((g) => tags.appendChild(el("span", "ci-tag", g)));
      item.appendChild(tags);
    }
    list.appendChild(item);
  });
}

// ---------- 工具函数 ----------
function dismissWelcome() {
  const welcome = $("#welcome");
  if (welcome) welcome.remove();
}

function setBusy(busy) {
  state.busy = busy;
  $("#composer-send").disabled = busy;
  composerInput.disabled = busy;
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    stream.scrollTop = stream.scrollHeight;
  });
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value);
  return div.innerHTML;
}

let toastTimer = null;
function toast(text) {
  const node = $("#toast");
  node.textContent = text;
  node.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), 2200);
}

async function getJSON(url, options) {
  const response = await fetch(url, options);
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    const message =
      (data && (data.error?.message || data.error)) ||
      `请求失败 (${response.status})`;
    throw new Error(message);
  }
  return data;
}
