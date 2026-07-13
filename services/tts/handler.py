"""Lambda handler for text-to-speech generation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Protocol

from hearletter_domain.models import S3ObjectRef
from hearletter_events.contracts import GeneratedEpisodePayload, EventEnvelope, new_id, utc_now_iso
from hearletter_shared.event_codec import dumps_event
from hearletter_shared.lambda_events import iter_pipeline_events, log_lambda_event


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    """Result returned by a TTS provider."""

    content: bytes
    mime_type: str
    duration_seconds: int | None = None


class TTSProvider(Protocol):
    """Provider contract for pluggable TTS backends."""

    def synthesize(self, *, text: str, voice: str) -> SynthesizedAudio:
        """Synthesize speech from text."""


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Generate episode metadata after TTS audio is created."""

    import boto3

    log_lambda_event("tts", event)
    sqs_client = boto3.client("sqs")
    output_events = [process_event(pipeline_event) for pipeline_event in iter_pipeline_events(event)]

    queue_url = os.environ.get("EPISODE_QUEUE_URL")
    if queue_url:
        for output_event in output_events:
            sqs_client.send_message(QueueUrl=queue_url, MessageBody=dumps_event(output_event))

    return {"processed": len(output_events), "events": [output_event.to_dict() for output_event in output_events]}


def process_event(event: dict[str, Any]) -> EventEnvelope[GeneratedEpisodePayload]:
    """Create a generated-episode event from a briefing-scripted event."""

    now = utc_now_iso()
    tenant_id = str(event["tenant_id"])
    newsletter_id = str(event["newsletter_id"])
    episode_id = new_id("ep")
    audio_key = f"audio/{tenant_id}/{episode_id}.mp3"
    title = str(event["payload"].get("title", f"Morning Briefing - {now[:10]}"))

    payload = GeneratedEpisodePayload(
        episode_id=episode_id,
        title=title,
        audio=S3ObjectRef(bucket="AUDIO_BUCKET", key=audio_key),
        audio_url=f"https://example.invalid/{audio_key}",
        mime_type="audio/mpeg",
        byte_length=0,
        duration_seconds=None,
        published_at=now,
    )
    return EventEnvelope(
        event_id=new_id("evt"),
        event_type="episode.generated",
        schema_version="1.0",
        correlation_id=str(event["correlation_id"]),
        tenant_id=tenant_id,
        newsletter_id=newsletter_id,
        occurred_at=now,
        payload=payload,
    )
