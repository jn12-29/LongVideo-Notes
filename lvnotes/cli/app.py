from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import logging
from pathlib import Path

import click

from lvnotes.audio_pipeline import extract, refine, segment, transcribe
from lvnotes.cli._cuda_bootstrap import preload_cuda_libs
from lvnotes.cli.output_tidy import apply_output_tidy_plan, build_output_tidy_plan
from lvnotes.core.artifacts import AudioArtifacts, VisualArtifacts
from lvnotes.core.cache import hash_file
from lvnotes.core.config import load_config
from lvnotes.core.context import ArtifactBundle, PipelineContext
from lvnotes.core.exceptions import CacheError, LVNotesError
from lvnotes.core.locks import input_cache_lock
from lvnotes.core.logging import configure_logging
from lvnotes.core.paths import PipelinePaths, build_paths
from lvnotes.core.progress import progress_write
from lvnotes.media.probe import probe_media
from lvnotes.media.trim import resolve_head_trim_path, trim_media_head
from lvnotes.merge import assemble, outline, section, unify
from lvnotes.visual_pipeline import align, describe, filter, sample, semantic_filter
from lvnotes.llm import for_task

log = logging.getLogger(__name__)
StageRun = Callable[[PipelineContext], object]

VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus"})
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


@dataclass(frozen=True)
class InputTask:
    source_path: Path
    output_subdir: Path | None = None
    from_directory: bool = False


@dataclass(frozen=True)
class BatchFailure:
    source_path: Path
    message: str


class OrderedGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)


AUDIO_STAGES: dict[str, StageRun] = {
    "extract": extract.run,
    "transcribe": transcribe.run,
    "segment": segment.run,
    "refine": refine.run,
}
VISUAL_STAGES: dict[str, StageRun] = {
    "sample": sample.run,
    "filter": filter.run,
    "semantic-filter": semantic_filter.run,
    "align": align.run,
    "describe": describe.run,
}
MERGE_STAGES: dict[str, StageRun] = {
    "unify": unify.run,
    "outline": outline.run,
    "section": section.run,
    "assemble": assemble.run,
}

STAGE_OUTPUTS: dict[str, tuple[str, ...]] = {
    "extract": ("cache/{input_hash}/audio/audio.wav", "cache/{input_hash}/audio/extract.json"),
    "transcribe": ("cache/{input_hash}/transcript_raw.json",),
    "segment": ("cache/{input_hash}/segments.json",),
    "refine": ("cache/{input_hash}/refined_transcript.json", "cache/{input_hash}/refined/{seg_id:04d}.json"),
    "sample": ("cache/{input_hash}/visual/raw_frames/", "cache/{input_hash}/visual/sample.json"),
    "filter": ("cache/{input_hash}/visual/filter_frames/", "cache/{input_hash}/visual/filtered_sample.json", "cache/{input_hash}/visual/filter_variants/"),
    "semantic-filter": ("cache/{input_hash}/visual/semantic_frames/", "cache/{input_hash}/visual/semantic_sample.json", "cache/{input_hash}/visual/semantic_judgements.json"),
    "align": ("cache/{input_hash}/visual/alignments.json",),
    "describe": ("cache/{input_hash}/visual/descriptions.json",),
    "unify": ("cache/{input_hash}/content_blocks.json",),
    "outline": ("cache/{input_hash}/outline.json",),
    "section": ("cache/{input_hash}/sections/{chapter_id:03d}.md",),
    "assemble": (
        "output_dir/<relative-dir>/<source-stem>.md",
        "output_dir/<relative-dir>/<source-stem>-YYYYMMDD-HHMMSS.md",
        "cache/{input_hash}/note.md",
    ),
}

@click.group(cls=OrderedGroup)
def main() -> None:
    """Generate structured Markdown notes from long video or audio.

    \b
    Recommended workflow:
      lvnotes run <input-path>
      lvnotes run <input-path> --mm
      lvnotes run ./courses --mm
      lvnotes run <input-path> --head-minutes 10
      lvnotes output tidy --apply
      lvnotes inspect audio refined <input-path>
      lvnotes inspect merge note <input-path> --paths
      lvnotes inspect merge note <input-path> --head-minutes 10 --paths
      lvnotes assemble <input-path> --no-cache

    \b
    Modes:
      Audio files and video files without --mm run in audio-only mode.
      Video files with --mm run in multimodal mode.
      Directory inputs process media files recursively; with --mm, videos run in multimodal mode and audio files remain audio-only.

    \b
    Useful options:
      --head-minutes N  Process only the first N minutes; inspect reads an existing trim.
      --config PATH     Load a specific config file.
      --no-cache        Recompute stages run by commands that support it.
      --debug           Enable refine review flow during refine.
      --paths           Print only the artifact path in inspect.
      --json            Print raw artifact content in inspect.

    \b
    Main outputs:
      run writes the final Markdown note, a timestamped archive, and cache artifacts.
      output tidy moves timestamped archives under output/_archive/.
      stage commands write their listed cache artifacts; inspect only reads existing artifacts.
    """
    preload_cuda_libs()


@main.command("run", short_help="Generate a Markdown note end to end.")
@click.argument("input_path", type=click.Path(path_type=Path))
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
@click.option("--mm", is_flag=True, help="Enable multimodal mode for video input.")
@click.option("--head-minutes", type=click.FloatRange(min=0, min_open=True), default=None, help="Trim and process only the first N minutes.")
@click.option("--no-cache", is_flag=True)
@click.option("--debug", is_flag=True)
def run_command(input_path: Path, config_path: Path | None, mm: bool, head_minutes: float | None, no_cache: bool, debug: bool) -> None:
    """Generate a Markdown note end to end.

    Use --mm for multimodal video runs. Directory inputs are processed recursively; with --mm, videos run in multimodal mode and audio files remain audio-only.
    Use --head-minutes N for a quick trial on the first N minutes. Use --no-cache
    to recompute all stages run by run.

    \b
    Audio-only outputs:
      cache/{input_hash}/audio/audio.wav and audio/extract.json
      cache/{input_hash}/transcript_raw.json, segments.json, refined_transcript.json
      cache/{input_hash}/content_blocks.json, outline.json, sections/{chapter_id:03d}.md
      output_dir/<relative-dir>/<source-stem>.md and output_dir/<relative-dir>/<source-stem>-YYYYMMDD-HHMMSS.md
      cache/{input_hash}/note.md

    \b
    Multimodal extras with --mm:
      cache/{input_hash}/visual/raw_frames/ and visual/sample.json
      cache/{input_hash}/visual/filter_frames/, visual/filtered_sample.json, and visual/filter_variants/
      cache/{input_hash}/visual/semantic_frames/, visual/semantic_sample.json, visual/semantic_judgements.json
      cache/{input_hash}/visual/alignments.json and visual/descriptions.json
    """
    tasks = _resolve_input_tasks(input_path)
    failures = _run_batch(
        tasks,
        lambda task: _run_one_input(task, config_path, mm, no_cache, False, False, head_minutes, True, _run_end_to_end, debug),
    )
    _raise_for_batch_failures(failures)


@main.command("inspect", short_help="Inspect existing artifacts without running stages.")
@click.argument("namespace", type=click.Choice(["audio", "visual", "merge"]))
@click.argument("stage")
@click.argument("input_path", type=click.Path(path_type=Path))
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
@click.option("--mm", is_flag=True)
@click.option("--head-minutes", type=click.FloatRange(min=0, min_open=True), default=None, help="Inspect artifacts for the existing first-N-minutes trim.")
@click.option("--json", "as_json", is_flag=True)
@click.option("--paths", "paths_only", is_flag=True)
def inspect_command(namespace: str, stage: str, input_path: Path, config_path: Path | None, mm: bool, head_minutes: float | None, as_json: bool, paths_only: bool) -> None:
    """Inspect existing artifacts without running stages.

    Use --paths to print only the artifact path and --json to print raw artifact content.
    With --head-minutes N, inspect reads the existing first-N-minutes trim.

    \b
    Inspect does not generate files; it only reads existing artifacts:
      audio: extract, transcript, segments, refined
      visual: sample, filter, filter-variants, semantic-filter, semantic-judgements, align, describe
      merge: blocks, unify, outline, note, assemble
    """
    tasks = _resolve_input_tasks(input_path)
    if len(tasks) == 1 and not tasks[0].from_directory:
        _inspect_one_input(tasks[0], config_path, mm, head_minutes, namespace, stage, as_json, paths_only, aggregate_json=False)
        return
    failures: list[BatchFailure] = []
    json_items: list[dict[str, object]] = []
    for task in tasks:
        try:
            item = _inspect_one_input(task, config_path, mm, head_minutes, namespace, stage, as_json, paths_only, aggregate_json=True)
            if item is not None:
                json_items.append(item)
        except click.ClickException as exc:
            failures.append(BatchFailure(task.source_path, exc.message))
        except Exception as exc:
            log.exception("unexpected inspect failure")
            failures.append(BatchFailure(task.source_path, str(exc)))
    if as_json:
        click.echo(json.dumps({"items": json_items, "failures": _json_failures(failures)}, ensure_ascii=False, indent=2))
        if failures:
            raise click.exceptions.Exit(1)
        return
    _raise_for_batch_failures(failures)


@main.group("output", short_help="Maintain generated output files.")
def output_command() -> None:
    """Maintain generated output files."""


@output_command.command("tidy", short_help="Move timestamped note archives under output/_archive/.")
@click.argument("output_dir", required=False, type=click.Path(path_type=Path))
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None, help="Load output_dir from this config when OUTPUT_DIR is omitted.")
@click.option("--apply", "apply_changes", is_flag=True, help="Move files. Without this, only print the plan.")
def output_tidy_command(output_dir: Path | None, config_path: Path | None, apply_changes: bool) -> None:
    """Move timestamped note archives under output/_archive/.

    By default this is a dry run. Pass --apply to move files. Latest notes stay
    in place; matching timestamped assets directories move with their Markdown.
    """
    try:
        target_output_dir = output_dir.expanduser().resolve() if output_dir is not None else load_config(config_path).project.output_dir
        plan = build_output_tidy_plan(target_output_dir)
    except (LVNotesError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Output: {plan.output_dir}")
    click.echo(f"Archive: {plan.archive_root}")
    click.echo(f"Mode: {'apply' if apply_changes else 'dry-run'}")
    if plan.moves:
        click.echo("Moves:")
        for move in plan.moves:
            click.echo(f"  {move.source_md} -> {move.destination_md}")
            if move.source_assets is not None and move.destination_assets is not None:
                click.echo(f"  {move.source_assets} -> {move.destination_assets}")
    else:
        click.echo("Moves: 0")
    if plan.conflicts:
        click.echo("Conflicts:")
        for conflict in plan.conflicts:
            click.echo(f"  {conflict.source} -> {conflict.destination}: {conflict.reason}")
        raise click.ClickException(f"output tidy found {len(plan.conflicts)} conflict(s)")
    if not apply_changes:
        click.echo("Dry run only; pass --apply to move files.")
        return
    try:
        apply_output_tidy_plan(plan)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Moved archives: {len(plan.moves)}")


def _register_stage_commands() -> None:
    for stage_name in AUDIO_STAGES:
        main.add_command(_stage_command(stage_name, AUDIO_STAGES[stage_name], require_mm=False), stage_name)
    for stage_name in VISUAL_STAGES:
        main.add_command(_stage_command(stage_name, VISUAL_STAGES[stage_name], require_mm=True), stage_name)
    for stage_name in MERGE_STAGES:
        main.add_command(_stage_command(stage_name, MERGE_STAGES[stage_name], require_mm=False), stage_name)


def _stage_command(stage_name: str, stage_run: StageRun, require_mm: bool) -> click.Command:
    @click.command(
        name=stage_name,
        help=_stage_help(stage_name, require_mm),
        short_help=_stage_short_help(stage_name, require_mm),
    )
    @click.argument("input_path", type=click.Path(path_type=Path))
    @click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
    @click.option("--mm", is_flag=True)
    @click.option("--head-minutes", type=click.FloatRange(min=0, min_open=True), default=None, help="Trim and process only the first N minutes.")
    @click.option("--no-cache", is_flag=True)
    @click.option("--debug", is_flag=True)
    def command(input_path: Path, config_path: Path | None, mm: bool, head_minutes: float | None, no_cache: bool, debug: bool) -> None:
        tasks = _resolve_input_tasks(input_path)
        failures = _run_batch(
            tasks,
            lambda task: _run_one_input(task, config_path, mm, no_cache, debug and stage_name == "refine", require_mm, head_minutes, True, _run_single_stage, stage_name, stage_run),
        )
        _raise_for_batch_failures(failures)

    return command


def _stage_short_help(stage_name: str, require_mm: bool) -> str:
    if stage_name in AUDIO_STAGES:
        return f"Run audio {stage_name} stage."
    if require_mm:
        return f"Run visual {stage_name} stage; requires --mm."
    return f"Run merge {stage_name} stage."


def _stage_help(stage_name: str, require_mm: bool) -> str:
    help_text = f"{_stage_short_help(stage_name, require_mm)} Supports --head-minutes N and --no-cache."
    output_text = f"\b\n{_stage_output_help(stage_name)}"
    if require_mm:
        return f"{help_text} Video input must be run with --mm.\n\n{output_text}"
    return f"{help_text}\n\n{output_text}"


def _stage_output_help(stage_name: str) -> str:
    lines = ["Produces:"]
    lines.extend(f"  {path}" for path in STAGE_OUTPUTS[stage_name])
    return "\n".join(lines)


def _resolve_input_tasks(input_path: Path) -> list[InputTask]:
    source_path = input_path.expanduser().resolve()
    if not source_path.exists():
        raise click.ClickException(f"input path not found: {source_path}")
    if source_path.is_file():
        return [InputTask(source_path)]
    if not source_path.is_dir():
        raise click.ClickException(f"input path is not a file or directory: {source_path}")
    tasks = [
        InputTask(path, _relative_output_subdir(source_path, path), from_directory=True)
        for path in _iter_media_files(source_path)
    ]
    if not tasks:
        raise click.ClickException(f"no supported media files found in directory: {source_path}")
    return tasks


def _iter_media_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*"):
        if _has_hidden_part(path.relative_to(root)) or _is_head_trim_file(path):
            continue
        if path.is_dir() and path.is_symlink():
            continue
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
            paths.append(path.resolve())
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def _has_hidden_part(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _is_head_trim_file(path: Path) -> bool:
    marker = ".head-"
    if marker not in path.stem:
        return False
    minutes = path.stem.rsplit(marker, 1)[1]
    if minutes.endswith("m"):
        minutes = minutes[:-1]
    return bool(minutes) and all(char.isdigit() or char == "." for char in minutes)


def _relative_output_subdir(root: Path, source_path: Path) -> Path | None:
    parent = source_path.parent.relative_to(root)
    if parent == Path("."):
        return None
    return parent


def _run_batch(tasks: list[InputTask], runner: Callable[[InputTask], None]) -> list[BatchFailure]:
    failures: list[BatchFailure] = []
    for task in tasks:
        try:
            runner(task)
        except click.ClickException as exc:
            failures.append(BatchFailure(task.source_path, exc.message))
            progress_write(f"failed: {task.source_path}: {exc.message}")
        except Exception as exc:
            log.exception("unexpected batch failure")
            failures.append(BatchFailure(task.source_path, str(exc)))
            progress_write(f"failed: {task.source_path}: {exc}")
    if len(tasks) > 1:
        _echo_batch_summary(len(tasks), failures)
    return failures


def _echo_batch_summary(total: int, failures: list[BatchFailure]) -> None:
    progress_write("Batch summary:")
    progress_write(f"succeeded: {total - len(failures)}")
    progress_write(f"failed: {len(failures)}")
    for failure in failures:
        progress_write(f"  {failure.source_path}: {failure.message}")


def _raise_for_batch_failures(failures: list[BatchFailure]) -> None:
    if failures:
        raise click.ClickException(f"batch completed with {len(failures)} failure(s)")


def _run_one_input(
    task: InputTask,
    config_path: Path | None,
    mm: bool,
    no_cache: bool,
    debug: bool,
    require_mm: bool,
    head_minutes: float | None,
    create_trim: bool,
    runner: Callable[..., None],
    *runner_args: object,
) -> None:
    effective_mm = _effective_mm(task, mm)
    ctx = _make_context(task.source_path, config_path, effective_mm, no_cache, debug, require_mm, head_minutes=head_minutes, create_trim=create_trim, create_dirs=False, output_subdir=task.output_subdir)
    with _input_cache_lock(ctx):
        runner(ctx, *runner_args)


def _effective_mm(task: InputTask, mm: bool) -> bool:
    if not mm or not task.from_directory:
        return mm
    return task.source_path.suffix.lower() in VIDEO_EXTENSIONS


def _run_end_to_end(ctx: PipelineContext, debug: bool) -> None:
    _echo_run_header(ctx)
    if ctx.mode == "multimodal":
        _validate_multimodal_llm_profiles(ctx)
        _run_multimodal_upstream(ctx, debug)
        if not ctx.artifacts.audio.is_complete():
            raise click.ClickException("visual describe requires completed audio refine stage")
        _run_stage(ctx, describe.run)
    else:
        _run_audio_upstream(ctx, debug)
    assemble_output = _run_stage_sequence(ctx, [unify.run, outline.run, section.run, assemble.run])
    output_paths = getattr(assemble_output, "output_paths", [ctx.paths.output_note_md])
    progress_write("Output:")
    for path in output_paths:
        progress_write(str(path))


def _run_single_stage(ctx: PipelineContext, stage_name: str, stage_run: StageRun) -> None:
    if stage_name == "describe" and not ctx.artifacts.audio.is_complete():
        raise click.ClickException(str(CacheError("visual describe requires completed audio refine stage; run refine first")))
    output = _run_stage(ctx, stage_run)
    paths = getattr(output, "output_paths", [])
    for path in paths:
        progress_write(str(path))


def _make_context(
    input_path: Path,
    config_path: Path | None,
    mm: bool,
    no_cache: bool,
    debug: bool,
    require_mm: bool,
    head_minutes: float | None = None,
    create_trim: bool = True,
    create_dirs: bool = True,
    output_subdir: Path | None = None,
) -> PipelineContext:
    try:
        configure_logging(debug)
        source_path = input_path.expanduser().resolve()
        if not source_path.exists():
            raise click.ClickException(f"input file not found: {source_path}")
        if head_minutes is not None:
            source_path = trim_media_head(source_path, head_minutes) if create_trim else resolve_head_trim_path(source_path, head_minutes)
        config = load_config(config_path)
        probe = probe_media(source_path)
        if probe.audio is None:
            raise click.ClickException("input must contain an audio stream")
        if require_mm and not mm:
            raise click.BadParameter("this visual stage requires --mm")
        if require_mm and probe.video is None:
            raise click.BadParameter("this visual stage requires a video input")
        mode = "multimodal" if mm and probe.video is not None else "audio_only"
        input_hash = hash_file(source_path)
        paths = build_paths(source_path, config.project.cache_dir, config.project.output_dir, input_hash, output_subdir=output_subdir)
        if create_dirs:
            _ensure_runtime_dirs(paths)
        artifacts = ArtifactBundle(audio=AudioArtifacts(input_hash, paths), visual=VisualArtifacts(input_hash, paths) if mode == "multimodal" else None)
        return PipelineContext(source_path, input_hash, mode, config, paths, artifacts, debug, no_cache)
    except click.ClickException:
        raise
    except LVNotesError as exc:
        log.error("command failed: %s", exc)
        raise click.ClickException(str(exc)) from exc
    except Exception:
        log.exception("unexpected command failure")
        raise


def _ensure_runtime_dirs(paths: PipelinePaths) -> None:
    for directory in (
        paths.run_dir,
        paths.audio_dir,
        paths.visual_dir,
        paths.visual_raw_frames_dir,
        paths.visual_filter_frames_dir,
        paths.visual_filter_variants_dir,
        paths.visual_semantic_frames_dir,
        paths.debug_dir,
        paths.refined_dir,
        paths.sections_dir,
        paths.output_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


@contextmanager
def _input_cache_lock(ctx: PipelineContext):
    try:
        progress_write(f"cache lock: waiting {ctx.paths.run_dir / '.lvnotes.lock'}")
        with input_cache_lock(ctx.paths.run_dir):
            progress_write("cache lock: acquired")
            _ensure_runtime_dirs(ctx.paths)
            yield
    except LVNotesError as exc:
        log.error("command failed: %s", exc)
        raise click.ClickException(str(exc)) from exc


def _run_stage_sequence(ctx: PipelineContext, stages: list[StageRun]) -> object | None:
    output = None
    for stage_run in stages:
        output = _run_stage(ctx, stage_run)
    return output


def _run_audio_upstream(ctx: PipelineContext, debug: bool) -> None:
    _run_stage_sequence(ctx, [extract.run, transcribe.run, segment.run])
    _run_stage(replace(ctx, debug=debug), refine.run)


def _run_visual_upstream(ctx: PipelineContext) -> None:
    _run_stage_sequence(ctx, [sample.run, filter.run, semantic_filter.run])


def _run_multimodal_upstream(ctx: PipelineContext, debug: bool) -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        audio_future = executor.submit(_run_audio_upstream, ctx, debug)
        visual_future = executor.submit(_run_visual_upstream, ctx)
        audio_future.result()
        visual_future.result()
    _run_stage(ctx, align.run)


def _validate_multimodal_llm_profiles(ctx: PipelineContext) -> None:
    for_task(ctx.config, "slide_judge")
    for_task(ctx.config, "slide_describe")


def _run_stage(ctx: PipelineContext, stage_run: StageRun) -> object:
    label = _stage_label(stage_run)
    progress_write(f"{label}: running")
    log.info("stage started: %s input_hash=%s", label, ctx.input_hash)
    try:
        output = stage_run(ctx)
    except LVNotesError as exc:
        log.error("stage failed: %s", exc)
        raise click.ClickException(str(exc)) from exc
    except Exception:
        log.exception("unexpected stage failure")
        raise
    stage_name = getattr(output, "stage_name", label)
    cache_hit = getattr(output, "cache_hit", False)
    status = "cache hit" if cache_hit else "done"
    log.info("stage %s: %s stage_name=%s input_hash=%s", status, label, stage_name, ctx.input_hash)
    progress_write(f"{label}: {status}")
    return output


def _stage_label(stage_run: StageRun) -> str:
    module_name = stage_run.__module__
    for prefix, replacement in (
        ("lvnotes.audio_pipeline.", "audio."),
        ("lvnotes.visual_pipeline.", "visual."),
        ("lvnotes.merge.", "merge."),
    ):
        if module_name.startswith(prefix):
            return replacement + module_name.removeprefix(prefix).split(".", 1)[0]
    return module_name


def _echo_run_header(ctx: PipelineContext) -> None:
    progress_write(f"Input: {ctx.source_path}")
    progress_write(f"Mode: {ctx.mode}")
    progress_write(f"Cache: {'disabled' if ctx.no_cache else 'enabled'}")
    progress_write("")


def _inspect_one_input(
    task: InputTask,
    config_path: Path | None,
    mm: bool,
    head_minutes: float | None,
    namespace: str,
    stage: str,
    as_json: bool,
    paths_only: bool,
    aggregate_json: bool,
) -> dict[str, object] | None:
    effective_mm = _effective_mm(task, mm)
    ctx = _make_context(task.source_path, config_path, effective_mm, False, False, require_mm=False, head_minutes=head_minutes, create_trim=False, create_dirs=False, output_subdir=task.output_subdir)
    path = _inspect_path(ctx, namespace, stage)
    if paths_only:
        line = f"{task.source_path}\t{path}" if task.from_directory else str(path)
        click.echo(line)
        return None
    if not path.exists():
        raise click.ClickException(f"artifact not found: {path}")
    if as_json:
        content = path.read_text(encoding="utf-8")
        if not aggregate_json:
            click.echo(content)
            return None
        return {"source": str(task.source_path), "path": str(path), "content": content}
    if task.from_directory:
        click.echo(f"source: {task.source_path}")
    click.echo(f"stage: {namespace} {stage}")
    click.echo(f"input_hash: {ctx.input_hash}")
    click.echo(f"path: {path}")
    click.echo(f"size_bytes: {path.stat().st_size}")
    if task.from_directory:
        click.echo("")
    return None


def _json_failures(failures: list[BatchFailure]) -> list[dict[str, str]]:
    return [{"source": str(failure.source_path), "message": failure.message} for failure in failures]


def _inspect_path(ctx: PipelineContext, namespace: str, stage: str) -> Path:
    paths = {
        ("audio", "extract"): ctx.paths.audio_extract_json,
        ("audio", "transcript"): ctx.paths.transcript_raw_json,
        ("audio", "segments"): ctx.paths.segments_json,
        ("audio", "refined"): ctx.paths.refined_transcript_json,
        ("visual", "sample"): ctx.paths.visual_sample_json,
        ("visual", "filter"): ctx.paths.visual_filtered_sample_json,
        ("visual", "semantic-filter"): ctx.paths.visual_semantic_sample_json,
        ("visual", "semantic-judgements"): ctx.paths.visual_semantic_judgements_json,
        ("visual", "align"): ctx.paths.visual_alignments_json,
        ("visual", "filter-variants"): ctx.paths.visual_filter_variants_dir / "summary.json",
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
