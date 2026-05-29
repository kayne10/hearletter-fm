from hearletter_shared.rss import RssEpisode, build_podcast_feed


def test_build_podcast_feed_contains_enclosure() -> None:
    feed = build_podcast_feed(
        title="Hearletter FM",
        description="Private newsletter briefings.",
        feed_url="https://example.com/feed.xml",
        site_url="https://example.com",
        episodes=[
            RssEpisode(
                guid="ep_123",
                title="Morning Briefing - 2026-05-29",
                description="A private briefing.",
                audio_url="https://example.com/audio/ep_123.mp3",
                byte_length=123,
                published_at="2026-05-29T12:00:00Z",
                duration_seconds=60,
            )
        ],
    )

    assert "<title>Hearletter FM</title>" in feed
    assert 'url="https://example.com/audio/ep_123.mp3"' in feed
    assert "<guid isPermaLink=\"false\">ep_123</guid>" in feed

