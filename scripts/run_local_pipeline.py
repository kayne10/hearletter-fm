#!/usr/bin/env python3
"""Run the Hearletter FM text pipeline locally against raw email samples."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from email import policy
from email.message import Message
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

from hearletter_domain.models import S3ObjectRef  # noqa: E402
from hearletter_events.contracts import (  # noqa: E402
    EventEnvelope,
    ParsedNewsletterPayload,
    new_id,
    utc_now_iso,
)
from hearletter_shared.polly import (  # noqa: E402
    DEFAULT_POLLY_ENGINE,
    DEFAULT_POLLY_REGION,
    DEFAULT_POLLY_VOICE_ID,
    PollySynthesisConfig,
    build_polly_synthesizer,
)

LOCAL_BUCKET = "local-artifacts"
IGNORED_INPUT_NAMES = {".DS_Store"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Hearletter FM text pipeline.")
    parser.add_argument(
        "--input",
        type=Path,
        default=REPO_ROOT / "data",
        help="Raw MIME file, decoded text file, or directory of samples. Defaults to data/.",
    )
    parser.add_argument(
        "--raw-email",
        type=Path,
        help="Backward-compatible alias for --input.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for output files. Defaults to artifacts/local/<timestamp>.",
    )
    parser.add_argument(
        "--clean-output",
        action="store_true",
        help="Remove the output directory before running. Useful for artifacts/local/latest.",
    )
    parser.add_argument(
        "--synthesize-audio",
        action="store_true",
        help="Call Amazon Polly and write MP3 files locally.",
    )
    parser.add_argument(
        "--polly-region",
        default=DEFAULT_POLLY_REGION,
        help=f"Amazon Polly region. Defaults to {DEFAULT_POLLY_REGION}.",
    )
    parser.add_argument(
        "--polly-engine",
        default=DEFAULT_POLLY_ENGINE,
        help=f"Amazon Polly engine. Defaults to {DEFAULT_POLLY_ENGINE}.",
    )
    parser.add_argument(
        "--polly-voice-id",
        default=DEFAULT_POLLY_VOICE_ID,
        help=f"Amazon Polly voice id. Defaults to {DEFAULT_POLLY_VOICE_ID}.",
    )
    args = parser.parse_args()

    input_path = args.raw_email or args.input
    output_dir = args.output_dir or default_output_dir(input_path)
    if args.clean_output:
        clean_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    modules = PipelineModules(
        email_parser=load_module("local_email_parser", REPO_ROOT / "services" / "email-parser" / "handler.py"),
        cleaner=load_module(
            "local_newsletter_cleaner", REPO_ROOT / "services" / "newsletter-cleaner" / "handler.py"
        ),
        summarizer=load_module("local_summarizer", REPO_ROOT / "services" / "summarizer" / "handler.py"),
    )

    polly_synthesizer = None
    polly_config = PollySynthesisConfig(
        region_name=args.polly_region,
        engine=args.polly_engine,
        voice_id=args.polly_voice_id,
    )
    if args.synthesize_audio:
        import boto3

        polly_synthesizer = build_polly_synthesizer(
            boto3_session=boto3.Session(),
            config=polly_config,
        )

    input_files = discover_input_files(input_path)
    sample_outputs = []
    for sample_path in input_files:
        sample_dir = output_dir / safe_sample_name(sample_path)
        sample_outputs.append(
            run_sample(
                sample_path=sample_path,
                output_dir=sample_dir,
                modules=modules,
                polly_synthesizer=polly_synthesizer,
                polly_config=polly_config,
            )
        )

    write_json(
        output_dir / "manifest.json",
        {
            "input": str(input_path),
            "output_dir": str(output_dir),
            "sample_count": len(sample_outputs),
            "audio_synthesis_enabled": args.synthesize_audio,
            "polly": {
                "region_name": polly_config.region_name,
                "engine": polly_config.engine,
                "voice_id": polly_config.voice_id,
                "output_format": polly_config.output_format,
            },
            "samples": sample_outputs,
        },
    )

    print(f"local text pipeline complete: {output_dir}")
    for sample in sample_outputs:
        print(f"- {sample['sample_name']}: {sample['podcast_context']}")


class PipelineModules:
    """Loaded service modules used by the local runner."""

    def __init__(self, *, email_parser: Any, cleaner: Any, summarizer: Any) -> None:
        self.email_parser = email_parser
        self.cleaner = cleaner
        self.summarizer = summarizer


def run_sample(
    *,
    sample_path: Path,
    output_dir: Path,
    modules: PipelineModules,
    polly_synthesizer: Any | None = None,
    polly_config: PollySynthesisConfig | None = None,
) -> dict[str, Any]:
    """Run all local text stages for a single sample."""

    raw_stage = output_dir / "00_input"
    parsed_stage = output_dir / "01_email_parser"
    cleaned_stage = output_dir / "02_newsletter_cleaner"
    scripted_stage = output_dir / "03_podcast_context"
    for stage in (raw_stage, parsed_stage, cleaned_stage, scripted_stage):
        stage.mkdir(parents=True, exist_ok=True)

    shutil.copy2(sample_path, raw_stage / sample_path.name)

    parsed_event, bodies, sample_kind = run_parser_stage(
        sample_path=sample_path,
        output_dir=parsed_stage,
        email_parser=modules.email_parser,
    )
    clean_text, story_candidates, cleaned_event = run_cleaner_stage(
        parsed_event=parsed_event,
        bodies=bodies,
        output_dir=cleaned_stage,
        cleaner=modules.cleaner,
    )
    script_event = run_context_stage(
        cleaned_event=cleaned_event,
        clean_text=clean_text,
        story_candidates=story_candidates,
        output_dir=scripted_stage,
        summarizer=modules.summarizer,
    )
    audio_output = None
    if polly_synthesizer is not None:
        audio_output = run_polly_audio_stage(
            script_event=script_event,
            ssml_path=scripted_stage / "polly_ssml.xml",
            output_dir=output_dir / "04_polly_audio",
            polly_synthesizer=polly_synthesizer,
            polly_config=polly_config or PollySynthesisConfig(),
        )

    sample_manifest = {
        "sample_name": sample_path.name,
        "sample_kind": sample_kind,
        "output_dir": str(output_dir),
        "clean_word_count": len(clean_text.split()),
        "story_candidate_count": len(story_candidates),
        "podcast_context": str(scripted_stage / "podcast_context.json"),
        "polly_ssml": str(scripted_stage / "polly_ssml.xml"),
        "audio_output": audio_output,
        "script_event_type": script_event.event_type,
    }
    write_json(output_dir / "manifest.json", sample_manifest)
    return sample_manifest


def run_parser_stage(
    *,
    sample_path: Path,
    output_dir: Path,
    email_parser: Any,
) -> tuple[EventEnvelope[ParsedNewsletterPayload], dict[str, str | None], str]:
    """Decode raw MIME or wrap decoded text as parser output."""

    raw_bytes = sample_path.read_bytes()
    if looks_like_email(raw_bytes):
        message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        bodies = email_parser.parse_mime(raw_bytes)
        sample_kind = "raw_mime"
    else:
        message = synthetic_message(sample_path)
        bodies = {"html": None, "text": raw_bytes.decode("utf-8", errors="replace")}
        sample_kind = "decoded_text"

    message_id = message_id_for(message, sample_path)
    newsletter_id = stable_newsletter_id(sample_path)
    now = utc_now_iso()
    subject = str(message.get("Subject", sample_path.stem))

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
        raw_email=S3ObjectRef(bucket=LOCAL_BUCKET, key=str(sample_path)),
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
            "sample_kind": sample_kind,
            "subject": subject,
            "sender": payload.sender,
            "recipient": payload.recipient,
            "html_chars": len(bodies.get("html") or ""),
            "text_chars": len(bodies.get("text") or ""),
        },
    )
    return event, bodies, sample_kind


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

    (output_dir / "clean_text.txt").write_text(clean_text, encoding="utf-8")
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


def run_context_stage(
    *,
    cleaned_event: EventEnvelope[Any],
    clean_text: str,
    story_candidates: list[dict[str, Any]],
    output_dir: Path,
    summarizer: Any,
) -> EventEnvelope[Any]:
    """Build standardized podcast context and Amazon Polly SSML."""

    write_json(output_dir / "input_event.json", cleaned_event)
    payload = cleaned_event.payload
    context = summarizer.build_podcast_context(
        title=str(payload["title"]),
        source=payload.get("source"),
        clean_text=clean_text,
        story_candidates=story_candidates,
    )
    polly_ssml = summarizer.build_polly_ssml(context)
    prompt = summarizer.build_morning_briefing_prompt(clean_text)

    write_json(output_dir / "podcast_context.json", context)
    (output_dir / "agent_prompt.txt").write_text(prompt, encoding="utf-8")
    (output_dir / "polly_ssml.xml").write_text(polly_ssml, encoding="utf-8")

    script_payload = summarizer.script_event_payload(
        tenant_id=cleaned_event.tenant_id,
        newsletter_id=cleaned_event.newsletter_id,
        title=context["episode"]["title"],
        ssml_key="03_podcast_context/polly_ssml.xml",
        ssml_text_value=polly_ssml,
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
            "ssml_chars": len(polly_ssml),
            "estimated_duration_seconds": script_payload.estimated_duration_seconds,
            "story_count": len(context["story_candidates"]),
            "story_titles": [story["title"] for story in context["story_candidates"]],
            "polly_voice": script_payload.voice,
            "text_type": "ssml",
        },
    )
    return event


def run_polly_audio_stage(
    *,
    script_event: EventEnvelope[Any],
    ssml_path: Path,
    output_dir: Path,
    polly_synthesizer: Any,
    polly_config: PollySynthesisConfig,
) -> dict[str, Any]:
    """Synthesize local Polly MP3 audio from generated SSML."""

    output_dir.mkdir(parents=True, exist_ok=True)
    ssml = ssml_path.read_text(encoding="utf-8")
    audio = polly_synthesizer.synthesize_ssml(ssml)
    mp3_path = output_dir / "episode.mp3"
    mp3_path.write_bytes(audio.content)
    summary = {
        "audio_file": str(mp3_path),
        "byte_length": len(audio.content),
        "content_type": audio.content_type,
        "request_characters": audio.request_characters,
        "region_name": polly_config.region_name,
        "engine": polly_config.engine,
        "voice_id": polly_config.voice_id,
        "output_format": polly_config.output_format,
        "source_event_id": script_event.event_id,
    }
    write_json(output_dir / "summary.json", summary)
    return summary


def discover_input_files(input_path: Path) -> list[Path]:
    """Return local samples from a single file or directory."""

    resolved = input_path.expanduser()
    if resolved.is_file():
        return [resolved]
    if not resolved.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")
    files = [
        path
        for path in sorted(resolved.iterdir())
        if path.is_file() and path.name not in IGNORED_INPUT_NAMES and not path.name.startswith(".")
    ]
    if not files:
        raise FileNotFoundError(f"No sample files found in {input_path}")
    return files


def clean_output_dir(output_dir: Path) -> None:
    """Remove a local pipeline output directory after basic safety checks."""

    resolved = output_dir.expanduser().resolve()
    repo_root = REPO_ROOT.resolve()
    if resolved in {repo_root, repo_root / "data", repo_root / "tests"}:
        raise ValueError(f"Refusing to clean unsafe output directory: {resolved}")
    if not resolved.is_relative_to(repo_root / "artifacts"):
        raise ValueError(f"Refusing to clean output outside artifacts/: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def looks_like_email(raw_bytes: bytes) -> bool:
    """Heuristically detect raw RFC 5322/MIME email samples."""

    head = raw_bytes[:4096].decode("utf-8", errors="ignore").lower()
    return any(header in head for header in ("return-path:", "received:", "mime-version:", "message-id:"))


def synthetic_message(sample_path: Path) -> Message:
    """Create minimal message metadata for decoded text samples."""

    message = Message(policy=policy.default)
    message["From"] = "local-sample"
    message["To"] = "listen@example.invalid"
    message["Subject"] = sample_path.stem
    message["Message-ID"] = f"<{sample_path.stem}@local.hearletter>"
    return message


def message_id_for(message: Message, sample_path: Path) -> str:
    """Return a stable-ish message id for local run correlation."""

    return str(message.get("Message-ID", f"{sample_path.stem}@local.hearletter")).strip("<>")


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
    safe_stem = "".join(char if char.isalnum() else "_" for char in path.stem)
    return f"nws_local_{safe_stem}"


def safe_sample_name(path: Path) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in path.name)


def default_output_dir(input_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = input_path.expanduser().name if input_path.expanduser().name else "samples"
    return REPO_ROOT / "artifacts" / "local" / f"{safe_sample_name(Path(stem))}-{timestamp}"


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
