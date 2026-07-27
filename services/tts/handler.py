"""Lambda handler for text-to-speech generation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Protocol

from hearletter_domain.models import S3ObjectRef
from hearletter_events.contracts import GeneratedEpisodePayload, EventEnvelope, new_id, utc_now_iso
from hearletter_shared.event_codec import dumps_event
from hearletter_shared.lambda_events import iter_pipeline_events, log_lambda_event
from hearletter_shared.polly import (
    DEFAULT_POLLY_ENGINE,
    DEFAULT_POLLY_REGION,
    DEFAULT_POLLY_VOICE_ID,
    PollySynthesisConfig,
    build_polly_synthesizer,
)


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
    boto3_session = boto3.Session()
    s3_client = boto3_session.client("s3")
    sqs_client = boto3.client("sqs")
    polly_synthesizer = build_polly_synthesizer(
        boto3_session=boto3_session,
        config=PollySynthesisConfig(
            region_name=os.environ.get("POLLY_REGION", DEFAULT_POLLY_REGION),
            engine=os.environ.get("POLLY_ENGINE", DEFAULT_POLLY_ENGINE),
            voice_id=os.environ.get("POLLY_VOICE_ID", DEFAULT_POLLY_VOICE_ID),
        ),
    )
    output_events = [
        process_event(pipeline_event, s3_client=s3_client, polly_synthesizer=polly_synthesizer)
        for pipeline_event in iter_pipeline_events(event)
    ]

    queue_url = os.environ.get("EPISODE_QUEUE_URL")
    if queue_url:
        for output_event in output_events:
            sqs_client.send_message(QueueUrl=queue_url, MessageBody=dumps_event(output_event))

    return {"processed": len(output_events), "events": [output_event.to_dict() for output_event in output_events]}


def process_event(
    event: dict[str, Any],
    *,
    s3_client: Any | None = None,
    polly_synthesizer: Any | None = None,
) -> EventEnvelope[GeneratedEpisodePayload]:
    """Create a generated-episode event from a briefing-scripted event."""

    now = utc_now_iso()
    tenant_id = str(event["tenant_id"])
    newsletter_id = str(event["newsletter_id"])
    episode_id = new_id("ep")
    audio_key = f"audio/{tenant_id}/{episode_id}.mp3"
    title = str(event["payload"].get("title", f"Morning Briefing - {now[:10]}"))
    byte_length = 0

    if s3_client is not None and polly_synthesizer is not None:
        ssml_ref = event["payload"]["script"]
        validate_script_ref(ssml_ref)
        ssml = read_text_from_s3(
            s3_client,
            S3ObjectRef(bucket=str(ssml_ref["bucket"]), key=str(ssml_ref["key"])),
        )
        audio = polly_synthesizer.synthesize_ssml(ssml)
        byte_length = len(audio.content)
        s3_client.put_object(
            Bucket=os.environ["AUDIO_BUCKET"],
            Key=audio_key,
            Body=audio.content,
            ContentType=audio.content_type,
        )

    payload = GeneratedEpisodePayload(
        episode_id=episode_id,
        title=title,
        audio=S3ObjectRef(bucket=os.environ.get("AUDIO_BUCKET", "AUDIO_BUCKET"), key=audio_key),
        audio_url=f"https://example.invalid/{audio_key}",
        mime_type="audio/mpeg",
        byte_length=byte_length,
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


def read_text_from_s3(s3_client: Any, ref: S3ObjectRef) -> str:
    """Read a UTF-8 text artifact from S3."""

    response = s3_client.get_object(Bucket=ref.bucket, Key=ref.key)
    return response["Body"].read().decode("utf-8")


def validate_script_ref(ref: Any) -> None:
    """Fail fast for placeholder artifact pointers from stale/upstream events."""

    bucket = str(ref.get("bucket", "")) if isinstance(ref, dict) else ""
    key = str(ref.get("key", "")) if isinstance(ref, dict) else ""
    if not bucket or bucket == "ARTIFACT_BUCKET":
        raise RuntimeError(f"Invalid TTS script artifact bucket: bucket={bucket!r}, key={key!r}")
