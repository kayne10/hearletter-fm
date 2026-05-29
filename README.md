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

## Architecture Docs

- [Architecture](docs/architecture.md)
- [Event Contracts](docs/event-contracts.md)
- [MVP Plan](docs/mvp-plan.md)
- [Local Development](docs/local-development.md)

