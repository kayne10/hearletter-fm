"""Lambda handler for turning cleaned newsletter text into a spoken script."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from typing import Any

from hearletter_domain.models import ContentMode, S3ObjectRef
from hearletter_events.contracts import BriefingScriptPayload, EventEnvelope, new_id, utc_now_iso
from hearletter_shared.event_codec import dumps_event
from hearletter_shared.lambda_events import iter_pipeline_events, log_lambda_event
from hearletter_utils.text import estimate_spoken_duration_seconds

DEFAULT_VOICE = "alloy"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Create a script event for the TTS stage.

    The actual OpenAI call will be added behind a client abstraction. This skeleton establishes the
    contract and deterministic artifact location.
    """

    import boto3

    log_lambda_event("summarizer", event)
    sqs_client = boto3.client("sqs")
    output_events = [process_event(pipeline_event) for pipeline_event in iter_pipeline_events(event)]

    queue_url = os.environ.get("SCRIPT_QUEUE_URL")
    if queue_url:
        for output_event in output_events:
            sqs_client.send_message(QueueUrl=queue_url, MessageBody=dumps_event(output_event))

    return {"processed": len(output_events), "events": [output_event.to_dict() for output_event in output_events]}


def process_event(event: dict[str, Any]) -> EventEnvelope[BriefingScriptPayload]:
    """Create a briefing-scripted event from a cleaned-newsletter event."""

    now = utc_now_iso()
    tenant_id = str(event["tenant_id"])
    newsletter_id = str(event["newsletter_id"])
    script_key = f"scripts/{tenant_id}/{newsletter_id}/script.txt"

    payload = BriefingScriptPayload(
        mode=ContentMode.MORNING_BRIEFING,
        title=f"Morning Briefing - {now[:10]}",
        script=S3ObjectRef(bucket="ARTIFACT_BUCKET", key=script_key),
        estimated_duration_seconds=None,
        voice=DEFAULT_VOICE,
    )
    return EventEnvelope(
        event_id=new_id("evt"),
        event_type="briefing.scripted",
        schema_version="1.0",
        correlation_id=str(event["correlation_id"]),
        tenant_id=tenant_id,
        newsletter_id=newsletter_id,
        occurred_at=now,
        payload=payload,
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


def script_event_payload(
    *,
    tenant_id: str,
    newsletter_id: str,
    title: str,
    script_key: str,
    script_text: str,
    artifact_bucket: str,
) -> BriefingScriptPayload:
    """Create script payload metadata after writing a local or S3 script artifact."""

    return BriefingScriptPayload(
        mode=ContentMode.MORNING_BRIEFING,
        title=title,
        script=S3ObjectRef(bucket=artifact_bucket, key=script_key),
        estimated_duration_seconds=estimate_spoken_duration_seconds(script_text),
        voice=DEFAULT_VOICE,
    )
