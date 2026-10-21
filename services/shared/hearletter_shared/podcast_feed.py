"""Private podcast RSS feed of a listener's recent episodes.

Episode metadata comes from the `<episode_id>.json` files the producer writes next to each MP3.
The feed lists the last `days` days, newest first, keeping only the latest version of each day.
Enclosure URLs point at the token-protected CloudFront path, so any podcast app can download
them without expiring signatures.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from xml.etree import ElementTree as ET

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ET.register_namespace("itunes", ITUNES_NS)

FEED_TITLE = "Hearletter FM"
FEED_DESCRIPTION = "Your forwarded newsletters as a private morning briefing."
DEFAULT_FEED_DAYS = 7
FEED_CACHE_CONTROL = "max-age=300"


@dataclass(frozen=True, slots=True)
class FeedEpisode:
    episode_id: str
    title: str
    episode_date: str
    published_at: str
    audio_key: str
    byte_length: int
    version: int = 1
    summary: str = ""

    @classmethod
    def from_metadata(cls, value: Mapping[str, Any]) -> FeedEpisode:
        return cls(
            episode_id=str(value["episode_id"]),
            title=str(value["title"]),
            episode_date=str(value["episode_date"]),
            published_at=str(value.get("published_at") or f"{value['episode_date']}T12:00:00Z"),
            audio_key=str(value["audio_key"]),
            byte_length=int(value.get("byte_length", 0)),
            version=int(value.get("version", 1)),
            summary=str(value.get("summary", "")),
        )


def recent_episodes(
    episodes: Iterable[FeedEpisode], *, today: date, days: int = DEFAULT_FEED_DAYS
) -> list[FeedEpisode]:
    """Latest version per day within the window, newest day first."""

    oldest = (today - timedelta(days=days - 1)).isoformat()
    latest: dict[str, FeedEpisode] = {}
    for episode in episodes:
        if episode.episode_date < oldest:
            continue
        current = latest.get(episode.episode_date)
        if current is None or (episode.version, episode.published_at) > (
            current.version,
            current.published_at,
        ):
            latest[episode.episode_date] = episode
    return sorted(latest.values(), key=lambda episode: episode.episode_date, reverse=True)


def build_feed_xml(
    episodes: Iterable[FeedEpisode],
    *,
    base_url: str,
    feed_url: str,
    title: str = FEED_TITLE,
    description: str = FEED_DESCRIPTION,
) -> str:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    for tag, text in (
        ("title", title),
        ("link", feed_url),
        ("description", description),
        ("language", "en-us"),
    ):
        ET.SubElement(channel, tag).text = text
    for tag, text in (
        ("author", title),
        ("explicit", "false"),
        ("block", "Yes"),  # keep this private feed out of podcast directories
        ("type", "episodic"),
    ):
        ET.SubElement(channel, f"{{{ITUNES_NS}}}{tag}").text = text

    for episode in episodes:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = episode.title
        ET.SubElement(item, "description").text = episode.summary or episode.title
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = episode.episode_id
        ET.SubElement(item, "pubDate").text = rfc2822(episode.published_at)
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": f"{base_url.rstrip('/')}/{episode.audio_key}",
                "length": str(episode.byte_length),
                "type": "audio/mpeg",
            },
        )
    body = ET.tostring(rss, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def rfc2822(iso_timestamp: str) -> str:
    return format_datetime(datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00")))


def feed_key(forwarder_slug: str) -> str:
    return f"feeds/{forwarder_slug}/feed.xml"
