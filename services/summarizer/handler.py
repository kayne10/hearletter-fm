"""Lambda handler for turning cleaned newsletter text into a spoken script."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
import json
import os
import re
from typing import Any

from hearletter_domain.models import ContentMode, S3ObjectRef
from hearletter_events.contracts import BriefingScriptPayload, EventEnvelope, new_id, utc_now_iso
from hearletter_shared.event_codec import dumps_event
from hearletter_shared.lambda_events import iter_pipeline_events, log_lambda_event
from hearletter_utils.text import estimate_spoken_duration_seconds

DEFAULT_VOICE = "alloy"
DEFAULT_POLLY_VOICE = "Joanna"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Create a script event for the TTS stage.

    The actual OpenAI call will be added behind a client abstraction. This skeleton establishes the
    contract and deterministic artifact location.
    """

    import boto3

    log_lambda_event("summarizer", event)
    s3_client = boto3.client("s3")
    sqs_client = boto3.client("sqs")
    output_events = [
        process_event(
            pipeline_event,
            s3_client=s3_client,
            artifact_bucket=os.environ.get("ARTIFACT_BUCKET"),
        )
        for pipeline_event in iter_pipeline_events(event)
    ]

    queue_url = os.environ.get("SCRIPT_QUEUE_URL")
    if queue_url:
        for output_event in output_events:
            sqs_client.send_message(QueueUrl=queue_url, MessageBody=dumps_event(output_event))

    return {"processed": len(output_events), "events": [output_event.to_dict() for output_event in output_events]}


def process_event(
    event: dict[str, Any],
    *,
    s3_client: Any | None = None,
    artifact_bucket: str | None = None,
) -> EventEnvelope[BriefingScriptPayload]:
    """Create a briefing-scripted event from a cleaned-newsletter event."""

    now = utc_now_iso()
    tenant_id = str(event["tenant_id"])
    newsletter_id = str(event["newsletter_id"])
    payload = event["payload"]
    ssml_key = f"scripts/{tenant_id}/{newsletter_id}/polly_ssml.xml"

    if s3_client is not None and artifact_bucket:
        clean_text_ref = payload["clean_text"]
        clean_text = read_text_from_s3(
            s3_client,
            S3ObjectRef(bucket=str(clean_text_ref["bucket"]), key=str(clean_text_ref["key"])),
        )
        story_candidates = read_story_candidates(
            s3_client,
            bucket=str(clean_text_ref["bucket"]),
            key=f"cleaned/{tenant_id}/{newsletter_id}/story_candidates.json",
        )
        context = build_podcast_context(
            title=str(payload.get("title", "Untitled newsletter")),
            source=payload.get("source"),
            clean_text=clean_text,
            story_candidates=story_candidates,
            episode_date=now[:10],
        )
        polly_ssml = build_polly_ssml(context)
        prompt = build_morning_briefing_prompt(clean_text)
        put_text(
            s3_client,
            bucket=artifact_bucket,
            key=f"scripts/{tenant_id}/{newsletter_id}/podcast_context.json",
            body=json.dumps(context, indent=2, sort_keys=True),
            content_type="application/json; charset=utf-8",
        )
        put_text(
            s3_client,
            bucket=artifact_bucket,
            key=f"scripts/{tenant_id}/{newsletter_id}/agent_prompt.txt",
            body=prompt,
            content_type="text/plain; charset=utf-8",
        )
        put_text(
            s3_client,
            bucket=artifact_bucket,
            key=ssml_key,
            body=polly_ssml,
            content_type="application/ssml+xml; charset=utf-8",
        )
        script_payload = script_event_payload(
            tenant_id=tenant_id,
            newsletter_id=newsletter_id,
            title=context["episode"]["title"],
            ssml_key=ssml_key,
            ssml_text_value=polly_ssml,
            artifact_bucket=artifact_bucket,
        )
    else:
        script_payload = BriefingScriptPayload(
            mode=ContentMode.MORNING_BRIEFING,
            title=f"Morning Briefing - {now[:10]}",
            script=S3ObjectRef(bucket="ARTIFACT_BUCKET", key=ssml_key),
            estimated_duration_seconds=None,
            voice=DEFAULT_POLLY_VOICE,
        )

    return EventEnvelope(
        event_id=new_id("evt"),
        event_type="briefing.scripted",
        schema_version="1.0",
        correlation_id=str(event["correlation_id"]),
        tenant_id=tenant_id,
        newsletter_id=newsletter_id,
        occurred_at=now,
        payload=script_payload,
    )


def read_text_from_s3(s3_client: Any, ref: S3ObjectRef) -> str:
    """Read a UTF-8 text artifact from S3."""

    response = s3_client.get_object(Bucket=ref.bucket, Key=ref.key)
    return response["Body"].read().decode("utf-8")


def read_story_candidates(s3_client: Any, *, bucket: str, key: str) -> list[dict[str, Any]]:
    """Read story candidates from S3."""

    response = s3_client.get_object(Bucket=bucket, Key=key)
    value = json.loads(response["Body"].read().decode("utf-8"))
    return value if isinstance(value, list) else []


def put_text(
    s3_client: Any,
    *,
    bucket: str,
    key: str,
    body: str,
    content_type: str,
) -> None:
    """Write a UTF-8 text artifact to S3."""

    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType=content_type,
    )


def build_morning_briefing_prompt(clean_text: str) -> str:
    """Create the prompt used to transform newsletter text into narration."""

    return (
        "Transform this newsletter into a concise, conversational morning audio briefing. "
        "Lead with the most important story, explain why it matters, remove newsletter chrome, "
        "and avoid saying phrases like 'welcome to the newsletter'.\n\n"
        f"{clean_text}"
    )


def build_podcast_context(
    *,
    title: str,
    source: str | None,
    clean_text: str,
    story_candidates: list[dict[str, Any]],
    episode_date: str | None = None,
) -> dict[str, Any]:
    """Build agent-ready context for a spoken morning briefing."""

    date = episode_date or datetime.now(UTC).date().isoformat()
    selected = story_candidates[:5]
    return {
        "episode": {
            "title": f"Morning Briefing - {date}",
            "source_title": title,
            "source": source,
            "mode": ContentMode.MORNING_BRIEFING.value,
            "target_duration_seconds": 420,
            "voice": DEFAULT_VOICE,
        },
        "editorial_direction": {
            "goal": "Turn the newsletter into a concise, conversational morning podcast segment.",
            "style": [
                "Lead with what matters, not newsletter branding.",
                "Explain why each story matters in plain language.",
                "Use natural transitions between stories.",
                "Avoid reading ads, unsubscribe text, tracking copy, and nav links.",
            ],
            "host_persona": "Smart, warm, direct morning briefing host.",
        },
        "story_candidates": [
            {
                "rank": story["rank"],
                "title": story["title"],
                "agenda_match": story.get("agenda_match"),
                "source_excerpt": story["summary_source_text"],
                "suggested_angle": suggested_angle(story),
            }
            for story in selected
        ],
        "source_stats": {
            "clean_word_count": len(clean_text.split()),
            "candidate_count": len(story_candidates),
        },
    }


def build_script_draft(context: dict[str, Any]) -> str:
    """Create a deterministic local script draft for debugging the TTS input shape."""

    episode = context["episode"]
    stories = context["story_candidates"]
    lines = [
        f"{episode['title']}.",
        "Here are the stories worth carrying into your day.",
        "",
    ]

    for story in stories:
        excerpt = first_sentence(story["source_excerpt"])
        lines.extend(
            [
                f"Story {story['rank']}: {story['title']}.",
                story["suggested_angle"],
                excerpt,
                "",
            ]
        )

    lines.append("That is your Hearletter FM briefing.")
    return "\n".join(lines).strip() + "\n"


def build_polly_ssml(context: dict[str, Any]) -> str:
    """Create Amazon Polly SSML for a podcast-like morning briefing."""

    episode = context["episode"]
    stories = context["story_candidates"]
    parts = [
        '<speak>',
        '  <prosody rate="92%">',
        f'    <p><s>{ssml_text(episode["title"])}.</s></p>',
        '    <break time="650ms"/>',
        '    <p><s>Here are the stories worth carrying into your day.</s></p>',
        '    <break time="500ms"/>',
    ]

    for story in stories:
        title = ssml_text(story["title"].rstrip("."))
        excerpt = ssml_text(clean_spoken_sentence(first_sentence(story["source_excerpt"])))
        parts.extend(
            [
                f'    <p><s>{transition_for_rank(story["rank"])}: {title}.</s></p>',
                '    <break time="350ms"/>',
                f'    <p><s>The key thing to know: {excerpt}</s></p>',
                '    <break time="700ms"/>',
            ]
        )

    parts.extend(
        [
            '    <p><s>That is your Hearletter FM briefing.</s></p>',
            '  </prosody>',
            '</speak>',
            '',
        ]
    )
    return "\n".join(parts)


def suggested_angle(story: dict[str, Any]) -> str:
    """Create a local placeholder angle an AI writer can improve."""

    agenda_match = story.get("agenda_match")
    if agenda_match:
        return f"The useful angle: this connects to {agenda_match}."
    return "The useful angle: explain the consequence for listeners before the details."


def first_sentence(value: str) -> str:
    """Return the first sentence-ish chunk from source text."""

    for separator in (". ", "? ", "! "):
        if separator in value:
            return value.split(separator, 1)[0].strip() + separator.strip()
    return value[:280].strip()


def clean_spoken_sentence(value: str) -> str:
    """Normalize source text before placing it in SSML."""

    normalized = value.replace("*", "")
    normalized = strip_emoji(normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.replace(" ,", ",")
    normalized = normalized.replace(" .", ".")
    return normalized.strip()


def ssml_text(value: str) -> str:
    """Escape text content for SSML."""

    return escape(clean_spoken_sentence(value), quote=False)


def transition_for_rank(rank: int) -> str:
    """Return a natural transition for a ranked story."""

    transitions = {
        1: "First up",
        2: "Next",
        3: "Also worth knowing",
        4: "Looking ahead",
        5: "And finally",
    }
    return transitions.get(rank, f"Story {rank}")


def strip_emoji(value: str) -> str:
    """Remove symbols that Polly may spell awkwardly."""

    return "".join(char for char in value if not is_emoji_or_symbol(char))


def is_emoji_or_symbol(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or 0x1F1E6 <= codepoint <= 0x1F1FF
    )


def script_event_payload(
    *,
    tenant_id: str,
    newsletter_id: str,
    title: str,
    ssml_key: str,
    ssml_text_value: str,
    artifact_bucket: str,
) -> BriefingScriptPayload:
    """Create script payload metadata after writing a local or S3 SSML artifact."""

    return BriefingScriptPayload(
        mode=ContentMode.MORNING_BRIEFING,
        title=title,
        script=S3ObjectRef(bucket=artifact_bucket, key=ssml_key),
        estimated_duration_seconds=estimate_spoken_duration_seconds(ssml_text_value),
        voice=DEFAULT_POLLY_VOICE,
    )
