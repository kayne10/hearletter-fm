#!/usr/bin/env python3
"""Run the Hearletter FM pipeline locally against a raw MIME email fixture."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from enum import Enum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

for import_root in (
    REPO_ROOT / "packages" / "domain",
    REPO_ROOT / "packages" / "types",
    REPO_ROOT / "packages" / "utils",
    REPO_ROOT / "services" / "shared",
):
    sys.path.insert(0, str(import_root))

from hearletter_domain.models import ContentMode, S3ObjectRef  # noqa: E402
from hearletter_events.contracts import (  # noqa: E402
    EventEnvelope,
    ParsedNewsletterPayload,
    new_id,
    utc_now_iso,
)

LOCAL_BUCKET = "local-artifacts"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Hearletter FM E2E pipeline.")
    parser.add_argument(
        "--raw-email",
        type=Path,
        default=REPO_ROOT / "tests" / "fixtures" / "raw_email",
        help="Path to a raw MIME email fixture.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for stage input/output files. Defaults to artifacts/local/<timestamp>.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or default_output_dir(args.raw_email)
    output_dir.mkdir(parents=True, exist_ok=True)

    email_parser = load_module("local_email_parser", REPO_ROOT / "services" / "email-parser" / "handler.py")
    cleaner = load_module(
        "local_newsletter_cleaner", REPO_ROOT / "services" / "newsletter-cleaner" / "handler.py"
    )
    summarizer = load_module("local_summarizer", REPO_ROOT / "services" / "summarizer" / "handler.py")

    raw_message = args.raw_email.read_bytes()
    raw_stage = output_dir / "00_raw_email"
    parsed_stage = output_dir / "01_email_parser"
    cleaned_stage = output_dir / "02_newsletter_cleaner"
    scripted_stage = output_dir / "03_summarizer"
    tts_stage = output_dir / "04_tts_request"
    for stage in (raw_stage, parsed_stage, cleaned_stage, scripted_stage, tts_stage):
        stage.mkdir(parents=True, exist_ok=True)

    shutil.copy2(args.raw_email, raw_stage / "input.eml")

    parsed_event, bodies = run_parser_stage(
        raw_message=raw_message,
        raw_email_path=args.raw_email,
        output_dir=parsed_stage,
        email_parser=email_parser,
    )
    clean_text, story_candidates, cleaned_event = run_cleaner_stage(
        parsed_event=parsed_event,
        bodies=bodies,
        output_dir=cleaned_stage,
        cleaner=cleaner,
    )
    script_event = run_summarizer_stage(
        cleaned_event=cleaned_event,
        clean_text=clean_text,
        story_candidates=story_candidates,
        output_dir=scripted_stage,
        summarizer=summarizer,
    )
    run_tts_request_stage(script_event=script_event, output_dir=tts_stage)

    write_json(
        output_dir / "manifest.json",
        {
            "raw_email": str(args.raw_email),
            "output_dir": str(output_dir),
            "stages": [
                "00_raw_email",
                "01_email_parser",
                "02_newsletter_cleaner",
                "03_summarizer",
                "04_tts_request",
            ],
        },
    )

    print(f"local pipeline complete: {output_dir}")
    print(f"clean text: {cleaned_stage / 'clean_text.txt'}")
    print(f"story candidates: {cleaned_stage / 'story_candidates.json'}")
    print(f"podcast context: {scripted_stage / 'podcast_context.json'}")
    print(f"script draft: {scripted_stage / 'script_draft.txt'}")


def run_parser_stage(
    *,
    raw_message: bytes,
    raw_email_path: Path,
    output_dir: Path,
    email_parser: Any,
) -> tuple[EventEnvelope[ParsedNewsletterPayload], dict[str, str | None]]:
    """Decode raw MIME and write email-parser stage artifacts."""

    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    bodies = email_parser.parse_mime(raw_message)
    message_id = message.get("Message-ID", raw_email_path.stem).strip("<>")
    newsletter_id = stable_newsletter_id(raw_email_path)
    now = utc_now_iso()
    subject = str(message.get("Subject", "Untitled newsletter"))

    input_event = local_ses_event(
        message_id=message_id,
        source=str(message.get("From", "unknown")),
        recipient=str(message.get("To", "listen@example.invalid")),
        subject=subject,
    )
    write_json(output_dir / "input_event.json", input_event)

    html_ref = write_optional_text(output_dir / "body.html", bodies.get("html"), "parsed/body.html")
    text_ref = write_optional_text(output_dir / "body.txt", bodies.get("text"), "parsed/body.txt")

    payload = ParsedNewsletterPayload(
        raw_email=S3ObjectRef(bucket=LOCAL_BUCKET, key=str(raw_email_path)),
        sender=str(message.get("From", "unknown")),
        recipient=str(message.get("To", "listen@example.invalid")),
        subject=subject,
        received_at=now,
        html=html_ref,
        text=text_ref,
    )
    event = EventEnvelope(
        event_id=new_id("evt"),
        event_type="newsletter.parsed",
        schema_version="1.0",
        correlation_id=message_id,
        tenant_id="local",
        newsletter_id=newsletter_id,
        occurred_at=now,
        payload=payload,
    )
    write_json(output_dir / "output_event.json", event)
    write_json(
        output_dir / "summary.json",
        {
            "subject": subject,
            "sender": payload.sender,
            "recipient": payload.recipient,
            "html_chars": len(bodies.get("html") or ""),
            "text_chars": len(bodies.get("text") or ""),
        },
    )
    return event, bodies


def run_cleaner_stage(
    *,
    parsed_event: EventEnvelope[ParsedNewsletterPayload],
    bodies: dict[str, str | None],
    output_dir: Path,
    cleaner: Any,
) -> tuple[str, list[dict[str, Any]], EventEnvelope[Any]]:
    """Clean newsletter bodies and extract candidate stories."""

    write_json(output_dir / "input_event.json", parsed_event)
    clean_text, removed_sections = cleaner.clean_newsletter_content(
        html=bodies.get("html"),
        text=bodies.get("text"),
    )
    story_candidates = cleaner.extract_story_candidates(clean_text)

    clean_path = output_dir / "clean_text.txt"
    clean_path.write_text(clean_text, encoding="utf-8")
    write_json(output_dir / "story_candidates.json", story_candidates)

    payload = {
        "title": parsed_event.payload.subject,
        "source": parsed_event.payload.sender,
        "clean_text": {"bucket": LOCAL_BUCKET, "key": "02_newsletter_cleaner/clean_text.txt"},
        "removed_sections": removed_sections,
        "word_count": len(clean_text.split()),
        "story_candidates": {"bucket": LOCAL_BUCKET, "key": "02_newsletter_cleaner/story_candidates.json"},
    }
    event = EventEnvelope(
        event_id=new_id("evt"),
        event_type="newsletter.cleaned",
        schema_version="1.0",
        correlation_id=parsed_event.correlation_id,
        tenant_id=parsed_event.tenant_id,
        newsletter_id=parsed_event.newsletter_id,
        occurred_at=utc_now_iso(),
        payload=payload,
    )
    write_json(output_dir / "output_event.json", event)
    write_json(
        output_dir / "summary.json",
        {
            "word_count": payload["word_count"],
            "removed_sections": removed_sections,
            "story_candidate_count": len(story_candidates),
            "top_story_titles": [story["title"] for story in story_candidates[:5]],
        },
    )
    return clean_text, story_candidates, event


def run_summarizer_stage(
    *,
    cleaned_event: EventEnvelope[Any],
    clean_text: str,
    story_candidates: list[dict[str, Any]],
    output_dir: Path,
    summarizer: Any,
) -> EventEnvelope[Any]:
    """Build podcast context and local script draft for the future AI agent."""

    write_json(output_dir / "input_event.json", cleaned_event)
    payload = cleaned_event.payload
    context = summarizer.build_podcast_context(
        title=str(payload["title"]),
        source=payload.get("source"),
        clean_text=clean_text,
        story_candidates=story_candidates,
    )
    script_text = summarizer.build_script_draft(context)
    prompt = summarizer.build_morning_briefing_prompt(clean_text)

    write_json(output_dir / "podcast_context.json", context)
    (output_dir / "agent_prompt.txt").write_text(prompt, encoding="utf-8")
    (output_dir / "script_draft.txt").write_text(script_text, encoding="utf-8")

    script_payload = summarizer.script_event_payload(
        tenant_id=cleaned_event.tenant_id,
        newsletter_id=cleaned_event.newsletter_id,
        title=context["episode"]["title"],
        script_key="03_summarizer/script_draft.txt",
        script_text=script_text,
        artifact_bucket=LOCAL_BUCKET,
    )
    event = EventEnvelope(
        event_id=new_id("evt"),
        event_type="briefing.scripted",
        schema_version="1.0",
        correlation_id=cleaned_event.correlation_id,
        tenant_id=cleaned_event.tenant_id,
        newsletter_id=cleaned_event.newsletter_id,
        occurred_at=utc_now_iso(),
        payload=script_payload,
    )
    write_json(output_dir / "output_event.json", event)
    write_json(
        output_dir / "summary.json",
        {
            "script_chars": len(script_text),
            "estimated_duration_seconds": script_payload.estimated_duration_seconds,
            "story_count": len(context["story_candidates"]),
        },
    )
    return event


def run_tts_request_stage(*, script_event: EventEnvelope[Any], output_dir: Path) -> None:
    """Write the local input a TTS provider would consume."""

    write_json(output_dir / "input_event.json", script_event)
    request = {
        "provider": "openai",
        "voice": script_event.payload.voice,
        "input_script": script_event.payload.script.key,
        "mode": ContentMode.MORNING_BRIEFING.value,
        "expected_output": {
            "mime_type": "audio/mpeg",
            "artifact_key": f"audio/{script_event.tenant_id}/{script_event.newsletter_id}.mp3",
        },
    }
    write_json(output_dir / "tts_request.json", request)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_optional_text(path: Path, value: str | None, key: str) -> S3ObjectRef | None:
    if value is None:
        return None
    path.write_text(value, encoding="utf-8")
    return S3ObjectRef(bucket=LOCAL_BUCKET, key=key)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, default=json_default, indent=2, sort_keys=True), encoding="utf-8")


def json_default(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def stable_newsletter_id(path: Path) -> str:
    return f"nws_local_{path.stem.replace('-', '_')}"


def default_output_dir(raw_email: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "artifacts" / "local" / f"{raw_email.stem}-{timestamp}"


def local_ses_event(*, message_id: str, source: str, recipient: str, subject: str) -> dict[str, Any]:
    return {
        "Records": [
            {
                "ses": {
                    "mail": {
                        "messageId": message_id,
                        "source": source,
                        "destination": [recipient],
                        "commonHeaders": {"subject": subject},
                    },
                    "receipt": {
                        "action": {
                            "type": "Lambda",
                            "functionArn": "local",
                        }
                    },
                }
            }
        ]
    }


if __name__ == "__main__":
    main()

