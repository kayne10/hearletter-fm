# Terraform Infrastructure

This directory contains the AWS MVP scaffold for Hearletter FM:

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
  -var="notification_from_email=no-reply@example.com"
```

SES inbound receiving is region-specific. Deploy this stack in a region that supports SES email receiving.
The notification sender must be a verified SES identity in the deployment region, unless your account is out of the SES sandbox and the domain identity covers it.
