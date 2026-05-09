from dataclasses import dataclass
from pathlib import Path

from lvnotes.cli import app
from lvnotes.core.pipeline import StageOutput


@dataclass(frozen=True)
class Ctx:
    input_hash: str = "inputhash"


def test_run_stage_uses_consistent_user_visible_label(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    messages: list[str] = []

    def stage_run(ctx: Ctx) -> StageOutput:
        return StageOutput("extract", [Path("audio.wav")], False, "hash", {})

    stage_run.__module__ = "lvnotes.audio_pipeline.extract"

    monkeypatch.setattr(app, "progress_write", messages.append)

    app._run_stage(Ctx(), stage_run)

    assert messages == ["audio.extract: running", "audio.extract: done"]


def test_run_stage_uses_consistent_cache_hit_label(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    messages: list[str] = []

    def stage_run(ctx: Ctx) -> StageOutput:
        return StageOutput("extract", [Path("audio.wav")], True, "hash", {})

    stage_run.__module__ = "lvnotes.audio_pipeline.extract"

    monkeypatch.setattr(app, "progress_write", messages.append)

    app._run_stage(Ctx(), stage_run)

    assert messages == ["audio.extract: running", "audio.extract: cache hit"]
