"""Core domain value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ContentMode(StrEnum):
    """Supported newsletter-to-audio modes."""

    FULL_READ = "full_read"
    MORNING_BRIEFING = "morning_briefing"


@dataclass(frozen=True, slots=True)
class S3ObjectRef:
    """Reference to an object stored in S3."""

    bucket: str
    key: str


@dataclass(frozen=True, slots=True)
class AudioArtifact:
    """Generated audio object metadata."""

    ref: S3ObjectRef
    url: str
    mime_type: str
    byte_length: int
    duration_seconds: int | None = None

