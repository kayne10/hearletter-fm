"""OpenAI text-to-speech helpers."""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_TTS_VOICE = "marin"
DEFAULT_OPENAI_TTS_RESPONSE_FORMAT = "mp3"
DEFAULT_OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
DEFAULT_OPENAI_TTS_INSTRUCTIONS = (
    "Sound like a warm, natural morning podcast host. Keep the delivery conversational, "
    "clear, lightly energetic, and never salesy."
)
OPENAI_TTS_MAX_INPUT_CHARS = 4096

HttpPost = Callable[[str, dict[str, str], bytes], tuple[bytes, str]]


@dataclass(frozen=True, slots=True)
class OpenAITTSConfig:
    """OpenAI speech synthesis options."""

    api_key: str
    model: str = DEFAULT_OPENAI_TTS_MODEL
    voice: str = DEFAULT_OPENAI_TTS_VOICE
    response_format: str = DEFAULT_OPENAI_TTS_RESPONSE_FORMAT
    instructions: str = DEFAULT_OPENAI_TTS_INSTRUCTIONS
    api_url: str = DEFAULT_OPENAI_TTS_URL


@dataclass(frozen=True, slots=True)
class OpenAIAudio:
    """Synthesized OpenAI audio bytes and metadata."""

    content: bytes
    content_type: str
    request_characters: int | None = None


class OpenAITTSSynthesizer:
    """Small wrapper around OpenAI's speech API."""

    def __init__(self, *, config: OpenAITTSConfig, http_post: HttpPost | None = None) -> None:
        self._config = config
        self._http_post = http_post or post_json

    def synthesize_ssml(self, ssml: str) -> OpenAIAudio:
        """Convert SSML to plain text and synthesize it to MP3 bytes."""

        input_text = ssml_to_text(ssml)
        if len(input_text) > OPENAI_TTS_MAX_INPUT_CHARS:
            raise ValueError(
                "OpenAI TTS input is too long: "
                f"{len(input_text)} chars > {OPENAI_TTS_MAX_INPUT_CHARS} chars"
            )

        body = json.dumps(
            {
                "model": self._config.model,
                "voice": self._config.voice,
                "input": input_text,
                "instructions": self._config.instructions,
                "response_format": self._config.response_format,
            }
        ).encode("utf-8")
        content, content_type = self._http_post(
            self._config.api_url,
            {
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            },
            body,
        )
        return OpenAIAudio(
            content=content,
            content_type=content_type or "audio/mpeg",
            request_characters=len(input_text),
        )


def build_openai_tts_synthesizer(
    *,
    config: OpenAITTSConfig,
    http_post: HttpPost | None = None,
) -> OpenAITTSSynthesizer:
    """Build an OpenAI TTS synthesizer."""

    return OpenAITTSSynthesizer(config=config, http_post=http_post)


def post_json(url: str, headers: dict[str, str], body: bytes) -> tuple[bytes, str]:
    """Post JSON and return response bytes plus content type."""

    request = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return bytes(response.read()), str(response.headers.get("Content-Type", "audio/mpeg"))


def ssml_to_text(ssml: str) -> str:
    """Convert a simple SSML document into plain text for OpenAI TTS."""

    try:
        root = ElementTree.fromstring(ssml)
    except ElementTree.ParseError:
        return normalize_text(ssml)
    return normalize_text(" ".join(root.itertext()))


def normalize_text(value: str) -> str:
    """Normalize whitespace after SSML text extraction."""

    return re.sub(r"\s+", " ", value).strip()


def secret_value_to_api_key(secret_value: str) -> str:
    """Extract an API key from either a plain secret string or JSON secret."""

    stripped = secret_value.strip()
    if not stripped.startswith("{"):
        return stripped

    decoded: Any = json.loads(stripped)
    if not isinstance(decoded, dict):
        raise ValueError("OpenAI API key secret JSON must be an object")
    for key in ("OPENAI_API_KEY", "openai_api_key", "api_key"):
        value = decoded.get(key)
        if value:
            return str(value)
    raise ValueError("OpenAI API key secret JSON must contain api_key")
