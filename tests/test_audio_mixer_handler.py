# LEGACY: not used by the briefing pipeline. Safe to delete; see docs/briefing-pipeline.md.
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from hearletter_domain.audio import AudioFile, ShowPlan


def load_audio_mixer_handler() -> Any:
    handler_path = Path(__file__).resolve().parents[1] / "services" / "audio-mixer" / "handler.py"
    spec = importlib.util.spec_from_file_location("test_audio_mixer_handler", handler_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load audio mixer handler from {handler_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBody:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def read(self) -> bytes:
        return self._content


class FakeS3Client:
    def __init__(self) -> None:
        self.objects = {("audio-bucket", "narration/default/ep_123.mp3"): b"narration"}
        self.puts: list[dict[str, Any]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, FakeBody]:
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}

    def put_object(self, **kwargs: Any) -> None:
        self.puts.append(kwargs)


class FakeMixer:
    def __init__(self) -> None:
        self.plans: list[ShowPlan] = []

    def mix(self, show_plan: ShowPlan) -> AudioFile:
        self.plans.append(show_plan)
        show_plan.output.path.write_bytes(b"mixed")
        return AudioFile(path=show_plan.output.path, byte_length=5)


class MissingFfmpegMixer:
    def mix(self, show_plan: ShowPlan) -> AudioFile:
        raise FileNotFoundError("ffmpeg")


def test_audio_mixer_process_event_uploads_final_episode(tmp_path: Path) -> None:
    handler = load_audio_mixer_handler()
    fake_s3 = FakeS3Client()
    fake_mixer = FakeMixer()

    output_event = handler.process_event(
        narration_event(),
        s3_client=fake_s3,
        mixer=fake_mixer,
        env={
            "AUDIO_BUCKET": "audio-bucket",
            "MIXER_WORK_DIR": str(tmp_path),
            "MIXER_ASSET_ROOT": str(tmp_path / "assets"),
        },
    )

    assert output_event.event_type == "episode.generated"
    assert output_event.payload.episode_id == "ep_123"
    assert output_event.payload.audio.key == "audio/default/ep_123.mp3"
    assert output_event.payload.byte_length == 5
    assert output_event.payload.notification_email == "listener@example.com"
    assert output_event.payload.show_style["pace"] == "slow"
    assert fake_s3.puts[0]["Body"] == b"mixed"
    assert fake_s3.puts[0]["ContentType"] == "audio/mpeg"
    assert len(fake_mixer.plans[0].segments) == 1


def test_audio_mixer_passes_narration_through_when_ffmpeg_is_missing(tmp_path: Path) -> None:
    handler = load_audio_mixer_handler()
    fake_s3 = FakeS3Client()

    output_event = handler.process_event(
        narration_event(),
        s3_client=fake_s3,
        mixer=MissingFfmpegMixer(),
        env={
            "AUDIO_BUCKET": "audio-bucket",
            "MIXER_WORK_DIR": str(tmp_path),
            "MIXER_ASSET_ROOT": str(tmp_path / "assets"),
        },
    )

    assert output_event.event_type == "episode.generated"
    assert output_event.payload.byte_length == len(b"narration")
    assert fake_s3.puts[0]["Body"] == b"narration"
    assert fake_s3.puts[0]["Key"] == "audio/default/ep_123.mp3"


def test_build_show_plan_uses_show_style_assets(tmp_path: Path) -> None:
    handler = load_audio_mixer_handler()
    asset_root = tmp_path / "assets"
    intro = asset_root / "intro" / "minimalist_intro.mp3"
    transition = asset_root / "transitions" / "soft_chime.mp3"
    outro = asset_root / "outro" / "closing_theme.mp3"
    for path in (intro, transition, outro):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")

    show_plan = handler.build_show_plan(
        title="Morning Briefing",
        narration_path=tmp_path / "narration.mp3",
        output_path=tmp_path / "episode.mp3",
        asset_root=asset_root,
        env={},
        show_style={
            "voice": "calm",
            "pace": "slow",
            "music": "soft",
            "transition": "light_chime",
        },
    )

    assert len(show_plan.segments) == 4
    assert show_plan.segments[0].audio.path == intro
    assert show_plan.segments[1].audio.path == transition
    assert show_plan.segments[2].audio.path == tmp_path / "narration.mp3"
    assert show_plan.segments[3].audio.path == outro


def narration_event() -> dict[str, Any]:
    return {
        "event_id": "evt_123",
        "event_type": "narration.audio.generated",
        "schema_version": "1.0",
        "correlation_id": "msg_123",
        "tenant_id": "default",
        "newsletter_id": "nws_123",
        "occurred_at": "2026-07-30T12:00:00Z",
        "payload": {
            "episode_id": "ep_123",
            "title": "Morning Briefing",
            "narration": {"bucket": "audio-bucket", "key": "narration/default/ep_123.mp3"},
            "mime_type": "audio/mpeg",
            "byte_length": 123,
            "duration_seconds": None,
            "generated_at": "2026-07-30T12:00:00Z",
            "notification_email": "listener@example.com",
            "show_style": {
                "voice": "calm",
                "pace": "slow",
                "music": "soft",
                "transition": "light_chime",
            },
        },
    }
