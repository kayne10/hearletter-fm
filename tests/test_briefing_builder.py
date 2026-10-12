from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from briefing_fixtures import FORWARDER, MORNING_BREW, RUNDOWN, TLDR, UNCONFIGURED, sqs_record
from hearletter_domain.briefing import BriefingConfig
from hearletter_shared.config import REPO_CONFIG_PATH, load_briefing_config
from hearletter_shared.object_store import LocalObjectStore

NOW = datetime(2026, 10, 12, 14, 0, tzinfo=UTC)
SCRIPT_KEY = "scripts/reader_example.com/2026-10-12/script.json"


def load_builder() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "services" / "briefing-builder" / "handler.py"
    spec = importlib.util.spec_from_file_location("briefing_builder_handler", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = load_builder()


@pytest.fixture
def config() -> BriefingConfig:
    return load_briefing_config(REPO_CONFIG_PATH)


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "processed")


def run(
    emails: dict[str, bytes],
    *,
    store: LocalObjectStore,
    config: BriefingConfig,
    records: list[dict[str, Any]] | None = None,
) -> Any:
    records = records or [sqs_record(f"m-{key}", key) for key in emails]
    return builder.process_batch(
        records,
        read_raw=lambda ref: emails[ref.key],
        processed=store,
        config=config,
        now=NOW,
    )


def test_repo_config_has_the_three_morning_sources(config: BriefingConfig) -> None:
    assert [source.email for source in config.sources] == [
        "dan@tldrnewsletter.com",
        "crew@morningbrew.com",
        "news@daily.therundown.ai",
    ]
    assert config.required_source_count == 3


def test_one_batch_with_all_sources_builds_one_script(
    store: LocalObjectStore, config: BriefingConfig
) -> None:
    outcome = run({"a": TLDR, "b": MORNING_BREW, "c": RUNDOWN}, store=store, config=config)

    assert outcome.failed_message_ids == []
    assert len(outcome.staged) == 3
    assert [item["status"] for item in outcome.briefings] == ["built"]
    script = json.loads(store.get_bytes(SCRIPT_KEY))
    assert script["forwarder"] == FORWARDER
    assert script["title"] == "Morning Briefing - 2026-10-12"
    assert script["sources"] == [source.email for source in config.sources]
    text = script["text"]
    assert text.index("TLDR") < text.index("Morning Brew") < text.index("The Rundown")
    assert "Unsubscribe" not in text


def test_sources_split_across_batches_wait_then_build_once(
    store: LocalObjectStore, config: BriefingConfig
) -> None:
    first = run({"a": TLDR, "b": MORNING_BREW}, store=store, config=config)
    assert first.briefings[0]["status"] == "waiting"
    assert first.briefings[0]["missing"] == ["news@daily.therundown.ai"]
    assert not store.exists(SCRIPT_KEY)

    second = run({"c": RUNDOWN}, store=store, config=config)
    assert second.briefings[0]["status"] == "built"

    redelivered = run({"c": RUNDOWN}, store=store, config=config)
    assert redelivered.briefings[0]["status"] == "already_built"


def test_min_sources_allows_partial_briefing(
    store: LocalObjectStore, config: BriefingConfig
) -> None:
    outcome = run(
        {"a": TLDR, "b": MORNING_BREW},
        store=store,
        config=dataclasses.replace(config, min_sources=2),
    )
    assert outcome.briefings[0]["status"] == "built"


def test_unconfigured_source_is_ignored(store: LocalObjectStore, config: BriefingConfig) -> None:
    outcome = run({"x": UNCONFIGURED}, store=store, config=config)

    assert outcome.staged == [] and outcome.failed_message_ids == []
    assert outcome.ignored == [{"key": "x", "reason": "unconfigured_source:hello@randomnews.com"}]


def test_disallowed_forwarder_is_ignored(store: LocalObjectStore, config: BriefingConfig) -> None:
    restricted = dataclasses.replace(config, allowed_forwarders=("someone@else.com",))
    outcome = run({"a": TLDR}, store=store, config=restricted)

    assert outcome.staged == []
    assert outcome.ignored[0]["reason"] == f"forwarder_not_allowed:{FORWARDER}"


def test_ses_setup_and_s3_test_events_are_skipped(
    store: LocalObjectStore, config: BriefingConfig
) -> None:
    records = [
        {"messageId": "t", "body": json.dumps({"Event": "s3:TestEvent"})},
        sqs_record("s", "raw-email/default/AMAZON_SES_SETUP_NOTIFICATION"),
    ]
    outcome = run({}, store=store, config=config, records=records)
    assert outcome.staged == [] and outcome.failed_message_ids == []


def test_unreadable_email_fails_only_its_message(
    store: LocalObjectStore, config: BriefingConfig
) -> None:
    records = [sqs_record("good", "a"), sqs_record("bad", "missing")]
    outcome = run({"a": TLDR}, store=store, config=config, records=records)

    assert outcome.failed_message_ids == ["bad"]
    assert len(outcome.staged) == 1


def test_lost_conditional_write_reports_already_built(
    store: LocalObjectStore, config: BriefingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_put = store.put_bytes

    def racing_put(key: str, body: bytes, **kwargs: Any) -> bool:
        if key == SCRIPT_KEY:  # another invocation wrote the script first
            original_put(key, b'{"other": true}', content_type="application/json")
        return original_put(key, body, **kwargs)

    monkeypatch.setattr(store, "put_bytes", racing_put)
    outcome = run({"a": TLDR, "b": MORNING_BREW, "c": RUNDOWN}, store=store, config=config)

    assert outcome.briefings[0]["status"] == "already_built"
    assert json.loads(store.get_bytes(SCRIPT_KEY)) == {"other": True}


def test_handler_returns_partial_batch_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcome = builder.BatchOutcome(failed_message_ids=["m-1"])
    monkeypatch.setattr(builder, "process_batch", lambda *args, **kwargs: outcome)
    monkeypatch.setenv("PROCESSED_BUCKET", "processed")
    monkeypatch.setattr(builder, "get_config", lambda: None)

    assert builder.handler({"Records": []}, None) == {
        "batchItemFailures": [{"itemIdentifier": "m-1"}]
    }


def test_local_date_uses_configured_timezone() -> None:
    assert builder.local_date("2026-10-13T03:00:00Z", "America/Denver") == "2026-10-12"


class StubWriter:
    name = "claude-opus-5-5"

    def __init__(self, result: str | Exception) -> None:
        self.result = result
        self.calls = 0

    def write(self, **kwargs: Any) -> str:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def run_with_writer(store: LocalObjectStore, config: BriefingConfig, writer: StubWriter) -> Any:
    return builder.process_batch(
        [sqs_record(f"m-{k}", k) for k in ("a", "b", "c")],
        read_raw=lambda ref: {"a": TLDR, "b": MORNING_BREW, "c": RUNDOWN}[ref.key],
        processed=store,
        config=config,
        now=NOW,
        script_writer=writer,
    )


def test_ai_writer_text_is_used_and_template_kept(
    store: LocalObjectStore, config: BriefingConfig
) -> None:
    writer = StubWriter("Good morning. Today's big AI story is Anthropic.")
    outcome = run_with_writer(store, config, writer)

    script = json.loads(store.get_bytes(SCRIPT_KEY))
    assert outcome.briefings[0]["writer"] == "claude-opus-5-5"
    assert script["text"].startswith("Good morning. Today's big AI story")
    assert script["writer"] == "claude-opus-5-5"
    assert "Let's start with TLDR." in script["template_text"]


def test_ai_writer_failure_falls_back_to_template(
    store: LocalObjectStore, config: BriefingConfig
) -> None:
    outcome = run_with_writer(store, config, StubWriter(RuntimeError("API down")))

    script = json.loads(store.get_bytes(SCRIPT_KEY))
    assert outcome.failed_message_ids == []
    assert script["writer"] == "template" and script["template_text"] is None
    assert "Let's start with TLDR." in script["text"]


def test_ai_writer_not_called_while_waiting_or_after_built(
    store: LocalObjectStore, config: BriefingConfig
) -> None:
    writer = StubWriter("Good morning.")
    builder.process_batch(
        [sqs_record("m-a", "a")],
        read_raw=lambda ref: TLDR,
        processed=store,
        config=config,
        now=NOW,
        script_writer=writer,
    )
    assert writer.calls == 0
    run_with_writer(store, config, writer)
    run_with_writer(store, config, writer)
    assert writer.calls == 1
