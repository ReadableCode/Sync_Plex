from typer.testing import CliRunner

from engine.cli import app

runner = CliRunner()


def test_help_lists_flat_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("search", "add", "seasons", "instances", "tui", "drive", "web", "users"):
        assert command in result.output


def test_drive_check_needs_a_folder():
    """`syncplex drive --check` with no folder is exit 2, before any Plex call."""
    result = runner.invoke(app, ["drive", "--check"])
    assert result.exit_code == 2


def test_there_is_no_second_entry_point():
    import tomllib
    from pathlib import Path

    scripts = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["scripts"]
    assert list(scripts) == ["syncplex"]


def test_media_group_is_gone():
    """Commands are flat — no `syncplex media ...` nesting."""
    result = runner.invoke(app, ["media", "--help"])
    assert result.exit_code != 0
