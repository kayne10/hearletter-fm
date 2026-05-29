# Terraform Infrastructure

This directory contains the AWS MVP scaffold for Hearletter FM:

- S3 buckets for raw email, artifacts, audio, and RSS feed output.
- SQS queues and dead-letter queues between pipeline stages.
- DynamoDB metadata/idempotency table.
- Lambda execution role and one Lambda function per service stage.
- SES receipt rule that stores inbound mail in S3 and invokes the parser.

## Expected Packaging

Terraform expects Lambda zip files in:

```text
artifacts/lambda/
├── email-parser.zip
├── newsletter-cleaner.zip
├── summarizer.zip
├── tts.zip
└── rss-generator.zip
```

The packaging script is intentionally not baked into Terraform. Keeping build and deploy separate makes CI easier and avoids Terraform running local build tools.

## Deploy Sketch

```bash
terraform init
terraform plan \
  -var="project_name=hearletter-fm" \
  -var="domain_name=example.com" \
  -var="inbound_recipient=listen@example.com"
```

SES inbound receiving is region-specific. Deploy this stack in a region that supports SES email receiving.

