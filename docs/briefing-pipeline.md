# Morning Briefing Pipeline

One MP3 per listener per day, built from every newsletter listed in `config/briefing.json`.

```text
Forward newsletters to listen@beta.hearletter.fm
  -> SES receipt rule (S3 action only)
  -> S3 raw-email bucket            raw-email/default/<ses-message-id>
  -> S3 ObjectCreated -> SQS raw-email (DLQ: raw-email-dlq)
  -> briefing-builder Lambda         batch size = number of sources, 60s window
       parse forward -> filter by source -> clean -> stage
       -> S3 processed bucket       inbox/<forwarder>/<date>/<source>.json
       -> once all sources staged:   template script -> Claude rewrite (template on failure)
                                     scripts/<forwarder>/<date>/<fingerprint>/script.json
  -> S3 ObjectCreated (scripts/**/script.json) -> episode-producer Lambda (async, DLQ: episode-dlq)
       chunked TTS -> S3 audio bucket  episodes/<forwarder>/<date>/<episode_id>.mp3
       -> SES email from no-reply@beta.hearletter.fm with a link to the MP3
```

Two Lambdas, one work queue, two dead-letter queues.

## Source config

`config/briefing.json` is the single place to manage newsletters:

| Field | Meaning |
| --- | --- |
| `sources[]` | `email` (canonical sender) and `name` (spoken in the episode). Order = episode order. |
| `sources[].aliases` | Other exact sender addresses, e.g. Morning Brew's `morningbrew@mail.sailthru.com`. |
| `sources[].domains` | Sender domains, subdomains included. Never list a shared email-provider domain like `sailthru.com`. |
| `sources[].parser` | Template-aware story parser: `tldr`, `rundown`, or `divider` for Substack-style items separated by rules. Omit to use the generic extractor. |
| `sources[].required` | Default `true`. Set `false` for an occasional newsletter, such as a weekly, that should never hold an episode back. See "Optional sources" below. |
| `sources[].carry_over_days` | For an optional source: how many days an unused issue waits for an episode (default 7). |
| `sources[].max_stories` | Per-source cap on staged stories, for example 2 for a long weekly. |
| `sources[].sender_names` | From display names, case-insensitive. Last resort when the address is new. |
| `flush_at` | Optional local `HH:MM` for a daily scheduled flush. Off (`null`) by default; see "Short days" below. |
| `min_sources` | Sources needed before the script is built. `null` means all of them. |
| `max_stories_per_source` | Stories read from each newsletter. |
| `timezone` | Decides which calendar day an email belongs to. |
| `allowed_forwarders` | Optional allow-list of forwarding addresses. Empty accepts anyone. |
| `title_template` | Episode title; `{date}` is replaced with `YYYY-MM-DD`. |

The file is bundled into the briefing-builder zip, and Terraform reads it to set the SQS batch size.
Changing it means `make package-lambdas` then `terraform apply`.

Matching tries exact addresses and aliases first, then domains, then display names. Anything
that still doesn't match shows up as `unconfigured_source:<name> <address>` in the builder's
`ignored` log, which is the dashboard's pipeline-events table. Add that address as an alias.

## Story extraction

Newsletters with a stable layout get their own parser in `hearletter_shared/story_parsers.py`.
They read the newsletter's own markers instead of guessing from paragraph shape:

- **TLDR**: bold titles ending in "(N minute read)", followed by their paragraph. Entries marked
  "(Sponsor)" are skipped.
- **Divider** (Data Engineering Weekly and other Substack-style newsletters): items separated by
  horizontal rules, each a "Company: Title" line, an optional image caption, and a summary. The
  masthead and editor's note before the first rule, sponsored items ("Event Alert", "Sponsored",
  "Partner"), and the footer are skipped.
- **The Rundown**: the stories under "LATEST DEVELOPMENTS", each made of "The Rundown:",
  "Why it matters:" and "The details:". Sponsor blocks ("TOGETHER WITH", "PRESENTED BY"), the
  "AI TRAINING" how-to and "QUICK HITS" are skipped.

Stories keep the newsletter's order, and the first `max_stories_per_source` are used. Invisible
preheader characters are stripped for every newsletter. If a parser finds nothing, for example
after a layout change, the generic extractor takes over. A `story_parser_found_nothing` log line
records it, and the staged file's `extractor` field shows which one ran. The tests use
`tests/fixtures/*_2026-10-20.txt`. Add a new fixture when a layout changes.

## Optional sources

Optional sources such as Data Engineering Weekly never count toward an episode being ready, and
never trigger one on their own. Each issue is used by exactly one episode:

- **Arrives before the day's episode:** it joins that episode.
- **Arrives after the day's episode:** it waits for the next day's episode instead of producing
  an "(update N)". A later update for a required newsletter keeps the same optional issues as
  the version it replaces.
- **No episode within `carry_over_days`:** it's dropped.
- **Only the weekly arrived:** `make episode` builds from it alone if you want an episode anyway.

"Used" is derived from the scripts themselves. Each script records the `included_digests` of the
newsletters in it, so there's no separate marker to keep in sync. Polling or retrying until a
weekly shows up would delay six mornings out of seven and misuse failure retries as a scheduler,
so the design avoids it.

## Why staging, not just batching

SQS batching is best-effort. Lambda polls with several pollers, and the 60-second window closes
with whatever has arrived, so three forwards can land in separate invocations. The builder
therefore stages each newsletter under the forwarder and local date, and writes the script only
when the required sources are all present. The batch is just the wake-up call.

## Short days

If a newsletter doesn't arrive, because it didn't publish, you skipped it, or its sender isn't
recognised, the day keeps waiting. Build it from whatever has arrived with a manual flush. It uses
today's date in the config timezone, so run it the same day:

```bash
make episode
```

That wraps this call. Override `AWS_REGION` or `BRIEFING_BUILDER` for another stack:

```bash
aws lambda invoke --function-name hearletter-fm-dev-briefing-builder --region us-east-2 \
  --cli-binary-format raw-in-base64-out --payload '{"action":"flush"}' /dev/stdout
```

If the missing newsletter shows up later, it produces an updated episode, described below.

Setting `flush_at` (for example `"08:00"`) adds an EventBridge Scheduler rule that runs the same
flush daily. It's off because a fixed time suits early-morning forwarding only. Forwards after that
time get no help, and stragglers after it produce a partial episode and then an update.

## Sending emails at any time

Each script is keyed by a fingerprint of the newsletter content in it: source, subject and
stories. It ignores which forward delivered a newsletter and when. So you can forward the
newsletters at any time of day, as often as you like:

- **The same newsletters again**, for example re-forwarded at noon, give the same fingerprint.
  Nothing is rebuilt and no second email is sent.
- **Before the day's first episode**, the builder waits until every source is in, or until you
  run a manual flush.
- **After the day has an episode**, any changed content is built as the next version and
  emailed with "(update N)" in the title. Examples are a newsletter arriving after a flush, or a
  corrected issue.

The episode id comes from the forwarder, date and fingerprint, so each version gets its own MP3
and email, and retries of a version reuse them.

## Script writing with Claude

When `anthropic_api_key_secret_arn` is set, the builder makes one Claude call per episode. Claude
turns the staged stories into a conversational, podcast-style script. It merges stories that
several newsletters cover, and it is told to use only facts from the newsletters.

| Setting (Terraform variable) | Default | Effect |
| --- | --- | --- |
| `anthropic_api_key_secret_arn` | empty | Secret holding the key, raw or JSON with `api_key`. Empty means template only. |
| `script_writer` | `claude` | Set `template` to switch Claude off without removing the key. |
| `script_model` | `claude-opus-5-5` | `claude-sonnet-5-5` roughly halves the cost. |
| `script_effort` | `medium` | Higher effort means more thinking and higher cost. |

### Priorities and house rules

The `editorial` block in `config/briefing.json` steers what Claude focuses on:

| Field | Meaning |
| --- | --- |
| `topics[]` | `name`, `weight` and optional `examples`. Weights are relative: 4/3/2/1 gives 40/30/20/10% of airtime. Higher weights also come earlier. |
| `rules[]` | Plain-language house rules, passed to Claude as written. |
| `target_minutes` | Episode length; at 150 words per minute, 7 minutes is about 1,050 words. A script under half of that falls back to the template. |
| `candidates_per_source` | Stories staged per newsletter for Claude to choose from (default 6). The template fallback still reads `max_stories_per_source`. |

The current priorities are tech and programming (4), AI agent development (3), news and politics
(2), then humor and light stories (1). Light stories are saved for the end. Edit the block, then
run `make package-lambdas` and apply. The next new episode uses it.

The call uses structured output, so it returns validated segments. It also enables the API's
refusal fallback. A refusal, truncation, error, or a script under half the target length keeps the
template text, and the episode still ships. `script.json` records `writer` and, when Claude wrote the text,
the `template_text` it replaced. The `script_writer_succeeded` and `script_writer_failed` log lines
show up in the dashboard's pipeline-events table.

Try it locally:

```bash
ANTHROPIC_API_KEY=sk-ant-... python3 scripts/run_local_briefing.py tests/fixtures/raw_email --min-sources 1 --ai-script
```

## Idempotency and failure handling

| Situation | Behaviour |
| --- | --- |
| SQS redelivers a message | Staging overwrites the same key. The fingerprint matches an existing script, so the status is `unchanged`. |
| Same newsletters forwarded again | Same fingerprint, so the status is `unchanged`: no new script, MP3, or email. |
| A newsletter changes or arrives late | New fingerprint, so the next version of the day's episode is built and emailed. |
| Two invocations build the same content at once | Conditional put (`If-None-Match: *`) on the fingerprint key; the loser reports `unchanged`. |
| One email in a batch fails | Only that message is returned in `batchItemFailures` and retried; after 5 receives it goes to raw-email-dlq. |
| Building the script fails | Every message that touched that day is retried. |
| Email from an unknown sender | Logged as ignored and acknowledged. The raw MIME stays in S3. |
| episode-producer fails | Lambda retries twice, then the event goes to episode-dlq. |
| episode-producer runs twice | MP3, `<episode_id>.json` and `<episode_id>.notified.json` are keyed by episode id, so there is no second email. |

## Local development

```bash
make test
# Build a script from local raw MIME files (only one sample exists, so lower min_sources):
python3 scripts/run_local_briefing.py tests/fixtures/raw_email --min-sources 1
# Also produce the MP3 (prints the email instead of sending it):
OPENAI_API_KEY=sk-... python3 scripts/run_local_briefing.py <dir-of-raw-emails> --synthesize
```

Output lands in `artifacts/local/briefing/{processed,audio}` using the same key layout as S3.

## Deploy

```bash
make package-lambdas
cd infra/terraform
terraform apply \
  -var="aws_region=us-east-2" \
  -var="domain_name=beta.hearletter.fm" \
  -var="inbound_recipient=listen@beta.hearletter.fm" \
  -var="openai_api_key_secret_arn=<secret-arn>" \
  -var="anthropic_api_key_secret_arn=<secret-arn>"
```


## Private podcast feed

`infra/terraform/feed.tf` puts CloudFront in front of the audio bucket using origin access
control, so the bucket stays private. A CloudFront Function requires a random token as the first
path segment. With the token present, it serves only `feeds/**.xml` and `episodes/**.mp3`, and it
strips the token before reaching S3.

After each episode the producer rewrites `feeds/<forwarder>/feed.xml`. It lists the last
`feed_days` days, default 7, using the newest version of each day, with the script's opening as
show notes. The feed is cached for 5 minutes, and `itunes:block` keeps it out of podcast
directories. `make feed` rebuilds every feed from existing episodes and prints the URLs.

For personal use there is one shared token. With multiple listeners, give each forwarder its own
token, for example an HMAC of the forwarder checked in the CloudFront Function, so one listener's
URL can't read another's feed.

## Web player

The `web/player/` folder holds a static, dependency-free page with `index.html`, `styles.css`,
`app.js` and `lib.js`. Terraform uploads it to `player/` in the audio bucket. CloudFront serves
it behind the same token at `<feed base>/player/#<forwarder>/<episode_id>`. The fragment never
leaves the browser.

- **Data:** the page fetches `feeds/<forwarder>/episodes.json`. The producer writes it next to
  `feed.xml` with the same 7-day selection. Audio paths are relative, so the token never appears
  in the file, and the page validates every entry before using it.
- **Playback:** large controls, skip back 15 seconds and forward 30, speed from 1x to 2x, and
  the Media Session API for lock-screen and headphone controls. Position, speed and played state
  are kept in `localStorage` on each device. A position is saved every 5 seconds and whenever
  the page is hidden.
- **Security:** the URL contains the token, so the page sends no referrer and is marked
  `noindex`. Its content-security policy allows only its own scripts, styles, data and audio.
  Episode text is rendered with `textContent`, never `innerHTML`.
- **Switching back:** `episode_link` controls the email link. `player` is the default. `mp3`
  links straight to the MP3, which is a permanent CloudFront URL, or a presigned S3 URL if the
  feed isn't deployed. The player files stay deployed either way.
- **Tests:** `make test-web` runs the player-logic tests and the CloudFront Function tests.
  `make preview-player` serves the real page locally with sample audio.

## Known limits

- A day with a missing newsletter waits until you run a manual flush. If short days become
  common, a quiet period works at any hour: build 30 minutes after the day's last arrival.
- Claude writes the script, but the voice is still OpenAI TTS by default, because Claude has
  no text-to-speech. `tts_provider = "polly"` removes the OpenAI dependency at some cost to voice
  quality.
- Story extraction in `hearletter_shared/cleaning.py` was tuned on Morning Brew. Check TLDR and The
  Rundown output with the local runner once you have raw samples.

## Legacy pipeline

The original seven-Lambda, twelve-queue pipeline (email-parser through notifier), its DynamoDB
table, and the artifacts and feed buckets were removed once this flow was verified in production.
Git history has the old code if anything needs to be recovered.
