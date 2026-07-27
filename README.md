# Hearletter FM

Hearletter FM turns forwarded email newsletters into a private, podcast-like morning audio briefing.

Core flow:

```text
Forward newsletter -> SES inbound -> S3 raw MIME -> parse -> clean -> summarize -> TTS -> RSS
```

This repository is intentionally serverless-first and event-driven. The MVP favors small Lambda services connected by SQS queues, S3 for durable artifacts, and a private RSS feed that podcast apps can subscribe to.

## Repository Layout

```text
hearletter-fm/
├── docs/                 # Architecture notes and implementation plan
├── infra/
│   └── terraform/        # AWS infrastructure skeleton
├── packages/
│   ├── domain/           # Domain models
│   ├── types/            # Event contracts
│   └── utils/            # Shared utility helpers
├── services/
│   ├── email-parser/     # Raw MIME -> ParsedNewsletterEvent
│   ├── newsletter-cleaner/# Parsed -> Cleaned
│   ├── summarizer/       # Cleaned -> Spoken script
│   ├── tts/              # Script -> MP3
│   ├── rss-generator/    # Episode metadata -> feed.xml
│   └── shared/           # Lambda/service helpers
└── tests/
```

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

For local imports without packaging a Lambda bundle, the test configuration adds these roots to `PYTHONPATH`:

- `packages/domain`
- `packages/types`
- `packages/utils`
- `services/shared`

## Inspecting A Raw SES Email

After SES stores a test email in S3, download the raw MIME file and inspect it without dumping large inline images:

```bash
aws s3 cp s3://<raw-email-bucket>/<object-key> tmp/test.eml
python3 scripts/inspect_raw_email.py tmp/test.eml
```

To print decoded text parts in full:

```bash
python3 scripts/inspect_raw_email.py tmp/test.eml --extract-text
```

To save decoded non-text parts such as inline images:

```bash
python3 scripts/inspect_raw_email.py tmp/test.eml --save-attachments-dir tmp/email-parts
```

## Running The Pipeline Locally

Use local samples to generate stage-by-stage files without invoking AWS:

```bash
python3 scripts/run_local_pipeline.py --input data --output-dir artifacts/local/latest --clean-output
```

You can also run a single raw MIME or decoded text file:

```bash
python3 scripts/run_local_pipeline.py --input tests/fixtures/raw_email
python3 scripts/run_local_pipeline.py --input data/01_email_with_body.txt
```

The runner writes per-sample folders under `artifacts/local/` with each stage's input and output:

- `01_email_parser/body.txt`
- `01_email_parser/body.html`
- `02_newsletter_cleaner/clean_text.txt`
- `02_newsletter_cleaner/story_candidates.json`
- `03_podcast_context/podcast_context.json`
- `03_podcast_context/agent_prompt.txt`
- `03_podcast_context/polly_ssml.xml`

This local runner intentionally stops before audio generation. The goal is to master newsletter parsing, cleanup, story extraction, and Amazon Polly-ready SSML before invoking a TTS provider.

To synthesize a local MP3 with Amazon Polly:

```bash
python3 scripts/run_local_pipeline.py \
  --input data \
  --output-dir artifacts/local/latest \
  --clean-output \
  --synthesize-audio \
  --polly-region us-east-1 \
  --polly-engine generative \
  --polly-voice-id Joanna
```

MP3 files are written to each sample's `04_polly_audio/episode.mp3`.

## Lambda Event Troubleshooting

Each Lambda logs a compact JSON event summary to CloudWatch when invoked. To inspect the parser and cleaner logs:

```bash
aws logs tail /aws/lambda/hearletter-fm-dev-email-parser --follow --region us-east-2
aws logs tail /aws/lambda/hearletter-fm-dev-newsletter-cleaner --follow --region us-east-2
```

By default, logs include event shape, record counts, SQS message IDs, and pipeline IDs. To temporarily log truncated full events, set the Lambda environment variable:

```text
LOG_FULL_EVENTS=true
```

Turn it back off after debugging to avoid noisy CloudWatch logs.

## Architecture Docs

- [Architecture](docs/architecture.md)
- [Event Contracts](docs/event-contracts.md)
- [MVP Plan](docs/mvp-plan.md)
- [Milestones](docs/milestones.md)
- [AWS Bootstrap Checklist](docs/aws-bootstrap.md)
- [Local Development](docs/local-development.md)
