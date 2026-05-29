# Hearletter FM Architecture

## Product Goal

Hearletter FM is a private audio pipeline for newsletters:

```text
Forward newsletter -> listen like a morning podcast
```

The MVP should produce a private RSS feed with MP3 episodes generated from forwarded newsletters. The architecture is AWS-first, low-cost, event-driven, and split into small services so each stage can retry independently.

## MVP Pipeline

```text
SES inbound receipt rule
  -> S3 raw email bucket
  -> email-parser Lambda
  -> parsed-newsletter SQS
  -> newsletter-cleaner Lambda
  -> cleaned-newsletter SQS
  -> summarizer Lambda
  -> briefing-script SQS
  -> tts Lambda
  -> generated-episode SQS
  -> rss-generator Lambda
  -> S3 private feed bucket
```

## Service Boundaries

| Service | Responsibility | Durable Input | Durable Output |
| --- | --- | --- | --- |
| `email-parser` | Parse SES/S3 raw MIME, extract headers and HTML/text bodies | Raw MIME in S3 | `ParsedNewsletterEvent` |
| `newsletter-cleaner` | Remove newsletter chrome, sponsor/legal/footer sections, produce readable article text | Parsed event | `CleanedNewsletterEvent` |
| `summarizer` | Produce spoken full-read or morning-briefing script | Cleaned event | `BriefingScriptEvent` |
| `tts` | Convert script to MP3 through a pluggable provider | Script event | MP3 in S3 plus `GeneratedEpisodeEvent` |
| `rss-generator` | Update private podcast RSS feed with chronological episodes | Episode event | `feed.xml` in S3 |

## Storage Choices

Use S3 as the durable system of record for large artifacts:

- Raw MIME: `raw-email/<tenant_id>/<message_id>.eml`
- Parsed JSON: `parsed/<tenant_id>/<newsletter_id>.json`
- Cleaned text: `cleaned/<tenant_id>/<newsletter_id>.txt`
- Scripts: `scripts/<tenant_id>/<newsletter_id>.txt`
- Audio: `audio/<tenant_id>/<episode_id>.mp3`
- RSS: `feeds/<tenant_id>/feed.xml`

Use DynamoDB only for metadata and idempotency, not large content:

- `tenant_id`
- `newsletter_id`
- `message_id`
- current processing `status`
- artifact S3 keys
- provider metadata
- RSS ordering metadata
- TTL-backed idempotency records if needed

For the first local scaffold, the metadata table is represented in Terraform but service code can proceed with S3-only persistence until idempotency needs become concrete.

## Queue Topology

Each pipeline edge gets its own SQS queue and dead-letter queue:

- `parsed-newsletter`
- `cleaned-newsletter`
- `briefing-script`
- `generated-episode`

This keeps failure domains small. For example, an OpenAI outage should not cause SES parsing retries or re-clean already-cleaned newsletters.

Recommended defaults:

- Standard queues for the MVP.
- `maxReceiveCount = 3` to `5`.
- Visibility timeout at least six times the Lambda timeout.
- Message bodies carry typed contracts and S3 pointers, not large content.

FIFO queues can be introduced later for strict per-tenant RSS ordering, but standard queues are cheaper and sufficient if `rss-generator` reads episode metadata sorted by timestamp.

## Idempotency

Every event includes:

- `event_id`
- `event_type`
- `schema_version`
- `correlation_id`
- `tenant_id`
- `newsletter_id`
- `occurred_at`

Each service writes deterministic artifact keys and can safely overwrite artifacts for the same `newsletter_id`. The metadata table can enforce stage-level idempotency with a conditional write such as:

```text
PK = TENANT#<tenant_id>
SK = STAGE#<stage>#NEWSLETTER#<newsletter_id>
condition attribute_not_exists(PK)
```

## SES Inbound Setup

SES inbound receiving requires:

1. Verify the domain in SES.
2. Add MX records pointing inbound mail to the SES receiving endpoint for the selected region.
3. Create an SES receipt rule set.
4. Add a receipt rule for `listen@<domain>`.
5. Store the raw MIME message in S3.
6. Invoke `email-parser` with the SES receipt event.

Important detail: SES inbound receiving is region-specific. Pick a region that supports SES receiving before deploying.

## MIME Parsing Strategy

Use Python's standard `email` package with `policy.default` to parse raw MIME safely:

- Preserve original headers.
- Prefer `text/html` when available for cleaning.
- Fall back to `text/plain`.
- Capture attachments only as metadata in the MVP.
- Store raw and parsed artifacts for reproducibility.

Forwarded newsletters often contain nested MIME parts and original sender headers. The parser should extract both envelope-level forwarding metadata and newsletter-level hints where possible.

## Newsletter Cleaning Strategy

MVP cleaning should be layered:

1. Parse HTML with BeautifulSoup.
2. Remove obvious non-content elements: scripts, styles, nav, footer, tracking pixels.
3. Drop lines matching known junk phrases such as unsubscribe, view in browser, sponsor, privacy policy.
4. Convert remaining HTML to readable markdown/plain text.
5. Keep deterministic cleaning so test fixtures can lock expected behavior.

Later improvements can add readability extraction and publisher-specific adapters for high-value newsletters.

## RSS Generation

Generate podcast-compatible RSS 2.0 with audio enclosures:

- Stable channel GUID.
- Episode title.
- Episode GUID.
- `pubDate`.
- `enclosure` URL, length, and MIME type.
- Private feed URL hosted through S3 or CloudFront.

For the MVP, keep feed access obscure and private-by-URL. For a stronger privacy model, put CloudFront in front of S3 and use signed URLs or basic auth at the edge.

## Observability

Use structured logs with these fields in every service:

- `correlation_id`
- `event_id`
- `event_type`
- `tenant_id`
- `newsletter_id`
- `stage`

Use CloudWatch alarms for:

- DLQ visible messages > 0.
- Lambda errors.
- Lambda throttles.
- TTS provider failures.

## Practical Tradeoffs

- Use standard SQS first; add FIFO only when ordering bugs appear.
- Use S3 for content and DynamoDB for metadata/idempotency.
- Keep one Lambda per stage, but share common contract and storage utilities.
- Generate RSS from metadata, not by appending XML in-place inside queue handlers.
- Start with OpenAI TTS, then add Polly fallback behind the provider abstraction.

