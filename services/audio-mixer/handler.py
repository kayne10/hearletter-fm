# LEGACY: not used by the briefing pipeline. Safe to delete; see docs/briefing-pipeline.md.
"""Lambda handler for producing final mixed podcast episodes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from hearletter_domain.audio import (
    AudioFile,
    MusicSegment,
    NarrationSegment,
    ShowPlan,
    TransitionSegment,
)
from hearletter_domain.models import S3ObjectRef
from hearletter_events.contracts import EventEnvelope, GeneratedEpisodePayload, new_id, utc_now_iso
from hearletter_shared.audio_mixer import FfmpegAudioMixer
from hearletter_shared.event_codec import dumps_event
from hearletter_shared.lambda_events import iter_pipeline_events, log_lambda_event

DEFAULT_ASSET_ROOT = "/opt/assets"
DEFAULT_MIXER_WORK_DIR = "/tmp/hearletter-mixer"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Create final mixed episode audio from generated narration."""

    import boto3

    log_lambda_event("audio-mixer", event)
    s3_client = boto3.client("s3")
    sqs_client = boto3.client("sqs")
    mixer = FfmpegAudioMixer(
        ffmpeg_path=os.environ.get("FFMPEG_PATH", "ffmpeg"),
        work_dir=Path(os.environ.get("MIXER_WORK_DIR", DEFAULT_MIXER_WORK_DIR)),
    )
    output_events = [
        process_event(
            pipeline_event,
            s3_client=s3_client,
            mixer=mixer,
            env=os.environ,
        )
        for pipeline_event in iter_pipeline_events(event)
    ]

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
    s3_client: Any,
    mixer: Any,
    env: Any,
) -> EventEnvelope[GeneratedEpisodePayload]:
    """Mix a narration-audio event and return a generated-episode event."""

    now = utc_now_iso()
    tenant_id = str(event["tenant_id"])
    newsletter_id = str(event["newsletter_id"])
    payload = event["payload"]
    episode_id = str(payload["episode_id"])
    title = str(payload["title"])
    narration_ref = payload["narration"]
    work_dir = Path(env.get("MIXER_WORK_DIR", DEFAULT_MIXER_WORK_DIR)) / episode_id
    work_dir.mkdir(parents=True, exist_ok=True)

    narration_path = work_dir / "narration.mp3"
    download_s3_object(
        s3_client,
        S3ObjectRef(bucket=str(narration_ref["bucket"]), key=str(narration_ref["key"])),
        narration_path,
    )

    final_path = work_dir / "episode.mp3"
    show_plan = build_show_plan(
        title=title,
        narration_path=narration_path,
        output_path=final_path,
        asset_root=Path(env.get("MIXER_ASSET_ROOT", DEFAULT_ASSET_ROOT)),
        env=env,
        show_style=show_style_from_payload(payload),
    )
    mixed_audio = mix_with_passthrough_fallback(
        mixer,
        show_plan=show_plan,
        narration_path=narration_path,
        allow_passthrough=parse_bool(env.get("MIXER_ALLOW_PASSTHROUGH_ON_ERROR", "true")),
    )
    audio_key = f"audio/{tenant_id}/{episode_id}.mp3"
    upload_audio(s3_client, bucket=str(env["AUDIO_BUCKET"]), key=audio_key, path=mixed_audio.path)

    episode_payload = GeneratedEpisodePayload(
        episode_id=episode_id,
        title=title,
        audio=S3ObjectRef(bucket=str(env["AUDIO_BUCKET"]), key=audio_key),
        audio_url=f"https://example.invalid/{audio_key}",
        mime_type="audio/mpeg",
        byte_length=mixed_audio.byte_length or file_size(mixed_audio.path),
        duration_seconds=payload.get("duration_seconds"),
        published_at=now,
        notification_email=payload.get("notification_email"),
        show_style=show_style_from_payload(payload),
    )
    return EventEnvelope(
        event_id=new_id("evt"),
        event_type="episode.generated",
        schema_version="1.0",
        correlation_id=str(event["correlation_id"]),
        tenant_id=tenant_id,
        newsletter_id=newsletter_id,
        occurred_at=now,
        payload=episode_payload,
    )


def build_show_plan(
    *,
    title: str,
    narration_path: Path,
    output_path: Path,
    asset_root: Path,
    env: Any,
    show_style: dict[str, str] | None = None,
) -> ShowPlan:
    """Create a default show plan with optional local assets."""

    segments = []
    style = show_style or {}
    intro = optional_asset(
        asset_root,
        env.get("MIXER_INTRO_MUSIC") or intro_music_for_style(style.get("music")),
    )
    if intro is not None:
        segments.append(
            MusicSegment(
                audio=AudioFile(path=intro),
                role="intro",
                gain_db=float(env.get("MIXER_INTRO_GAIN_DB", "-8")),
                fade_in_ms=int(env.get("MIXER_INTRO_FADE_IN_MS", "800")),
                fade_out_ms=int(env.get("MIXER_INTRO_FADE_OUT_MS", "1200")),
                trailing_silence_ms=250,
            )
        )

    transition = optional_asset(
        asset_root,
        env.get("MIXER_TRANSITION_SOUND") or transition_sound_for_style(style.get("transition")),
    )
    if transition is not None:
        segments.append(
            TransitionSegment(
                audio=AudioFile(path=transition),
                style=str(style.get("transition", "light_chime")),
                gain_db=float(env.get("MIXER_TRANSITION_GAIN_DB", "-6")),
                trailing_silence_ms=150,
            )
        )

    segments.append(
        NarrationSegment(
            audio=AudioFile(path=narration_path),
            gain_db=float(env.get("MIXER_NARRATION_GAIN_DB", "0")),
            fade_in_ms=int(env.get("MIXER_NARRATION_FADE_IN_MS", "150")),
            fade_out_ms=int(env.get("MIXER_NARRATION_FADE_OUT_MS", "300")),
        )
    )

    outro = optional_asset(
        asset_root,
        env.get("MIXER_OUTRO_MUSIC") or outro_music_for_style(style.get("music")),
    )
    if outro is not None:
        segments.append(
            MusicSegment(
                audio=AudioFile(path=outro),
                role="outro",
                gain_db=float(env.get("MIXER_OUTRO_GAIN_DB", "-8")),
                fade_in_ms=int(env.get("MIXER_OUTRO_FADE_IN_MS", "500")),
                fade_out_ms=int(env.get("MIXER_OUTRO_FADE_OUT_MS", "1800")),
                leading_silence_ms=300,
            )
        )

    return ShowPlan(
        title=title,
        output=AudioFile(path=output_path),
        segments=segments,
        normalize=parse_bool(env.get("MIXER_NORMALIZE", "true")),
        bitrate=str(env.get("MIXER_BITRATE", "192k")),
    )


def optional_asset(asset_root: Path, relative_path: str | None) -> Path | None:
    """Return an asset path only when it exists."""

    if not relative_path:
        return None
    path = asset_root / relative_path
    return path if path.exists() else None


def show_style_from_payload(payload: Any) -> dict[str, str]:
    """Return a normalized show style from an event payload."""

    value = payload.get("show_style") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def intro_music_for_style(music: str | None) -> str:
    """Map a music style to an intro asset path."""

    paths = {
        "soft": "intro/minimalist_intro.mp3",
        "theme": "intro/morning_theme.mp3",
    }
    return paths.get((music or "theme").lower(), "intro/morning_theme.mp3")


def outro_music_for_style(music: str | None) -> str:
    """Map a music style to an outro asset path."""

    return "outro/closing_theme.mp3"


def transition_sound_for_style(transition: str | None) -> str:
    """Map a transition style to a transition asset path."""

    paths = {
        "light_chime": "transitions/soft_chime.mp3",
        "soft_chime": "transitions/soft_chime.mp3",
        "whoosh": "transitions/whoosh.mp3",
        "synth_sting": "transitions/synth_sting.mp3",
    }
    return paths.get((transition or "light_chime").lower(), "transitions/soft_chime.mp3")


def download_s3_object(s3_client: Any, ref: S3ObjectRef, path: Path) -> None:
    """Download an S3 object to a local path."""

    response = s3_client.get_object(Bucket=ref.bucket, Key=ref.key)
    path.write_bytes(response["Body"].read())


def upload_audio(s3_client: Any, *, bucket: str, key: str, path: Path) -> None:
    """Upload the final MP3 to S3."""

    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=path.read_bytes(),
        ContentType="audio/mpeg",
    )


def mix_with_passthrough_fallback(
    mixer: Any,
    *,
    show_plan: ShowPlan,
    narration_path: Path,
    allow_passthrough: bool,
) -> AudioFile:
    """Mix audio, or pass narration through when FFmpeg is unavailable."""

    try:
        return mixer.mix(show_plan)
    except FileNotFoundError as exc:
        if not allow_passthrough:
            raise
        print(
            {
                "message": "audio_mixer_passthrough_fallback",
                "reason": "mixer_executable_not_found",
                "error": str(exc),
                "narration_path": str(narration_path),
                "output_path": str(show_plan.output.path),
            }
        )
        show_plan.output.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(narration_path, show_plan.output.path)
        return AudioFile(
            path=show_plan.output.path,
            mime_type="audio/mpeg",
            byte_length=file_size(show_plan.output.path),
        )


def output_queue_urls(env: Any) -> list[str]:
    """Return downstream queues for final episode events."""

    return [
        str(env[name])
        for name in ("EPISODE_QUEUE_URL", "NOTIFICATION_QUEUE_URL")
        if env.get(name)
    ]


def parse_bool(value: str) -> bool:
    """Parse a common environment boolean string."""

    return value.lower() in {"1", "true", "yes", "y"}


def file_size(path: Path) -> int:
    """Return a local file size."""

    return path.stat().st_size
