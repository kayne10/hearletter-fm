# Hearletter FM

Hearletter FM turns forwarded email newsletters into a private, podcast-like morning audio briefing.

Core flow (see [docs/briefing-pipeline.md](docs/briefing-pipeline.md)):

```text
Forward newsletters -> SES -> S3 raw MIME -> SQS (batch) -> briefing-builder -> script in S3
  -> episode-producer -> one MP3 + email link from no-reply@
```

Newsletter sources are managed in [config/briefing.json](config/briefing.json).

This repository is intentionally serverless-first and event-driven. Two Lambdas, one SQS queue, and S3 as the system of record keep it cheap and easy to reason about.

## Repository Layout

```text
hearletter-fm/
├── config/
│   └── briefing.json       # Newsletter sources that make up the morning briefing
├── docs/                   # Pipeline design, bootstrap checklist, plans
├── infra/
│   └── terraform/          # AWS stack (SES, S3, SQS, Lambda, CloudWatch dashboard)
├── packages/
│   ├── domain/             # Briefing domain models and config types
│   └── utils/              # Text helpers
├── scripts/                # Packaging, local runner, raw email inspector
├── services/
│   ├── briefing-builder/   # SQS batch of raw emails -> one daily script
│   ├── episode-producer/   # Script -> MP3 -> email
│   └── shared/             # Parsing, cleaning, script, TTS, storage, notification
└── tests/
```

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make test
```

Run the builder against raw MIME files on disk. Output uses the same key layout as S3, under
`artifacts/local/briefing/`:

```bash
make local-briefing                                            # the Morning Brew fixture
python3 scripts/run_local_briefing.py <dir-of-raw-emails>     # your own samples
OPENAI_API_KEY=sk-... python3 scripts/run_local_briefing.py <dir> --synthesize   # also make the MP3
```

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

## Managing Newsletter Sources

Edit [config/briefing.json](config/briefing.json), then package and apply. The SQS batch size is
derived from the number of sources. See [docs/briefing-pipeline.md](docs/briefing-pipeline.md) for
every field.

## TTS Providers

`episode-producer` picks a provider with `TTS_PROVIDER` (Terraform variable `tts_provider`):

- `openai` (deployed default): `gpt-4o-mini-tts`, voice `marin` unless `openai_tts_voice` is set.
- `polly`: Amazon Polly generative engine, voice Joanna.

Long scripts are split at paragraph and sentence boundaries to fit each provider's request limit,
and the MP3 chunks are joined into one file.

The OpenAI key lives in Secrets Manager as the raw key or JSON like `{"api_key":"sk-..."}`.

## Observability

`terraform output dashboard_url` links to a CloudWatch dashboard covering S3, SQS, both Lambdas,
SES sending, and the pipeline's structured log events. To tail logs:

```bash
aws logs tail /aws/lambda/hearletter-fm-dev-briefing-builder --follow --region us-east-2
aws logs tail /aws/lambda/hearletter-fm-dev-episode-producer --follow --region us-east-2
```

Set `LOG_FULL_EVENTS=true` on a Lambda to log truncated full events while debugging.

## Deployment

```bash
make package-lambdas

cd infra/terraform
terraform apply \
  -var="aws_region=us-east-2" \
  -var="domain_name=beta.hearletter.fm" \
  -var="inbound_recipient=listen@beta.hearletter.fm" \
  -var="openai_api_key_secret_arn=arn:aws:secretsmanager:us-east-2:112894377436:secret:hearletter/openai-api-key-jxNQKi"
```

## Docs

- [Briefing Pipeline](docs/briefing-pipeline.md)
- [AWS Bootstrap Checklist](docs/aws-bootstrap.md)
- [Local Development](docs/local-development.md)
- [MVP Plan](docs/mvp-plan.md)
- [Milestones](docs/milestones.md)
