// Hearletter web player: wires the pure helpers in lib.js to the page and the <audio> element.

import {
  SKIP_BACK_SECONDS,
  SKIP_FORWARD_SECONDS,
  audioUrl,
  createProgressStore,
  episodesJsonUrl,
  formatEpisodeDate,
  formatPlaybackRate,
  formatTime,
  isFinished,
  locationHash,
  nextPlaybackRate,
  parseEpisodes,
  parseLocationHash,
  pickEpisode,
  resumePosition,
} from "./lib.js";

const SAVE_EVERY_SECONDS = 5;

const byId = (id) => document.getElementById(id);
const ui = {
  status: byId("status"),
  nowPlaying: byId("now-playing"),
  date: byId("episode-date"),
  title: byId("episode-title"),
  summary: byId("episode-summary"),
  seek: byId("seek"),
  elapsed: byId("elapsed"),
  remaining: byId("remaining"),
  back: byId("back"),
  play: byId("play"),
  forward: byId("forward"),
  rate: byId("rate"),
  list: byId("episodes"),
  audio: byId("audio"),
};

const store = createProgressStore(localStorageOrNull());
const player = { feed: null, episodes: [], current: null, lastSavedAt: 0, isSeeking: false };

function localStorageOrNull() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function showStatus(message) {
  ui.status.textContent = message;
  ui.status.hidden = false;
}

async function loadEpisodes(feed) {
  const response = await fetch(episodesJsonUrl(feed, window.location.href), { cache: "no-cache" });
  if (!response.ok) {
    throw new Error(`episodes.json request failed with HTTP ${response.status}`);
  }
  return parseEpisodes(await response.json());
}

function renderList() {
  const items = player.episodes.map((episode) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = store.isPlayed(episode.episodeId) ? "episode played" : "episode";
    button.setAttribute("aria-current", String(episode === player.current));

    const name = document.createElement("span");
    name.className = "episode-name";
    name.textContent = episode.title;
    const meta = document.createElement("span");
    meta.className = "episode-meta";
    meta.textContent = store.isPlayed(episode.episodeId)
      ? `${formatEpisodeDate(episode.episodeDate)} · played`
      : formatEpisodeDate(episode.episodeDate);

    button.append(name, meta);
    button.addEventListener("click", () => selectEpisode(episode, { autoplay: true }));

    const item = document.createElement("li");
    item.append(button);
    return item;
  });
  ui.list.replaceChildren(...items);
}

function selectEpisode(episode, { autoplay }) {
  if (player.current && player.current !== episode) {
    saveProgress(true);
  }
  player.current = episode;
  ui.audio.src = audioUrl(episode.audioPath, window.location.href);
  ui.audio.playbackRate = store.playbackRate();

  ui.date.textContent = formatEpisodeDate(episode.episodeDate);
  ui.title.textContent = episode.title;
  ui.summary.textContent = episode.summary;
  ui.nowPlaying.hidden = false;
  document.title = `${episode.title} · Hearletter FM`;
  window.history.replaceState(null, "", locationHash(player.feed, episode.episodeId));

  updateMediaSession(episode);
  renderList();
  if (autoplay) {
    play();
  }
}

function play() {
  ui.audio.play().catch((error) => {
    // Browsers can refuse playback until the user taps; the play button stays available.
    console.warn("Playback did not start:", error);
  });
}

function seekBy(seconds) {
  const duration = Number.isFinite(ui.audio.duration) ? ui.audio.duration : Infinity;
  ui.audio.currentTime = Math.min(Math.max(ui.audio.currentTime + seconds, 0), duration);
}

function saveProgress(force = false) {
  const { current } = player;
  if (!current) return;
  const now = ui.audio.currentTime;
  if (!force && Math.abs(now - player.lastSavedAt) < SAVE_EVERY_SECONDS) return;
  player.lastSavedAt = now;
  if (isFinished(now, ui.audio.duration)) {
    if (!store.isPlayed(current.episodeId)) {
      store.markPlayed(current.episodeId);
      renderList();
    }
  } else {
    store.savePosition(current.episodeId, now);
  }
}

function updateTimes() {
  const { currentTime, duration } = ui.audio;
  if (!player.isSeeking) {
    ui.seek.value = String(Math.floor(currentTime));
  }
  ui.elapsed.textContent = formatTime(currentTime);
  ui.remaining.textContent = `-${formatTime((Number.isFinite(duration) ? duration : 0) - currentTime)}`;
}

function updateMediaSession(episode) {
  if (!("mediaSession" in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: episode.title,
    artist: "Hearletter FM",
    album: formatEpisodeDate(episode.episodeDate),
  });
}

function neighbour(offset) {
  const index = player.episodes.indexOf(player.current);
  return player.episodes[index + offset] ?? null;
}

function registerMediaSessionHandlers() {
  if (!("mediaSession" in navigator)) return;
  const handlers = {
    play: () => play(),
    pause: () => ui.audio.pause(),
    seekbackward: (details) => seekBy(-(details.seekOffset || SKIP_BACK_SECONDS)),
    seekforward: (details) => seekBy(details.seekOffset || SKIP_FORWARD_SECONDS),
    seekto: (details) => {
      ui.audio.currentTime = details.seekTime;
    },
    // The list is newest first: "previous" is the newer episode, "next" the older one.
    previoustrack: () => {
      const newer = neighbour(-1);
      if (newer) selectEpisode(newer, { autoplay: true });
    },
    nexttrack: () => {
      const older = neighbour(1);
      if (older) selectEpisode(older, { autoplay: true });
    },
  };
  for (const [action, handler] of Object.entries(handlers)) {
    try {
      navigator.mediaSession.setActionHandler(action, handler);
    } catch {
      // Older browsers reject actions they don't support; the on-page controls still work.
    }
  }
}

function bindControls() {
  ui.play.addEventListener("click", () => (ui.audio.paused ? play() : ui.audio.pause()));
  ui.back.addEventListener("click", () => seekBy(-SKIP_BACK_SECONDS));
  ui.forward.addEventListener("click", () => seekBy(SKIP_FORWARD_SECONDS));
  ui.rate.addEventListener("click", () => {
    const rate = nextPlaybackRate(ui.audio.playbackRate);
    ui.audio.playbackRate = rate;
    store.savePlaybackRate(rate);
  });

  ui.seek.addEventListener("input", () => {
    player.isSeeking = true;
    ui.elapsed.textContent = formatTime(Number(ui.seek.value));
  });
  ui.seek.addEventListener("change", () => {
    ui.audio.currentTime = Number(ui.seek.value);
    player.isSeeking = false;
  });

  ui.audio.addEventListener("loadedmetadata", () => {
    ui.seek.max = String(Math.floor(ui.audio.duration));
    ui.audio.currentTime = resumePosition(store.position(player.current.episodeId), ui.audio.duration);
    ui.audio.playbackRate = store.playbackRate();
    player.lastSavedAt = ui.audio.currentTime;
    updateTimes();
  });
  ui.audio.addEventListener("timeupdate", () => {
    updateTimes();
    saveProgress();
  });
  ui.audio.addEventListener("ratechange", () => {
    ui.rate.textContent = formatPlaybackRate(ui.audio.playbackRate);
  });
  ui.audio.addEventListener("play", () => {
    ui.play.setAttribute("aria-pressed", "true");
    ui.play.setAttribute("aria-label", "Pause");
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "playing";
  });
  ui.audio.addEventListener("pause", () => {
    ui.play.setAttribute("aria-pressed", "false");
    ui.play.setAttribute("aria-label", "Play");
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "paused";
    saveProgress(true);
  });
  ui.audio.addEventListener("ended", () => saveProgress(true));
  ui.audio.addEventListener("error", () => {
    showStatus("This episode couldn't be loaded. It may be older than a week.");
  });

  // Phones suspend pages without warning: persist the spot whenever the page is hidden.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") saveProgress(true);
  });
  window.addEventListener("pagehide", () => saveProgress(true));
}

async function main() {
  const { feed, episodeId } = parseLocationHash(window.location.hash);
  if (!feed) {
    showStatus("This link is missing its feed. Open the player from your episode email.");
    return;
  }
  player.feed = feed;
  bindControls();
  registerMediaSessionHandlers();

  try {
    player.episodes = await loadEpisodes(feed);
  } catch (error) {
    console.error(error);
    showStatus("Couldn't load your episodes. Check your connection and try again.");
    return;
  }
  if (player.episodes.length === 0) {
    showStatus("No episodes in the last week yet.");
    return;
  }
  selectEpisode(pickEpisode(player.episodes, episodeId), { autoplay: false });
}

main();
