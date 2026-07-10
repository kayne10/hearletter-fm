#!/usr/bin/env python3
"""Inspect a raw SES email/MIME file without dumping large attachments."""

from __future__ import annotations

import argparse
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a raw MIME email saved by SES.")
    parser.add_argument("email_file", type=Path, help="Path to the raw .eml file")
    parser.add_argument(
        "--text-preview-chars",
        type=int,
        default=2000,
        help="Maximum characters to print from each text part",
    )
    parser.add_argument(
        "--extract-text",
        action="store_true",
        help="Print decoded text/html and text/plain parts in full.",
    )
    parser.add_argument(
        "--save-attachments-dir",
        type=Path,
        help="Optional directory where decoded non-text MIME parts should be written.",
    )
    args = parser.parse_args()

    raw_message = args.email_file.read_bytes()
    message = BytesParser(policy=policy.default).parsebytes(raw_message)

    print_headers(message)
    print("\nMIME parts:")
    for index, part in enumerate(message.walk(), start=1):
        print_part(index, part, args.text_preview_chars, extract_text=args.extract_text)
        if args.save_attachments_dir:
            save_attachment(index, part, args.save_attachments_dir)


def print_headers(message: Message) -> None:
    print("Headers:")
    for header in ("From", "To", "Subject", "Date", "Message-ID", "Content-Type"):
        value = message.get(header)
        if value:
            print(f"  {header}: {value}")


def print_part(
    index: int,
    part: Message,
    text_preview_chars: int,
    *,
    extract_text: bool,
) -> None:
    content_type = part.get_content_type()
    disposition = part.get_content_disposition() or "body"
    filename = part.get_filename()

    if part.is_multipart():
        print(f"  [{index}] multipart {content_type}")
        return

    payload_size = len(part.get_payload(decode=True) or b"")
    filename_suffix = f" filename={filename!r}" if filename else ""
    print(f"  [{index}] {content_type} disposition={disposition} bytes={payload_size}{filename_suffix}")

    if content_type.startswith("text/"):
        content = get_text_content(part)

        if isinstance(content, str):
            preview = content.strip() if extract_text else content.strip()[:text_preview_chars]
            if preview:
                print(indent(preview))


def get_text_content(part: Message) -> str:
    """Decode a text MIME part according to its transfer encoding and charset."""

    if isinstance(part, EmailMessage):
        content = part.get_content()
        return content if isinstance(content, str) else str(content)

    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def save_attachment(index: int, part: Message, output_dir: Path) -> None:
    """Write decoded non-text MIME parts to disk for manual inspection."""

    if part.is_multipart() or part.get_content_type().startswith("text/"):
        return

    payload = part.get_payload(decode=True)
    if not payload:
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = part.get_filename() or f"part-{index}.{extension_for(part.get_content_type())}"
    destination = output_dir / filename
    destination.write_bytes(payload)
    print(f"      saved attachment: {destination}")


def extension_for(content_type: str) -> str:
    extension_by_type = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "application/pdf": "pdf",
    }
    return extension_by_type.get(content_type, "bin")


def indent(value: str) -> str:
    return "\n".join(f"      {line}" for line in value.splitlines())


if __name__ == "__main__":
    main()
