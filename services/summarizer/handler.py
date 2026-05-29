"""Lambda handler for turning cleaned newsletter text into a spoken script."""

from __future__ import annotations

from typing import Any

from hearletter_domain.models import ContentMode, S3ObjectRef
from hearletter_events.contracts import BriefingScriptPayload, EventEnvelope, new_id, utc_now_iso

DEFAULT_VOICE = "alloy"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Create a script event for the TTS stage.

    The actual OpenAI call will be added behind a client abstraction. This skeleton establishes the
    contract and deterministic artifact location.
    """

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
    scripted_event = EventEnvelope(
        event_id=new_id("evt"),
        event_type="briefing.scripted",
        schema_version="1.0",
        correlation_id=str(event["correlation_id"]),
        tenant_id=tenant_id,
        newsletter_id=newsletter_id,
        occurred_at=now,
        payload=payload,
    )
    return scripted_event.to_dict()


def build_morning_briefing_prompt(clean_text: str) -> str:
    """Create the prompt used to transform newsletter text into narration."""

    return (
        "Transform this newsletter into a concise, conversational morning audio briefing. "
        "Lead with the most important story, explain why it matters, remove newsletter chrome, "
        "and avoid saying phrases like 'welcome to the newsletter'.\n\n"
        f"{clean_text}"
    )

