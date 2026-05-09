from click.testing import CliRunner

from lvnotes.cli import app


def test_top_level_help_guides_common_workflow() -> None:
    result = CliRunner().invoke(app.main, ["--help"])

    assert result.exit_code == 0
    assert "Recommended workflow:" in result.output
    assert "lvnotes run <input-file>" in result.output
    assert "lvnotes run <input-file> --mm" in result.output
    assert "lvnotes run <input-file> --head-minutes 10" in result.output
    assert "audio-only mode" in result.output
    assert "multimodal mode" in result.output
    assert "lvnotes inspect merge note <input-file> --paths" in result.output
    assert "lvnotes inspect merge note <input-file> --head-minutes 10 --paths" in result.output


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


def test_top_level_help_lists_commands_with_short_help_in_workflow_order() -> None:
    result = CliRunner().invoke(app.main, ["--help"])

    assert result.exit_code == 0
    assert "run         Generate a Markdown note end to end." in result.output
    assert "inspect     Inspect existing artifacts without running stages." in result.output
    assert "extract     Run audio extract stage." in result.output
    assert "describe    Run visual describe stage; requires --mm." in result.output
    assert "assemble    Run merge assemble stage." in result.output
    assert result.output.index("  run") < result.output.index("  inspect")
    assert result.output.index("  inspect") < result.output.index("  extract")
    assert result.output.index("  extract") < result.output.index("  sample")
    assert result.output.index("  sample") < result.output.index("  unify")


def test_run_help_mentions_common_run_options() -> None:
    result = CliRunner().invoke(app.main, ["run", "--help"])

    assert result.exit_code == 0
    assert "Use --mm for multimodal video runs." in result.output
    assert "Use --head-minutes N for a quick trial" in result.output
    assert "--head-minutes FLOAT RANGE" in result.output
    assert "Use --no-cache to recompute all stages run by run." in result.output


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


def test_stage_help_mentions_head_minutes_and_no_cache() -> None:
    result = CliRunner().invoke(app.main, ["describe", "--help"])

    assert result.exit_code == 0
    assert "Supports --head-minutes N" in result.output
    assert "--no-cache" in result.output
    assert "Video input must be run with --mm." in result.output
