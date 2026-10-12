# LEGACY: not used by the briefing pipeline. Safe to delete; see docs/briefing-pipeline.md.
"""Typed event contracts for Hearletter FM."""

from hearletter_events.contracts import (
    BriefingScriptPayload,
    CleanedNewsletterPayload,
    EventEnvelope,
    GeneratedEpisodePayload,
    NarrationAudioPayload,
    ParsedNewsletterPayload,
)

__all__ = [
    "BriefingScriptPayload",
    "CleanedNewsletterPayload",
    "EventEnvelope",
    "GeneratedEpisodePayload",
    "NarrationAudioPayload",
    "ParsedNewsletterPayload",
]
