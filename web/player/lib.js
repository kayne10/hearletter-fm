// Pure helpers for the Hearletter web player. No DOM access, so they run under `node --test`.

export const PLAYBACK_RATES = [1, 1.25, 1.5, 1.75, 2];
export const SKIP_BACK_SECONDS = 15;
export const SKIP_FORWARD_SECONDS = 30;
export const PLAYED_WHEN_SECONDS_LEFT = 20;

const MIN_RESUME_SECONDS = 5;
const MAX_REMEMBERED_EPISODES = 60;
const STORAGE_KEY = "hearletter.player.v1";
const FEED_PATTERN = /^[a-z0-9][a-z0-9._-]*$/;
const EPISODE_ID_PATTERN = /^ep_[A-Za-z0-9_]+$/;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const AUDIO_PATH_PATTERN = /^episodes\/[a-z0-9._-]+\/\d{4}-\d{2}-\d{2}\/[A-Za-z0-9_.-]+\.mp3$/;

// "#<feed>/<episode_id>" -> { feed, episodeId }; anything malformed becomes null.
export function parseLocationHash(hash) {
  const [feed = "", episodeId = ""] = hash.replace(/^#/, "").split("/");
  return {
    feed: FEED_PATTERN.test(feed) && !feed.includes("..") ? feed : null,
    episodeId: EPISODE_ID_PATTERN.test(episodeId) ? episodeId : null,
  };
}

export const locationHash = (feed, episodeId) => `#${feed}/${episodeId}`;

// The page lives at <base>/player/, so feeds and episodes are siblings one level up. Resolving
// relative URLs keeps the secret path token out of episodes.json.
export const episodesJsonUrl = (feed, pageUrl) =>
  new URL(`../feeds/${feed}/episodes.json`, pageUrl).href;

export const audioUrl = (audioPath, pageUrl) => new URL(`../${audioPath}`, pageUrl).href;

// Validate the untrusted episodes.json payload; drop entries that don't match the contract.
export function parseEpisodes(payload) {
  const items = Array.isArray(payload?.episodes) ? payload.episodes : [];
  const episodes = items.filter(isValidEpisode).map((item) => ({
    episodeId: item.episode_id,
    title: item.title,
    episodeDate: item.episode_date,
    audioPath: item.audio_path,
    summary: typeof item.summary === "string" ? item.summary : "",
  }));
  return episodes.sort((a, b) => b.episodeDate.localeCompare(a.episodeDate));
}

function isValidEpisode(item) {
  return (
    typeof item === "object" &&
    item !== null &&
    typeof item.episode_id === "string" &&
    EPISODE_ID_PATTERN.test(item.episode_id) &&
    typeof item.title === "string" &&
    item.title.length > 0 &&
    typeof item.episode_date === "string" &&
    DATE_PATTERN.test(item.episode_date) &&
    typeof item.audio_path === "string" &&
    AUDIO_PATH_PATTERN.test(item.audio_path)
  );
}

export function pickEpisode(episodes, requestedId) {
  return episodes.find((episode) => episode.episodeId === requestedId) ?? episodes[0] ?? null;
}

export function formatTime(totalSeconds) {
  if (!Number.isFinite(totalSeconds) || totalSeconds < 0) return "0:00";
  const seconds = Math.floor(totalSeconds);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = String(seconds % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

// Episode dates are calendar days, so format in UTC to avoid shifting to the previous day.
export function formatEpisodeDate(isoDate) {
  return new Date(`${isoDate}T12:00:00Z`).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function nextPlaybackRate(rate) {
  const index = PLAYBACK_RATES.indexOf(rate);
  return index === -1 ? PLAYBACK_RATES[0] : PLAYBACK_RATES[(index + 1) % PLAYBACK_RATES.length];
}

export const formatPlaybackRate = (rate) => `${rate}×`;

export function isFinished(currentTime, duration) {
  return Number.isFinite(duration) && duration > 0 && duration - currentTime <= PLAYED_WHEN_SECONDS_LEFT;
}

// Where to start: the saved spot, unless it's negligible or the episode was nearly done.
export function resumePosition(savedSeconds, duration) {
  if (!Number.isFinite(savedSeconds) || savedSeconds < MIN_RESUME_SECONDS) return 0;
  if (isFinished(savedSeconds, duration)) return 0;
  return savedSeconds;
}

// Per-browser progress (positions, played episodes, speed) in localStorage. Falls back to memory
// when storage is unavailable, e.g. some private-browsing modes.
export function createProgressStore(storage) {
  let state = readState(storage);

  const persist = () => {
    try {
      storage?.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // Storage full or blocked: progress stays in memory for this visit.
    }
  };

  const remember = (episodeId, update) => {
    const entry = { ...(state.episodes[episodeId] ?? {}), ...update, touchedAt: Date.now() };
    const entries = Object.entries({ ...state.episodes, [episodeId]: entry })
      .sort(([, a], [, b]) => b.touchedAt - a.touchedAt)
      .slice(0, MAX_REMEMBERED_EPISODES);
    state = { ...state, episodes: Object.fromEntries(entries) };
    persist();
  };

  return {
    position: (episodeId) => state.episodes[episodeId]?.position ?? 0,
    savePosition: (episodeId, seconds) => remember(episodeId, { position: Math.floor(seconds) }),
    isPlayed: (episodeId) => state.episodes[episodeId]?.played === true,
    markPlayed: (episodeId) => remember(episodeId, { played: true, position: 0 }),
    playbackRate: () => (PLAYBACK_RATES.includes(state.playbackRate) ? state.playbackRate : 1),
    savePlaybackRate: (rate) => {
      state = { ...state, playbackRate: rate };
      persist();
    },
  };
}

function readState(storage) {
  const empty = { episodes: {}, playbackRate: 1 };
  try {
    const parsed = JSON.parse(storage?.getItem(STORAGE_KEY) ?? "null");
    const episodes = parsed?.episodes;
    return typeof episodes === "object" && episodes !== null && !Array.isArray(episodes)
      ? { episodes, playbackRate: parsed.playbackRate }
      : empty;
  } catch {
    return empty;
  }
}
