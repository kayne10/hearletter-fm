#!/usr/bin/env python3
"""Run the two-Lambda briefing flow locally against raw MIME files.

Simulates: raw emails in S3 -> SQS batch -> briefing-builder -> script -> episode-producer.
Storage is a local folder; email sending is printed instead of sent.

Examples:
    python3 scripts/run_local_briefing.py tests/fixtures/raw_email --min-sources 1
    OPENAI_API_KEY=sk-... python3 scripts/run_local_briefing.py emails/ --synthesize
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for import_root in ("packages/domain", "packages/utils", "services/shared"):
    sys.path.insert(0, str(REPO_ROOT / import_root))

from hearletter_shared.config import REPO_CONFIG_PATH, load_briefing_config  # noqa: E402
from hearletter_shared.notification import EpisodeEmail  # noqa: E402
from hearletter_shared.object_store import LocalObjectStore  # noqa: E402

RAW_BUCKET = "local-raw-email"


def load_handler(service: str) -> ModuleType:
    path = REPO_ROOT / "services" / service / "handler.py"
    spec = importlib.util.spec_from_file_location(f"local_{service.replace('-', '_')}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


def sqs_record(index: int, key: str) -> dict[str, Any]:
    """An SQS record wrapping an S3 ObjectCreated notification, as Lambda receives it."""

    s3_event = {
        "Records": [
            {
                "eventName": "ObjectCreated:Put",
                "eventTime": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "s3": {"bucket": {"name": RAW_BUCKET}, "object": {"key": key}},
            }
        ]
    }
    return {"messageId": f"local-{index}", "body": json.dumps(s3_event)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("inputs", nargs="+", type=Path, help="Raw MIME files or directories")
    parser.add_argument("--config", type=Path, default=REPO_CONFIG_PATH)
    parser.add_argument("--min-sources", type=int, help="Override min_sources (handy with 1 file)")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "artifacts/local/briefing")
    parser.add_argument("--keep", action="store_true", help="Keep earlier staged newsletters")
    parser.add_argument("--synthesize", action="store_true", help="Call the TTS provider")
    args = parser.parse_args()

    if args.output_dir.exists() and not args.keep:
        shutil.rmtree(args.output_dir)
    processed = LocalObjectStore(args.output_dir / "processed")
    audio = LocalObjectStore(args.output_dir / "audio")

    config = load_briefing_config(args.config)
    if args.min_sources is not None:
        config = dataclasses.replace(config, min_sources=args.min_sources)

    files = sorted(
        path
        for item in args.inputs
        for path in (item.rglob("*") if item.is_dir() else [item])
        if path.is_file() and not path.name.startswith(".")
    )
    raw_by_key = {f"raw-email/default/{path.name}": path for path in files}

    builder = load_handler("briefing-builder")
    outcome = builder.process_batch(
        [sqs_record(index, key) for index, key in enumerate(raw_by_key)],
        read_raw=lambda ref: raw_by_key[ref.key].read_bytes(),
        processed=processed,
        config=config,
        now=datetime.now(UTC),
    )
    print(json.dumps(dataclasses.asdict(outcome), indent=2))

    built = [item for item in outcome.briefings if item.get("status") in {"built", "already_built"}]
    if not built:
        print("\nNo script yet: not all required sources are present.")
        return
    for item in built:
        script = json.loads(processed.get_bytes(item["script_key"]))
        print(f"\n===== {item['script_key']} =====\n{script['text']}\n")
        if not args.synthesize:
            continue
        import boto3
        from hearletter_shared.tts import build_tts_provider

        producer = load_handler("episode-producer")
        result = producer.produce_episode(
            processed,
            item["script_key"],
            audio=audio,
            tts_factory=lambda: build_tts_provider(env=os.environ, boto3_session=boto3.Session()),
            link_for=lambda key: str(audio.root / key),
            send_email=print_email,
            from_email="no-reply@beta.hearletter.fm",
            url_ttl_seconds=3600,
        )
        print(json.dumps(result, indent=2))


def print_email(email: EpisodeEmail) -> str:
    print(f"[email] {email.from_email} -> {email.to_email}: {email.title}\n  {email.audio_url}")
    return "local-message-id"


if __name__ == "__main__":
    main()
