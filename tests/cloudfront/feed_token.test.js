// Tests the CloudFront viewer-request function in infra/terraform/feed_token.js.tftpl.
// Run with: make test-feed-function   (needs Node; no npm packages)
"use strict";

const fs = require("fs");
const path = require("path");

const TOKEN = "TESTTOKEN123";
const template = path.join(__dirname, "..", "..", "infra", "terraform", "feed_token.js.tftpl");
const code = fs.readFileSync(template, "utf8").replace("${token}", TOKEN);
const handler = new Function(`${code}\nreturn handler;`)();

const T = `/${TOKEN}`;
const FEED = "/feeds/reader_example.com/feed.xml";
const MP3 = "/episodes/reader_example.com/2026-10-20/ep_20261020_cbcecb46fa19.mp3";
const cases = [
  ["feed with token is served, token stripped", T + FEED, FEED],
  ["episode MP3 with token is served", T + MP3, MP3],
  ["no token", FEED, 403],
  ["wrong token", `/WRONGTOKEN${FEED}`, 403],
  ["token prefix collision", `${T}x${FEED}`, 403],
  ["bare token", T, 403],
  ["token root", `${T}/`, 403],
  ["episode metadata JSON", `${T}/episodes/r/2026-10-20/ep_1.json`, 403],
  ["email marker", `${T}/episodes/r/2026-10-20/ep_1.notified.json`, 403],
  ["feed with wrong extension", `${T}/feeds/r/feed.json`, 403],
  ["staged newsletters", `${T}/inbox/r/2026-10-20/tldr.json`, 403],
  ["path traversal into feeds", `${T}/episodes/../feeds/r/feed.xml`, 403],
  ["path traversal into episodes", `${T}/feeds/r/../../episodes/x/a.mp3`, 403],
  ["player root serves index.html", `${T}/player/`, "/player/index.html"],
  ["player script", `${T}/player/app.js`, "/player/app.js"],
  ["player module", `${T}/player/lib.js`, "/player/lib.js"],
  ["player stylesheet", `${T}/player/styles.css`, "/player/styles.css"],
  ["player episode list", `${T}/feeds/reader_example.com/episodes.json`, "/feeds/reader_example.com/episodes.json"],
  ["player without token", "/player/", 403],
  ["player without trailing slash", `${T}/player`, 403],
  ["player subdirectory", `${T}/player/x/app.js`, 403],
  ["player other file type", `${T}/player/secrets.json`, 403],
  ["player traversal", `${T}/player/../inbox/a.json`, 403],
  ["other json under feeds", `${T}/feeds/r/other.json`, 403],
];

let failed = 0;
for (const [name, uri, expected] of cases) {
  const result = handler({ request: { method: "GET", uri, headers: {}, querystring: {} } });
  const got = result.statusCode ? result.statusCode : result.uri;
  const ok = got === expected;
  failed += ok ? 0 : 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}: ${uri} -> ${got}${ok ? "" : ` (expected ${expected})`}`);
}
console.log(failed ? `${failed} failed` : `all ${cases.length} passed`);
process.exit(failed ? 1 : 0);
