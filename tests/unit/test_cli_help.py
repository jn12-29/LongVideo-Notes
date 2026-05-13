from click.testing import CliRunner
import pytest

from lvnotes.cli import app


def test_top_level_help_guides_common_workflow() -> None:
    result = CliRunner().invoke(app.main, ["--help"])

    assert result.exit_code == 0
    assert "Recommended workflow:" in result.output
    assert "lvnotes run <input-path>" in result.output
    assert "lvnotes run <input-path> --mm" in result.output
    assert "lvnotes run ./courses --mm" in result.output
    assert "lvnotes run <input-path> --head-minutes 10" in result.output
    assert "lvnotes output tidy --apply" in result.output
    assert "audio-only mode" in result.output
    assert "multimodal mode" in result.output
    assert "with --mm, videos run in multimodal mode and audio files remain audio-only" in result.output
    assert "lvnotes inspect merge note <input-path> --paths" in result.output
    assert "lvnotes inspect merge note <input-path> --head-minutes 10 --paths" in result.output


def test_top_level_help_lists_common_options() -> None:
    result = CliRunner().invoke(app.main, ["--help"])

    assert result.exit_code == 0
    assert "Useful options:" in result.output
    assert "--head-minutes N" in result.output
    assert "--config PATH" in result.output
    assert "--no-cache" in result.output
    assert "--debug" in result.output
    assert "--paths" in result.output
    assert "--json" in result.output
    assert "Print raw artifact content in inspect." in result.output
    assert "Main outputs:" in result.output
    assert "timestamped archive" in result.output


def test_top_level_help_lists_commands_with_short_help_in_workflow_order() -> None:
    result = CliRunner().invoke(app.main, ["--help"])

    assert result.exit_code == 0
    commands = result.output.split("Commands:", 1)[1]
    assert "run              Generate a Markdown note end to end." in result.output
    assert "inspect          Inspect existing artifacts without running stages." in result.output
    assert "output           Maintain generated output files." in result.output
    assert "extract          Run audio extract stage." in result.output
    assert "describe         Run visual describe stage; requires --mm." in result.output
    assert "assemble         Run merge assemble stage." in result.output
    assert commands.index("  run") < commands.index("  inspect")
    assert commands.index("  inspect") < commands.index("  output")
    assert commands.index("  output") < commands.index("  extract")
    assert commands.index("  extract") < commands.index("  sample")
    assert commands.index("  sample") < commands.index("  filter")
    assert commands.index("  filter") < commands.index("  semantic-filter")
    assert commands.index("  semantic-filter") < commands.index("  align")
    assert commands.index("  align") < commands.index("  describe")
    assert commands.index("  describe") < commands.index("  unify")


def test_run_help_mentions_common_run_options() -> None:
    result = CliRunner().invoke(app.main, ["run", "--help"])

    assert result.exit_code == 0
    assert "Use --mm for multimodal video runs." in result.output
    assert "with --mm, videos run in multimodal mode" in result.output
    assert "audio files remain" in result.output
    assert "Use --head-minutes N for a quick trial" in result.output
    assert "--head-minutes FLOAT RANGE" in result.output
    assert "--no-cache to recompute" in result.output


def test_run_help_lists_end_to_end_outputs() -> None:
    result = CliRunner().invoke(app.main, ["run", "--help"])

    assert result.exit_code == 0
    assert "Audio-only outputs:" in result.output
    assert "output_dir/<relative-dir>/<source-stem>.md" in result.output
    assert "YYYYMMDD-HHMMSS.md" in result.output
    assert "cache/{input_hash}/note.md" in result.output
    assert "Multimodal extras with --mm:" in result.output
    assert "cache/{input_hash}/visual/raw_frames/" in result.output
    assert "cache/{input_hash}/visual/filter_frames/" in result.output
    assert "visual/filtered_sample.json" in result.output
    assert "visual/filter_variants/" in result.output
    assert "visual/descriptions.json" in result.output


def test_inspect_help_mentions_path_json_and_head_minutes() -> None:
    result = CliRunner().invoke(app.main, ["inspect", "--help"])

    assert result.exit_code == 0
    assert "Inspect existing artifacts without running stages." in result.output
    assert "Use --paths to print only the artifact path" in result.output
    assert "raw artifact" in result.output
    assert "content" in result.output
    assert "--head-minutes FLOAT RANGE" in result.output
    assert "--json" in result.output
    assert "--paths" in result.output


def test_inspect_help_lists_readable_artifacts_without_generation() -> None:
    result = CliRunner().invoke(app.main, ["inspect", "--help"])

    assert result.exit_code == 0
    assert "Inspect does not generate files" in result.output
    assert "audio: extract, transcript, segments, refined" in result.output
    assert "visual: sample, filter, filter-variants, semantic-filter, semantic-judgements, align, describe" in result.output
    assert "merge: blocks, unify, outline, note, assemble" in result.output


def test_output_tidy_help_mentions_dry_run_apply_and_assets() -> None:
    result = CliRunner().invoke(app.main, ["output", "tidy", "--help"])

    assert result.exit_code == 0
    assert "Move timestamped note archives under output/_archive/." in result.output
    assert "dry run" in result.output
    assert "--apply" in result.output
    assert "--config" in result.output
    assert "assets" in result.output


def test_stage_help_mentions_head_minutes_and_no_cache() -> None:
    result = CliRunner().invoke(app.main, ["describe", "--help"])

    assert result.exit_code == 0
    assert "Supports --head-minutes N" in result.output
    assert "--no-cache" in result.output
    assert "Video input must be run with --mm." in result.output


@pytest.mark.parametrize(
    ("command", "expected_outputs"),
    [
        ("extract", ("cache/{input_hash}/audio/audio.wav", "cache/{input_hash}/audio/extract.json")),
        ("transcribe", ("cache/{input_hash}/transcript_raw.json",)),
        ("segment", ("cache/{input_hash}/segments.json",)),
        ("refine", ("cache/{input_hash}/refined_transcript.json", "cache/{input_hash}/refined/{seg_id:04d}.json")),
        ("sample", ("cache/{input_hash}/visual/raw_frames/", "cache/{input_hash}/visual/sample.json")),
        ("filter", ("cache/{input_hash}/visual/filter_frames/", "cache/{input_hash}/visual/filtered_sample.json", "cache/{input_hash}/visual/filter_variants/")),
        ("semantic-filter", ("cache/{input_hash}/visual/semantic_frames/", "cache/{input_hash}/visual/semantic_sample.json", "cache/{input_hash}/visual/semantic_judgements.json")),
        ("align", ("cache/{input_hash}/visual/alignments.json",)),
        ("describe", ("cache/{input_hash}/visual/descriptions.json",)),
        ("unify", ("cache/{input_hash}/content_blocks.json",)),
        ("outline", ("cache/{input_hash}/outline.json",)),
        ("section", ("cache/{input_hash}/sections/{chapter_id:03d}.md",)),
        (
            "assemble",
            (
                "output_dir/<relative-dir>/<source-stem>.md",
                "output_dir/<relative-dir>/<source-stem>-YYYYMMDD-HHMMSS.md",
                "YYYYMMDD-HHMMSS.md",
                "cache/{input_hash}/note.md",
            ),
        ),
    ],
)
def test_stage_help_lists_produced_files(command: str, expected_outputs: tuple[str, ...]) -> None:
    result = CliRunner().invoke(app.main, [command, "--help"])

    assert result.exit_code == 0
    assert "Produces:" in result.output
    for expected in expected_outputs:
        assert expected in result.output
