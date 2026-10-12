"""Audio mixing domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, TypeAlias


@dataclass(frozen=True, slots=True)
class AudioFile:
    """Local audio file metadata used by mixer implementations."""

    path: Path
    mime_type: str = "audio/mpeg"
    duration_seconds: float | None = None
    byte_length: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentTiming:
    """Common timing controls for an audio segment."""

    leading_silence_ms: int = 0
    trailing_silence_ms: int = 0
    fade_in_ms: int = 0
    fade_out_ms: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class NarrationSegment(SegmentTiming):
    """Spoken narration produced by an upstream TTS provider."""

    audio: AudioFile
    gain_db: float = 0.0
    label: str = "narration"


@dataclass(frozen=True, slots=True, kw_only=True)
class MusicSegment(SegmentTiming):
    """Music used as intro, outro, or a short bed."""

    audio: AudioFile
    role: Literal["intro", "outro", "bed"] = "bed"
    gain_db: float = -6.0
    label: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionSegment(SegmentTiming):
    """Short sound used between show sections."""

    audio: AudioFile
    style: str = "soft_chime"
    gain_db: float = -3.0


@dataclass(frozen=True, slots=True, kw_only=True)
class AmbienceSegment(SegmentTiming):
    """Optional ambience mixed underneath the final episode."""

    audio: AudioFile
    loop: bool = True
    gain_db: float = -18.0


ShowSegment: TypeAlias = NarrationSegment | MusicSegment | TransitionSegment


@dataclass(frozen=True, slots=True)
class ShowPlan:
    """Description of the final audio episode to produce."""

    title: str
    output: AudioFile
    segments: list[ShowSegment]
    ambience: AmbienceSegment | None = None
    normalize: bool = True
    sample_rate_hz: int = 44100
    channels: int = 2
    bitrate: str = "192k"
    metadata: dict[str, str] = field(default_factory=dict)


class AudioMixer(Protocol):
    """Audio mixer interface."""

    def mix(self, show_plan: ShowPlan) -> AudioFile:
        """Render a final audio file from a show plan."""
