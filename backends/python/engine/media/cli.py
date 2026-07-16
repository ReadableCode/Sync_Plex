"""`syncplex media ...` — CLI over the media aggregation core."""

import asyncio
import json

import typer

from .aggregation import (
    add_to_instance,
    check_plex_availability,
    enrich_tv_statuses,
    episodes_everywhere,
    search_everywhere,
)
from .config import load_media_config
from .health import format_bytes
from .models import AggregatedResult, MediaType, PresenceState

media_app = typer.Typer(name="media", help="Search/add media across all Sonarr/Radarr/Plex instances")

STATE_GLYPHS = {
    PresenceState.MONITORED_COMPLETE: "●",
    PresenceState.MONITORED_INCOMPLETE: "◐",
    PresenceState.NOT_PRESENT: "○",
    PresenceState.UNREACHABLE: "✗",
}


def _echo_warnings(config) -> None:
    for warning in config.warnings:
        typer.secho(f"  ! {warning}", fg=typer.colors.YELLOW, err=True)


def _render_result(aggregated: AggregatedResult) -> None:
    r = aggregated.result
    year = f" ({r.year})" if r.year else ""
    ids = r.external_key if not r.external_key.startswith("title:") else "no external id"
    typer.secho(f"\n  {r.title}{year}", bold=True, nl=False)
    typer.echo(f"  [{ids}]")
    for s in aggregated.statuses:
        glyph = STATE_GLYPHS[s.state]
        detail = ""
        if s.state == PresenceState.MONITORED_INCOMPLETE:
            if s.missing_episode_count is None:
                detail = "  in library, not downloaded"  # movie without a file
            elif s.total_episode_count == 0:
                detail = "  in library, no monitored episodes"
            else:
                detail = f"  missing {s.missing_episode_count}/{s.total_episode_count} episodes"
        elif s.state == PresenceState.UNREACHABLE:
            detail = f"  unreachable: {s.error[:60]}"
        if s.size_on_disk:
            detail += f"  · {format_bytes(s.size_on_disk)}"
        typer.echo(f"    {glyph} {s.instance:<20} {s.state.value}{detail}")
    for p in aggregated.plex:
        glyph = "▶" if p.available else "·"
        note = "watch-ready" if p.available else (f"error: {p.error[:60]}" if p.error else "not in library")
        typer.echo(f"    {glyph} {p.server:<20} {note}")


def _dump_json(results: list[AggregatedResult]) -> None:
    typer.echo(json.dumps([r.model_dump(mode="json") for r in results], indent=2))


@media_app.command()
def instances(output_json: bool = typer.Option(False, "--json", help="Output as JSON")):
    """List configured media instances (from hosts.json services + .env)."""
    config = load_media_config()
    if output_json:
        data = {
            "sonarr": [{"name": i.name, "base_url": i.base_url} for i in config.sonarr],
            "radarr": [{"name": i.name, "base_url": i.base_url} for i in config.radarr],
            "plex": [{"name": s.name, "base_url": s.base_url} for s in config.plex],
            "warnings": config.warnings,
        }
        typer.echo(json.dumps(data, indent=2))
        return
    for kind, items in (("sonarr", config.sonarr), ("radarr", config.radarr), ("plex", config.plex)):
        typer.secho(f"  {kind}:", bold=True)
        for i in items:
            typer.echo(f"    {i.name:<20} {i.base_url}")
        if not items:
            typer.echo("    (none configured)")
    _echo_warnings(config)


@media_app.command()
def search(
    query: str = typer.Argument(..., help="Title to search for"),
    media_type: MediaType = typer.Option(MediaType.TV, "--type", "-t", help="tv or movie"),
    plex: bool = typer.Option(False, "--plex", "-p", help="Also check Plex watch-readiness"),
    limit: int = typer.Option(5, "--limit", "-n", help="Max results to show"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Search every configured instance and show status per instance."""
    config = load_media_config()
    if not config.arr_instances(media_type.value):
        typer.echo(f"No {'sonarr' if media_type == MediaType.TV else 'radarr'} instances configured.")
        _echo_warnings(config)
        raise typer.Exit(1)

    async def _run() -> list[AggregatedResult]:
        results = (await search_everywhere(query, media_type, config))[:limit]
        if plex and results:
            await asyncio.gather(*(check_plex_availability(r, config) for r in results))
        return results

    results = asyncio.run(_run())

    if output_json:
        _dump_json(results)
        return

    if not results:
        typer.echo("No results.")
    for aggregated in results:
        _render_result(aggregated)
    _echo_warnings(config)


def _season_label(number: int) -> str:
    return "Specials" if number == 0 else f"S{number:02d}"


@media_app.command()
def seasons(
    query: str = typer.Argument(..., help="Show title (or tvdb:12345)"),
    index: int = typer.Option(0, "--index", "-i", help="Which search result to inspect (0 = first)"),
    episodes: bool = typer.Option(False, "--episodes", "-e", help="Also list every episode"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Per-season (and optionally per-episode) monitoring/availability on every instance."""
    config = load_media_config()

    async def _run():
        results = await search_everywhere(query, MediaType.TV, config)
        if not results:
            return None, {}
        target = results[min(index, len(results) - 1)]
        await enrich_tv_statuses(target, config)
        eps = await episodes_everywhere(target, config) if episodes else {}
        return target, eps

    target, eps_by_instance = asyncio.run(_run())
    if target is None:
        typer.echo("No results.")
        raise typer.Exit(1)

    if output_json:
        data = target.model_dump(mode="json")
        data["episodes"] = {
            name: [e.model_dump(mode="json") for e in eps] for name, eps in eps_by_instance.items()
        }
        typer.echo(json.dumps(data, indent=2))
        return

    r = target.result
    year = f" ({r.year})" if r.year else ""
    meta = " · ".join(x for x in (r.network, r.status, f"{r.season_count} seasons" if r.season_count else "") if x)
    typer.secho(f"\n  {r.title}{year}", bold=True, nl=False)
    typer.echo(f"  [{r.external_key}]" + (f"  {meta}" if meta else ""))

    for status in target.statuses:
        if not status.series_id:
            typer.echo(f"\n  {STATE_GLYPHS[status.state]} {status.instance}: {status.state.value}")
            continue
        summary = ""
        if status.total_episode_count is not None:
            have = status.total_episode_count - (status.missing_episode_count or 0)
            summary = f"  ({have}/{status.total_episode_count} monitored episodes on disk)"
        typer.secho(f"\n  {STATE_GLYPHS[status.state]} {status.instance}{summary}", bold=True)

        eps = eps_by_instance.get(status.instance, [])
        for season in sorted(status.seasons, key=lambda s: (s.season_number == 0, s.season_number)):
            mon = "monitored  " if season.monitored else "unmonitored"
            denominator = season.total_episode_count or season.episode_count
            counts = f"{season.episode_file_count}/{denominator}" if denominator else "—"
            size = f"  {format_bytes(season.size_on_disk)}" if season.size_on_disk else ""
            typer.echo(f"      {_season_label(season.season_number):<9} {mon} {counts:>7}{size}")
            if episodes:
                for ep in (e for e in eps if e.season_number == season.season_number):
                    glyph = "✓" if ep.has_file else ("○" if ep.monitored else "·")
                    aired = f"  ({ep.air_date})" if ep.air_date else ""
                    typer.echo(f"          {glyph} E{ep.episode_number:02d}  {ep.title}{aired}")

    _echo_warnings(config)


@media_app.command()
def add(
    query: str = typer.Argument(..., help="Title (or tvdb:12345 / tmdb:12345) to add"),
    to: str = typer.Option(..., "--to", help="Instance name to add to (see `media instances`)"),
    media_type: MediaType = typer.Option(MediaType.TV, "--type", "-t", help="tv or movie"),
    profile: str = typer.Option("", "--profile", help="Quality profile name (default: instance default)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Add the top search result to a specific instance."""
    config = load_media_config()
    results = asyncio.run(search_everywhere(query, media_type, config))
    if not results:
        typer.echo("No results.")
        raise typer.Exit(1)

    target = results[0]
    if not yes and not output_json:
        _render_result(target)
        typer.confirm(f"\nAdd '{target.result.title}' to {to}?", abort=True)

    add_result = asyncio.run(add_to_instance(target, to, config, quality_profile=profile))
    if output_json:
        typer.echo(add_result.model_dump_json(indent=2))
    else:
        icon = "✓" if add_result.ok else "✗"
        typer.echo(f"  {icon} {add_result.message}")
    if not add_result.ok:
        raise typer.Exit(1)
