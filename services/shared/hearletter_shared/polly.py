"""Amazon Polly synthesis helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_POLLY_REGION = "us-east-1"
DEFAULT_POLLY_ENGINE = "generative"
DEFAULT_POLLY_VOICE_ID = "Joanna"
DEFAULT_POLLY_OUTPUT_FORMAT = "mp3"
POLLY_MAX_INPUT_CHARS = 3000


@dataclass(frozen=True, slots=True)
class PollySynthesisConfig:
    """Amazon Polly synthesis options."""

    region_name: str = DEFAULT_POLLY_REGION
    engine: str = DEFAULT_POLLY_ENGINE
    voice_id: str = DEFAULT_POLLY_VOICE_ID
    output_format: str = DEFAULT_POLLY_OUTPUT_FORMAT


@dataclass(frozen=True, slots=True)
class PollyAudio:
    """Synthesized Polly audio bytes and metadata."""

    content: bytes
    content_type: str
    request_characters: int | None = None


class PollySynthesizer:
    """Small wrapper around Amazon Polly's SynthesizeSpeech API."""

    max_input_chars = POLLY_MAX_INPUT_CHARS

    def __init__(self, *, polly_client: Any, config: PollySynthesisConfig) -> None:
        self._polly_client = polly_client
        self._config = config

    def synthesize(self, text: str) -> PollyAudio:
        """Synthesize plain text (at most max_input_chars) to MP3 bytes."""

        response = self._polly_client.synthesize_speech(
            Engine=self._config.engine,
            OutputFormat=self._config.output_format,
            Text=text,
            TextType="text",
            VoiceId=self._config.voice_id,
        )
        stream = response["AudioStream"]
        return PollyAudio(
            content=bytes(stream.read()),
            content_type=str(response.get("ContentType", "audio/mpeg")),
            request_characters=response.get("RequestCharacters"),
        )


def build_polly_synthesizer(
    *,
    boto3_session: Any,
    config: PollySynthesisConfig,
) -> PollySynthesizer:
    """Build a Polly synthesizer from a boto3 session."""

    return PollySynthesizer(
        polly_client=boto3_session.client("polly", region_name=config.region_name),
        config=config,
    )
