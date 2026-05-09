from pathlib import Path

from lvnotes.core.config import MergeSectionConfig
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import Chapter, ContentBlock, Outline
from lvnotes.merge import section


class Paths:
    sections_dir: Path
    content_blocks_json: Path
    outline_json: Path


class Ctx:
    paths: Paths
    config: object
    no_cache = False


class MergeConfig:
    section = MergeSectionConfig(concurrent_calls=1)


class LLMConfig:
    profiles = {"main": {}}


class Config:
    merge = MergeConfig()
    llm = LLMConfig()
    tasks = {"section": "main"}


def test_section_reports_cache_hit_when_all_chapters_cached(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    paths = Paths()
    paths.sections_dir = tmp_path
    paths.content_blocks_json = tmp_path / "blocks.json"
    paths.outline_json = tmp_path / "outline.json"
    ctx = Ctx()
    ctx.paths = paths
    ctx.config = Config()
    path = tmp_path / "001.md"
    path.write_text("cached", encoding="utf-8")
    outline = Outline([Chapter(1, "Title", "Summary", 0, 0)])
    blocks = [ContentBlock(0, 0.0, 1.0, "topic", "text", "summary", [], [])]

    monkeypatch.setattr(section, "read_outline", lambda path: outline)
    monkeypatch.setattr(section, "read_blocks", lambda path: blocks)
    monkeypatch.setattr(section, "prompt_path", lambda name: tmp_path / name)
    monkeypatch.setattr(section, "hash_prompt_template", lambda path: "prompt")
    monkeypatch.setattr(
        section,
        "cached_output",
        lambda *args, **kwargs: StageOutput("section_chapter_001", [path], True, "hash", {}),
    )

    output = section.run(ctx)  # type: ignore[arg-type]

    assert output.cache_hit is True
    assert output.metadata["cached_count"] == 1
    assert output.metadata["job_count"] == 0


def test_section_reports_done_when_partially_cached(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    paths = Paths()
    paths.sections_dir = tmp_path
    paths.content_blocks_json = tmp_path / "blocks.json"
    paths.outline_json = tmp_path / "outline.json"
    ctx = Ctx()
    ctx.paths = paths
    ctx.config = Config()
    cached_path = tmp_path / "001.md"
    generated_path = tmp_path / "002.md"
    cached_path.write_text("cached", encoding="utf-8")
    outline = Outline([Chapter(1, "One", "Summary", 0, 0), Chapter(2, "Two", "Summary", 1, 1)])
    blocks = [
        ContentBlock(0, 0.0, 1.0, "topic", "text", "summary", [], []),
        ContentBlock(1, 1.0, 2.0, "topic", "text", "summary", [], []),
    ]

    monkeypatch.setattr(section, "read_outline", lambda path: outline)
    monkeypatch.setattr(section, "read_blocks", lambda path: blocks)
    monkeypatch.setattr(section, "prompt_path", lambda name: tmp_path / name)
    monkeypatch.setattr(section, "hash_prompt_template", lambda path: "prompt")

    def cached_output(stage_name: str, output_paths: list[Path], cache_key: str):
        if stage_name == "section_chapter_001":
            return StageOutput(stage_name, output_paths, True, "hash", {})
        return None

    monkeypatch.setattr(section, "cached_output", cached_output)
    def write_section(*args: object, **kwargs: object) -> Path:
        generated_path.write_text("generated", encoding="utf-8")
        return generated_path

    monkeypatch.setattr(section, "_write_section", write_section)

    output = section.run(ctx)  # type: ignore[arg-type]

    assert output.cache_hit is False
    assert output.metadata["cached_count"] == 1
    assert output.metadata["job_count"] == 1
