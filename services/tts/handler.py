"""Lambda handler for text-to-speech generation."""

from __future__ import annotations

import os
from typing import Any

from hearletter_domain.models import S3ObjectRef
from hearletter_events.contracts import EventEnvelope, NarrationAudioPayload, new_id, utc_now_iso
from hearletter_shared.event_codec import dumps_event
from hearletter_shared.lambda_events import iter_pipeline_events, log_lambda_event
from hearletter_shared.openai_tts import (
    DEFAULT_OPENAI_TTS_INSTRUCTIONS,
    DEFAULT_OPENAI_TTS_MODEL,
    DEFAULT_OPENAI_TTS_RESPONSE_FORMAT,
    DEFAULT_OPENAI_TTS_VOICE,
    OpenAITTSConfig,
    build_openai_tts_synthesizer,
    secret_value_to_api_key,
)
from hearletter_shared.polly import (
    DEFAULT_POLLY_ENGINE,
    DEFAULT_POLLY_REGION,
    DEFAULT_POLLY_VOICE_ID,
    PollySynthesisConfig,
    build_polly_synthesizer,
)

DEFAULT_TTS_PROVIDER = "polly"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Generate episode metadata after TTS audio is created."""

    import boto3

    log_lambda_event("tts", event)
    boto3_session = boto3.Session()
    s3_client = boto3_session.client("s3")
    sqs_client = boto3.client("sqs")
    output_events = []
    for pipeline_event in iter_pipeline_events(event):
        show_style = show_style_from_payload(pipeline_event.get("payload", {}))
        tts_synthesizer = build_tts_synthesizer(
            env=os.environ,
            boto3_session=boto3_session,
            show_style=show_style,
        )
        output_events.append(
            process_event(pipeline_event, s3_client=s3_client, tts_synthesizer=tts_synthesizer)
        )

    for queue_url in output_queue_urls(os.environ):
        for output_event in output_events:
            sqs_client.send_message(QueueUrl=queue_url, MessageBody=dumps_event(output_event))

    return {
        "processed": len(output_events),
        "events": [output_event.to_dict() for output_event in output_events],
    }


def process_event(
    event: dict[str, Any],
    *,
    s3_client: Any | None = None,
    tts_synthesizer: Any | None = None,
) -> EventEnvelope[NarrationAudioPayload]:
    """Create a narration-audio event from a briefing-scripted event."""

    now = utc_now_iso()
    tenant_id = str(event["tenant_id"])
    newsletter_id = str(event["newsletter_id"])
    episode_id = new_id("ep")
    audio_key = f"narration/{tenant_id}/{episode_id}.mp3"
    title = str(event["payload"].get("title", f"Morning Briefing - {now[:10]}"))
    byte_length = 0

    if s3_client is not None and tts_synthesizer is not None:
        ssml_ref = event["payload"]["script"]
        validate_script_ref(ssml_ref)
        ssml = read_text_from_s3(
            s3_client,
            S3ObjectRef(bucket=str(ssml_ref["bucket"]), key=str(ssml_ref["key"])),
        )
        audio = tts_synthesizer.synthesize_ssml(ssml)
        byte_length = len(audio.content)
        s3_client.put_object(
            Bucket=os.environ["AUDIO_BUCKET"],
            Key=audio_key,
            Body=audio.content,
            ContentType=audio.content_type,
        )

    payload = NarrationAudioPayload(
        episode_id=episode_id,
        title=title,
        narration=S3ObjectRef(bucket=os.environ.get("AUDIO_BUCKET", "AUDIO_BUCKET"), key=audio_key),
        mime_type="audio/mpeg",
        byte_length=byte_length,
        duration_seconds=None,
        generated_at=now,
        notification_email=event["payload"].get("notification_email"),
        show_style=event["payload"].get("show_style"),
    )
    return EventEnvelope(
        event_id=new_id("evt"),
        event_type="narration.audio.generated",
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


def output_queue_urls(env: Any) -> list[str]:
    """Return queues that should receive generated episode events."""

    return [
        str(env[name])
        for name in ("NARRATION_QUEUE_URL",)
        if env.get(name)
    ]


def build_tts_synthesizer(
    *,
    env: Any,
    boto3_session: Any,
    show_style: dict[str, str] | None = None,
) -> Any:
    """Build the configured TTS provider."""

    provider = str(env.get("TTS_PROVIDER", DEFAULT_TTS_PROVIDER)).strip().lower()
    if provider == "polly":
        return build_polly_synthesizer(
            boto3_session=boto3_session,
            config=PollySynthesisConfig(
                region_name=env.get("POLLY_REGION", DEFAULT_POLLY_REGION),
                engine=env.get("POLLY_ENGINE", DEFAULT_POLLY_ENGINE),
                voice_id=env.get("POLLY_VOICE_ID", DEFAULT_POLLY_VOICE_ID),
            ),
        )
    if provider == "openai":
        return build_openai_tts_synthesizer(
            config=openai_config_from_env(
                env=env,
                boto3_session=boto3_session,
                show_style=show_style,
            )
        )
    raise ValueError(f"Unsupported TTS_PROVIDER: {provider}")


def openai_config_from_env(
    *,
    env: Any,
    boto3_session: Any,
    show_style: dict[str, str] | None = None,
) -> OpenAITTSConfig:
    """Build OpenAI TTS config from env and optional show style."""

    style = show_style or {}
    return OpenAITTSConfig(
        api_key=resolve_openai_api_key(env=env, boto3_session=boto3_session),
        model=env.get("OPENAI_TTS_MODEL", DEFAULT_OPENAI_TTS_MODEL),
        voice=env.get("OPENAI_TTS_VOICE") or openai_voice_for_style(style.get("voice")),
        response_format=env.get(
            "OPENAI_TTS_RESPONSE_FORMAT",
            DEFAULT_OPENAI_TTS_RESPONSE_FORMAT,
        ),
        instructions=openai_instructions_for_style(
            base_instructions=env.get("OPENAI_TTS_INSTRUCTIONS", DEFAULT_OPENAI_TTS_INSTRUCTIONS),
            show_style=style,
        ),
    )


def show_style_from_payload(payload: Any) -> dict[str, str]:
    """Return a normalized show style from an event payload."""

    value = payload.get("show_style") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def openai_voice_for_style(voice_style: str | None) -> str:
    """Map semantic voice style to an OpenAI voice."""

    voices = {
        "calm": DEFAULT_OPENAI_TTS_VOICE,
        "warm": DEFAULT_OPENAI_TTS_VOICE,
        "bright": "coral",
        "deep": "cedar",
    }
    if not voice_style:
        return DEFAULT_OPENAI_TTS_VOICE
    return voices.get(voice_style.lower(), voice_style)


def openai_instructions_for_style(
    *,
    base_instructions: str,
    show_style: dict[str, str],
) -> str:
    """Combine base TTS instructions with show style controls."""

    parts = [base_instructions.strip()]
    voice = show_style.get("voice")
    pace = show_style.get("pace")
    if voice:
        parts.append(f"Voice style: {voice}.")
    if pace:
        parts.append(f"Pace: {pace}.")
    return " ".join(part for part in parts if part)


def resolve_openai_api_key(*, env: Any, boto3_session: Any) -> str:
    """Resolve the OpenAI API key from env or Secrets Manager."""

    direct_value = env.get("OPENAI_API_KEY")
    if direct_value:
        return str(direct_value)

    secret_arn = env.get("OPENAI_API_KEY_SECRET_ARN")
    if not secret_arn:
        raise RuntimeError("OpenAI TTS requires OPENAI_API_KEY or OPENAI_API_KEY_SECRET_ARN")

    secrets_client = boto3_session.client("secretsmanager")
    response = secrets_client.get_secret_value(SecretId=str(secret_arn))
    secret_value = response.get("SecretString")
    if not secret_value:
        raise RuntimeError("OpenAI API key secret must contain SecretString")
    return secret_value_to_api_key(str(secret_value))
