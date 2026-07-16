from typer.testing import CliRunner

from engine.cli import app

runner = CliRunner()


def test_help_lists_flat_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("search", "add", "seasons", "instances", "tui", "web", "users"):
        assert command in result.output


def test_media_group_is_gone():
    """Commands are flat — no `syncplex media ...` nesting."""
    result = runner.invoke(app, ["media", "--help"])
    assert result.exit_code != 0
