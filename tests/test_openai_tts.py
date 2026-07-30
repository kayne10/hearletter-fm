from __future__ import annotations

import json

import pytest
from hearletter_shared.openai_tts import (
    OPENAI_TTS_MAX_INPUT_CHARS,
    OpenAITTSConfig,
    OpenAITTSSynthesizer,
    secret_value_to_api_key,
    ssml_to_text,
)


def test_openai_tts_converts_ssml_and_posts_speech_request() -> None:
    calls: list[dict[str, object]] = []

    def fake_post(url: str, headers: dict[str, str], body: bytes) -> tuple[bytes, str]:
        calls.append(
            {
                "url": url,
                "headers": headers,
                "body": json.loads(body.decode("utf-8")),
            }
        )
        return b"mp3-bytes", "audio/mpeg"

    synthesizer = OpenAITTSSynthesizer(
        config=OpenAITTSConfig(api_key="sk-test", model="gpt-4o-mini-tts", voice="coral"),
        http_post=fake_post,
    )

    audio = synthesizer.synthesize_ssml("<speak><p><s>Hello there.</s></p></speak>")

    assert audio.content == b"mp3-bytes"
    assert audio.content_type == "audio/mpeg"
    assert audio.request_characters == len("Hello there.")
    headers = calls[0]["headers"]
    body = calls[0]["body"]
    assert isinstance(headers, dict)
    assert isinstance(body, dict)
    assert headers["Authorization"] == "Bearer sk-test"
    assert body["model"] == "gpt-4o-mini-tts"
    assert body["voice"] == "coral"
    assert body["input"] == "Hello there."
    assert body["response_format"] == "mp3"
    assert "podcast host" in body["instructions"]


def test_openai_tts_rejects_oversized_input() -> None:
    synthesizer = OpenAITTSSynthesizer(config=OpenAITTSConfig(api_key="sk-test"))

    with pytest.raises(ValueError, match="OpenAI TTS input is too long"):
        synthesizer.synthesize_ssml("x" * (OPENAI_TTS_MAX_INPUT_CHARS + 1))


def test_ssml_to_text_handles_breaks_and_marks() -> None:
    assert ssml_to_text('<speak>Hello <break time="1s"/>world<mark name="m1"/></speak>') == (
        "Hello world"
    )


def test_secret_value_to_api_key_accepts_plain_or_json_secret() -> None:
    assert secret_value_to_api_key("sk-plain") == "sk-plain"
    assert secret_value_to_api_key('{"api_key": "sk-json"}') == "sk-json"
