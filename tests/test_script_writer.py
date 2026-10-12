from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from hearletter_domain.briefing import StagedNewsletter, Story
from hearletter_shared.config import REPO_CONFIG_PATH, load_briefing_config
from hearletter_shared.script_writer import (
    SERVER_SIDE_FALLBACK_BETA,
    BriefingDraft,
    ClaudeScriptWriter,
    ScriptSegment,
    ScriptWriterError,
    build_script_writer,
    resolve_anthropic_api_key,
)

CONFIG = load_briefing_config(REPO_CONFIG_PATH)
LONG_TEXT = " ".join(["Nvidia shipped a new chip and analysts say it matters."] * 20)


def staged(source_email: str, name: str, title: str) -> StagedNewsletter:
    return StagedNewsletter(
        forwarder="reader@example.com",
        episode_date="2026-10-12",
        source_email=source_email,
        source_name=name,
        subject=f"{name} today",
        received_at="2026-10-12T13:00:00Z",
        raw_email_key="raw/x",
        clean_word_count=500,
        stories=(Story(title=title, summary="Short.", source_text=f"Full text about {title}."),),
    )


NEWSLETTERS = [
    staged("news@daily.therundown.ai", "The Rundown", "Anthropic"),
    staged("dan@tldrnewsletter.com", "TLDR", "Nvidia"),
]


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def fake_client(parsed: BriefingDraft | None, stop_reason: str = "end_turn") -> Any:
    response = SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=1200, output_tokens=900),
    )
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(response)))


def draft(*texts: str) -> BriefingDraft:
    return BriefingDraft(segments=[ScriptSegment(source="host", text=text) for text in texts])


def write(client: Any) -> str:
    writer = ClaudeScriptWriter(client_factory=lambda: client, effort="low")
    return writer.write(newsletters=NEWSLETTERS, config=CONFIG, episode_date="2026-10-12")


def test_writes_script_from_structured_segments() -> None:
    client = fake_client(draft("Good morning.", LONG_TEXT, "  ", "That's it."))

    text = write(client)

    assert text == f"Good morning.\n\n{LONG_TEXT}\n\nThat's it."
    call = client.beta.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is BriefingDraft
    assert call["output_config"] == {"effort": "low"}
    assert call["betas"] == [SERVER_SIDE_FALLBACK_BETA] and call["fallbacks"] == "default"
    assert "untrusted" in call["system"]
    prompt = call["messages"][0]["content"]
    assert "Monday, October 12" in prompt
    assert prompt.index("TLDR") < prompt.index("The Rundown")  # config order
    assert "Full text about Nvidia." in prompt


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_non_final_stop_reason_raises(stop_reason: str) -> None:
    with pytest.raises(ScriptWriterError, match=stop_reason):
        write(fake_client(draft(LONG_TEXT), stop_reason=stop_reason))


def test_missing_or_short_output_raises() -> None:
    with pytest.raises(ScriptWriterError, match="no parsable"):
        write(fake_client(None))
    with pytest.raises(ScriptWriterError, match="too short"):
        write(fake_client(draft("Hi.")))


def test_writer_is_off_without_key_or_in_template_mode() -> None:
    def no_session() -> Any:
        raise AssertionError("must not touch AWS")

    assert build_script_writer(env={}, boto3_session_factory=no_session) is None
    assert (
        build_script_writer(
            env={"ANTHROPIC_API_KEY": "sk", "SCRIPT_WRITER": "template"},
            boto3_session_factory=no_session,
        )
        is None
    )
    writer = build_script_writer(
        env={"ANTHROPIC_API_KEY_SECRET_ARN": "arn", "SCRIPT_MODEL": "claude-sonnet-5-5"},
        boto3_session_factory=no_session,
    )
    assert writer is not None and writer.name == "claude-sonnet-5-5"


def test_resolves_key_from_json_secret() -> None:
    secret = {"SecretString": '{"api_key": "sk-1"}'}
    secrets = SimpleNamespace(get_secret_value=lambda SecretId: secret)
    session = SimpleNamespace(client=lambda name: secrets)

    key = resolve_anthropic_api_key(
        env={"ANTHROPIC_API_KEY_SECRET_ARN": "arn"}, boto3_session_factory=lambda: session
    )
    assert key == "sk-1"
