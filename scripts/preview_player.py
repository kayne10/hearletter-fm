#!/usr/bin/env python3
"""Serve the web player locally with sample episodes, mirroring the CloudFront layout.

    make preview-player            # then open the printed URL (or <your-LAN-IP>:8765 on a phone)

Sample audio comes from MP3s in artifacts/voice-samples (created when comparing Polly voices)
or any folder given with --audio-dir. Python's static server doesn't support HTTP range
requests, so seeking may be limited in Safari here; CloudFront supports it.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import shutil
from datetime import UTC, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYER_DIR = REPO_ROOT / "web" / "player"
PREVIEW_ROOT = REPO_ROOT / "artifacts" / "player-preview"
TOKEN = "preview-token"
FEED = "preview"


def build_site(audio_files: list[Path]) -> Path:
    if PREVIEW_ROOT.exists():
        shutil.rmtree(PREVIEW_ROOT)
    site = PREVIEW_ROOT / TOKEN
    shutil.copytree(PLAYER_DIR, site / "player")

    today = datetime.now(UTC).date()
    episodes = []
    for offset, source in enumerate(audio_files[:7]):
        day = (today - timedelta(days=offset)).isoformat()
        episode_id = f"ep_{day.replace('-', '')}_preview{offset}"
        audio_path = f"episodes/{FEED}/{day}/{episode_id}.mp3"
        target = site / audio_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        episodes.append(
            {
                "episode_id": episode_id,
                "title": f"Morning Briefing - {day}",
                "episode_date": day,
                "published_at": f"{day}T13:00:00Z",
                "audio_path": audio_path,
                "byte_length": target.stat().st_size,
                "version": 1,
                "summary": f"Preview using {source.name}. From TLDR, Morning Brew, The Rundown.",
            }
        )
    feeds = site / "feeds" / FEED
    feeds.mkdir(parents=True)
    (feeds / "episodes.json").write_text(json.dumps({"episodes": episodes}, indent=2))
    return PREVIEW_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--audio-dir", type=Path, default=REPO_ROOT / "artifacts" / "voice-samples")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--build-only", action="store_true", help="Build the site without serving")
    args = parser.parse_args()

    audio_files = sorted(args.audio_dir.glob("*.mp3"))
    if not audio_files:
        parser.error(f"No MP3s in {args.audio_dir}; pass --audio-dir with a few sample MP3s")
    root = build_site(audio_files)
    url = f"http://localhost:{args.port}/{TOKEN}/player/#{FEED}"
    print(f"Built {root} with {min(len(audio_files), 7)} sample episodes")
    if args.build_only:
        return

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    with ThreadingHTTPServer(("0.0.0.0", args.port), handler) as server:
        print(f"Serving {url}  (Ctrl+C to stop)")
        with contextlib.suppress(KeyboardInterrupt):  # Ctrl+C is the normal way to stop
            server.serve_forever()


if __name__ == "__main__":
    main()
