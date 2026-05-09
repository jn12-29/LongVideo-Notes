from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

_UNSAFE_FILENAME_RE = re.compile(r"[\s\[\]()`*_{}<>#!|\\/?:;,.]+")
_DASH_RE = re.compile(r"-+")


@dataclass(frozen=True)
class PipelinePaths:
    source_path: Path
    cache_dir: Path
    output_dir: Path
    run_dir: Path
    audio_dir: Path
    visual_dir: Path
    visual_frames_dir: Path
    debug_dir: Path
    refined_dir: Path
    sections_dir: Path
    audio_wav: Path
    audio_extract_json: Path
    visual_sample_json: Path
    visual_segments_json: Path
    visual_judgements_json: Path
    visual_selections_json: Path
    visual_descriptions_json: Path
    transcript_raw_json: Path
    segments_json: Path
    refined_transcript_json: Path
    content_blocks_json: Path
    outline_json: Path
    cache_note_md: Path
    output_note_md: Path


def build_paths(source_path: Path, cache_dir: Path, output_dir: Path, input_hash: str) -> PipelinePaths:
    run_dir = cache_dir / input_hash
    audio_dir = run_dir / "audio"
    visual_dir = run_dir / "visual"
    output_stem = make_output_stem(source_path)
    return PipelinePaths(
        source_path=source_path,
        cache_dir=cache_dir,
        output_dir=output_dir,
        run_dir=run_dir,
        audio_dir=audio_dir,
        visual_dir=visual_dir,
        visual_frames_dir=visual_dir / "frames",
        debug_dir=run_dir / "debug",
        refined_dir=run_dir / "refined",
        sections_dir=run_dir / "sections",
        audio_wav=audio_dir / "audio.wav",
        audio_extract_json=audio_dir / "extract.json",
        visual_sample_json=visual_dir / "sample.json",
        visual_segments_json=visual_dir / "segments.json",
        visual_judgements_json=visual_dir / "judgements.json",
        visual_selections_json=visual_dir / "selections.json",
        visual_descriptions_json=visual_dir / "descriptions.json",
        transcript_raw_json=run_dir / "transcript_raw.json",
        segments_json=run_dir / "segments.json",
        refined_transcript_json=run_dir / "refined_transcript.json",
        content_blocks_json=run_dir / "content_blocks.json",
        outline_json=run_dir / "outline.json",
        cache_note_md=run_dir / "note.md",
        output_note_md=output_dir / f"{output_stem}.md",
    )


def make_output_stem(source_path: Path) -> str:
    normalized = unicodedata.normalize("NFKC", source_path.stem).strip()
    safe = _UNSAFE_FILENAME_RE.sub("-", normalized)
    safe = _DASH_RE.sub("-", safe).strip("-")
    if safe:
        return safe
    return "note"


def make_timestamped_output_path(output_note_md: Path, timestamp: str) -> Path:
    return output_note_md.with_name(f"{output_note_md.stem}-{timestamp}{output_note_md.suffix}")


def resolve_visual_image_path(paths: PipelinePaths, image_source_path: Path) -> Path:
    if image_source_path.is_absolute():
        raise ValueError("image_source_path must be relative to visual_frames_dir")
    return (paths.visual_frames_dir / image_source_path).resolve()


def make_markdown_image_path(paths: PipelinePaths, image_source_path: Path) -> Path:
    absolute = resolve_visual_image_path(paths, image_source_path)
    return Path(os_path_relpath(absolute, paths.output_note_md.parent))


def os_path_relpath(path: Path, start: Path) -> str:
    import os

    return os.path.relpath(path, start)
