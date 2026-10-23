# Terraform Infrastructure

| File | Contents |
| --- | --- |
| `main.tf` | SES inbound (S3 action only), raw-email and audio buckets |
| `pipeline.tf` | raw-email SQS queue and DLQ, briefing-builder, processed bucket, episode-producer and its DLQ, IAM |
| `feed.tf` | CloudFront and secret token for the private podcast feed and web player; uploads `web/player/` |
| `observability.tf` | CloudWatch dashboard for S3, SQS, Lambda, and SES (`dashboard_url` output) |

The SQS batch size comes from the number of sources in `config/briefing.json`. See
`docs/briefing-pipeline.md` for the flow, key layout, and failure handling.

## Packaging

Terraform expects these zips, built by `make package-lambdas` from the repo root:

```text
artifacts/lambda/
├── briefing-builder.zip   # includes config/briefing.json as briefing.json
└── episode-producer.zip
```

Packaging stays outside Terraform so CI can build once and deploy many times.

## Deploy

```bash
terraform init
terraform apply \
  -var="aws_region=us-east-2" \
  -var="domain_name=beta.hearletter.fm" \
  -var="inbound_recipient=listen@beta.hearletter.fm" \
  -var="openai_api_key_secret_arn=<secret-arn>" \
  -var="anthropic_api_key_secret_arn=<secret-arn>"
```

- SES inbound receiving is regional, so deploy in a region that supports it.
- The sender `no-reply@<domain_name>` must be covered by a verified SES identity. Override it with
  `notification_from_email`.
- While SES is in sandbox mode, every forwarding address must also be verified.
- Set `anthropic_api_key_secret_arn` to have Claude write the script. `script_writer`,
  `script_model` and `script_effort` tune it.
- Set `tts_provider = "polly"` to switch away from OpenAI without code changes. `polly_region`
  (default us-east-1), `polly_engine` and `polly_voice_id` tune it. us-east-2 has no generative engine.
- Set `enable_s3_request_metrics = true` for paid S3 request metrics on the dashboard.
