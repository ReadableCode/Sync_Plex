"""Presentation helpers both UIs share: how a status, a season, a server's
stats, a title's facts, an add's cost and the approval picker's options are
worded. Pure functions on the models, no framework; the web app and the TUI
each wrap the words in their own styling."""

from __future__ import annotations

from .health import format_bytes
from .models import InstanceStatus, MediaSearchResult, PresenceState, ServerHealth

STATE_BADGE = {
    PresenceState.MONITORED_COMPLETE: ("● complete", "state-complete"),
    PresenceState.MONITORED_INCOMPLETE: ("◐ partial", "state-partial"),
    PresenceState.NOT_PRESENT: ("○ not present", "state-absent"),
    PresenceState.UNREACHABLE: ("✗ unreachable", "state-error"),
}


def badge(status: InstanceStatus) -> tuple[str, str]:
    label, color = STATE_BADGE[status.state]
    # A movie is either downloaded or not — "partial" only makes sense for TV
    if status.state == PresenceState.MONITORED_INCOMPLETE and status.missing_episode_count is None:
        label = "◐ not downloaded"
    return label, color


def short(instance_name: str) -> str:
    """Compact instance label for badges: 'sonarr-behemoth' -> 'behemoth'."""
    return instance_name.split("-", 1)[-1]


def season_chip(season) -> str:
    """'✓S2 8/10 · 19.1 GB' — monitoring, have/total, and the season folder size."""
    mark = "✓" if season.monitored else "✗"
    label = "SP" if season.season_number == 0 else f"S{season.season_number}"
    chip = f"{mark}{label} {season.episode_file_count}/{season.total_episode_count or season.episode_count}"
    if season.size_on_disk:
        chip += f" · {format_bytes(season.size_on_disk)}"
    return chip


def stats_line(health: ServerHealth) -> str:
    """'812 shows · 24,331 episodes · 18.9 TB' — only the parts this server has."""
    parts = []
    if health.series_count is not None:
        parts.append(f"{health.series_count:,} shows")
    if health.episode_count is not None:
        parts.append(f"{health.episode_count:,} episodes")
    if health.movie_count is not None:
        parts.append(f"{health.movie_count:,} movies")
    if health.library_size_bytes:
        parts.append(format_bytes(health.library_size_bytes))
    return " · ".join(parts)


def meta_line(result: MediaSearchResult) -> str:
    """'HBO · continuing · 3 seasons · Drama, Thriller' — the lookup facts worth a glance."""
    return " · ".join(
        x
        for x in (
            result.network,
            result.status,
            f"{result.season_count} seasons" if result.season_count else "",
            ", ".join(result.genres[:3]),
        )
        if x
    )


def headroom_line(estimate: int, free: int | None, instance_name: str) -> tuple[str, str] | None:
    """What is left on the instance after an add of ``estimate`` bytes: (text, css class).

    A warning when the add would not fit, plain free space when it would,
    nothing when the server's disk reading is unknown."""
    if free is None:
        return None
    if estimate > free:
        return (
            f"⚠ needs ~{format_bytes(estimate)} but only {format_bytes(free)} free on {short(instance_name)}",
            "text-xs state-partial",
        )
    return f"{format_bytes(free)} free on {short(instance_name)}", "text-xs muted"


def server_option(name: str, status, estimate: int, free: int | None) -> str:
    """Label for the approval picker: the cost of putting the title here, or why it is moot."""
    if status is not None and status.state != PresenceState.NOT_PRESENT:
        return f"{name} · {badge(status)[0]}"
    label = f"{name} · ~{format_bytes(estimate)}"
    if free is not None:
        label += f" · {format_bytes(free)} free"
    return label
