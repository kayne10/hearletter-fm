"""Lambda handler for updating the private podcast RSS feed."""

from __future__ import annotations

from typing import Any

from hearletter_shared.lambda_events import iter_pipeline_events, log_lambda_event
from hearletter_shared.rss import RssEpisode, build_podcast_feed


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Build a feed from the provided generated episode.

    The production version should load existing episode metadata from DynamoDB, sort by
    `published_at`, and write the resulting feed XML to S3.
    """

    log_lambda_event("rss-generator", event)
    outputs = [process_event(pipeline_event) for pipeline_event in iter_pipeline_events(event)]
    return {"processed": len(outputs), "feeds": outputs}


def process_event(event: dict[str, Any]) -> dict[str, Any]:
    """Build a feed for a single generated-episode event."""

    payload = event["payload"]
    episode = RssEpisode(
        guid=str(payload["episode_id"]),
        title=str(payload["title"]),
        description="Private Hearletter FM briefing.",
        audio_url=str(payload["audio_url"]),
        byte_length=int(payload["byte_length"]),
        published_at=str(payload["published_at"]),
        duration_seconds=payload.get("duration_seconds"),
    )
    feed_xml = build_podcast_feed(
        title="Hearletter FM",
        description="Private newsletter briefings.",
        feed_url="https://example.invalid/feed.xml",
        site_url="https://example.invalid",
        episodes=[episode],
    )
    return {"feed_xml": feed_xml}
