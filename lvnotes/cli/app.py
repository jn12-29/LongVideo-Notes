from collections.abc import Callable
import json
import logging
from pathlib import Path

import click

from lvnotes.audio_pipeline import extract, refine, segment, transcribe
from lvnotes.core.artifacts import AudioArtifacts, VisualArtifacts
from lvnotes.core.cache import hash_file
from lvnotes.core.config import load_config
from lvnotes.core.context import ArtifactBundle, PipelineContext
from lvnotes.core.exceptions import LVNotesError
from lvnotes.core.logging import configure_logging
from lvnotes.core.paths import build_paths, ensure_runtime_dirs
from lvnotes.media.probe import probe_media
from lvnotes.merge import assemble, outline, section, unify
from lvnotes.visual_pipeline import cluster, describe, judge, sample, select

log = logging.getLogger(__name__)
StageRun = Callable[[PipelineContext], object]

AUDIO_STAGES: dict[str, StageRun] = {
    "extract": extract.run,
    "transcribe": transcribe.run,
    "segment": segment.run,
    "refine": refine.run,
}
VISUAL_STAGES: dict[str, StageRun] = {
    "sample": sample.run,
    "cluster": cluster.run,
    "judge": judge.run,
    "select": select.run,
    "describe": describe.run,
}
MERGE_STAGES: dict[str, StageRun] = {
    "unify": unify.run,
    "outline": outline.run,
    "section": section.run,
    "assemble": assemble.run,
}


@click.group()
def main() -> None:
    """LongVideo-Notes command line interface."""


@main.command("run")
@click.argument("input_file", type=click.Path(path_type=Path))
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
@click.option("--mm", is_flag=True, help="Enable multimodal mode for video input.")
@click.option("--no-cache", is_flag=True)
@click.option("--debug", is_flag=True)
def run_command(input_file: Path, config_path: Path | None, mm: bool, no_cache: bool, debug: bool) -> None:
    ctx = _make_context(input_file, config_path, mm, no_cache, debug, require_mm=False)
    _run_stage_sequence(ctx, [extract.run, transcribe.run, segment.run, refine.run])
    if ctx.mode == "multimodal":
        _run_stage_sequence(ctx, [sample.run, cluster.run, judge.run, select.run])
        if not ctx.artifacts.audio.is_complete():
            raise click.ClickException("visual describe requires completed audio refine stage")
        _run_stage(ctx, describe.run)
    _run_stage_sequence(ctx, [unify.run, outline.run, section.run, assemble.run])
    click.echo(f"Output: {ctx.paths.output_note_md}")


@main.command("inspect")
@click.argument("namespace", type=click.Choice(["audio", "visual", "merge"]))
@click.argument("stage")
@click.argument("input_file", type=click.Path(path_type=Path))
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
@click.option("--mm", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--paths", "paths_only", is_flag=True)
def inspect_command(namespace: str, stage: str, input_file: Path, config_path: Path | None, mm: bool, as_json: bool, paths_only: bool) -> None:
    ctx = _make_context(input_file, config_path, mm, False, False, require_mm=False)
    path = _inspect_path(ctx, namespace, stage)
    if paths_only:
        click.echo(path)
        return
    if not path.exists():
        raise click.ClickException(f"artifact not found: {path}")
    if as_json:
        click.echo(path.read_text(encoding="utf-8"))
        return
    click.echo(f"stage: {namespace} {stage}")
    click.echo(f"input_hash: {ctx.input_hash}")
    click.echo(f"path: {path}")
    click.echo(f"size_bytes: {path.stat().st_size}")


def _register_stage_commands() -> None:
    for stage_name in AUDIO_STAGES:
        main.add_command(_stage_command(stage_name, AUDIO_STAGES[stage_name], require_mm=False), stage_name)
    for stage_name in VISUAL_STAGES:
        main.add_command(_stage_command(stage_name, VISUAL_STAGES[stage_name], require_mm=True), stage_name)
    for stage_name in MERGE_STAGES:
        main.add_command(_stage_command(stage_name, MERGE_STAGES[stage_name], require_mm=False), stage_name)


def _stage_command(stage_name: str, stage_run: StageRun, require_mm: bool) -> click.Command:
    @click.command(name=stage_name)
    @click.argument("input_file", type=click.Path(path_type=Path))
    @click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
    @click.option("--mm", is_flag=True)
    @click.option("--no-cache", is_flag=True)
    @click.option("--debug", is_flag=True)
    def command(input_file: Path, config_path: Path | None, mm: bool, no_cache: bool, debug: bool) -> None:
        ctx = _make_context(input_file, config_path, mm, no_cache, debug and stage_name == "refine", require_mm=require_mm)
        output = _run_stage(ctx, stage_run)
        cache_hit = getattr(output, "cache_hit", False)
        paths = getattr(output, "output_paths", [])
        click.echo(f"{stage_name}: {'cache hit' if cache_hit else 'done'}")
        for path in paths:
            click.echo(path)

    return command


def _make_context(input_file: Path, config_path: Path | None, mm: bool, no_cache: bool, debug: bool, require_mm: bool) -> PipelineContext:
    try:
        configure_logging(debug)
        source_path = input_file.expanduser().resolve()
        if not source_path.exists():
            raise click.ClickException(f"input file not found: {source_path}")
        config = load_config(config_path)
        probe = probe_media(source_path)
        if probe.audio is None:
            raise click.ClickException("input must contain an audio stream")
        if mm and probe.video is None:
            raise click.BadParameter("--mm requires a video input; audio files always run in audio-only mode.")
        if require_mm and not mm:
            raise click.BadParameter("this visual stage requires --mm")
        mode = "multimodal" if mm and probe.video is not None else "audio_only"
        input_hash = hash_file(source_path)
        paths = build_paths(source_path, config.project.cache_dir, config.project.output_dir, input_hash)
        ensure_runtime_dirs(paths)
        artifacts = ArtifactBundle(audio=AudioArtifacts(input_hash, paths), visual=VisualArtifacts(input_hash, paths) if mode == "multimodal" else None)
        return PipelineContext(source_path, input_hash, mode, config, paths, artifacts, debug, no_cache)
    except LVNotesError as exc:
        log.error("command failed: %s", exc)
        raise click.ClickException(str(exc)) from exc


def _run_stage_sequence(ctx: PipelineContext, stages: list[StageRun]) -> None:
    for stage_run in stages:
        _run_stage(ctx, stage_run)


def _run_stage(ctx: PipelineContext, stage_run: StageRun) -> object:
    try:
        output = stage_run(ctx)
    except LVNotesError as exc:
        log.error("stage failed: %s", exc)
        raise click.ClickException(str(exc)) from exc
    stage_name = getattr(output, "stage_name", stage_run.__module__)
    cache_hit = getattr(output, "cache_hit", False)
    click.echo(f"{stage_name}: {'cache hit' if cache_hit else 'done'}")
    return output


def _inspect_path(ctx: PipelineContext, namespace: str, stage: str) -> Path:
    paths = {
        ("audio", "extract"): ctx.paths.audio_extract_json,
        ("audio", "transcript"): ctx.paths.transcript_raw_json,
        ("audio", "segments"): ctx.paths.segments_json,
        ("audio", "refined"): ctx.paths.refined_transcript_json,
        ("visual", "sample"): ctx.paths.visual_sample_json,
        ("visual", "cluster"): ctx.paths.visual_segments_json,
        ("visual", "judge"): ctx.paths.visual_judgements_json,
        ("visual", "select"): ctx.paths.visual_selections_json,
        ("visual", "describe"): ctx.paths.visual_descriptions_json,
        ("merge", "blocks"): ctx.paths.content_blocks_json,
        ("merge", "unify"): ctx.paths.content_blocks_json,
        ("merge", "outline"): ctx.paths.outline_json,
        ("merge", "note"): ctx.paths.output_note_md,
        ("merge", "assemble"): ctx.paths.output_note_md,
    }
    try:
        return paths[(namespace, stage)]
    except KeyError as exc:
        raise click.ClickException(f"unknown artifact: {namespace} {stage}") from exc


_register_stage_commands()
