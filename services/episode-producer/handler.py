"""Lambda: briefing script in S3 -> one MP3 -> email the listener.

Trigger: S3 ObjectCreated on the processed bucket (scripts/**/script.json), invoked
asynchronously. Lambda retries failures twice, then sends the event to the episode DLQ.

Idempotent: the MP3 key is derived from the deterministic episode id, and a notified.json
marker prevents a second email when S3 or Lambda delivers the event again.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote_plus

from hearletter_domain.briefing import BriefingScript, slugify
from hearletter_shared.lambda_events import log_json, log_lambda_event
from hearletter_shared.notification import (
    DEFAULT_URL_TTL_SECONDS,
    EpisodeEmail,
    presigned_audio_url,
    send_episode_email,
)
from hearletter_shared.object_store import ObjectStore, S3ObjectStore, get_json, put_json
from hearletter_shared.tts import TTSProvider, build_tts_provider, synthesize_long_text

AUDIO_CONTENT_TYPE = "audio/mpeg"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    import boto3

    log_lambda_event("episode-producer", event)
    session = boto3.Session()
    s3_client = session.client("s3")
    ses_client = session.client("ses", region_name=os.environ.get("SES_SEND_REGION") or None)
    audio_bucket = os.environ["AUDIO_BUCKET"]
    ttl = int(os.environ.get("AUDIO_URL_TTL_SECONDS", DEFAULT_URL_TTL_SECONDS))
    from_email = os.environ["NOTIFICATION_FROM_EMAIL"]

    results = [
        produce_episode(
            S3ObjectStore(bucket=bucket, s3_client=s3_client),
            key,
            audio=S3ObjectStore(bucket=audio_bucket, s3_client=s3_client),
            tts_factory=lambda: build_tts_provider(env=os.environ, boto3_session=session),
            link_for=lambda audio_key: presigned_audio_url(
                s3_client, bucket=audio_bucket, key=audio_key, expires_in=ttl
            ),
            send_email=lambda email: send_episode_email(ses_client, email),
            from_email=from_email,
            url_ttl_seconds=ttl,
        )
        for bucket, key in script_refs(event)
    ]
    log_json("episodes_produced", results=results)
    return {"processed": len(results), "results": results}


def script_refs(event: dict[str, Any]) -> list[tuple[str, str]]:
    """(bucket, key) pairs from an S3 event notification."""

    return [
        (str(record["s3"]["bucket"]["name"]), unquote_plus(str(record["s3"]["object"]["key"])))
        for record in event.get("Records", [])
        if str(record.get("eventName", "")).startswith("ObjectCreated")
    ]


def produce_episode(
    processed: ObjectStore,
    script_key: str,
    *,
    audio: ObjectStore,
    tts_factory: Callable[[], TTSProvider],
    link_for: Callable[[str], str],
    send_email: Callable[[EpisodeEmail], str | None],
    from_email: str,
    url_ttl_seconds: int,
) -> dict[str, Any]:
    script = BriefingScript.from_dict(get_json(processed, script_key))
    prefix = episode_prefix(script)
    audio_key = f"{prefix}{script.episode_id}.mp3"
    marker_key = f"{prefix}notified.json"

    if not audio.exists(audio_key):
        synthesized = synthesize_long_text(tts_factory(), script.text)
        audio.put_bytes(audio_key, synthesized.content, content_type=AUDIO_CONTENT_TYPE)
        put_json(
            audio,
            f"{prefix}episode.json",
            {
                "episode_id": script.episode_id,
                "title": script.title,
                "episode_date": script.episode_date,
                "sources": list(script.sources),
                "audio_key": audio_key,
                "byte_length": len(synthesized.content),
                "tts_chunks": synthesized.chunk_count,
                "published_at": now_iso(),
            },
        )

    if audio.exists(marker_key):
        return {"status": "already_notified", "episode_id": script.episode_id}

    message_id = send_email(
        EpisodeEmail(
            to_email=script.forwarder,
            from_email=from_email,
            title=script.title,
            audio_url=link_for(audio_key),
            sources=script.sources,
            url_ttl_seconds=url_ttl_seconds,
        )
    )
    put_json(audio, marker_key, {"message_id": message_id, "sent_at": now_iso()})
    return {
        "status": "sent",
        "episode_id": script.episode_id,
        "audio_key": audio_key,
        "message_id": message_id,
    }


def episode_prefix(script: BriefingScript) -> str:
    return f"episodes/{slugify(script.forwarder)}/{script.episode_date}/"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
