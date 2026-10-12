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
       -> once all sources staged:   scripts/<forwarder>/<date>/script.json
  -> S3 ObjectCreated (scripts/**/script.json) -> episode-producer Lambda (async, DLQ: episode-dlq)
       chunked TTS -> S3 audio bucket  episodes/<forwarder>/<date>/<episode_id>.mp3
       -> SES email from no-reply@beta.hearletter.fm with a link to the MP3
```

Two Lambdas, one work queue, two dead-letter queues.

## Source config

`config/briefing.json` is the single place to manage newsletters:

| Field | Meaning |
| --- | --- |
| `sources[]` | `email` (original newsletter sender) and `name` (spoken in the episode). Order = episode order. |
| `min_sources` | Sources needed before the script is built. `null` means all of them. |
| `max_stories_per_source` | Stories read from each newsletter. |
| `timezone` | Decides which calendar day an email belongs to. |
| `allowed_forwarders` | Optional allow-list of forwarding addresses. Empty accepts anyone. |
| `title_template` | Episode title; `{date}` is replaced with `YYYY-MM-DD`. |

The file is bundled into the briefing-builder zip, and Terraform reads it to set the SQS batch size.
Changing it means `make package-lambdas` then `terraform apply`.

## Why staging, not just batching

SQS batching is best-effort. Lambda polls with several pollers, and the 60-second window closes
with whatever has arrived, so three forwards can land in separate invocations. The builder
therefore stages each newsletter under the forwarder and local date, and writes the script only
when the required sources are all present. The batch is just the wake-up call.

## Idempotency and failure handling

| Situation | Behaviour |
| --- | --- |
| SQS redelivers a message | Staging overwrites the same key; script already exists, so nothing new happens. |
| Two invocations finish the day at once | Script is written with S3 conditional put (`If-None-Match: *`); the loser reports `already_built`. |
| One email in a batch fails | Only that message is returned in `batchItemFailures` and retried; after 5 receives it goes to raw-email-dlq. |
| Building the script fails | Every message that touched that day is retried. |
| Email from an unknown sender | Logged as ignored and acknowledged. The raw MIME stays in S3. |
| Same newsletter forwarded twice | Latest copy wins in staging. Once a day's script exists, later forwards are ignored. |
| episode-producer fails | Lambda retries twice, then the event goes to episode-dlq. |
| episode-producer runs twice | MP3 key is deterministic and is reused; `notified.json` marker prevents a second email. |

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
  -var="openai_api_key_secret_arn=<secret-arn>"
```

The first apply also removes the SES rule's Lambda action. From then on the old parser is no longer
invoked, and the legacy resources sit idle until you remove them.

## Known limits

- Presigned links are signed with the Lambda role's temporary credentials. They stop working when
  those credentials expire (hours), even though the email says 7 days. Serving MP3s through
  CloudFront with signed URLs, or attaching small MP3s to the email, fixes this.
- If a configured newsletter never arrives, that day has no episode. Lower `min_sources` to accept
  partial days. A scheduled "flush at 9am" invocation is the natural next step.
- The script is template-based. Swapping `build_briefing_script` for an LLM rewrite is the next
  quality step.
- Story extraction in `hearletter_shared/cleaning.py` was tuned on Morning Brew. Check TLDR and The
  Rundown output with the local runner once you have raw samples.

## Old code and infra that can be deleted

The legacy pipeline is fully disconnected once the new stack is applied.

### Infrastructure (all in `infra/terraform/legacy.tf`)

- 7 Lambdas: email-parser, newsletter-cleaner, summarizer, tts, audio-mixer, rss-generator, notifier.
- 12 SQS queues: parsed-newsletter, cleaned-newsletter, briefing-script, narration-audio,
  generated-episode, and notification-email, each with its DLQ.
- 6 event source mappings and the SES invoke permission for email-parser.
- DynamoDB `metadata` table. No code ever read or wrote it.
- `artifacts` and `feed` S3 buckets and their public-access blocks.
- Shared `lambda-exec` IAM role, its inline policy, and its policy attachment.
- Legacy-only variables (`feed_public_read`, `ffmpeg_path`, `mixer_*`, `show_style`) and outputs.

Steps: empty the two buckets, because they have no `force_destroy`. Then delete `legacy.tf` and
run `terraform apply`.

```bash
aws s3 rm s3://hearletter-fm-dev-artifacts-20260713210400807900000004 --recursive
aws s3 rm s3://hearletter-fm-dev-feed-20260713210400807700000003 --recursive
```

### Code

- `services/email-parser/`, `services/newsletter-cleaner/`, `services/summarizer/`, `services/tts/`,
  `services/audio-mixer/`, `services/rss-generator/`, `services/notifier/`.
- `services/shared/hearletter_shared/audio_mixer.py`, `event_codec.py`, and `rss.py`.
  Delete `rss.py` only if you won't revive RSS; it is an unused stub today.
- From `lambda_events.py`: `iter_pipeline_events` and `decode_json_object`, which are the only users
  of `event_codec`.
- From `openai_tts.py` and `polly.py`: the SSML paths `synthesize_ssml`, `ssml_to_text`, and the
  `text_type` config.
- `packages/types/` (the whole `hearletter_events` package), `packages/domain/hearletter_domain/audio.py`
  and `models.py`, and `estimate_spoken_duration_seconds` in `packages/utils/hearletter_utils/text.py`.
- `scripts/run_local_pipeline.py` and the `local-pipeline` Makefile target.
- The legacy entries in `SERVICES` in `scripts/package_lambdas.py`, and the old zips in `artifacts/lambda/`.
- Tests: `test_audio_mixer.py`, `test_audio_mixer_handler.py`, `test_email_parser.py`,
  `test_event_codec.py`, `test_local_pipeline.py`, `test_notifier.py`, `test_rss.py`,
  `test_tts_selector.py`, and the SSML cases in `test_openai_tts.py`.
- Unused dependencies in `pyproject.toml`: `feedgen`, `defusedxml`, `html2text`, `openai`.
  The OpenAI client uses `urllib` directly.
- Docs describing the old flow: `docs/architecture.md`, `docs/event-contracts.md`.
