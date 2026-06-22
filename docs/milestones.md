# Hearletter FM Milestones

This plan is meant to restart the project without making it feel like a giant cloud migration. Each milestone has a clear exit condition and leaves the repo in a better state even if progress pauses again.

## Milestone 0: Re-Open The AWS Account

Goal: get back into AWS safely and confirm the account is usable.

Tasks:

- Sign in to AWS and confirm root account MFA is enabled.
- Confirm billing access and set a low monthly budget alert.
- Confirm the target region for SES inbound email receiving.
- Install or update the AWS CLI locally.
- Create or recover an admin access path through IAM Identity Center or an existing admin IAM user.
- Run `aws sts get-caller-identity` successfully from the local machine.

Done when:

- You can authenticate locally with the AWS CLI.
- You know the AWS account ID and default deployment region.
- A budget alert exists.

## Milestone 1: Terraform Deploy Role

Goal: create a role/profile Terraform can use consistently.

Tasks:

- Create a human admin access path first, ideally through IAM Identity Center.
- Create a Terraform deploy role named something like `HearletterTerraformDeployRole`.
- Give the role broad permissions for the MVP bootstrap, then tighten later once resources stabilize.
- Configure a local AWS profile that assumes the deploy role.
- Run `aws sts get-caller-identity --profile hearletter-dev`.
- Document the AWS account ID, region, and role ARN in a local-only `.env` or shell profile, not in git.

Done when:

- Terraform can authenticate through the deploy role.
- `terraform init` works in `infra/terraform`.
- `terraform plan` reaches a real plan instead of an auth error.

Recommended first-pass posture:

- Use admin-level deploy permissions only while bootstrapping the single dev environment.
- Add least-privilege IAM tightening as a later milestone once the Terraform resource set stops changing every hour.

## Milestone 2: Terraform Planable Dev Stack

Goal: make the current infrastructure scaffold plan cleanly.

Tasks:

- Create the Lambda zip packaging script expected by Terraform.
- Package the five service Lambdas into `artifacts/lambda/`.
- Run `terraform fmt -check`.
- Run `terraform validate`.
- Run `terraform plan`.
- Fix any missing IAM permissions, provider configuration, or artifact path issues.

Done when:

- `terraform plan` succeeds for the dev stack.
- No AWS resources have to be created by hand except the bootstrap deploy role and SES/domain prerequisites.

## Milestone 3: Deploy Skeleton Infrastructure

Goal: create the empty event-driven AWS backbone.

Tasks:

- Apply Terraform for S3, SQS, DLQs, DynamoDB, Lambdas, and SES receipt-rule skeletons.
- Confirm queues and DLQs exist.
- Confirm Lambda functions are deployed.
- Confirm CloudWatch logs are created on test invocation.
- Add output notes for bucket names, queue URLs, and receipt rule names.

Done when:

- `terraform apply` succeeds.
- Each Lambda can be invoked with a small fixture event.
- No messages are stuck in DLQs from basic smoke tests.

## Milestone 4: Local Vertical Slice

Goal: prove the application logic locally before wiring real email.

Tasks:

- Add fixture raw MIME newsletter emails under `tests/fixtures/`.
- Implement MIME parsing into HTML/text artifacts.
- Implement deterministic HTML cleaning.
- Generate a fake briefing script without calling OpenAI.
- Generate a fake MP3 placeholder or stub audio artifact.
- Generate RSS XML from a generated episode event.

Done when:

- A local test can run raw email -> parsed -> cleaned -> scripted -> episode -> RSS.
- The RSS output has an MP3 enclosure and stable episode metadata.

## Milestone 5: Real Email Ingestion

Goal: receive one forwarded newsletter through SES.

Tasks:

- Verify the sending/receiving domain in SES.
- Add the SES inbound MX records.
- Configure `listen@<domain>` receipt rule.
- Forward a real newsletter.
- Confirm raw MIME lands in S3.
- Confirm `email-parser` receives the SES event.
- Confirm the parsed event reaches the first SQS queue.

Done when:

- Forwarding an email creates a raw S3 object and a parsed pipeline event.

## Milestone 6: First Private Podcast Episode

Goal: produce a playable private RSS episode.

Tasks:

- Wire cleaner output to S3 and SQS.
- Add OpenAI summarizer client behind an interface.
- Add OpenAI TTS provider behind `TTSProvider`.
- Store the MP3 in S3.
- Generate and publish `feed.xml`.
- Subscribe to the private feed in a podcast app.

Done when:

- A forwarded newsletter appears as a playable podcast episode.

## Milestone 7: Resilience And Cleanup

Goal: make the MVP reliable enough to keep using.

Tasks:

- Add idempotency records in DynamoDB.
- Add structured logging with correlation IDs.
- Add CloudWatch alarms for Lambda errors and DLQ depth.
- Add retry-safe writes for RSS generation.
- Add a replay runbook for failed events.
- Tighten Terraform deploy-role permissions.

Done when:

- You can explain how to replay a failed newsletter.
- DLQ and Lambda errors alert you.
- Terraform deploy permissions are narrower than admin.

## Milestone 8: Product Polish

Goal: make the morning briefing feel good.

Tasks:

- Improve prompt style for podcast-like narration.
- Add per-source cleaner tweaks for recurring newsletters.
- Add episode title formatting.
- Add artwork support.
- Add CloudFront in front of feed/audio.
- Add a simple user config file or metadata record for voice and mode.

Done when:

- The experience feels like "forward newsletter -> morning podcast" rather than a tech demo.

