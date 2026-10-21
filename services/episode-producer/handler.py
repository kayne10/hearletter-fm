"""Lambda: briefing script in S3 -> one MP3 -> email the listener.

Trigger: S3 ObjectCreated on the processed bucket (scripts/**/script.json), invoked
asynchronously. Lambda retries failures twice, then sends the event to the episode DLQ.

When FEED_BASE_URL is set (CloudFront + secret path token), the producer also rewrites the
listener's private podcast feed after each episode, and the email links to the permanent
CloudFront URL instead of an expiring presigned S3 link. Invoke with {"action": "rebuild_feeds"}
to regenerate every feed from existing episodes.

Idempotent: every file is keyed by the episode id, which is derived from the script's content
fingerprint. A per-episode notified marker prevents a second email when S3 or Lambda delivers
the event again, while a new version of the day's episode still gets its own MP3 and email.
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
from hearletter_shared.podcast_feed import (
    DEFAULT_FEED_DAYS,
    FEED_CACHE_CONTROL,
    FeedEpisode,
    build_feed_xml,
    feed_key,
    recent_episodes,
)
from hearletter_shared.tts import TTSProvider, build_tts_provider, synthesize_long_text

AUDIO_CONTENT_TYPE = "audio/mpeg"
REBUILD_FEEDS_ACTION = "rebuild_feeds"
SUMMARY_MAX_CHARS = 600


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    import boto3

    log_lambda_event("episode-producer", event)
    session = boto3.Session()
    s3_client = session.client("s3")
    ses_client = session.client("ses", region_name=os.environ.get("SES_SEND_REGION") or None)
    audio_bucket = os.environ["AUDIO_BUCKET"]
    audio = S3ObjectStore(bucket=audio_bucket, s3_client=s3_client)
    feed_base_url = os.environ.get("FEED_BASE_URL") or None
    feed_days = int(os.environ.get("FEED_DAYS", DEFAULT_FEED_DAYS))
    ttl = int(os.environ.get("AUDIO_URL_TTL_SECONDS", DEFAULT_URL_TTL_SECONDS))
    from_email = os.environ["NOTIFICATION_FROM_EMAIL"]

    if event.get("action") == REBUILD_FEEDS_ACTION:
        if feed_base_url is None:
            raise RuntimeError("FEED_BASE_URL is not configured")
        feeds = rebuild_all_feeds(audio, base_url=feed_base_url, days=feed_days)
        log_json("feeds_rebuilt", feeds=feeds)
        return {"feeds": feeds}

    def link_for(audio_key: str) -> str:
        if feed_base_url:
            return f"{feed_base_url}/{audio_key}"
        return presigned_audio_url(s3_client, bucket=audio_bucket, key=audio_key, expires_in=ttl)

    results = [
        produce_episode(
            S3ObjectStore(bucket=bucket, s3_client=s3_client),
            key,
            audio=audio,
            tts_factory=lambda: build_tts_provider(env=os.environ, boto3_session=session),
            link_for=link_for,
            send_email=lambda email: send_episode_email(ses_client, email),
            from_email=from_email,
            url_ttl_seconds=None if feed_base_url else ttl,
            feed_base_url=feed_base_url,
            feed_days=feed_days,
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
    url_ttl_seconds: int | None,
    feed_base_url: str | None = None,
    feed_days: int = DEFAULT_FEED_DAYS,
) -> dict[str, Any]:
    script = BriefingScript.from_dict(get_json(processed, script_key))
    prefix = episode_prefix(script)
    audio_key = f"{prefix}{script.episode_id}.mp3"
    marker_key = f"{prefix}{script.episode_id}.notified.json"

    if not audio.exists(audio_key):
        synthesized = synthesize_long_text(tts_factory(), script.text)
        audio.put_bytes(audio_key, synthesized.content, content_type=AUDIO_CONTENT_TYPE)
        put_json(
            audio,
            f"{prefix}{script.episode_id}.json",
            {
                "episode_id": script.episode_id,
                "title": script.title,
                "episode_date": script.episode_date,
                "version": script.version,
                "fingerprint": script.fingerprint,
                "sources": list(script.sources),
                "audio_key": audio_key,
                "byte_length": len(synthesized.content),
                "tts_chunks": synthesized.chunk_count,
                "published_at": now_iso(),
                "summary": episode_summary(script),
            },
        )

    feed_url = None
    if feed_base_url:
        feed_url = rebuild_feed(
            audio, slugify(script.forwarder), base_url=feed_base_url, days=feed_days
        )

    if audio.exists(marker_key):
        return {"status": "already_notified", "episode_id": script.episode_id}

    message_id = send_email(
        EpisodeEmail(
            to_email=script.forwarder,
            from_email=from_email,
            title=script.title,
            audio_url=link_for(audio_key),
            sources=script.source_names or script.sources,
            url_ttl_seconds=url_ttl_seconds,
            feed_url=feed_url,
        )
    )
    put_json(audio, marker_key, {"message_id": message_id, "sent_at": now_iso()})
    return {
        "status": "sent",
        "episode_id": script.episode_id,
        "audio_key": audio_key,
        "message_id": message_id,
    }


def episode_summary(script: BriefingScript) -> str:
    """Podcast show notes: sources plus the script's opening paragraph."""

    opening = script.text.split("\n\n", 1)[0].strip()
    if len(opening) > SUMMARY_MAX_CHARS:
        opening = opening[:SUMMARY_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    names = ", ".join(script.source_names or script.sources)
    return f"From {names}. {opening}" if names else opening


def rebuild_feed(audio: ObjectStore, forwarder_slug: str, *, base_url: str, days: int) -> str:
    """Rewrite one listener's feed from their episode metadata; return the feed URL."""

    episodes = [
        FeedEpisode.from_metadata(get_json(audio, key))
        for key in audio.list_keys(f"episodes/{forwarder_slug}/")
        if is_episode_metadata(key)
    ]
    key = feed_key(forwarder_slug)
    feed_url = f"{base_url}/{key}"
    today = datetime.now(UTC).date()
    xml = build_feed_xml(
        recent_episodes(episodes, today=today, days=days), base_url=base_url, feed_url=feed_url
    )
    audio.put_bytes(
        key,
        xml.encode("utf-8"),
        content_type="application/rss+xml; charset=utf-8",
        cache_control=FEED_CACHE_CONTROL,
    )
    return feed_url


def rebuild_all_feeds(audio: ObjectStore, *, base_url: str, days: int) -> list[str]:
    slugs = sorted(
        {key.split("/")[1] for key in audio.list_keys("episodes/") if key.count("/") >= 3}
    )
    return [rebuild_feed(audio, slug, base_url=base_url, days=days) for slug in slugs]


def is_episode_metadata(key: str) -> bool:
    """`<episode_id>.json` (or the older `episode.json`), not the `.notified.json` markers."""

    return key.endswith(".json") and not key.endswith(".notified.json")


def episode_prefix(script: BriefingScript) -> str:
    return f"episodes/{slugify(script.forwarder)}/{script.episode_date}/"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
