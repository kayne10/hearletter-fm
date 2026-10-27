"""Lambda: batch of raw forwarded emails -> one daily morning-briefing script.

Trigger: SQS queue fed by S3 ObjectCreated notifications on the raw-email bucket.

SQS batching (batch size = number of sources, 60s window) is best-effort: newsletters can
arrive in different batches or different Lambda invocations. So each configured newsletter is
staged in the processed bucket under its forwarder and local date, and the script is written
once every required source for that day is present. Scripts are keyed by a fingerprint of the
newsletter content, so re-sending the same emails at any time is a no-op, while a late or changed
newsletter produces a new version of the day's episode. A conditional write keeps each version
exactly-once even when two invocations race.

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
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_plus
from zoneinfo import ZoneInfo

from hearletter_domain.briefing import (
    BriefingConfig,
    BriefingScript,
    StagedNewsletter,
    Story,
    briefing_fingerprint,
    slugify,
)
from hearletter_shared.briefing_script import (
    build_briefing_script,
    story_from_candidate,
    story_from_parsed,
)
from hearletter_shared.cleaning import clean_newsletter_content, extract_story_candidates
from hearletter_shared.config import load_briefing_config
from hearletter_shared.forwarded_email import parse_forwarded_email
from hearletter_shared.lambda_events import log_json, log_lambda_event
from hearletter_shared.object_store import ObjectStore, S3ObjectStore, get_json, put_json
from hearletter_shared.story_parsers import STORY_PARSERS

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
    return {"batchItemFailures": [{"itemIdentifier": mid} for mid in outcome.failed_message_ids]}


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
    stories, extractor = extract_stories(
        source.parser,
        newsletter.text,
        clean_text,
        max_stories=config.stories_to_stage_for(source),
    )
    staged = StagedNewsletter(
        forwarder=newsletter.forwarder,
        episode_date=local_date(newsletter.received_at, config.timezone),
        source_email=source.email,
        source_name=source.name,
        subject=newsletter.subject,
        received_at=newsletter.received_at,
        raw_email_key=ref.key,
        clean_word_count=len(clean_text.split()),
        stories=stories,
        extractor=extractor,
    )
    put_json(processed, staged_key(staged), staged.to_dict())
    return staged, "staged"


def extract_stories(
    parser_name: str | None,
    text: str | None,
    clean_text: str,
    *,
    max_stories: int,
) -> tuple[tuple[Story, ...], str]:
    """Use the source's template parser; fall back to the generic extractor if it finds nothing.

    Template parsers return stories in newsletter order, which is the newsletter's own ranking,
    so the first max_stories are kept.
    """

    parser = STORY_PARSERS.get(parser_name or "")
    if parser is not None and text:
        parsed = parser(text)
        if parsed:
            stories = tuple(
                story_from_parsed(item.title, item.text, item.section) for item in parsed
            )
            return stories[:max_stories], str(parser_name)
        log_json("story_parser_found_nothing", parser=parser_name)
    candidates = extract_story_candidates(clean_text, max_stories=max_stories)
    return tuple(story_from_candidate(candidate) for candidate in candidates), "generic"


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
    """Write a script for the day's staged newsletters, at most once per distinct content.

    Scripts are keyed by a fingerprint of the newsletter content, so this is idempotent:
    - same content as an existing script (redelivery, re-forward): nothing happens;
    - the day's first script: waits for the required sources (a manual flush passes
      required_sources=1 and also counts optional issues);
    - the day already has a script and a required newsletter changed (late or corrected):
      a new version is written, and the producer emails it.

    Optional sources never hold an episode back and never cause an update on their own; see
    optional_newsletters for how a carried-over issue joins exactly one episode.
    """

    staged = load_staged(processed, forwarder, episode_date, config)
    required = [item for item in staged if is_required(item, config)]
    present = {item.source_email for item in required}
    existing = day_scripts(processed, forwarder, episode_date)
    optional = optional_newsletters(
        processed, forwarder=forwarder, episode_date=episode_date, config=config, existing=existing
    )
    newsletters = required + optional

    needed = required_sources or config.required_source_count
    ready = len(present) + (len(optional) if required_sources else 0)
    if not newsletters or (not existing and ready < needed):
        missing = [s.email for s in config.required_sources if s.email not in present]
        return {"status": "waiting", "present": sorted(present), "missing": missing}

    key = script_key(forwarder, episode_date, briefing_fingerprint(newsletters))
    if key in existing:
        return {"status": "unchanged", "script_key": key}

    script = build_briefing_script(
        newsletters=newsletters,
        config=config,
        forwarder=forwarder,
        episode_date=episode_date,
        created_at=created_at,
        version=len(existing) + 1,
    )
    if script_writer is not None:
        script = rewrite_script(script, newsletters, config=config, script_writer=script_writer)
    written = put_json(processed, key, script.to_dict(), if_absent=True)
    return {
        "status": "built" if written else "unchanged",
        "script_key": key,
        "version": script.version,
        "writer": script.writer,
        "optional": [item.source_email for item in optional],
    }


def optional_newsletters(
    processed: ObjectStore,
    *,
    forwarder: str,
    episode_date: str,
    config: BriefingConfig,
    existing: dict[str, BriefingScript],
) -> list[StagedNewsletter]:
    """Optional issues for this episode, each used by exactly one day's episode.

    "Used" is derived from the scripts themselves (their included_digests), so there is no
    separate marker to keep in sync. Rules:
    - the day already has an episode: keep exactly the optional issues its latest version
      used, so a weekly arriving after the morning's episode waits for tomorrow instead of
      triggering an update;
    - otherwise: the newest issue per optional source staged within that source's
      carry_over_days and not used by an earlier day's episode.
    """

    if not config.optional_sources:
        return []
    day = date.fromisoformat(episode_date)
    window = [
        (day - timedelta(days=offset)).isoformat()
        for offset in range(config.carry_over_window_days + 1)
    ]
    candidates = [
        item
        for staged_date in window
        for item in load_staged(processed, forwarder, staged_date, config)
        if is_optional_in_window(item, config, day)
    ]
    if existing:
        latest = max(existing.values(), key=lambda script: script.version)
        kept = set(latest.included_digests)
        return unique_by_digest(item for item in candidates if item.content_digest() in kept)

    used = {
        digest
        for staged_date in window
        for script in day_scripts(processed, forwarder, staged_date).values()
        for digest in script.included_digests
    }
    newest_per_source: dict[str, StagedNewsletter] = {}
    for item in sorted(candidates, key=lambda candidate: candidate.received_at):
        if item.content_digest() not in used:
            newest_per_source[item.source_email] = item
    return list(newest_per_source.values())


def is_required(item: StagedNewsletter, config: BriefingConfig) -> bool:
    source = config.source_for(item.source_email)
    return source is not None and source.required


def is_optional_in_window(item: StagedNewsletter, config: BriefingConfig, day: date) -> bool:
    source = config.source_for(item.source_email)
    if source is None or source.required:
        return False
    oldest = (day - timedelta(days=source.carry_over_days)).isoformat()
    return oldest <= item.episode_date <= day.isoformat()


def unique_by_digest(items: Iterable[StagedNewsletter]) -> list[StagedNewsletter]:
    unique: dict[str, StagedNewsletter] = {}
    for item in items:
        unique.setdefault(item.content_digest(), item)
    return list(unique.values())


def load_staged(
    processed: ObjectStore, forwarder: str, episode_date: str, config: BriefingConfig
) -> list[StagedNewsletter]:
    """The day's staged newsletters from configured sources."""

    staged = [
        StagedNewsletter.from_dict(get_json(processed, key))
        for key in processed.list_keys(inbox_prefix(forwarder, episode_date))
        if key.endswith(".json")
    ]
    return [item for item in staged if config.source_for(item.source_email) is not None]


def day_scripts(
    processed: ObjectStore, forwarder: str, episode_date: str
) -> dict[str, BriefingScript]:
    """The day's scripts by key (every version, including pre-versioning scripts)."""

    return {
        key: BriefingScript.from_dict(get_json(processed, key))
        for key in processed.list_keys(day_scripts_prefix(forwarder, episode_date))
        if key.endswith(f"/{SCRIPT_FILE_NAME}")
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


def day_scripts_prefix(forwarder: str, episode_date: str) -> str:
    return f"scripts/{slugify(forwarder)}/{episode_date}/"


def script_key(forwarder: str, episode_date: str, fingerprint: str) -> str:
    return f"{day_scripts_prefix(forwarder, episode_date)}{fingerprint}/{SCRIPT_FILE_NAME}"


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
