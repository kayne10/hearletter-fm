You are a Senior Cloud and Data Engineer
You use best code practices
Your python uses type-hinting and modular design
You have strong skills in building infrastructure with Terraform

## Goal

A user forwards an email newsletter to a special email address (e.g. listen@mailcast.app).

The system:
- Receives the email
- Extracts the meaningful content from the newsletter
- Optionally summarizes/reformats it for spoken audio
- Converts it to speech
- Produces an MP3
- Adds it to a private RSS feed
- Makes it playable in podcast apps and optionally Amazon Alexa

Core UX:
“Forward newsletter → listen like a morning podcast”

The long-term vision is more:
“AI-generated personalized morning show”

## High-level architecture (AWS-first)

I want this to be cheap, event-driven, and serverless-first.

Use:
- AWS SES (inbound email)
- AWS Lambda
- S3
- SQS
- OpenAI and Polly for text-to-speech
- RSS feed generation

Expected flow:
```
Forwarded Email
        ↓
AWS SES inbound
        ↓
S3 (store raw MIME email)
        ↓
Lambda: parse email
        ↓
SQS
        ↓
Lambda: content extraction / cleanup
        ↓
Lambda: summarize/rewrite for spoken format
        ↓
Lambda: TTS generation
        ↓
S3 (mp3)
        ↓
Lambda: update RSS feed XML
        ↓
Private podcast URL
```

I want the architecture to be resilient and loosely coupled.
Do not build one giant Lambda.

Prefer:
```
SES
 ↓
Lambda (parse)
 ↓
SQS
 ↓
Lambda (clean)
 ↓
SQS
 ↓
Lambda (summarize)
 ↓
SQS
 ↓
Lambda (tts)
 ↓
Lambda (rss update)
```
Goal:
- retries
- idempotency
- dead-letter queues
- observable pipeline
- easy future extensibility

AWS SES can receive inbound mail and trigger Lambda/S3 receipt rules. The email body will likely arrive as raw MIME stored in S3 and must be parsed.

Product requirements
MVP

## User flow:

User forwards newsletter to:
listen@mydomain.com
System receives email
Extract:
- sender
- subject
- newsletter title
- clean article content
Remove:
- unsubscribe links
- ads
- sponsor sections
- legal footer
- nav junk
- “view in browser”

Generate one of two outputs:
Mode A: Full Read
Reads cleaned newsletter almost verbatim.

Mode B: Morning Briefing (preferred)
Transforms newsletter into a conversational spoken briefing.

Example desired style:

Bad:

“Welcome to TechCrunch. Here are five AI stories…”

Good:

“Today’s big AI story is OpenAI launching X. Here’s why people care…”

Feels podcast-like and natural.

## Audio generation

Initially use:

OpenAI TTS

For natural voice quality.

Later:

ElevenLabs support
Amazon Polly fallback

Need pluggable TTS provider abstraction.

Example interface:

interface TTSProvider {
  synthesize(text: string): Promise<AudioFile>
}
RSS feed

Need a private podcast feed.

Requirements:

RSS XML generation
Podcast-compatible
MP3 enclosure URLs
chronological episodes
metadata
artwork support later

Output:

<rss>
  <channel>
    <item>
      <title>Morning Briefing - 2026-05-29</title>
      <enclosure url="episode.mp3" />
    </item>
</channel>
</rss>

Hosted in:

S3
optionally CloudFront

Goal:
User subscribes in:

Apple Podcasts
Overcast
Pocket Casts

Then new episodes appear automatically.

Podcast feeds are standard RSS with audio enclosures.

Alexa integration (future)

Not priority.

Potential ideas:

Option A (simple)

Always overwrite:

latest.mp3

Then Alexa routine plays:

“Alexa, play my morning briefing”

via a static audio URL.

Option B

Custom Alexa skill:

“Alexa, open Morning Briefing”

Skill fetches latest generated episode and streams it.

Technical preferences

## Language:
Python

## Infra:
Prefer AWS CDK Python or Terraform

## Repo structure should be monorepo-friendly:

```
hearletter-fm/
├── infra/ # CDK or Terraform
├── services/
│   ├── email-parser/
│   ├── newsletter-cleaner/
│   ├── summarizer/
│   ├── tts/
│   ├── rss-generator/
│   └── shared/
├── packages/
│   ├── domain/
│   ├── types/
│   └── utils/
└── docs/
```

## Design priorities

Optimize for:

Low cost
Event-driven architecture
Easy local development
Idempotency
Clean domain boundaries
Extensibility
Good developer experience

Avoid premature overengineering.

I want help with

Please help me design:

Domain architecture
Event contracts between services
SQS queue topology
Database choices (if any)
SES inbound setup
MIME parsing strategy
Newsletter cleaning strategy
RSS generation
Failure handling
Local dev/testing approach
CDK infrastructure design
A practical MVP implementation plan

I want a pragmatic architecture review, not enterprise architecture