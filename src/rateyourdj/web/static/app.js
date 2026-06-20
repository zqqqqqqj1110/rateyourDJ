const state = {
  userId: "demo-user",
  topK: 10,
  trajectoryId: null,
  sessionId: null,
};

const $ = (selector) => document.querySelector(selector);
const recommendations = $("#recommendations");
const message = $("#message");
const controls = $("#controls");
let activePreview = null;
let spotifyIframeApi = null;
let pendingPreview = null;

window.onSpotifyIframeApiReady = (IFrameAPI) => {
  spotifyIframeApi = IFrameAPI;
  if (pendingPreview) {
    const preview = pendingPreview;
    pendingPreview = null;
    createSpotifyPreview(preview);
  }
};

controls.addEventListener("submit", async (event) => {
  event.preventDefault();
  state.userId = $("#user-id").value.trim();
  state.topK = Number($("#top-k").value);
  state.sessionId = null;
  await loadDashboard();
});

$("#agent-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = $("#agent-query").value.trim();
  if (!query) {
    showMessage("请输入你想听的内容。");
    return;
  }
  state.userId = $("#user-id").value.trim();
  setLoading(true);
  hideMessage();
  try {
    const result = await getJSON(
      "/api/v1/agent/recommend",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: state.userId,
          message: query,
          constraints: { limit: state.topK },
          session_id: state.sessionId,
          include_trace: true,
        }),
      }
    );
    state.trajectoryId = result.run_id;
    state.sessionId = result.session_id;
    $("#agent-response").textContent = result.message;
    $("#agent-response").classList.remove("hidden");
    const trace = result.trace || {};
    const execution =
      trace.agent_mode === "model"
        ? `model ${trace.provider || "configured"}`
        : trace.fallback_reason
          ? "rules fallback"
          : "rules";
    $("#agent-meta").textContent =
      `session ${result.session_id.slice(0, 8)} · run ${result.run_id.slice(0, 8)} · ${execution} · ${result.recommendations.length} 首`;
    renderRecommendations(result);
  } catch (error) {
    showMessage(error.message);
  } finally {
    setLoading(false);
  }
});

async function loadDashboard() {
  if (!state.userId) {
    showMessage("请输入用户 ID。");
    return;
  }
  setLoading(true);
  hideMessage();
  try {
    const [profile, feedback, collection] = await Promise.all([
      getJSON(`/api/profile/${encodeURIComponent(state.userId)}`),
      getJSON(`/api/feedback/${encodeURIComponent(state.userId)}`),
      getJSON(`/api/collection/${encodeURIComponent(state.userId)}`),
    ]);
    renderProfile(profile, feedback);
    renderCollection(collection);
    recommendations.replaceChildren();
    hideAgentDebug();
    $("#result-meta").textContent = "等待 agent 推荐";
    state.trajectoryId = null;
  } catch (error) {
    $("#collection").replaceChildren();
    recommendations.replaceChildren();
    showMessage(error.message);
  } finally {
    setLoading(false);
  }
}

async function refreshMemoryPanels() {
  const [profile, feedback, collection] = await Promise.all([
    getJSON(`/api/profile/${encodeURIComponent(state.userId)}`),
    getJSON(`/api/feedback/${encodeURIComponent(state.userId)}`),
    getJSON(`/api/collection/${encodeURIComponent(state.userId)}`),
  ]);
  renderProfile(profile, feedback);
  renderCollection(collection);
}

function renderProfile(profile, feedback) {
  $("#collection-count").textContent = profile.collection_count;
  $("#feedback-count").textContent = feedback.total_events;
  $("#average-reward").textContent = feedback.average_reward.toFixed(2);
  $("#profile-version").textContent = `v${profile.version}`;
  renderChips("#top-artists", profile.top_artists);
  renderChips("#top-genres", profile.top_genres);

  const total = feedback.positive_events + feedback.negative_events;
  const positive = total ? (feedback.positive_events / total) * 100 : 0;
  const negative = total ? (feedback.negative_events / total) * 100 : 0;
  $("#positive-bar").style.width = `${positive}%`;
  $("#negative-bar").style.width = `${negative}%`;
  $("#positive-count").textContent = feedback.positive_events;
  $("#negative-count").textContent = feedback.negative_events;
}

function renderChips(selector, items) {
  const container = $(selector);
  container.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("span");
    empty.className = "chip";
    empty.textContent = "暂无数据";
    container.append(empty);
    return;
  }
  for (const item of items) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${item.name} · ${item.weight.toFixed(2)}`;
    container.append(chip);
  }
}

function renderCollection(result) {
  const collection = $("#collection");
  collection.replaceChildren();
  const missingSongIds = Array.isArray(result.missing_song_ids)
    ? result.missing_song_ids
    : [];
  const missing = missingSongIds.length;
  $("#collection-meta").textContent = missing
    ? `${result.total} 首可显示 · ${missing} 首画像缺失`
    : `${result.total} 首歌曲`;

  if (!result.songs.length) {
    const empty = document.createElement("p");
    empty.className = "collection-empty";
    empty.textContent = "还没有收藏歌曲。";
    collection.append(empty);
    return;
  }

  for (const song of result.songs) {
    const node = $("#collection-template").content.cloneNode(true);
    node.querySelector(".collection-title").textContent =
      song.title || song.song_id;
    node.querySelector(".collection-artist").textContent =
      song.artist || "未知歌手";
    node.querySelector(".collection-album").textContent =
      song.album || "未知专辑";

    if (song.added_via_feedback) {
      node.querySelector(".favorite-label").classList.remove("hidden");
    }

    const genres = node.querySelector(".collection-genres");
    for (const genre of song.genres) {
      const item = document.createElement("span");
      item.textContent = genre.replaceAll("_", " ");
      genres.append(item);
    }
    collection.append(node);
  }
}

function renderRecommendations(result) {
  closeActivePreview();
  recommendations.replaceChildren();
  const songs = normalizeRecommendations(result);
  const trace = result.trace || {};
  renderAgentDebug(trace);
  const seedSongIds = Array.isArray(trace.seed_song_ids)
    ? trace.seed_song_ids
    : Array.isArray(result.seed_song_ids)
      ? result.seed_song_ids
      : [];
  const resultLabel = Array.isArray(result.recommendations)
    ? "agent 推荐"
    : "基础推荐";
  $("#result-meta").textContent =
    `${songs.length} 首 ${resultLabel} · ${seedSongIds.length} 首种子`;

  if (!songs.length) {
    const stopReason = trace.stop_reason || "";
    if (result.message) {
      showMessage(result.message);
    } else if (stopReason === "external_search_failed") {
      showMessage("外部音乐搜索失败，请稍后重试。");
    } else if (stopReason === "empty_profile") {
      showMessage("收藏中还没有可用的种子歌曲，暂时无法生成推荐。");
    } else {
      showMessage("当前没有可推荐歌曲。请放宽条件或换一个搜索描述。");
    }
    return;
  }

  for (const song of songs) {
    const node = $("#track-template").content.cloneNode(true);
    const card = node.querySelector(".track-card");
    card.dataset.songId = song.songId;
    node.querySelector(".rank-number").textContent =
      String(song.rank).padStart(2, "0");
    node.querySelector(".track-title").textContent = song.title || song.songId;
    node.querySelector(".track-artist").textContent = song.artist || "未知歌手";
    node.querySelector(".track-album").textContent = song.album || "未知专辑";
    node.querySelector(".score-badge strong").textContent =
      Number(song.score || 0).toFixed(3);

    const reasonList = node.querySelector(".reason-list");
    for (const reason of song.reasons) {
      const item = document.createElement("span");
      item.className = "reason";
      item.textContent = reason.text;
      reasonList.append(item);
    }

    const evidenceList = node.querySelector(".evidence-list");
    renderEvidence(evidenceList, song.evidence);
    node.querySelector(".ai-answer-text").textContent = buildAiAnswer(song);

    const breakdown = node.querySelector(".score-breakdown");
    for (const [name, value] of Object.entries(song.scoreBreakdown)) {
      const item = document.createElement("div");
      item.className = "score-item";
      item.innerHTML = `<span>${name}</span><b>${Number(value).toFixed(3)}</b>`;
      breakdown.append(item);
    }

    for (const button of node.querySelectorAll("[data-feedback]")) {
      button.addEventListener("click", () =>
        sendFeedback(card, song, button.dataset.feedback)
      );
    }
    const previewButton = node.querySelector("[data-preview]");
    if (song.previewAvailable && song.spotifyEmbedUrl && song.spotifyTrackId) {
      previewButton.classList.remove("hidden");
      previewButton.addEventListener("click", () =>
        toggleSpotifyPreview(card, song, previewButton)
      );
    }
    recommendations.append(node);
  }
}

function renderAgentDebug(trace) {
  const panel = $("#agent-debug-panel");
  if (!trace || typeof trace !== "object") {
    hideAgentDebug();
    return;
  }
  const parsedRequest =
    trace.parsed_request && typeof trace.parsed_request === "object"
      ? trace.parsed_request
      : {};
  const anchorArtists = Array.isArray(parsedRequest.reference_artists)
    ? parsedRequest.reference_artists.filter(Boolean)
    : [];
  const toolCalls = Array.isArray(trace.tool_calls) ? trace.tool_calls : [];
  const similarArtistsCall = toolCalls.find(
    (call) => call && call.tool === "get_similar_artists"
  );
  const searchCall = [...toolCalls]
    .reverse()
    .find((call) => call && call.tool === "search_tracks");
  const expandedArtists = normalizeExpandedArtists(similarArtistsCall);
  const finalSearchQuery =
    searchCall &&
    searchCall.arguments &&
    typeof searchCall.arguments.query === "string"
      ? searchCall.arguments.query
      : "";
  const searchPlan = normalizeSearchPlan(toolCalls);

  if (
    !anchorArtists.length &&
    !expandedArtists.length &&
    !finalSearchQuery &&
    !searchPlan.length
  ) {
    hideAgentDebug();
    return;
  }

  $("#debug-anchor-artists").textContent = anchorArtists.length
    ? anchorArtists.join(", ")
    : "—";
  $("#debug-expanded-artists").textContent = expandedArtists.length
    ? expandedArtists.join(", ")
    : "—";
  $("#debug-search-query").textContent = finalSearchQuery || "—";
  $("#debug-search-plan").textContent = searchPlan.length
    ? searchPlan.join(" | ")
    : "—";
  panel.classList.remove("hidden");
}

function normalizeExpandedArtists(similarArtistsCall) {
  if (
    !similarArtistsCall ||
    !similarArtistsCall.observation ||
    !similarArtistsCall.observation.data
  ) {
    return [];
  }
  const artists = Array.isArray(similarArtistsCall.observation.data.artists)
    ? similarArtistsCall.observation.data.artists
    : [];
  const names = [];
  for (const item of artists) {
    if (!item || typeof item.name !== "string" || !item.name.trim()) {
      continue;
    }
    const normalized = item.name.trim();
    if (
      !names.some((existing) => existing.toLowerCase() === normalized.toLowerCase())
    ) {
      names.push(normalized);
    }
  }
  return names;
}

function hideAgentDebug() {
  $("#agent-debug-panel").classList.add("hidden");
  $("#debug-anchor-artists").textContent = "-";
  $("#debug-expanded-artists").textContent = "-";
  $("#debug-search-query").textContent = "-";
  $("#debug-search-plan").textContent = "-";
}

function normalizeSearchPlan(toolCalls) {
  const searchCalls = Array.isArray(toolCalls)
    ? toolCalls.filter((call) => call && call.tool === "search_tracks")
    : [];
  return searchCalls.map((call) => {
    const argumentsObject =
      call.arguments && typeof call.arguments === "object"
        ? call.arguments
        : {};
    const tier =
      typeof argumentsObject.search_tier === "string" &&
      argumentsObject.search_tier
        ? argumentsObject.search_tier
        : "search";
    const query =
      typeof argumentsObject.query === "string" ? argumentsObject.query : "";
    const anchors = Array.isArray(argumentsObject.anchor_artists)
      ? argumentsObject.anchor_artists.filter(Boolean)
      : [];
    const expanded = Array.isArray(argumentsObject.expanded_artists)
      ? argumentsObject.expanded_artists.filter(Boolean)
      : [];
    const prefix = anchors.length
      ? `${tier}[${anchors.join(", ")}]`
      : tier;
    const expandedSuffix = expanded.length
      ? ` -> ${expanded.join(", ")}`
      : "";
    return `${prefix}${expandedSuffix}: ${query}`;
  });
}

function normalizeRecommendations(result) {
  if (Array.isArray(result.recommendations)) {
    return result.recommendations.map((item) => {
      const track = item.track || {};
      const evidence = item.evidence || {};
      return {
        rank: item.rank,
        songId: track.track_id,
        title: track.title,
        artist: track.artist,
        album: track.album,
        score: item.score,
        scoreBreakdown: evidence.score_breakdown || {},
        reasons: normalizeReasons(item.reasons),
        evidence,
        spotifyTrackId: track.external_ids?.spotify_track_id,
        spotifyEmbedUrl: track.embed_urls?.spotify,
        previewAvailable: Boolean(track.preview_available),
      };
    });
  }

  const rankedSongs = Array.isArray(result.ranked_songs)
    ? result.ranked_songs
    : [];
  return rankedSongs.map((song) => ({
    rank: song.rank,
    songId: song.song_id,
    title: song.title,
    artist: song.artist,
    album: null,
    score: song.final_score,
    scoreBreakdown: song.score_breakdown || {},
    reasons: normalizeReasons(
      (song.ranking_reasons || []).map((reason) => ({
        type: "ranking",
        text: translateReason(reason),
      }))
    ),
    evidence: {
      ranking_reasons: song.ranking_reasons || [],
      best_seed_song_id: song.best_seed_song_id,
      retrieval_sources: song.retrieval_sources || [],
    },
    spotifyTrackId: song.spotify_track_id,
    spotifyEmbedUrl: song.spotify_embed_url,
    previewAvailable: Boolean(song.preview_available),
  }));
}

function normalizeReasons(reasons) {
  if (!Array.isArray(reasons) || !reasons.length) {
    return [{ type: "ranking", text: "根据当前画像和会话上下文选出。" }];
  }
  return reasons
    .map((reason) =>
      typeof reason === "string"
        ? { type: "ranking", text: translateReason(reason) }
        : {
            type: reason.type || "ranking",
            text: translateReason(reason.text || ""),
          }
    )
    .filter((reason) => reason.text);
}

function renderEvidence(container, evidence) {
  container.replaceChildren();
  const items = [];
  if (evidence.best_seed_song_id) {
    items.push(["参考种子", evidence.best_seed_song_id]);
  }
  if (
    Array.isArray(evidence.retrieval_sources) &&
    evidence.retrieval_sources.length
  ) {
    items.push(["召回来源", evidence.retrieval_sources.slice(0, 2).join(", ")]);
  }
  if (
    Array.isArray(evidence.preference_terms) &&
    evidence.preference_terms.length
  ) {
    items.push(["请求偏好", evidence.preference_terms.slice(0, 4).join(", ")]);
  }
  if (!items.length) {
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");
  for (const [label, value] of items) {
    const item = document.createElement("span");
    const labelNode = document.createElement("b");
    labelNode.textContent = label;
    item.append(labelNode, String(value));
    container.append(item);
  }
}

function buildAiAnswer(song) {
  const parts = [];
  const mainReason = song.reasons[0]?.text;
  if (mainReason) {
    parts.push(mainReason);
  }
  if (song.album && song.album !== "未知专辑") {
    parts.push(`它来自《${song.album}》`);
  }
  if (song.evidence?.preference_terms?.length) {
    parts.push(
      `和你这次提到的 ${song.evidence.preference_terms.slice(0, 3).join("、")} 更接近`
    );
  }
  if (song.evidence?.retrieval_sources?.length) {
    const source = song.evidence.retrieval_sources[0];
    parts.push(
      source.includes("search")
        ? "这首歌是 agent 通过外部音乐搜索工具找到的"
        : "这首歌来自当前候选召回"
    );
  }
  if (!parts.length) {
    return "我选择这首歌，是因为它在当前偏好、会话上下文和可用证据中匹配度最高。";
  }
  return `${parts.join("；")}。`;
}

function toggleSpotifyPreview(card, song, button) {
  const player = card.querySelector(".spotify-player");
  const mount = player.querySelector(".spotify-embed-mount");
  const isActive = activePreview && activePreview.card === card;
  closeActivePreview();
  if (isActive) {
    return;
  }
  player.classList.remove("hidden");
  button.textContent = "收起试听";
  button.classList.add("is-playing");
  const preview = {
    card,
    player,
    mount,
    button,
    song,
    controller: null,
    playbackStarted: false,
    playRecorded: false,
    completeRecorded: false,
    quickSkipRecorded: false,
    maxPosition: 0,
    duration: 0,
  };
  activePreview = preview;
  if (spotifyIframeApi) {
    createSpotifyPreview(preview);
  } else {
    mount.textContent = "Spotify 播放器载入中…";
    pendingPreview = preview;
  }
}

function createSpotifyPreview(preview) {
  if (activePreview !== preview || !spotifyIframeApi) {
    return;
  }
  preview.mount.replaceChildren();
  spotifyIframeApi.createController(
    preview.mount,
    {
      width: "100%",
      height: 152,
      uri: `spotify:track:${preview.song.spotifyTrackId}`,
    },
    (controller) => {
      if (activePreview !== preview) {
        controller.destroy();
        return;
      }
      preview.controller = controller;
      controller.addListener("playback_started", () => {
        preview.playbackStarted = true;
        if (!preview.playRecorded) {
          preview.playRecorded = true;
          void recordPlaybackFeedback(preview, "play");
        }
      });
      controller.addListener("playback_update", (event) => {
        const position = Number(event.data?.position || 0);
        const duration = Number(event.data?.duration || 0);
        preview.maxPosition = Math.max(preview.maxPosition, position);
        preview.duration = Math.max(preview.duration, duration);
        if (
          !preview.completeRecorded &&
          duration > 0 &&
          position >= duration * 0.9
        ) {
          preview.completeRecorded = true;
          void recordPlaybackFeedback(preview, "play_complete");
        }
      });
    }
  );
}

function closeActivePreview() {
  if (!activePreview) {
    return;
  }
  const preview = activePreview;
  if (
    preview.playbackStarted &&
    !preview.completeRecorded &&
    !preview.quickSkipRecorded &&
    preview.maxPosition < 15000
  ) {
    preview.quickSkipRecorded = true;
    void recordPlaybackFeedback(preview, "quick_skip");
  }
  if (preview.controller) {
    preview.controller.destroy();
  }
  const mount = document.createElement("div");
  mount.className = "spotify-embed-mount";
  preview.player.replaceChildren(mount);
  preview.player.classList.add("hidden");
  preview.button.textContent = "试听";
  preview.button.classList.remove("is-playing");
  if (pendingPreview === preview) {
    pendingPreview = null;
  }
  activePreview = null;
}

async function recordPlaybackFeedback(preview, feedbackType) {
  try {
    await getJSON(`/api/feedback/${encodeURIComponent(state.userId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        song_id: preview.song.songId,
        feedback_type: feedbackType,
        recommendation_context: {
          rank: preview.song.rank,
          final_score: preview.song.score,
          source: "spotify_embed",
          trajectory_id: state.trajectoryId,
          playback_position_ms: Math.round(preview.maxPosition),
          playback_duration_ms: Math.round(preview.duration),
        },
      }),
    });
  } catch (error) {
    console.warn(`无法记录 ${feedbackType} 试听事件`, error);
  }
}

async function sendFeedback(card, song, feedbackType) {
  const buttons = card.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  hideMessage();
  const context = {
    rank: song.rank,
    final_score: song.score,
    source: "web",
    trajectory_id: state.trajectoryId,
  };
  if (["favorite", "playlist_add"].includes(feedbackType)) {
    context.track = {
      title: song.title,
      artist: song.artist,
      album: song.album,
      spotify_track_id: song.spotifyTrackId,
    };
  }
  try {
    await getJSON(`/api/feedback/${encodeURIComponent(state.userId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        song_id: song.songId,
        feedback_type: feedbackType,
        recommendation_context: context,
      }),
    });
    card.classList.add("feedback-sent");
    if (["favorite", "playlist_add"].includes(feedbackType)) {
      const favoriteButton = card.querySelector('[data-feedback="favorite"]');
      if (favoriteButton) {
        favoriteButton.textContent = "已收藏";
        favoriteButton.disabled = true;
      }
    }
    await refreshMemoryPanels();
    buttons.forEach((button) => {
      if (
        !["favorite", "playlist_add"].includes(feedbackType) ||
        button.dataset.feedback !== "favorite"
      ) {
        button.disabled = false;
      }
    });
  } catch (error) {
    showMessage(error.message);
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function getJSON(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `请求失败 (${response.status})`);
  }
  return payload;
}

function setLoading(value) {
  document.body.classList.toggle("loading", value);
  controls.querySelector("button").disabled = value;
}

function showMessage(text) {
  message.textContent = text;
  message.classList.remove("hidden");
}

function hideMessage() {
  message.classList.add("hidden");
}

function translateReason(reason) {
  if (reason.startsWith("Matches the current request preferences: ")) {
    return reason.replace(
      "Matches the current request preferences: ",
      "匹配本次请求偏好："
    );
  }
  const reasons = {
    "strong similarity to the collection seeds": "与收藏高度相似",
    "retrieved from collection-level song similarity": "收藏相似度召回",
    "matches a preferred artist": "匹配偏好歌手",
    "matches the collection genre profile": "匹配流派画像",
    "matches the collection tag profile": "匹配标签画像",
    "high-confidence song profile": "高置信歌曲画像",
    "promoted by positive feedback": "正反馈提升",
    "penalized by negative feedback": "负反馈降低",
    "penalized for similarity to higher-ranked songs": "多样性调整",
    "selected from the L3 candidate pool": "来自候选池",
    "found by external music provider search": "由外部音乐搜索工具找到",
    "matches the current listening request": "匹配当前收听请求",
    "matches user music profile": "匹配用户音乐画像",
    "has enough metadata for explanation": "具备可解释的歌曲元数据",
  };
  return reasons[reason] || reason;
}

loadDashboard();
