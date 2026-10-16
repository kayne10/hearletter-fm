from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from hearletter_domain.briefing import BriefingScript
from hearletter_shared.notification import EpisodeEmail
from hearletter_shared.object_store import LocalObjectStore, put_json
from hearletter_shared.tts import chunk_text


def load_producer() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "services" / "episode-producer" / "handler.py"
    spec = importlib.util.spec_from_file_location("episode_producer_handler", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


producer = load_producer()


@dataclass(frozen=True)
class FakeAudio:
    content: bytes
    content_type: str = "audio/mpeg"


class FakeTTS:
    max_input_chars = 60

    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize(self, text: str) -> FakeAudio:
        assert len(text) <= self.max_input_chars
        self.calls.append(text)
        return FakeAudio(content=f"<{len(self.calls)}>".encode())


SCRIPT = BriefingScript(
    episode_id="ep_20261012_abc",
    title="Morning Briefing - 2026-10-12",
    episode_date="2026-10-12",
    forwarder="reader@example.com",
    sources=("dan@tldrnewsletter.com", "crew@morningbrew.com"),
    text="Good morning.\n\n" + "A fairly long story sentence goes here. " * 4 + "\n\nBye.",
    created_at="2026-10-12T14:00:00Z",
)


def test_produces_one_mp3_and_one_email_idempotently(tmp_path: Path) -> None:
    processed = LocalObjectStore(tmp_path / "processed")
    audio = LocalObjectStore(tmp_path / "audio")
    put_json(processed, "scripts/x/script.json", SCRIPT.to_dict())
    tts = FakeTTS()
    sent: list[EpisodeEmail] = []

    def produce() -> dict[str, object]:
        return producer.produce_episode(
            processed,
            "scripts/x/script.json",
            audio=audio,
            tts_factory=lambda: tts,
            link_for=lambda key: f"https://signed/{key}",
            send_email=lambda email: sent.append(email) or "msg-1",
            from_email="no-reply@beta.hearletter.fm",
            url_ttl_seconds=3600,
        )

    first = produce()
    second = produce()

    mp3_key = "episodes/reader_example.com/2026-10-12/ep_20261012_abc.mp3"
    assert first["status"] == "sent"
    assert second["status"] == "already_notified"
    assert len(tts.calls) > 1  # long script was chunked
    expected_audio = b"".join(f"<{i}>".encode() for i in range(1, len(tts.calls) + 1))
    assert audio.get_bytes(mp3_key) == expected_audio
    assert len(sent) == 1
    assert sent[0].to_email == "reader@example.com"
    assert sent[0].from_email == "no-reply@beta.hearletter.fm"
    assert sent[0].audio_url == f"https://signed/{mp3_key}"
    episode_key = "episodes/reader_example.com/2026-10-12/ep_20261012_abc.json"
    episode = json.loads(audio.get_bytes(episode_key))
    assert episode["tts_chunks"] == len(tts.calls)


def test_chunk_text_respects_limit_and_keeps_all_words() -> None:
    long_paragraph = "Sentence number one is here. " * 30
    text = f"First paragraph is short.\n\n{long_paragraph}\n\n{'x' * 50}"
    chunks = chunk_text(text, 100)

    assert all(len(chunk) <= 100 for chunk in chunks)
    assert " ".join(chunks).split() == text.split()


def test_script_refs_decodes_s3_keys() -> None:
    event = {
        "Records": [
            {
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": "processed"},
                    "object": {"key": "scripts/a%40b/2026-10-12/script.json"},
                },
            }
        ]
    }
    assert producer.script_refs(event) == [("processed", "scripts/a@b/2026-10-12/script.json")]


def test_each_version_of_a_day_gets_its_own_mp3_and_email(tmp_path: Path) -> None:
    processed = LocalObjectStore(tmp_path / "processed")
    audio = LocalObjectStore(tmp_path / "audio")
    version_two = dataclasses.replace(SCRIPT, episode_id="ep_20261012_def", version=2)
    put_json(processed, "scripts/x/v1/script.json", SCRIPT.to_dict())
    put_json(processed, "scripts/x/v2/script.json", version_two.to_dict())
    sent: list[EpisodeEmail] = []

    def produce(key: str) -> dict[str, object]:
        return producer.produce_episode(
            processed,
            key,
            audio=audio,
            tts_factory=FakeTTS,
            link_for=lambda audio_key: audio_key,
            send_email=lambda email: sent.append(email) or "msg",
            from_email="no-reply@beta.hearletter.fm",
            url_ttl_seconds=3600,
        )

    statuses = [
        produce(k)["status"]
        for k in (
            "scripts/x/v1/script.json",
            "scripts/x/v2/script.json",
            "scripts/x/v2/script.json",
        )
    ]

    assert statuses == ["sent", "sent", "already_notified"]
    assert [email.audio_url.rsplit("/", 1)[1] for email in sent] == [
        "ep_20261012_abc.mp3",
        "ep_20261012_def.mp3",
    ]
