# Terraform Infrastructure

This directory contains the AWS stack for Hearletter FM.

| File | Contents |
| --- | --- |
| `main.tf` | SES inbound (S3 action only), raw-email and audio buckets |
| `pipeline.tf` | Briefing flow: raw-email SQS, briefing-builder, processed bucket, episode-producer, DLQs |
| `observability.tf` | CloudWatch dashboard for S3, SQS, Lambda, and SES (`dashboard_url` output) |
| `legacy.tf` | Old multi-queue pipeline, disconnected and safe to delete |

The SQS batch size comes from the number of sources in `config/briefing.json`.
See `docs/briefing-pipeline.md` for the flow and the cleanup list.

Legacy scaffold, for reference:

- S3 buckets for raw email, artifacts, audio, and RSS feed output.
- SQS queues and dead-letter queues between pipeline stages.
- DynamoDB metadata/idempotency table.
- Lambda execution role and one Lambda function per service stage.
- SES receipt rule that stores inbound mail in S3 and invokes the parser.
- SES outbound permission for emailing a presigned MP3 link back to the forwarding address.

## Expected Packaging

Terraform expects Lambda zip files in:

```text
artifacts/lambda/
├── email-parser.zip
├── newsletter-cleaner.zip
├── summarizer.zip
├── tts.zip
├── audio-mixer.zip
├── rss-generator.zip
└── notifier.zip
```

Build them from the repo root:

```bash
python3 scripts/package_lambdas.py
```

For a local Terraform planning pass without installing third-party dependencies:

```bash
python3 scripts/package_lambdas.py --skip-deps
```

The packaging script is intentionally not baked into Terraform. Keeping build and deploy separate makes CI easier and avoids Terraform running local build tools.

## Deploy Sketch

```bash
terraform init
terraform plan \
  -var="project_name=hearletter-fm" \
  -var="domain_name=example.com" \
  -var="inbound_recipient=listen@example.com" \
  -var="notification_from_email=no-reply@example.com" \
  -var="openai_api_key_secret_arn=arn:aws:secretsmanager:us-east-2:123456789012:secret:hearletter/openai-api-key-AbCdEf"
```

SES inbound receiving is region-specific. Deploy this stack in a region that supports SES email receiving.
The notification sender must be a verified SES identity in the deployment region, unless your account is out of the SES sandbox and the domain identity covers it.

The deployed Terraform default is `tts_provider = "openai"` for evaluation. Set `tts_provider = "polly"` to switch back to Amazon Polly without changing Lambda code.
The `audio-mixer` Lambda expects FFmpeg to be available through a Lambda layer or container image. It can deploy before audio assets exist; missing intro/outro files are skipped.
Use `show_style` to control semantic voice, pace, music, and transition defaults across the summarizer, TTS, and mixer stages.
