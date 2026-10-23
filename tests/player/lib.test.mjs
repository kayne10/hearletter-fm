import assert from "node:assert/strict";
import { test } from "node:test";

import {
  audioUrl,
  createProgressStore,
  episodesJsonUrl,
  formatEpisodeDate,
  formatTime,
  isFinished,
  nextPlaybackRate,
  parseEpisodes,
  parseLocationHash,
  pickEpisode,
  resumePosition,
} from "../../web/player/lib.js";

const PAGE = "https://d123.cloudfront.net/TOKEN/player/";

const episode = (date, overrides = {}) => ({
  episode_id: `ep_${date.replaceAll("-", "")}_abc123`,
  title: `Morning Briefing - ${date}`,
  episode_date: date,
  audio_path: `episodes/reader_example.com/${date}/ep_${date.replaceAll("-", "")}_abc123.mp3`,
  summary: "From TLDR.",
  ...overrides,
});

const memoryStorage = () => {
  const items = new Map();
  return {
    getItem: (key) => items.get(key) ?? null,
    setItem: (key, value) => items.set(key, value),
  };
};

test("parseLocationHash reads feed and episode, rejecting anything malformed", () => {
  assert.deepEqual(parseLocationHash("#reader_example.com/ep_20261020_abc"), {
    feed: "reader_example.com",
    episodeId: "ep_20261020_abc",
  });
  assert.deepEqual(parseLocationHash("#reader_example.com"), {
    feed: "reader_example.com",
    episodeId: null,
  });
  assert.deepEqual(parseLocationHash("#../secrets/<script>"), { feed: null, episodeId: null });
  assert.deepEqual(parseLocationHash("#a..b/ep_1"), { feed: null, episodeId: "ep_1" });
  assert.deepEqual(parseLocationHash("#.hidden/ep_1"), { feed: null, episodeId: "ep_1" });
  assert.deepEqual(parseLocationHash(""), { feed: null, episodeId: null });
});

test("feed and audio URLs resolve next to the player, keeping the token path", () => {
  assert.equal(
    episodesJsonUrl("reader_example.com", PAGE),
    "https://d123.cloudfront.net/TOKEN/feeds/reader_example.com/episodes.json",
  );
  assert.equal(
    audioUrl("episodes/r/2026-10-20/ep_1.mp3", `${PAGE}index.html`),
    "https://d123.cloudfront.net/TOKEN/episodes/r/2026-10-20/ep_1.mp3",
  );
});

test("parseEpisodes keeps valid entries, newest first, and drops bad ones", () => {
  const payload = {
    episodes: [
      episode("2026-10-19"),
      episode("2026-10-21"),
      episode("2026-10-20", { audio_path: "../../etc/passwd.mp3" }),
      episode("2026-10-18", { episode_id: "<img onerror=alert(1)>" }),
      { nonsense: true },
      null,
    ],
  };

  const parsed = parseEpisodes(payload);

  assert.deepEqual(
    parsed.map((item) => item.episodeDate),
    ["2026-10-21", "2026-10-19"],
  );
  assert.equal(parsed[0].summary, "From TLDR.");
  assert.deepEqual(parseEpisodes(null), []);
  assert.deepEqual(parseEpisodes({ episodes: "nope" }), []);
});

test("pickEpisode prefers the linked episode, else the newest", () => {
  const episodes = parseEpisodes({ episodes: [episode("2026-10-21"), episode("2026-10-20")] });

  assert.equal(pickEpisode(episodes, "ep_20261020_abc123").episodeDate, "2026-10-20");
  assert.equal(pickEpisode(episodes, "ep_missing").episodeDate, "2026-10-21");
  assert.equal(pickEpisode([], null), null);
});

test("formatTime and formatEpisodeDate", () => {
  assert.equal(formatTime(0), "0:00");
  assert.equal(formatTime(65.9), "1:05");
  assert.equal(formatTime(3725), "1:02:05");
  assert.equal(formatTime(Number.NaN), "0:00");
  assert.equal(formatEpisodeDate("2026-10-20"), "Tue, Oct 20");
});

test("nextPlaybackRate cycles and resets unknown rates", () => {
  assert.equal(nextPlaybackRate(1), 1.25);
  assert.equal(nextPlaybackRate(2), 1);
  assert.equal(nextPlaybackRate(3), 1);
});

test("resume skips negligible or nearly finished positions", () => {
  assert.equal(resumePosition(120, 400), 120);
  assert.equal(resumePosition(2, 400), 0);
  assert.equal(resumePosition(390, 400), 0);
  assert.equal(resumePosition(Number.NaN, 400), 0);
  assert.equal(isFinished(385, 400), true);
  assert.equal(isFinished(100, Number.NaN), false);
});

test("progress store remembers positions, played state, and speed across reloads", () => {
  const storage = memoryStorage();
  const store = createProgressStore(storage);
  store.savePosition("ep_1", 42.7);
  store.markPlayed("ep_2");
  store.savePlaybackRate(1.5);

  const reloaded = createProgressStore(storage);

  assert.equal(reloaded.position("ep_1"), 42);
  assert.equal(reloaded.isPlayed("ep_2"), true);
  assert.equal(reloaded.position("ep_2"), 0);
  assert.equal(reloaded.playbackRate(), 1.5);
});

test("progress store survives corrupt or unavailable storage", () => {
  const corrupt = { getItem: () => "{not json", setItem: () => {} };
  assert.equal(createProgressStore(corrupt).position("ep_1"), 0);

  const blocked = {
    getItem: () => {
      throw new Error("SecurityError");
    },
    setItem: () => {
      throw new Error("QuotaExceededError");
    },
  };
  const store = createProgressStore(blocked);
  store.savePosition("ep_1", 30);
  assert.equal(store.position("ep_1"), 30);
  assert.equal(createProgressStore(null).playbackRate(), 1);
});
