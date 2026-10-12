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
                                     scripts/<forwarder>/<date>/script.json
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

The call uses structured output, so it returns validated segments. It also enables the API's
refusal fallback. A refusal, truncation, error, or a script under 150 words keeps the template
text, and the episode still ships. `script.json` records `writer` and, when Claude wrote the text,
the `template_text` it replaced. The `script_writer_succeeded` and `script_writer_failed` log lines
show up in the dashboard's pipeline-events table.

Try it locally:

```bash
ANTHROPIC_API_KEY=sk-ant-... python3 scripts/run_local_briefing.py tests/fixtures/raw_email --min-sources 1 --ai-script
```

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
  -var="openai_api_key_secret_arn=<secret-arn>" \
  -var="anthropic_api_key_secret_arn=<secret-arn>"
```


## Known limits

- Presigned links are signed with the Lambda role's temporary credentials. They stop working when
  those credentials expire (hours), even though the email says 7 days. Serving MP3s through
  CloudFront with signed URLs, or attaching small MP3s to the email, fixes this.
- If a configured newsletter never arrives, that day has no episode. Lower `min_sources` to accept
  partial days. A scheduled "flush at 9am" invocation is the natural next step.
- Claude writes the script, but the voice is still OpenAI TTS by default, because Claude has
  no text-to-speech. `tts_provider = "polly"` removes the OpenAI dependency at some cost to voice
  quality.
- Story extraction in `hearletter_shared/cleaning.py` was tuned on Morning Brew. Check TLDR and The
  Rundown output with the local runner once you have raw samples.

## Legacy pipeline

The original seven-Lambda, twelve-queue pipeline (email-parser through notifier), its DynamoDB
table, and the artifacts and feed buckets were removed once this flow was verified in production.
Git history has the old code if anything needs to be recovered.
