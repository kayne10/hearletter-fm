# LEGACY: not used by the briefing pipeline. Safe to delete; see docs/briefing-pipeline.md.
"""FFmpeg-backed audio mixing utilities."""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from hearletter_domain.audio import (
    AmbienceSegment,
    AudioFile,
    MusicSegment,
    NarrationSegment,
    ShowPlan,
    ShowSegment,
    TransitionSegment,
)


class CommandRunner(Protocol):
    """Runs an external command."""

    def __call__(self, command: Sequence[str]) -> None:
        """Run a command or raise on failure."""


def subprocess_runner(command: Sequence[str]) -> None:
    """Run a command with subprocess."""

    subprocess.run(list(command), check=True)


class FfmpegAudioMixer:
    """Audio mixer implemented with FFmpeg command-line tools."""

    def __init__(
        self,
        *,
        ffmpeg_path: str = "ffmpeg",
        runner: CommandRunner = subprocess_runner,
        work_dir: Path | None = None,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._runner = runner
        self._work_dir = work_dir

    def mix(self, show_plan: ShowPlan) -> AudioFile:
        """Render a final MP3 file for a show plan."""

        validate_show_plan(show_plan)
        output_path = show_plan.output.path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if self._work_dir is not None:
            self._work_dir.mkdir(parents=True, exist_ok=True)
            return self._mix_in_work_dir(show_plan, self._work_dir)

        with tempfile.TemporaryDirectory(prefix="hearletter-mix-") as temp_dir:
            return self._mix_in_work_dir(show_plan, Path(temp_dir))

    def _mix_in_work_dir(self, show_plan: ShowPlan, work_dir: Path) -> AudioFile:
        rendered_segments = render_segments(
            show_plan,
            work_dir=work_dir,
            ffmpeg_path=self._ffmpeg_path,
            runner=self._runner,
        )
        concat_path = work_dir / "concat.txt"
        combined_path = work_dir / "combined.wav"
        write_concat_file(concat_path, rendered_segments)
        self._runner(
            [
                self._ffmpeg_path,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c",
                "copy",
                str(combined_path),
            ]
        )

        source_path = combined_path
        if show_plan.ambience is not None:
            source_path = work_dir / "with_ambience.wav"
            mix_ambience(
                show_plan.ambience,
                source_path=combined_path,
                output_path=source_path,
                ffmpeg_path=self._ffmpeg_path,
                runner=self._runner,
            )

        encode_final_mp3(
            show_plan,
            source_path=source_path,
            output_path=show_plan.output.path,
            ffmpeg_path=self._ffmpeg_path,
            runner=self._runner,
        )
        return AudioFile(
            path=show_plan.output.path,
            mime_type="audio/mpeg",
            byte_length=file_size(show_plan.output.path),
        )


def render_segments(
    show_plan: ShowPlan,
    *,
    work_dir: Path,
    ffmpeg_path: str,
    runner: CommandRunner,
) -> list[Path]:
    """Render show segments and requested silence clips to normalized WAV files."""

    rendered: list[Path] = []
    for index, segment in enumerate(show_plan.segments):
        if segment.leading_silence_ms:
            rendered.append(
                render_silence(
                    work_dir / f"{index:03d}_leading_silence.wav",
                    duration_ms=segment.leading_silence_ms,
                    show_plan=show_plan,
                    ffmpeg_path=ffmpeg_path,
                    runner=runner,
                )
            )

        segment_path = work_dir / f"{index:03d}_{segment_name(segment)}.wav"
        render_audio_segment(
            segment,
            output_path=segment_path,
            show_plan=show_plan,
            ffmpeg_path=ffmpeg_path,
            runner=runner,
        )
        rendered.append(segment_path)

        if segment.trailing_silence_ms:
            rendered.append(
                render_silence(
                    work_dir / f"{index:03d}_trailing_silence.wav",
                    duration_ms=segment.trailing_silence_ms,
                    show_plan=show_plan,
                    ffmpeg_path=ffmpeg_path,
                    runner=runner,
                )
            )
    return rendered


def render_audio_segment(
    segment: ShowSegment,
    *,
    output_path: Path,
    show_plan: ShowPlan,
    ffmpeg_path: str,
    runner: CommandRunner,
) -> None:
    """Render one segment with gain, fades, channel, and sample-rate settings."""

    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(segment.audio.path),
        "-vn",
        "-ac",
        str(show_plan.channels),
        "-ar",
        str(show_plan.sample_rate_hz),
    ]
    filters = segment_filters(segment)
    if filters:
        command.extend(["-af", ",".join(filters)])
    command.append(str(output_path))
    runner(command)


def render_silence(
    output_path: Path,
    *,
    duration_ms: int,
    show_plan: ShowPlan,
    ffmpeg_path: str,
    runner: CommandRunner,
) -> Path:
    """Render a silence clip."""

    duration_seconds = duration_ms / 1000
    runner(
        [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            (
                f"anullsrc=channel_layout={channel_layout(show_plan.channels)}:"
                f"sample_rate={show_plan.sample_rate_hz}"
            ),
            "-t",
            format_seconds(duration_seconds),
            str(output_path),
        ]
    )
    return output_path


def mix_ambience(
    ambience: AmbienceSegment,
    *,
    source_path: Path,
    output_path: Path,
    ffmpeg_path: str,
    runner: CommandRunner,
) -> None:
    """Mix ambience underneath the combined episode."""

    command = [ffmpeg_path, "-y", "-i", str(source_path)]
    if ambience.loop:
        command.extend(["-stream_loop", "-1"])
    command.extend(["-i", str(ambience.audio.path)])
    filters = ",".join(segment_filters(ambience))
    filter_complex = (
        f"[1:a]{filters}[amb];"
        "[0:a][amb]amix=inputs=2:duration=first:dropout_transition=0[mix]"
    )
    command.extend(["-filter_complex", filter_complex, "-map", "[mix]", str(output_path)])
    runner(command)


def encode_final_mp3(
    show_plan: ShowPlan,
    *,
    source_path: Path,
    output_path: Path,
    ffmpeg_path: str,
    runner: CommandRunner,
) -> None:
    """Encode the final MP3 with optional loudness normalization."""

    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(source_path),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        show_plan.bitrate,
    ]
    if show_plan.normalize:
        command.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])
    command.append(str(output_path))
    runner(command)


def segment_filters(segment: ShowSegment | AmbienceSegment) -> list[str]:
    """Return FFmpeg audio filters for a segment."""

    filters = [f"volume={segment.gain_db:g}dB"]
    if segment.fade_in_ms:
        filters.append(f"afade=t=in:d={format_seconds(segment.fade_in_ms / 1000)}")
    if segment.fade_out_ms:
        filters.extend(
            [
                "areverse",
                f"afade=t=in:d={format_seconds(segment.fade_out_ms / 1000)}",
                "areverse",
            ]
        )
    return filters


def write_concat_file(path: Path, segment_paths: list[Path]) -> None:
    """Write an FFmpeg concat demuxer file list."""

    lines = [f"file {shlex.quote(str(segment_path))}" for segment_path in segment_paths]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_show_plan(show_plan: ShowPlan) -> None:
    """Validate required mixer inputs."""

    if not show_plan.segments:
        raise ValueError("ShowPlan must contain at least one segment")
    for segment in show_plan.segments:
        if not segment.audio.path:
            raise ValueError("Every segment must reference an audio file path")


def segment_name(segment: ShowSegment) -> str:
    """Return a stable segment name for temporary files."""

    if isinstance(segment, NarrationSegment):
        return "narration"
    if isinstance(segment, MusicSegment):
        return segment.role
    if isinstance(segment, TransitionSegment):
        return f"transition_{segment.style}"
    return "segment"


def channel_layout(channels: int) -> str:
    """Return an FFmpeg channel layout for a channel count."""

    return "mono" if channels == 1 else "stereo"


def format_seconds(value: float) -> str:
    """Format seconds for FFmpeg flags."""

    return f"{value:.3f}".rstrip("0").rstrip(".")


def file_size(path: Path) -> int | None:
    """Return file size when the output exists."""

    return path.stat().st_size if path.exists() else None
