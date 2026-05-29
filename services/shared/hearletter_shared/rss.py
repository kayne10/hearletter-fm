"""Podcast RSS generation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import format_datetime
from xml.etree.ElementTree import Element, SubElement, tostring


@dataclass(frozen=True, slots=True)
class RssEpisode:
    """Episode metadata needed for a podcast RSS item."""

    guid: str
    title: str
    description: str
    audio_url: str
    byte_length: int
    published_at: str
    duration_seconds: int | None = None


def build_podcast_feed(
    *,
    title: str,
    description: str,
    feed_url: str,
    site_url: str,
    episodes: list[RssEpisode],
) -> str:
    """Build a small RSS 2.0 podcast feed."""

    rss = Element(
        "rss",
        {
            "version": "2.0",
            "xmlns:atom": "http://www.w3.org/2005/Atom",
            "xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        },
    )
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = title
    SubElement(channel, "description").text = description
    SubElement(channel, "link").text = site_url
    SubElement(channel, "atom:link", {"href": feed_url, "rel": "self", "type": "application/rss+xml"})

    for episode in episodes:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = episode.title
        SubElement(item, "description").text = episode.description
        SubElement(item, "guid", {"isPermaLink": "false"}).text = episode.guid
        SubElement(item, "pubDate").text = _rss_date(episode.published_at)
        SubElement(
            item,
            "enclosure",
            {
                "url": episode.audio_url,
                "length": str(episode.byte_length),
                "type": "audio/mpeg",
            },
        )
        if episode.duration_seconds is not None:
            SubElement(item, "itunes:duration").text = str(episode.duration_seconds)

    xml_body = tostring(rss, encoding="unicode", short_empty_elements=True)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_body}\n'


def _rss_date(value: str) -> str:
    from datetime import UTC, datetime

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return format_datetime(parsed.astimezone(UTC))
