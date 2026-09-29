// rateyourDJ — 纯对话式前端
// 对话: POST /api/v1/agent/recommend
//
// V2 用户（data/users/<id>/context.json，默认 participant_001）走研究流程：
//   状态: GET /api/v2/status?user_id=
//   反馈: POST /api/v1/agent/feedback { impression_id, event | survey }（没有曝光就没有反馈）
//   收藏: GET /api/v2/users/<id>/saved；删除数据: DELETE /api/v2/users/<id>/data
// 旧用户走原来的流程：
//   反馈/学习: POST /api/feedback/<user_id>  (favorite/like/dislike/skip/play)
//   收藏列表: GET /api/collection/<user_id>

const state = {
  userId: "participant_001",
  v2: null, // /api/v2/status 的结果；null 表示旧用户
  exploration: 0.5,
  count: 10,
  interleave: null, // [strategyA, strategyB] 或 null
  sessionId: null,
  lastRunId: null,
  busy: false,
  // track_id -> { fav: bool, vote: "like"|"dislike"|null }
  trackState: {},
  // track_id -> track payload(给收藏/反馈带上下文，让发现的歌也能显示标题)
  trackCache: {},
};

const $ = (sel) => document.querySelector(sel);

// 跨刷新持久化：记住 user_id 和当前会话，刷新/重开页面不丢上下文
const STORE_KEYS = {
  user: "rydj.userId",
  session: "rydj.sessionId",
  exploration: "rydj.exploration",
  count: "rydj.count",
  interleave: "rydj.interleave",
};

function lsGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}
function lsSet(key, value) {
  try {
    if (value == null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch (error) {
    /* localStorage 不可用时静默降级为内存态 */
  }
}

const stream = $("#chat-stream");
// 缓存初始欢迎屏，"新对话"时可还原
const WELCOME_HTML = stream.innerHTML;
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
  // 恢复上次的用户与会话
  const savedUser = lsGet(STORE_KEYS.user);
  if (savedUser) {
    userIdInput.value = savedUser;
    state.userId = savedUser;
  }
  state.userId = userIdInput.value.trim() || userIdInput.defaultValue;
  state.sessionId = lsGet(STORE_KEYS.session);

  userIdInput.addEventListener("change", async () => {
    state.userId = userIdInput.value.trim() || userIdInput.defaultValue;
    lsSet(STORE_KEYS.user, state.userId);
    // 换用户即开新会话
    startNewConversation();
    await loadStatus();
    refreshCollection();
  });

  initStudyControls();

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
  $("#new-chat").addEventListener("click", startNewConversation);

  lsSet(STORE_KEYS.user, state.userId);
  loadStatus().then(() => {
    refreshCollection();
    if (state.sessionId && !state.v2) restoreConversation();
  });
}

// 拉取已保存会话的历史消息，重建对话流
async function restoreConversation() {
  try {
    const session = await getJSON(
      `/api/v1/agent/session/${encodeURIComponent(state.sessionId)}?user_id=${encodeURIComponent(state.userId)}`
    );
    const messages = session.messages || [];
    if (messages.length === 0) return;
    dismissWelcome();
    messages.forEach((m) => {
      if (m.role === "user") appendUserBubble(m.text);
      else appendHistoryDJBubble(m.text);
    });
  } catch (error) {
    // 会话已失效或不属于该用户：丢弃，回到全新状态
    state.sessionId = null;
    lsSet(STORE_KEYS.session, null);
  }
}

function startNewConversation() {
  state.sessionId = null;
  state.lastRunId = null;
  state.trackState = {};
  state.trackCache = {};
  lsSet(STORE_KEYS.session, null);
  // 还原欢迎屏并重新绑定示例问题
  stream.innerHTML = WELCOME_HTML;
  const row = $("#suggestion-row");
  if (row) {
    row.addEventListener("click", (event) => {
      const button = event.target.closest(".suggestion");
      if (button) sendMessage(button.dataset.q);
    });
  }
}

async function loadStatus() {
  state.v2 = null;
  try {
    const v2 = await getJSON(`/api/v2/status?user_id=${encodeURIComponent(state.userId)}`);
    if (v2.enabled && v2.known_user) state.v2 = v2;
  } catch (error) {
    state.v2 = null;
  }
  applyMode();
  if (state.v2) {
    const agent = state.v2.agent_model ? "agent" : "规则排序";
    const play = state.v2.playback_lookup ? " · 自动找播放源" : "";
    setStatus(`${agent}${play}`, true);
    return;
  }
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
  state.userId = userIdInput.value.trim() || userIdInput.defaultValue;
  dismissWelcome();
  appendUserBubble(text);
  composerInput.value = "";
  setBusy(true);
  const thinking = appendThinking();

  try {
    const body = state.v2
      ? {
          user_id: state.userId,
          message: text,
          constraints: { limit: state.count },
          exploration_level: state.exploration,
          interleave: state.interleave || undefined,
        }
      : {
          user_id: state.userId,
          message: text,
          session_id: state.sessionId,
          constraints: { limit: 3 },
          include_trace: true,
        };
    const result = await getJSON("/api/v1/agent/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.lastRunId = result.run_id;
    if (!state.v2) {
      state.sessionId = result.session_id;
      lsSet(STORE_KEYS.session, state.sessionId);
    } else {
      state.v2.counts = state.v2.counts || { impressions: 0, feedback: 0 };
      state.v2.counts.impressions += (result.recommendations || []).length;
    }
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

// 恢复历史会话时使用：只重建 DJ 的话术气泡（歌曲卡片数据未持久化，故不重建）
function appendHistoryDJBubble(text) {
  const row = el("div", "msg msg-dj");
  const bubble = el("div", "bubble bubble-dj");
  const head = el("div", "dj-head");
  head.innerHTML = '<span class="dj-avatar">DJ</span>';
  head.appendChild(el("p", "dj-note", text));
  bubble.appendChild(head);
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
  const hasAnswer = Boolean((result.message || "").trim());
  if (recs.length === 0) {
    // 统一为「先回答，再决定是否推歌」后，很多回复本就有意不带歌（纯问答、
    // 解释已推荐、或这次没合适的歌）。只要有文字回答，就不显示「没找到歌」。
    // 仅当既没有回答、也没有歌时，才给一句兜底提示。
    if (!hasAnswer) {
      bubble.appendChild(
        el("p", "dj-empty", "这次没找到合适的内容，换个说法试试？")
      );
    }
  } else {
    const cards = el("div", "track-cards");
    recs.forEach((rec) =>
      cards.appendChild(rec.impression_id ? buildStudyCard(rec) : buildTrackCard(rec))
    );
    bubble.appendChild(cards);

    const actions = el("div", "reply-actions");
    const more = el("button", "chip-button", "换一批");
    more.type = "button";
    // V2：已推荐过的歌会被自动排除，所以直接用同一个请求再要一批
    more.addEventListener("click", () =>
      sendMessage(state.v2 ? query : "换一批，不要重复刚才推荐过的歌")
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
  const reasons = (rec.reasons || []).filter((r) => r && r.text).slice(0, 3);
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
  ["#collection-drawer", "#settings-drawer"].forEach((sel) => {
    $(sel).classList.add("hidden");
    $(sel).setAttribute("aria-hidden", "true");
  });
  $("#drawer-backdrop").classList.add("hidden");
}

async function refreshCollection() {
  if (state.v2) return refreshSaved();
  $("#stat-third-label").textContent = "平均评分";
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

    const info = el("div", "ci-info");
    info.appendChild(el("div", "ci-title", song.title || "未知曲目"));
    const sub = [song.artist, song.album].filter(Boolean).join(" · ");
    info.appendChild(el("div", "ci-sub", sub || "未知艺人"));
    if (Array.isArray(song.genres) && song.genres.length) {
      const tags = el("div", "ci-tags");
      song.genres.slice(0, 3).forEach((g) => tags.appendChild(el("span", "ci-tag", g)));
      info.appendChild(tags);
    }
    item.appendChild(info);

    const removeBtn = el("button", "ci-remove", "✕");
    removeBtn.type = "button";
    removeBtn.title = "从收藏中移除";
    removeBtn.setAttribute("aria-label", "从收藏中移除");
    removeBtn.addEventListener("click", () =>
      removeFromCollection(song.song_id, item, song.title)
    );
    item.appendChild(removeBtn);

    list.appendChild(item);
  });
}

async function removeFromCollection(trackId, itemEl, title) {
  if (!trackId) return;
  try {
    await getJSON(
      `/api/collection/${encodeURIComponent(state.userId)}/${encodeURIComponent(trackId)}`,
      { method: "DELETE" }
    );
    itemEl.remove();
    // 同步卡片上的 ♥ 状态，方便重新收藏
    const ts = state.trackState[trackId];
    if (ts) ts.fav = false;
    document
      .querySelectorAll(`.track-card[data-track-id="${cssEscape(trackId)}"] .fb-button.active`)
      .forEach((b) => {
        if (b.textContent === "♥") b.classList.remove("active");
      });
    toast(`已移除 ${title || "这首歌"}`);
    refreshCollection();
  } catch (error) {
    toast(`移除失败：${error.message}`);
  }
}

function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, "\\$&");
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

// =====================================================================
// 研究流程（V2 用户）
// =====================================================================

const SURVEY_SCALE = [1, 2, 3, 4, 5];
const REJECT_REASONS = [
  ["dislike_song", "不喜欢这首"],
  ["not_in_mood_to_explore", "现在不想听陌生的"],
  ["other", "其他原因"],
];

function applyMode() {
  const v2 = Boolean(state.v2);
  $("#controls").classList.toggle("hidden", !v2);
  $("#open-settings").classList.toggle("hidden", !v2);
  const pill = $("#phase-pill");
  if (v2) {
    const final = state.v2.phase === "final";
    pill.textContent = final ? "正式轮" : "开发轮";
    pill.classList.toggle("final", final);
    pill.classList.remove("hidden");
    if (lsGet(STORE_KEYS.exploration) == null && typeof state.v2.exploration_level === "number") {
      setExploration(state.v2.exploration_level);
    }
    renderSettings();
  } else {
    pill.classList.add("hidden");
  }
}

function initStudyControls() {
  const saved = parseFloat(lsGet(STORE_KEYS.exploration));
  if (!Number.isNaN(saved)) setExploration(saved);
  $("#exploration").addEventListener("input", (e) => {
    setExploration(parseFloat(e.target.value));
    lsSet(STORE_KEYS.exploration, String(state.exploration));
  });
  const savedCount = parseInt(lsGet(STORE_KEYS.count), 10);
  if ([5, 10].includes(savedCount)) {
    state.count = savedCount;
    $("#rec-count").value = String(savedCount);
  }
  $("#rec-count").addEventListener("change", (e) => {
    state.count = parseInt(e.target.value, 10);
    lsSet(STORE_KEYS.count, String(state.count));
  });
  try {
    const pair = JSON.parse(lsGet(STORE_KEYS.interleave) || "null");
    if (Array.isArray(pair) && pair.length === 2) state.interleave = pair;
  } catch (error) {
    state.interleave = null;
  }
  $("#open-settings").addEventListener("click", () => openPanel("#settings-drawer"));
  $("#close-settings").addEventListener("click", () => closePanel("#settings-drawer"));
  $("#arm-a").addEventListener("change", onArmChange);
  $("#arm-b").addEventListener("change", onArmChange);
  armDeleteButton($("#delete-interactions"), "interactions");
  armDeleteButton($("#delete-all"), "all");
}

function setExploration(value) {
  const v = Math.min(1, Math.max(0, Math.round(value * 10) / 10));
  state.exploration = v;
  $("#exploration").value = String(v);
  $("#exploration-value").textContent = v.toFixed(1);
}

function openPanel(sel) {
  $(sel).classList.remove("hidden");
  $(sel).setAttribute("aria-hidden", "false");
  $("#drawer-backdrop").classList.remove("hidden");
  if (sel === "#settings-drawer") refreshStatusCounts();
}

function closePanel(sel) {
  $(sel).classList.add("hidden");
  $(sel).setAttribute("aria-hidden", "true");
  $("#drawer-backdrop").classList.add("hidden");
}

// ---------- 设置面板 ----------
function renderSettings() {
  if (!state.v2) return;
  const final = state.v2.phase === "final";
  $("#settings-phase").textContent = final
    ? "正式轮（final）：这一轮的反馈只用于最终报告。"
    : "开发轮（dev）：用来检查系统是否正常、反馈是否合理。";
  const strategies = state.v2.strategies || [];
  if (state.interleave && !state.interleave.every((s) => strategies.includes(s))) {
    state.interleave = null;
    lsSet(STORE_KEYS.interleave, null);
  }
  [["#arm-a", 0], ["#arm-b", 1]].forEach(([sel, i]) => {
    const select = $(sel);
    select.replaceChildren();
    const off = el("option", null, "不对比");
    off.value = "";
    select.appendChild(off);
    strategies.forEach((name) => {
      const opt = el("option", null, name);
      opt.value = name;
      select.appendChild(opt);
    });
    select.value = state.interleave ? state.interleave[i] : "";
  });
  renderInterleaveState();
  renderCounts();
}

function onArmChange() {
  const a = $("#arm-a").value;
  const b = $("#arm-b").value;
  state.interleave = a && b && a !== b ? [a, b] : null;
  lsSet(STORE_KEYS.interleave, state.interleave ? JSON.stringify(state.interleave) : null);
  renderInterleaveState();
}

function renderInterleaveState() {
  const a = $("#arm-a").value;
  const b = $("#arm-b").value;
  let text = "未开启：每次请求只用默认策略。";
  if (state.interleave) text = "已开启：之后的每次请求都会交错混排这两个策略。";
  else if (a && a === b) text = "两个策略相同，对比未开启。";
  else if (a || b) text = "选两个不同的策略才会开启对比。";
  $("#interleave-state").textContent = text;
}

async function refreshStatusCounts() {
  try {
    const status = await getJSON(`/api/v2/status?user_id=${encodeURIComponent(state.userId)}`);
    if (status.known_user) {
      state.v2 = status;
      renderCounts();
    }
  } catch (error) {
    /* 保留旧数字 */
  }
}

function renderCounts() {
  const c = (state.v2 && state.v2.counts) || { impressions: 0, feedback: 0 };
  $("#settings-counts").textContent = `已记录 ${c.impressions} 次曝光、${c.feedback} 条反馈。`;
}

// 删除按钮：点一次进入确认状态，5 秒内再点一次才真正删除
function armDeleteButton(button, scope) {
  const label = button.textContent;
  let timer = null;
  button.addEventListener("click", async () => {
    if (!button.classList.contains("confirming")) {
      button.classList.add("confirming");
      button.textContent = "再点一次确认删除（不可恢复）";
      timer = setTimeout(() => {
        button.classList.remove("confirming");
        button.textContent = label;
      }, 5000);
      return;
    }
    clearTimeout(timer);
    button.classList.remove("confirming");
    button.textContent = label;
    try {
      const res = await getJSON(`/api/v2/users/${encodeURIComponent(state.userId)}/data`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, confirm: state.userId }),
      });
      const n = Object.values(res.removed || {}).reduce((x, y) => x + y, 0);
      $("#delete-state").textContent = `已删除（${n} 项）。`;
      startNewConversation();
      if (scope === "all") {
        closePanel("#settings-drawer");
        toast("该用户的数据已全部删除");
      }
      await loadStatus();
      refreshCollection();
    } catch (error) {
      $("#delete-state").textContent = `删除失败：${error.message}`;
    }
  });
}

// ---------- 收藏（V2） ----------
async function refreshSaved() {
  try {
    const saved = await getJSON(`/api/v2/users/${encodeURIComponent(state.userId)}/saved`);
    $("#collection-badge").textContent = saved.total || 0;
    $("#stat-collection").textContent = saved.total || 0;
    const counts = (state.v2 && state.v2.counts) || {};
    $("#stat-feedback").textContent = counts.feedback ?? 0;
    $("#stat-reward").textContent = counts.impressions ?? 0;
    $("#stat-third-label").textContent = "曝光";
    const list = $("#collection-list");
    if (!saved.songs.length) {
      list.innerHTML = '<p class="drawer-empty">还没有收藏。点歌曲卡片上的「收藏」就会出现在这里。</p>';
      return;
    }
    list.replaceChildren();
    saved.songs.forEach((song) => {
      const item = el("div", "collection-item");
      const info = el("div", "ci-info");
      info.appendChild(el("div", "ci-title", song.title || "未知曲目"));
      info.appendChild(el("div", "ci-sub", [song.artist, song.album].filter(Boolean).join(" · ")));
      item.appendChild(info);
      if (song.url) {
        const a = el("a", "ci-link", "播放 ↗");
        a.href = song.url;
        a.target = "_blank";
        a.rel = "noopener";
        item.appendChild(a);
      }
      list.appendChild(item);
    });
  } catch (error) {
    $("#collection-badge").textContent = "0";
  }
}

// ---------- 研究卡片 ----------
function buildStudyCard(rec) {
  const track = rec.track || {};
  const card = el("div", "track-card study-card");
  card.dataset.impressionId = rec.impression_id;

  const main = el("div", "track-main");
  const titleWrap = el("div", "track-title-wrap");
  titleWrap.appendChild(el("div", "track-title", track.title || "未知曲目"));
  const sub = [track.artist, track.album, track.release_year].filter(Boolean).join(" · ");
  titleWrap.appendChild(el("div", "track-sub", sub || "未知艺人"));
  main.appendChild(titleWrap);
  const badge = el("span", `source-badge source-${rec.channel === "explore" ? "explore" : rec.source_label === "长尾发现" ? "tail" : "familiar"}`, rec.source_label || "熟悉相关");
  main.appendChild(badge);
  card.appendChild(main);

  if (rec.reason) {
    const reasonWrap = el("div", "track-reasons");
    reasonWrap.appendChild(el("p", "reason", rec.reason));
    // 理由里已经写到的依据不再重复列出
    const evidence = (rec.evidence_items || [])
      .map((e) => e && e.detail)
      .filter((d) => d && !rec.reason.includes(d));
    if (evidence.length) {
      const ul = el("ul", "evidence-list");
      evidence.slice(0, 3).forEach((d) => ul.appendChild(el("li", null, d)));
      reasonWrap.appendChild(ul);
    }
    card.appendChild(reasonWrap);
  }

  card.appendChild(buildPlayRow(rec));
  card.appendChild(buildActions(rec, card));
  card.appendChild(buildSurvey(rec));
  return card;
}

function buildActions(rec, card) {
  const row = el("div", "track-feedback study-actions");
  const like = iconButton("♥", "喜欢", false);
  like.textContent = "♥ 喜欢";
  like.addEventListener("click", () => {
    if (like.classList.contains("active")) return;
    like.classList.add("active");
    sendStudyFeedback(rec, { event: "liked" }, "记下了，你喜欢这首");
  });
  const save = iconButton("＋", "收藏", false);
  save.textContent = "＋ 收藏";
  save.addEventListener("click", () => {
    if (save.classList.contains("active")) return;
    save.classList.add("active");
    sendStudyFeedback(rec, { event: "saved" }, "已收藏").then(refreshSaved);
  });
  const hide = iconButton("⊘", "不要这首", false);
  hide.textContent = "⊘ 不要";
  const reasons = el("div", "reject-reasons hidden");
  REJECT_REASONS.forEach(([value, text]) => {
    const b = el("button", "chip-button", text);
    b.type = "button";
    b.addEventListener("click", () => {
      reasons.classList.add("hidden");
      hide.classList.add("active");
      card.classList.add("skipped");
      sendStudyFeedback(rec, { event: "hide", survey: { reject_reason: value } }, "之后不会再推这首");
    });
    reasons.appendChild(b);
  });
  hide.addEventListener("click", () => {
    if (!hide.classList.contains("active")) reasons.classList.toggle("hidden");
  });
  row.append(like, save, hide, reasons);
  return row;
}

function buildSurvey(rec) {
  const wrap = el("div", "survey");
  wrap.appendChild(
    segmented("听过吗", [["yes", "听过"], ["no", "没听过"], ["unsure", "不确定"]], (v) =>
      sendStudyFeedback(rec, { survey: { heard_before: v } })
    )
  );
  wrap.appendChild(
    segmented("合口味", SURVEY_SCALE.map((n) => [n, String(n)]), (v) =>
      sendStudyFeedback(rec, { survey: { relevance: v } })
    , "1 = 完全不对，5 = 正是我想要的")
  );
  wrap.appendChild(
    segmented("发现价值", SURVEY_SCALE.map((n) => [n, String(n)]), (v) =>
      sendStudyFeedback(rec, { survey: { discovery_value: v } })
    , "1 = 没什么新意，5 = 很高兴发现它")
  );
  const toggles = el("div", "survey-row survey-toggles");
  toggles.appendChild(
    toggle("太陌生了", (on) => sendStudyFeedback(rec, { survey: { too_unfamiliar: on } }))
  );
  toggles.appendChild(
    toggle("愿意收藏", (on) => sendStudyFeedback(rec, { survey: { would_save: on } }))
  );
  wrap.appendChild(toggles);
  return wrap;
}

function segmented(label, options, onPick, hint) {
  const row = el("div", "survey-row");
  const name = el("span", "survey-label", label);
  if (hint) name.title = hint;
  row.appendChild(name);
  const group = el("div", "segmented");
  options.forEach(([value, text]) => {
    const b = el("button", "seg", text);
    b.type = "button";
    b.addEventListener("click", () => {
      if (b.classList.contains("active")) return;
      group.querySelectorAll(".seg").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      onPick(value);
    });
    group.appendChild(b);
  });
  row.appendChild(group);
  return row;
}

function toggle(label, onChange) {
  const b = el("button", "seg toggle", label);
  b.type = "button";
  b.addEventListener("click", () => {
    const on = !b.classList.contains("active");
    b.classList.toggle("active", on);
    onChange(on);
  });
  return b;
}

async function sendStudyFeedback(rec, payload, message) {
  try {
    await getJSON("/api/v1/agent/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: state.userId, impression_id: rec.impression_id, ...payload }),
    });
    if (state.v2) {
      state.v2.counts = state.v2.counts || { impressions: 0, feedback: 0 };
      state.v2.counts.feedback += 1;
    }
    if (message) toast(message);
  } catch (error) {
    toast(`反馈没记上：${error.message}`);
  }
}

// ---------- 播放与收听记录 ----------
// 每个曝光一条收听记录；切到另一首时结算上一首：
//   30 秒内离开且没听完 -> quick_skip；否则 -> play_progress（秒数与比例）；听完 -> completed
const listens = {}; // impression_id -> { rec, started, seconds, duration, completed, pause }
let nowPlaying = null;

function listenFor(rec) {
  if (!listens[rec.impression_id]) {
    listens[rec.impression_id] = { rec, started: false, seconds: 0, duration: 0, completed: false, pause: null };
  }
  return listens[rec.impression_id];
}

function onPlaying(rec, pauseFn) {
  const l = listenFor(rec);
  l.pause = pauseFn;
  if (nowPlaying && nowPlaying !== rec.impression_id) settle(nowPlaying, true);
  nowPlaying = rec.impression_id;
  if (!l.started) {
    l.started = true;
    sendStudyFeedback(rec, { event: "play_start" });
  }
}

function onProgress(rec, seconds, duration) {
  const l = listenFor(rec);
  l.seconds = Math.max(l.seconds, seconds);
  if (duration > 0) l.duration = duration;
  if (!l.completed && l.duration > 0 && l.seconds >= l.duration - 2) {
    l.completed = true;
    sendStudyFeedback(rec, { event: "completed", seconds: l.seconds, fraction: 1 });
  }
}

function settle(impressionId, switching) {
  const l = listens[impressionId];
  if (!l || !l.started || l.completed || l.settled) return;
  if (switching && l.pause) {
    try {
      l.pause();
    } catch (error) {
      /* 播放器可能已被移除 */
    }
  }
  l.settled = true;
  const fraction = l.duration > 0 ? Math.min(1, l.seconds / l.duration) : undefined;
  const event = l.seconds < 30 ? "quick_skip" : "play_progress";
  sendStudyFeedback(l.rec, { event, seconds: Math.round(l.seconds), fraction });
}

// 页面关闭前结算正在播放的那首
window.addEventListener("pagehide", () => {
  if (!nowPlaying) return;
  const l = listens[nowPlaying];
  if (!l || !l.started || l.completed || l.settled) return;
  l.settled = true;
  const body = JSON.stringify({
    user_id: state.userId,
    impression_id: nowPlaying,
    event: l.seconds < 30 ? "quick_skip" : "play_progress",
    seconds: Math.round(l.seconds),
    fraction: l.duration > 0 ? Math.min(1, l.seconds / l.duration) : undefined,
  });
  try {
    navigator.sendBeacon("/api/v1/agent/feedback", new Blob([body], { type: "application/json" }));
  } catch (error) {
    /* 尽力而为 */
  }
});

function buildPlayRow(rec) {
  const row = el("div", "track-play");
  const pb = rec.playback || {};
  const spotifyId = pb.source === "spotify" && pb.url ? (pb.url.match(/track\/([A-Za-z0-9]{22})/) || [])[1] : null;
  const youtubeId = pb.source === "youtube" && pb.url ? (pb.url.match(/[?&]v=([\w-]{11})/) || [])[1] : null;
  if (spotifyId || youtubeId) {
    const btn = el("button", "play-button", spotifyId ? "▶ 试听（Spotify）" : "▶ 播放（YouTube）");
    btn.type = "button";
    const slot = el("div", youtubeId ? "youtube-slot" : "spotify-slot");
    btn.addEventListener("click", () => {
      btn.style.display = "none";
      if (spotifyId) mountStudySpotify(slot, spotifyId, rec);
      else mountYouTube(slot, youtubeId, rec);
    });
    row.append(btn, slot);
    return row;
  }
  row.appendChild(el("span", "no-preview", "暂无核验过的播放源"));
  const links = pb.search_links || {};
  [["youtube", "YouTube 搜索 ↗"], ["spotify", "Spotify 搜索 ↗"]].forEach(([key, text]) => {
    if (!links[key]) return;
    const a = el("a", "play-link", text);
    a.href = links[key];
    a.target = "_blank";
    a.rel = "noopener";
    a.title = "搜索结果未经核验，可能不是同一首";
    row.appendChild(a);
  });
  return row;
}

function mountStudySpotify(container, trackId, rec) {
  const mount = () => {
    container.innerHTML = "";
    spotifyIframeApi.createController(
      container,
      { uri: `spotify:track:${trackId}`, width: "100%", height: 80 },
      (controller) => {
        controller.addListener("playback_update", (e) => {
          const d = e.data || {};
          if (!d.isPaused && !d.isBuffering) onPlaying(rec, () => controller.pause());
          onProgress(rec, (d.position || 0) / 1000, (d.duration || 0) / 1000);
        });
      }
    );
  };
  if (spotifyIframeApi) mount();
  else {
    container.innerHTML = '<div class="spotify-loading">加载播放器…</div>';
    const wait = setInterval(() => {
      if (spotifyIframeApi) {
        clearInterval(wait);
        mount();
      }
    }, 300);
  }
}

function mountYouTube(container, videoId, rec) {
  const mount = () => {
    const target = el("div");
    container.replaceChildren(target);
    let poll = null;
    const player = new YT.Player(target, {
      videoId,
      width: "100%",
      height: 200,
      playerVars: { autoplay: 1, rel: 0, modestbranding: 1 },
      events: {
        onStateChange: (e) => {
          const read = () => onProgress(rec, player.getCurrentTime() || 0, player.getDuration() || 0);
          if (e.data === YT.PlayerState.PLAYING) {
            onPlaying(rec, () => player.pauseVideo());
            clearInterval(poll);
            poll = setInterval(read, 2000);
          } else {
            clearInterval(poll);
            read();
          }
        },
      },
    });
  };
  if (window.YT && window.YT.Player) mount();
  else {
    container.innerHTML = '<div class="spotify-loading">加载播放器…</div>';
    const wait = setInterval(() => {
      if (window.YT && window.YT.Player) {
        clearInterval(wait);
        mount();
      }
    }, 300);
  }
}
