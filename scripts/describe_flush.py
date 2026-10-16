#!/usr/bin/env python3
"""Summarize a briefing-builder flush response for `make episode`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

MESSAGES = {
    "built": "Episode built: {title_hint}. The MP3 email follows in a minute or two.",
    "unchanged": "Nothing new: today's episode already covers these newsletters.",
    "waiting": "No newsletters staged for today yet.",
}


def main() -> int:
    response = json.loads(Path(sys.argv[1]).read_text())
    briefings = response.get("briefings", [])
    if not briefings:
        print("No newsletters have arrived today. Forward them to listen@beta.hearletter.fm first.")
        return 1
    for item in briefings:
        status = item.get("status", "unknown")
        version = item.get("version")
        title_hint = f"version {version}" if version else "today"
        print(
            f"{item.get('episode_date')}: "
            + MESSAGES.get(status, status).format(title_hint=title_hint)
        )
        if item.get("writer"):
            print(f"  script written by: {item['writer']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
