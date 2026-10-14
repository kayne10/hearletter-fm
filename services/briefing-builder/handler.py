"""Lambda: batch of raw forwarded emails -> one daily morning-briefing script.

Trigger: SQS queue fed by S3 ObjectCreated notifications on the raw-email bucket.

SQS batching (batch size = number of sources, 60s window) is best-effort: newsletters can
arrive in different batches or different Lambda invocations. So each configured newsletter is
staged in the processed bucket under its forwarder and local date, and the script is written
once every required source for that day is present. A conditional write makes the script
creation exactly-once even when two invocations race.

A scheduled "flush" invocation at the config's flush_at local time is the deadline: it builds
today's briefing from whatever has been staged, so a missing or unrecognised newsletter can't
block the episode.

When an Anthropic key is configured, Claude rewrites the template script into a podcast-style
script. Any Claude failure keeps the template text, so the episode is never blocked.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_plus
from zoneinfo import ZoneInfo

from hearletter_domain.briefing import BriefingConfig, BriefingScript, StagedNewsletter, slugify
from hearletter_shared.briefing_script import build_briefing_script, story_from_candidate
from hearletter_shared.cleaning import clean_newsletter_content, extract_story_candidates
from hearletter_shared.config import load_briefing_config
from hearletter_shared.forwarded_email import parse_forwarded_email
from hearletter_shared.lambda_events import log_json, log_lambda_event
from hearletter_shared.object_store import ObjectStore, S3ObjectStore, get_json, put_json

if TYPE_CHECKING:  # the writer pulls in anthropic/pydantic; template mode doesn't need them
    from hearletter_shared.script_writer import ScriptWriter

SES_SETUP_KEY_SUFFIX = "AMAZON_SES_SETUP_NOTIFICATION"
SCRIPT_FILE_NAME = "script.json"
FLUSH_ACTION = "flush"

_config: BriefingConfig | None = None
_script_writer: ScriptWriter | None = None
_script_writer_loaded = False


@dataclass(frozen=True, slots=True)
class RawEmailRef:
    bucket: str
    key: str
    event_time: str


@dataclass(slots=True)
class BatchOutcome:
    staged: list[str] = field(default_factory=list)
    ignored: list[dict[str, str]] = field(default_factory=list)
    briefings: list[dict[str, Any]] = field(default_factory=list)
    failed_message_ids: list[str] = field(default_factory=list)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    import boto3

    log_lambda_event("briefing-builder", event)
    s3_client = boto3.client("s3")
    if event.get("action") == FLUSH_ACTION:
        results = flush_due_briefings(
            processed=S3ObjectStore(bucket=os.environ["PROCESSED_BUCKET"], s3_client=s3_client),
            config=get_config(),
            now=datetime.now(UTC),
            script_writer=get_script_writer(),
        )
        log_json("briefing_flush_processed", briefings=results)
        return {"briefings": results}

    outcome = process_batch(
        event.get("Records", []),
        read_raw=lambda ref: S3ObjectStore(bucket=ref.bucket, s3_client=s3_client).get_bytes(
            ref.key
        ),
        processed=S3ObjectStore(bucket=os.environ["PROCESSED_BUCKET"], s3_client=s3_client),
        config=get_config(),
        now=datetime.now(UTC),
        script_writer=get_script_writer(),
    )
    log_json(
        "briefing_batch_processed",
        staged=outcome.staged,
        ignored=outcome.ignored,
        briefings=outcome.briefings,
        failed_message_ids=outcome.failed_message_ids,
    )
    return {
        "batchItemFailures": [{"itemIdentifier": mid} for mid in outcome.failed_message_ids]
    }


def get_config() -> BriefingConfig:
    global _config
    if _config is None:
        _config = load_briefing_config()
    return _config


def get_script_writer() -> ScriptWriter | None:
    """Built once per container; the Anthropic client itself is created on first use."""

    global _script_writer, _script_writer_loaded
    if not _script_writer_loaded:
        import boto3
        from hearletter_shared.script_writer import build_script_writer

        _script_writer = build_script_writer(env=os.environ, boto3_session_factory=boto3.Session)
        _script_writer_loaded = True
    return _script_writer


def process_batch(
    records: Iterable[dict[str, Any]],
    *,
    read_raw: Callable[[RawEmailRef], bytes],
    processed: ObjectStore,
    config: BriefingConfig,
    now: datetime,
    script_writer: ScriptWriter | None = None,
) -> BatchOutcome:
    """Stage every email in the batch, then try to finish each affected day's briefing."""

    outcome = BatchOutcome()
    failed: set[str] = set()
    touched: dict[tuple[str, str], list[str]] = defaultdict(list)

    for record in records:
        message_id = str(record.get("messageId", ""))
        try:
            for ref in raw_email_refs(str(record.get("body", "{}"))):
                staged, reason = stage_raw_email(
                    ref, raw_message=read_raw(ref), processed=processed, config=config
                )
                if staged is None:
                    outcome.ignored.append({"key": ref.key, "reason": reason})
                    continue
                outcome.staged.append(staged_key(staged))
                touched[(staged.forwarder, staged.episode_date)].append(message_id)
        except Exception as exc:
            log_json("raw_email_failed", message_id=message_id, error=repr(exc))
            failed.add(message_id)

    for (forwarder, episode_date), message_ids in touched.items():
        try:
            status = finalize_briefing(
                forwarder=forwarder,
                episode_date=episode_date,
                processed=processed,
                config=config,
                created_at=iso(now),
                script_writer=script_writer,
            )
            outcome.briefings.append(
                {"forwarder": forwarder, "episode_date": episode_date, **status}
            )
        except Exception as exc:
            log_json("briefing_failed", forwarder=forwarder, date=episode_date, error=repr(exc))
            failed.update(message_ids)  # redeliver; staging is idempotent

    outcome.failed_message_ids = sorted(mid for mid in failed if mid)
    return outcome


def raw_email_refs(sqs_body: str) -> list[RawEmailRef]:
    """Extract raw email objects from an S3-notification SQS message body."""

    body = json.loads(sqs_body)
    if body.get("Event") == "s3:TestEvent":
        return []
    refs: list[RawEmailRef] = []
    for record in body.get("Records", []):
        if not str(record.get("eventName", "")).startswith("ObjectCreated"):
            continue
        key = unquote_plus(str(record["s3"]["object"]["key"]))
        if key.endswith(SES_SETUP_KEY_SUFFIX):
            continue
        refs.append(
            RawEmailRef(
                bucket=str(record["s3"]["bucket"]["name"]),
                key=key,
                event_time=str(record.get("eventTime", "")),
            )
        )
    return refs


def stage_raw_email(
    ref: RawEmailRef,
    *,
    raw_message: bytes,
    processed: ObjectStore,
    config: BriefingConfig,
) -> tuple[StagedNewsletter | None, str]:
    """Parse, filter, clean, and stage one forwarded newsletter."""

    newsletter = parse_forwarded_email(raw_message, fallback_received_at=ref.event_time)
    if not newsletter.forwarder:
        return None, "missing_forwarder"
    if not config.is_forwarder_allowed(newsletter.forwarder):
        return None, f"forwarder_not_allowed:{newsletter.forwarder}"
    source = config.source_for(newsletter.source_email, newsletter.source_display_name)
    if source is None:
        sender = f"{newsletter.source_display_name or ''} <{newsletter.source_email}>".strip()
        return None, f"unconfigured_source:{sender}"

    clean_text, _ = clean_newsletter_content(html=newsletter.html, text=newsletter.text)
    candidates = extract_story_candidates(clean_text, max_stories=config.max_stories_per_source)
    staged = StagedNewsletter(
        forwarder=newsletter.forwarder,
        episode_date=local_date(newsletter.received_at, config.timezone),
        source_email=source.email,
        source_name=source.name,
        subject=newsletter.subject,
        received_at=newsletter.received_at,
        raw_email_key=ref.key,
        clean_word_count=len(clean_text.split()),
        stories=tuple(story_from_candidate(candidate) for candidate in candidates),
    )
    put_json(processed, staged_key(staged), staged.to_dict())
    return staged, "staged"


def finalize_briefing(
    *,
    forwarder: str,
    episode_date: str,
    processed: ObjectStore,
    config: BriefingConfig,
    created_at: str,
    script_writer: ScriptWriter | None = None,
    required_sources: int | None = None,
) -> dict[str, Any]:
    """Write the day's script once enough configured sources are staged.

    required_sources overrides the config's threshold (the deadline flush passes 1).
    """

    key = script_key(forwarder, episode_date)
    if processed.exists(key):
        return {"status": "already_built", "script_key": key}

    staged = [
        StagedNewsletter.from_dict(get_json(processed, staged_object))
        for staged_object in processed.list_keys(inbox_prefix(forwarder, episode_date))
        if staged_object.endswith(".json")
    ]
    staged = [item for item in staged if config.source_for(item.source_email) is not None]
    present = {item.source_email for item in staged}
    if len(present) < (required_sources or config.required_source_count):
        missing = [source.email for source in config.sources if source.email not in present]
        return {"status": "waiting", "present": sorted(present), "missing": missing}

    script = build_briefing_script(
        newsletters=staged,
        config=config,
        forwarder=forwarder,
        episode_date=episode_date,
        created_at=created_at,
    )
    if script_writer is not None:
        script = rewrite_script(script, staged, config=config, script_writer=script_writer)
    written = put_json(processed, key, script.to_dict(), if_absent=True)
    return {
        "status": "built" if written else "already_built",
        "script_key": key,
        "writer": script.writer,
    }


def rewrite_script(
    script: BriefingScript,
    newsletters: list[StagedNewsletter],
    *,
    config: BriefingConfig,
    script_writer: ScriptWriter,
) -> BriefingScript:
    """Swap in the AI-written text, keeping the template draft; fall back on any failure."""

    try:
        text = script_writer.write(
            newsletters=newsletters, config=config, episode_date=script.episode_date
        )
    except Exception as exc:
        log_json("script_writer_failed", writer=script_writer.name, error=repr(exc))
        return script
    log_json(
        "script_writer_succeeded",
        writer=script_writer.name,
        words=len(text.split()),
        usage=getattr(script_writer, "last_usage", None),
    )
    return dataclasses.replace(
        script, text=text, writer=script_writer.name, template_text=script.text
    )


def flush_due_briefings(
    *,
    processed: ObjectStore,
    config: BriefingConfig,
    now: datetime,
    script_writer: ScriptWriter | None = None,
) -> list[dict[str, Any]]:
    """Deadline run: build today's briefing for every forwarder with anything staged."""

    episode_date = now.astimezone(ZoneInfo(config.timezone)).date().isoformat()
    forwarders = sorted(
        {
            str(get_json(processed, key)["forwarder"])
            for key in processed.list_keys("inbox/")
            if key.endswith(".json") and key.split("/")[2:3] == [episode_date]
        }
    )
    results: list[dict[str, Any]] = []
    for forwarder in forwarders:
        status = finalize_briefing(
            forwarder=forwarder,
            episode_date=episode_date,
            processed=processed,
            config=config,
            created_at=iso(now),
            script_writer=script_writer,
            required_sources=1,
        )
        results.append({"forwarder": forwarder, "episode_date": episode_date, **status})
    return results


def inbox_prefix(forwarder: str, episode_date: str) -> str:
    return f"inbox/{slugify(forwarder)}/{episode_date}/"


def staged_key(staged: StagedNewsletter) -> str:
    prefix = inbox_prefix(staged.forwarder, staged.episode_date)
    return f"{prefix}{slugify(staged.source_email)}.json"


def script_key(forwarder: str, episode_date: str) -> str:
    return f"scripts/{slugify(forwarder)}/{episode_date}/{SCRIPT_FILE_NAME}"


def local_date(received_at: str, timezone: str) -> str:
    """Calendar date of receipt in the listener's timezone."""

    try:
        moment = datetime.fromisoformat(received_at)
    except ValueError:
        moment = datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(timezone)).date().isoformat()


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
