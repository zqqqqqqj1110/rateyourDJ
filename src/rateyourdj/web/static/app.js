const state = { userId: "demo-user", topK: 10 };

const $ = (selector) => document.querySelector(selector);
const recommendations = $("#recommendations");
const message = $("#message");
const controls = $("#controls");

controls.addEventListener("submit", async (event) => {
  event.preventDefault();
  state.userId = $("#user-id").value.trim();
  state.topK = Number($("#top-k").value);
  await loadDashboard();
});

async function loadDashboard() {
  if (!state.userId) {
    showMessage("请输入用户 ID。");
    return;
  }
  setLoading(true);
  hideMessage();
  try {
    const [profile, feedback, collection, ranking] = await Promise.all([
      getJSON(`/api/profile/${encodeURIComponent(state.userId)}`),
      getJSON(`/api/feedback/${encodeURIComponent(state.userId)}`),
      getJSON(`/api/collection/${encodeURIComponent(state.userId)}`),
      getJSON(`/api/recommendations/${encodeURIComponent(state.userId)}?top_k=${state.topK}`),
    ]);
    renderProfile(profile, feedback);
    renderCollection(collection);
    renderRecommendations(ranking);
  } catch (error) {
    $("#collection").replaceChildren();
    recommendations.replaceChildren();
    showMessage(error.message);
  } finally {
    setLoading(false);
  }
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
  $("#collection-meta").textContent = `${result.total} 首歌曲`;

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
  recommendations.replaceChildren();
  $("#result-meta").textContent =
    `${result.ranked_songs.length} 首推荐 · ${result.seed_song_ids.length} 首种子`;

  if (!result.ranked_songs.length) {
    showMessage("当前没有可推荐歌曲。请检查收藏种子和候选库。");
    return;
  }

  for (const song of result.ranked_songs) {
    const node = $("#track-template").content.cloneNode(true);
    const card = node.querySelector(".track-card");
    card.dataset.songId = song.song_id;
    node.querySelector(".rank-number").textContent =
      String(song.rank).padStart(2, "0");
    node.querySelector(".track-title").textContent = song.title || song.song_id;
    node.querySelector(".track-artist").textContent = song.artist || "未知歌手";
    node.querySelector(".score-badge strong").textContent =
      song.final_score.toFixed(3);

    const reasonList = node.querySelector(".reason-list");
    for (const reason of song.ranking_reasons) {
      const item = document.createElement("span");
      item.className = "reason";
      item.textContent = translateReason(reason);
      reasonList.append(item);
    }

    const breakdown = node.querySelector(".score-breakdown");
    for (const [name, value] of Object.entries(song.score_breakdown)) {
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
    recommendations.append(node);
  }
}

async function sendFeedback(card, song, feedbackType) {
  const buttons = card.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  hideMessage();
  try {
    await getJSON(`/api/feedback/${encodeURIComponent(state.userId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        song_id: song.song_id,
        feedback_type: feedbackType,
        recommendation_context: {
          rank: song.rank,
          final_score: song.final_score,
          source: "web",
        },
      }),
    });
    card.classList.add("feedback-sent");
    await loadDashboard();
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
  };
  return reasons[reason] || reason;
}

loadDashboard();
