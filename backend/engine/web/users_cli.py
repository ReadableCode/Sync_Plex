"""`syncplex users ...` — manage web UI accounts (see README "Users & roles").

Runs against syncplex.users in the shared `apps` database, over the superuser
connection (conventions I2, exception 3). Run it through the container, which
is where the POSTGRES_* environment lives:

    docker compose -f docker_compose_projects.yaml exec syncplex-web \
        syncplex users add <name> --role admin

A host shell would either fail on missing env or reach a different database, so
the compose form is the only supported one.

Passwords are always prompted (never CLI args), so they stay out of shell
history; only the argon2id hash is stored. The running web app picks changes up
within its 30-second account cache, no restart needed.
"""

import typer

from .. import bootstrap, config
from .users import ROLE_ADMIN, ROLE_USER, ROLES, UserStore


def _store() -> UserStore:
    """Every command starts here.

    Bootstrap runs from the CLI as well as the web app because creating the
    first admin is how a fresh deployment starts, and that can happen before
    the web process has ever booted.
    """
    bootstrap.bootstrap_best_effort()
    return UserStore()


users_app = typer.Typer(name="users", help="Manage web UI accounts (admins add; users request)")


def _prompt_password() -> str:
    return typer.prompt("Password (min 10 chars)", hide_input=True, confirmation_prompt=True)


@users_app.command()
def add(
    username: str = typer.Argument(..., help="Login name (lowercase letters/digits/._-)"),
    role: str = typer.Option(
        ROLE_USER, "--role", help=f"{ROLE_ADMIN}: adds directly + approves requests; {ROLE_USER}: can only request"
    ),
    display_name: str = typer.Option("", "--display-name", help="Shown instead of the username (optional)"),
):
    """Create an account. The first account you create should be your admin."""
    if role not in ROLES:
        typer.secho(f"  ✗ role must be one of: {', '.join(ROLES)}", fg=typer.colors.RED)
        raise typer.Exit(1)
    store = _store()
    try:
        user = store.add(username, _prompt_password(), role=role, display_name=display_name)
    except ValueError as exc:
        typer.secho(f"  ✗ {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.secho(f"  ✓ created {user.role} '{user.username}' in {config.APP_SCHEMA}.users", fg=typer.colors.GREEN)


@users_app.command("list")
def list_users():
    """List accounts, roles, and status."""
    store = _store()
    accounts = store.list()
    if not accounts:
        typer.echo(f"  (no accounts in {config.APP_SCHEMA}.users — `syncplex users add <name> --role admin`)")
        return
    for user in accounts:
        status = "disabled" if user.disabled else "active"
        typer.echo(f"  {user.username:<20} {user.role:<6} {status:<9} created {user.created_at:%Y-%m-%d}")


@users_app.command()
def passwd(username: str = typer.Argument(..., help="Account to reset")):
    """Change a password (logs out that user's existing sessions)."""
    store = _store()
    try:
        store.set_password(username, _prompt_password())
    except (KeyError, ValueError) as exc:
        typer.secho(f"  ✗ {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.secho(f"  ✓ password updated for '{username}' (existing sessions invalidated)", fg=typer.colors.GREEN)


@users_app.command()
def role(
    username: str = typer.Argument(..., help="Account to change"),
    new_role: str = typer.Argument(..., help=f"One of: {', '.join(ROLES)}"),
):
    """Promote/demote an account (e.g. make a second admin)."""
    store = _store()
    try:
        store.set_role(username, new_role)
    except (KeyError, ValueError) as exc:
        typer.secho(f"  ✗ {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.secho(f"  ✓ '{username}' is now a {new_role}", fg=typer.colors.GREEN)


@users_app.command()
def disable(username: str = typer.Argument(..., help="Account to lock out")):
    """Disable an account (immediately logs out its sessions)."""
    _set_disabled(username, True)


@users_app.command()
def enable(username: str = typer.Argument(..., help="Account to reinstate")):
    """Re-enable a disabled account."""
    _set_disabled(username, False)


def _set_disabled(username: str, disabled: bool) -> None:
    store = _store()
    try:
        store.set_disabled(username, disabled)
    except KeyError as exc:
        typer.secho(f"  ✗ {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.secho(f"  ✓ '{username}' {'disabled' if disabled else 'enabled'}", fg=typer.colors.GREEN)


@users_app.command()
def remove(
    username: str = typer.Argument(..., help="Account to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete an account entirely (their past requests keep the name)."""
    if not yes:
        typer.confirm(f"Delete user '{username}'?", abort=True)
    store = _store()
    try:
        store.remove(username)
    except KeyError as exc:
        typer.secho(f"  ✗ {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.secho(f"  ✓ removed '{username}'", fg=typer.colors.GREEN)
