"""Pluggable text-to-speech providers plus long-text chunking."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from hearletter_shared.openai_tts import (
    DEFAULT_OPENAI_TTS_INSTRUCTIONS,
    DEFAULT_OPENAI_TTS_MODEL,
    DEFAULT_OPENAI_TTS_VOICE,
    OpenAITTSConfig,
    build_openai_tts_synthesizer,
    secret_value_to_api_key,
)
from hearletter_shared.polly import (
    DEFAULT_POLLY_ENGINE,
    DEFAULT_POLLY_REGION,
    DEFAULT_POLLY_VOICE_ID,
    PollySynthesisConfig,
    build_polly_synthesizer,
)

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class AudioChunk(Protocol):
    @property
    def content(self) -> bytes: ...

    @property
    def content_type(self) -> str: ...


class TTSProvider(Protocol):
    """Anything that can turn a bounded amount of text into audio bytes."""

    max_input_chars: int

    def synthesize(self, text: str) -> AudioChunk: ...


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    content: bytes
    content_type: str
    chunk_count: int


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Pack paragraphs (then sentences, then words) into chunks of at most max_chars."""

    pieces: list[str] = []
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        for sentence in SENTENCE_SPLIT_RE.split(paragraph):
            pieces.extend(_split_words(sentence, max_chars))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _split_words(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence]
    parts: list[str] = []
    current = ""
    for word in sentence.split():
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = word[:max_chars]
    if current:
        parts.append(current)
    return parts


def synthesize_long_text(provider: TTSProvider, text: str) -> SynthesizedAudio:
    """Synthesize text of any length into one MP3.

    MP3 is a frame-based format, so concatenating same-encoder MP3 chunks yields a playable file.
    """

    chunks = chunk_text(text, provider.max_input_chars)
    if not chunks:
        raise ValueError("Cannot synthesize an empty script")
    audio = [provider.synthesize(chunk) for chunk in chunks]
    return SynthesizedAudio(
        content=b"".join(item.content for item in audio),
        content_type=audio[0].content_type or "audio/mpeg",
        chunk_count=len(chunks),
    )


def build_tts_provider(*, env: Mapping[str, str], boto3_session: Any) -> TTSProvider:
    """Select a provider from TTS_PROVIDER (openai | polly)."""

    provider = env.get("TTS_PROVIDER", "openai").strip().lower()
    if provider == "openai":
        return build_openai_tts_synthesizer(
            config=OpenAITTSConfig(
                api_key=resolve_openai_api_key(env=env, boto3_session=boto3_session),
                model=env.get("OPENAI_TTS_MODEL") or DEFAULT_OPENAI_TTS_MODEL,
                voice=env.get("OPENAI_TTS_VOICE") or DEFAULT_OPENAI_TTS_VOICE,
                instructions=env.get("OPENAI_TTS_INSTRUCTIONS") or DEFAULT_OPENAI_TTS_INSTRUCTIONS,
            )
        )
    if provider == "polly":
        return build_polly_synthesizer(
            boto3_session=boto3_session,
            config=PollySynthesisConfig(
                region_name=env.get("POLLY_REGION") or DEFAULT_POLLY_REGION,
                engine=env.get("POLLY_ENGINE") or DEFAULT_POLLY_ENGINE,
                voice_id=env.get("POLLY_VOICE_ID") or DEFAULT_POLLY_VOICE_ID,
            ),
        )
    raise ValueError(f"Unsupported TTS_PROVIDER: {provider}")


def resolve_openai_api_key(*, env: Mapping[str, str], boto3_session: Any) -> str:
    """OPENAI_API_KEY wins (local dev); otherwise read OPENAI_API_KEY_SECRET_ARN."""

    if env.get("OPENAI_API_KEY"):
        return env["OPENAI_API_KEY"]
    secret_arn = env.get("OPENAI_API_KEY_SECRET_ARN")
    if not secret_arn:
        raise RuntimeError("OpenAI TTS requires OPENAI_API_KEY or OPENAI_API_KEY_SECRET_ARN")
    response = boto3_session.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    secret_value = response.get("SecretString")
    if not secret_value:
        raise RuntimeError("OpenAI API key secret must contain SecretString")
    return secret_value_to_api_key(str(secret_value))
