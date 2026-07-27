#!/usr/bin/env python3
"""Build AWS Lambda zip artifacts for the Hearletter FM services."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "lambda"
BUILD_ROOT = REPO_ROOT / "artifacts" / "build" / "lambda"


@dataclass(frozen=True, slots=True)
class LambdaService:
    """Packaging metadata for a single Lambda service."""

    name: str
    source_dir: Path

    @property
    def zip_name(self) -> str:
        return f"{self.name}.zip"

    @property
    def requirements_file(self) -> Path:
        return self.source_dir / "requirements.txt"


SERVICES = [
    LambdaService(name="email-parser", source_dir=REPO_ROOT / "services" / "email-parser"),
    LambdaService(
        name="newsletter-cleaner",
        source_dir=REPO_ROOT / "services" / "newsletter-cleaner",
    ),
    LambdaService(name="summarizer", source_dir=REPO_ROOT / "services" / "summarizer"),
    LambdaService(name="tts", source_dir=REPO_ROOT / "services" / "tts"),
    LambdaService(name="rss-generator", source_dir=REPO_ROOT / "services" / "rss-generator"),
    LambdaService(name="notifier", source_dir=REPO_ROOT / "services" / "notifier"),
]

SHARED_PACKAGE_ROOTS = [
    REPO_ROOT / "packages" / "domain",
    REPO_ROOT / "packages" / "types",
    REPO_ROOT / "packages" / "utils",
    REPO_ROOT / "services" / "shared",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Package Hearletter FM Lambda functions.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where Lambda zip files should be written.",
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="Skip pip installing per-service requirements.txt files.",
    )
    parser.add_argument(
        "--service",
        choices=[service.name for service in SERVICES],
        help="Package only one service.",
    )
    args = parser.parse_args()

    selected_services = [service for service in SERVICES if args.service in (None, service.name)]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)

    for service in selected_services:
        build_service(service, output_dir=args.output_dir, install_deps=not args.skip_deps)


def build_service(service: LambdaService, *, output_dir: Path, install_deps: bool) -> None:
    build_dir = BUILD_ROOT / service.name
    zip_path = output_dir / service.zip_name

    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    copy_service_source(service, build_dir)
    copy_shared_packages(build_dir)

    if install_deps and service.requirements_file.exists():
        install_requirements(service.requirements_file, build_dir)

    if zip_path.exists():
        zip_path.unlink()
    archive_base = zip_path.with_suffix("")
    shutil.make_archive(str(archive_base), "zip", build_dir)
    print(f"built {zip_path.relative_to(REPO_ROOT)}")


def copy_service_source(service: LambdaService, build_dir: Path) -> None:
    handler = service.source_dir / "handler.py"
    if not handler.exists():
        raise FileNotFoundError(f"Missing Lambda handler: {handler}")
    shutil.copy2(handler, build_dir / "handler.py")


def copy_shared_packages(build_dir: Path) -> None:
    for package_root in SHARED_PACKAGE_ROOTS:
        for package_dir in package_root.iterdir():
            if package_dir.is_dir() and (package_dir / "__init__.py").exists():
                destination = build_dir / package_dir.name
                shutil.copytree(package_dir, destination, ignore=ignore_generated_files)


def ignore_generated_files(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}
    if Path(directory).name in {".pytest_cache", ".ruff_cache", ".mypy_cache"}:
        ignored.update(names)
    return ignored


def install_requirements(requirements_file: Path, target_dir: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--requirement",
        str(requirements_file),
        "--target",
        str(target_dir),
        "--upgrade",
    ]
    print(f"installing dependencies from {requirements_file.relative_to(REPO_ROOT)}")
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
