from click.testing import CliRunner

from lvnotes.cli import app


def test_top_level_help_guides_common_workflow() -> None:
    result = CliRunner().invoke(app.main, ["--help"])

    assert result.exit_code == 0
    assert "Recommended workflow:" in result.output
    assert "lvnotes run <input-file>" in result.output
    assert "lvnotes run <input-file> --mm" in result.output
    assert "audio-only mode" in result.output
    assert "multimodal mode" in result.output


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
