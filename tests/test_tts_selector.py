from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from hearletter_shared.openai_tts import OpenAITTSSynthesizer
from hearletter_shared.polly import PollySynthesizer


def load_tts_handler() -> Any:
    handler_path = Path(__file__).resolve().parents[1] / "services" / "tts" / "handler.py"
    spec = importlib.util.spec_from_file_location("test_tts_handler", handler_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load TTS handler from {handler_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBoto3Session:
    def __init__(self) -> None:
        self.client_calls: list[dict[str, str | None]] = []

    def client(self, service_name: str, region_name: str | None = None) -> object:
        self.client_calls.append({"service_name": service_name, "region_name": region_name})
        return object()


def test_tts_provider_defaults_to_polly() -> None:
    handler = load_tts_handler()
    session = FakeBoto3Session()

    synthesizer = handler.build_tts_synthesizer(env={}, boto3_session=session)

    assert isinstance(synthesizer, PollySynthesizer)
    assert session.client_calls == [{"service_name": "polly", "region_name": "us-east-1"}]


def test_tts_provider_can_select_openai() -> None:
    handler = load_tts_handler()
    session = FakeBoto3Session()

    synthesizer = handler.build_tts_synthesizer(
        env={
            "TTS_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_TTS_MODEL": "gpt-4o-mini-tts",
            "OPENAI_TTS_VOICE": "coral",
        },
        boto3_session=session,
    )

    assert isinstance(synthesizer, OpenAITTSSynthesizer)
    assert session.client_calls == []


def test_openai_config_uses_show_style_when_voice_is_not_explicit() -> None:
    handler = load_tts_handler()
    session = FakeBoto3Session()

    config = handler.openai_config_from_env(
        env={
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_TTS_MODEL": "gpt-4o-mini-tts",
            "OPENAI_TTS_VOICE": "",
            "OPENAI_TTS_INSTRUCTIONS": "Base host instructions.",
        },
        boto3_session=session,
        show_style={
            "voice": "bright",
            "pace": "slow",
            "music": "soft",
            "transition": "light_chime",
        },
    )

    assert config.voice == "coral"
    assert "Voice style: bright." in config.instructions
    assert "Pace: slow." in config.instructions
