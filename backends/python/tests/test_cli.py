from typer.testing import CliRunner

from engine.cli import app

runner = CliRunner()


def test_help_lists_media_tui_and_web():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "media" in result.output
    assert "tui" in result.output
    assert "web" in result.output


def test_media_help_lists_subcommands():
    result = runner.invoke(app, ["media", "--help"])
    assert result.exit_code == 0
    for command in ("search", "add", "seasons", "instances", "tui"):
        assert command in result.output
