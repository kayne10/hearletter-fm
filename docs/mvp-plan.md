# Practical MVP Plan

For a restart-friendly roadmap with account setup gates, see [Milestones](milestones.md).

## Phase 1: Repo and Contracts

- Establish monorepo layout.
- Define typed event contracts.
- Add Lambda handler skeletons for each stage.
- Add Terraform skeleton for S3, SQS, Lambda, and DynamoDB.
- Add local tests for contract serialization and deterministic RSS generation.

## Phase 2: Email Ingestion

- Configure SES domain receiving.
- Store raw MIME email in S3.
- Parse MIME with Python's `email` package.
- Emit `newsletter.parsed` events.
- Add fixtures with forwarded newsletter samples.

## Phase 3: Cleaning

- Implement HTML cleanup with BeautifulSoup.
- Convert newsletter body to readable text.
- Remove common newsletter junk.
- Write cleaned text to S3.
- Emit `newsletter.cleaned`.

## Phase 4: Morning Briefing Script

- Add an OpenAI summarizer client.
- Support two modes:
  - `full_read`
  - `morning_briefing`
- Store the generated script in S3.
- Emit `briefing.scripted`.

## Phase 5: TTS and Audio

- Add `TTSProvider` protocol.
- Implement OpenAI provider first.
- Add Polly fallback provider later.
- Store MP3 in S3.
- Emit `episode.generated`.

## Phase 6: Private RSS Feed

- Generate podcast-compatible RSS.
- Publish `feed.xml` to S3.
- Optionally put CloudFront in front of feed and audio.
- Validate feed in Apple Podcasts/Overcast/Pocket Casts.

## Near-Term Non-Goals

- Multi-user billing.
- Alexa custom skill.
- Publisher-specific scraping adapters.
- Complex orchestration with Step Functions.
- Full web application.
