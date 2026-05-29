# Event Contracts

Events are small JSON messages passed through SQS. Large content should live in S3 and be referenced by key.

## Envelope

All events share this envelope:

```json
{
  "event_id": "evt_01h...",
  "event_type": "newsletter.cleaned",
  "schema_version": "1.0",
  "correlation_id": "msg_01h...",
  "tenant_id": "default",
  "newsletter_id": "nws_01h...",
  "occurred_at": "2026-05-29T14:30:00Z",
  "payload": {}
}
```

## `newsletter.parsed`

Produced by `email-parser`.

```json
{
  "raw_email": {
    "bucket": "hearletter-raw-email",
    "key": "raw-email/default/message.eml"
  },
  "sender": "newsletter@example.com",
  "recipient": "listen@example.com",
  "subject": "This Week in AI",
  "received_at": "2026-05-29T14:30:00Z",
  "html": {
    "bucket": "hearletter-artifacts",
    "key": "parsed/default/nws_123/body.html"
  },
  "text": {
    "bucket": "hearletter-artifacts",
    "key": "parsed/default/nws_123/body.txt"
  }
}
```

## `newsletter.cleaned`

Produced by `newsletter-cleaner`.

```json
{
  "title": "This Week in AI",
  "source": "Example Newsletter",
  "clean_text": {
    "bucket": "hearletter-artifacts",
    "key": "cleaned/default/nws_123/content.txt"
  },
  "removed_sections": ["unsubscribe", "tracking_pixel", "footer"],
  "word_count": 1380
}
```

## `briefing.scripted`

Produced by `summarizer`.

```json
{
  "mode": "morning_briefing",
  "title": "Morning Briefing - 2026-05-29",
  "script": {
    "bucket": "hearletter-artifacts",
    "key": "scripts/default/nws_123/script.txt"
  },
  "estimated_duration_seconds": 420,
  "voice": "alloy"
}
```

## `episode.generated`

Produced by `tts`.

```json
{
  "episode_id": "ep_01h...",
  "title": "Morning Briefing - 2026-05-29",
  "audio": {
    "bucket": "hearletter-audio",
    "key": "audio/default/ep_123.mp3"
  },
  "audio_url": "https://podcast.example.com/audio/default/ep_123.mp3",
  "mime_type": "audio/mpeg",
  "byte_length": 1234567,
  "duration_seconds": 420,
  "published_at": "2026-05-29T14:45:00Z"
}
```

