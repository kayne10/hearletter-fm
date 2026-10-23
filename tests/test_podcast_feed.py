from __future__ import annotations

from datetime import date
from xml.etree import ElementTree as ET

from hearletter_shared.podcast_feed import ITUNES_NS, FeedEpisode, build_feed_xml, recent_episodes

BASE = "https://d123.cloudfront.net/secret-token"


def episode(day: str, version: int = 1, published: str = "12:00:00") -> FeedEpisode:
    return FeedEpisode(
        episode_id=f"ep_{day}_v{version}",
        title=f"Morning Briefing - {day}" + (f" (update {version - 1})" if version > 1 else ""),
        episode_date=day,
        published_at=f"{day}T{published}Z",
        audio_key=f"episodes/reader_example.com/{day}/ep_{day}_v{version}.mp3",
        byte_length=1000 * version,
        version=version,
        summary=f"From TLDR. Story for {day}.",
    )


def test_recent_episodes_keeps_last_week_latest_version_newest_first() -> None:
    episodes = [
        episode("2026-10-13"),  # 8 days before the 20th: outside the window
        episode("2026-10-14"),
        episode("2026-10-19"),
        episode("2026-10-20"),
        episode("2026-10-20", version=2, published="15:00:00"),
    ]

    selected = recent_episodes(episodes, today=date(2026, 10, 20), days=7)

    assert [(e.episode_date, e.version) for e in selected] == [
        ("2026-10-20", 2),
        ("2026-10-19", 1),
        ("2026-10-14", 1),
    ]


def test_feed_is_podcast_rss_with_tokenised_enclosures() -> None:
    xml = build_feed_xml(
        [episode("2026-10-20")], base_url=BASE, feed_url=f"{BASE}/feeds/r/feed.xml"
    )

    root = ET.fromstring(xml)
    channel = root.find("channel")
    assert root.tag == "rss" and channel is not None
    assert channel.findtext(f"{{{ITUNES_NS}}}block") == "Yes"
    item = channel.find("item")
    assert item is not None
    enclosure = item.find("enclosure")
    assert enclosure is not None
    assert enclosure.get("url") == (
        f"{BASE}/episodes/reader_example.com/2026-10-20/ep_2026-10-20_v1.mp3"
    )
    assert enclosure.get("type") == "audio/mpeg" and enclosure.get("length") == "1000"
    assert item.findtext("guid") == "ep_2026-10-20_v1"
    assert item.findtext("pubDate") == "Tue, 20 Oct 2026 12:00:00 +0000"
    assert item.findtext("description") == "From TLDR. Story for 2026-10-20."


def test_old_metadata_without_version_or_summary_still_loads() -> None:
    legacy = {
        "episode_id": "ep_1",
        "title": "Morning Briefing - 2026-10-14",
        "episode_date": "2026-10-14",
        "audio_key": "episodes/r/2026-10-14/ep_1.mp3",
        "byte_length": 5,
        "published_at": "2026-10-14T14:45:30Z",
    }
    loaded = FeedEpisode.from_metadata(legacy)
    assert loaded.version == 1 and loaded.summary == ""
