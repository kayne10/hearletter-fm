# LEGACY: not used by the briefing pipeline. Safe to delete; see docs/briefing-pipeline.md.
from __future__ import annotations

from pathlib import Path

from hearletter_domain.audio import (
    AmbienceSegment,
    AudioFile,
    MusicSegment,
    NarrationSegment,
    ShowPlan,
    TransitionSegment,
)
from hearletter_shared.audio_mixer import FfmpegAudioMixer


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> None:
        self.commands.append(command)


def test_ffmpeg_audio_mixer_builds_commands_for_polished_episode(tmp_path: Path) -> None:
    runner = RecordingRunner()
    narration = tmp_path / "narration.mp3"
    intro = tmp_path / "intro.mp3"
    chime = tmp_path / "chime.mp3"
    ambience = tmp_path / "rain.mp3"
    for path in (narration, intro, chime, ambience):
        path.write_bytes(b"audio")

    show_plan = ShowPlan(
        title="Morning Briefing",
        output=AudioFile(path=tmp_path / "episode.mp3"),
        segments=[
            MusicSegment(
                audio=AudioFile(path=intro),
                role="intro",
                fade_in_ms=500,
                fade_out_ms=800,
            ),
            NarrationSegment(
                audio=AudioFile(path=narration),
                trailing_silence_ms=250,
            ),
            TransitionSegment(audio=AudioFile(path=chime), leading_silence_ms=100),
        ],
        ambience=AmbienceSegment(audio=AudioFile(path=ambience)),
    )
    mixer = FfmpegAudioMixer(runner=runner, work_dir=tmp_path / "work")

    output = mixer.mix(show_plan)

    assert output.path == tmp_path / "episode.mp3"
    commands = [" ".join(command) for command in runner.commands]
    assert any("volume=-6dB" in command for command in commands)
    assert any("afade=t=in:d=0.5" in command for command in commands)
    assert any("areverse" in command for command in commands)
    assert any("anullsrc" in command for command in commands)
    assert any("-f concat" in command for command in commands)
    assert any("amix=inputs=2:duration=first" in command for command in commands)
    assert any("loudnorm=I=-16:TP=-1.5:LRA=11" in command for command in commands)
    assert any("libmp3lame" in command for command in commands)


def test_ffmpeg_audio_mixer_rejects_empty_show_plan(tmp_path: Path) -> None:
    mixer = FfmpegAudioMixer(runner=RecordingRunner(), work_dir=tmp_path)
    show_plan = ShowPlan(title="Empty", output=AudioFile(path=tmp_path / "empty.mp3"), segments=[])

    try:
        mixer.mix(show_plan)
    except ValueError as exc:
        assert str(exc) == "ShowPlan must contain at least one segment"
    else:
        raise AssertionError("Expected empty show plan to fail")
