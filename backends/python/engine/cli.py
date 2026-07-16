import typer

from .media.cli import media_app
from .web.users_cli import users_app

app = typer.Typer(name="syncplex", help="Sync_Plex — household media remote")

# Media commands are registered flat (syncplex search / seasons / add /
# instances) — no `syncplex media ...` nesting.
app.registered_commands.extend(media_app.registered_commands)

app.add_typer(users_app)


@app.command()
def tui():
    """Launch the TUI (media remote + drive sync screen)."""
    from .media.tui.app import run_tui

    run_tui()


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind (use your Tailscale IP to share)"),
    port: int = typer.Option(8788, "--port", help="Port to listen on"),
):
    """Launch the media remote web UI (NiceGUI)."""
    from .web.app import run_web

    run_web(host=host, port=port)


if __name__ == "__main__":
    app()
