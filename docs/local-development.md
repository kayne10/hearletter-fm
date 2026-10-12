# Local Development

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make test
```

## Running The Pipeline Locally

`scripts/run_local_briefing.py` wraps raw MIME files in the same SQS and S3 event shapes Lambda
receives. It then runs `briefing-builder` and, with `--synthesize`, `episode-producer`. Storage is a
local folder through `LocalObjectStore`, and emails are printed instead of sent.

```bash
python3 scripts/run_local_briefing.py tests/fixtures/raw_email --min-sources 1
OPENAI_API_KEY=sk-... python3 scripts/run_local_briefing.py <dir> --synthesize
```

Use `--keep` to add emails to an earlier run, which simulates sources arriving in separate batches.

## Handler Design

Each handler is a thin AWS adapter around a pure function that takes an `ObjectStore` and
callables: `process_batch` for the builder and `produce_episode` for the producer. Tests and the
local runner call those functions directly with local storage and fakes. No AWS mocking is needed.

## Environment Variables

| Lambda | Variables |
| --- | --- |
| briefing-builder | `PROCESSED_BUCKET`, optional `BRIEFING_CONFIG_PATH` |
| episode-producer | `AUDIO_BUCKET`, `NOTIFICATION_FROM_EMAIL`, `AUDIO_URL_TTL_SECONDS`, `TTS_PROVIDER`, `OPENAI_API_KEY_SECRET_ARN` (or `OPENAI_API_KEY` locally), `OPENAI_TTS_*`, `POLLY_*` |

## Testing Strategy

- Forwarded-email parsing against the real Gmail fixture and synthetic Apple Mail and auto-forward cases.
- Builder behavior: full batch, split batches, redelivery, partial `min_sources`, ignored senders,
  per-message failures, and conditional-write races.
- Producer behavior: chunked TTS, one MP3, and exactly one email across retries.
