"""Media remote web UI — a thin NiceGUI layer over engine.media.

Single process, server-rendered, calls core functions in-process (no internal
REST API). Meant to run on an always-on box, bound to the Tailscale interface.
All business logic lives in engine/media; this file only renders. Styling
follows the readablecode "terminal navy" design system (dotfiles
design/STYLE.md) — tokens live in _TOKENS_CSS, Quasar brand colors are mapped
onto them in index().
"""

from ..media.aggregation import (
    add_to_instance,
    check_plex_availability,
    enrich_tv_statuses,
    refresh_status,
    search_everywhere,
)
from ..media.config import MediaConfig, load_media_config
from ..media.models import AggregatedResult, MediaType, PresenceState

STATE_BADGE = {
    PresenceState.MONITORED_COMPLETE: ("● complete", "state-complete"),
    PresenceState.MONITORED_INCOMPLETE: ("◐ partial", "state-partial"),
    PresenceState.NOT_PRESENT: ("○ not present", "state-absent"),
    PresenceState.UNREACHABLE: ("✗ unreachable", "state-error"),
}

# readablecode "terminal navy" tokens (dotfiles design/tokens.css) plus the
# Quasar overrides that map the existing markup onto them. This block is the
# whole theme — don't add hex values elsewhere.
#
# NiceGUI loads Quasar's CSS into cascade layers, so layered !important
# utility classes (text-white, bg-green, text-grey, ...) beat anything we
# write here, even with !important. These rules therefore stay unlayered and
# normal (they win over Quasar's layered normal declarations), and the markup
# avoids Quasar color utilities in favor of the state-*/muted classes below.
_TOKENS_CSS = """
:root {
  --bg: #0d1420;
  --surface: #121b2a;
  --surface-2: #182333;
  --border: rgba(148, 163, 184, 0.16);
  --ink: #dbe4f0;
  --ink-2: #9fb0c3;
  --muted: #7d8b9e;
  --green: #2ea043;
  --green-bright: #56d364;
  --amber: #b8860b;
  --amber-bright: #e3b341;
  --dot-red: #f87171;
  --radius: 8px;
  --font-mono: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas,
    monospace;
}

body, body.body--dark {
  background: var(--bg);
  color: var(--ink);
  font-family: var(--font-mono);
}
.q-field__native, .q-field__input, .q-btn, .q-badge, .q-toggle,
.q-notification, .q-tooltip {
  font-family: var(--font-mono);
}

/* cards as stat pills: surface, hairline border, 8px radius, no shadows */
.q-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: none;
  color: var(--ink);
}

/* badges: quiet pills, state carried by text color */
.q-badge {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--ink-2);
}

/* semantic state / text colors (replace Quasar text-* utilities) */
.muted { color: var(--muted); }
.state-complete { color: var(--green-bright); }
.state-partial { color: var(--amber-bright); }
.state-absent { color: var(--muted); }
.state-error { color: var(--dot-red); }

.q-btn {
  border-radius: var(--radius);
  box-shadow: none;
  text-transform: none;
}
.q-btn.bg-positive { font-weight: 700; }
.q-btn-group { border: 1px solid var(--border); box-shadow: none; }

.q-field--outlined .q-field__control {
  border-radius: var(--radius);
  background: var(--surface);
}
.q-field--outlined .q-field__control:before { border: 1px solid var(--border); }
.q-field--outlined.q-field--focused .q-field__control:after {
  border-color: var(--green);
  border-width: 1px;
}
.q-field__native { color: var(--ink); }
.q-field__native::placeholder { color: var(--muted); }

.q-notification { border-radius: var(--radius); }

/* signature pieces: ❯ brand, // section headers */
.brand-prompt { color: var(--green-bright); }
.section-h {
  width: 100%;
  border-bottom: 1px solid var(--border);
  padding-bottom: 2px;
  font-weight: 700;
  color: var(--ink);
}
.section-h .sh-slash { color: var(--green-bright); }
"""


def _badge(status) -> tuple[str, str]:
    label, color = STATE_BADGE[status.state]
    # A movie is either downloaded or not — "partial" only makes sense for TV
    if status.state == PresenceState.MONITORED_INCOMPLETE and status.missing_episode_count is None:
        label = "◐ not downloaded"
    return label, color


def _short(instance_name: str) -> str:
    """Compact instance label for badges: 'sonarr-behemoth' -> 'behemoth'."""
    return instance_name.split("-", 1)[-1]


def run_web(host: str = "127.0.0.1", port: int = 8788) -> None:
    from nicegui import ui

    config: MediaConfig = load_media_config()

    @ui.page("/")
    def index() -> None:  # noqa: C901 — page builder wires the whole UI
        ui.colors(
            primary="#2ea043",
            secondary="#182333",
            accent="#56d364",
            dark="#121b2a",
            dark_page="#0d1420",
            positive="#2ea043",
            negative="#f87171",
            info="#9fb0c3",
            warning="#e3b341",
        )
        ui.add_css(_TOKENS_CSS)
        state: dict = {"media_type": MediaType.TV}

        def _section(title: str) -> None:
            ui.html(f'<span class="sh-slash">//</span> {title}').classes("section-h")

        async def do_search() -> None:
            query = (search_box.value or "").strip()
            if len(query) < 2:
                return
            spinner.visible = True
            try:
                results = await search_everywhere(query, state["media_type"], config)
            finally:
                spinner.visible = False
            render_results(results[:20])

        def render_results(results: list[AggregatedResult]) -> None:
            results_area.clear()
            with results_area:
                if not results:
                    ui.label("no results.").classes("muted")
                else:
                    _section("results")
                for aggregated in results:
                    _result_card(aggregated)

        def _result_card(aggregated: AggregatedResult) -> None:
            r = aggregated.result
            with (
                ui.card()
                .classes("w-full cursor-pointer")
                .on("click", lambda a=aggregated: open_detail(a))
            ):
                with ui.row().classes("items-center no-wrap w-full gap-4"):
                    if r.poster_url:
                        ui.image(r.poster_url).classes("w-16 rounded shrink-0")
                    with ui.column().classes("gap-1 min-w-0"):
                        year = f" ({r.year})" if r.year else ""
                        ui.label(f"{r.title}{year}").classes("text-lg font-bold")
                        with ui.row().classes("gap-1"):
                            for status in aggregated.statuses:
                                label, state_class = _badge(status)
                                ui.badge(f"{_short(status.instance)} {label}", color=None).classes(state_class)

        async def open_detail(aggregated: AggregatedResult) -> None:
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                r = aggregated.result
                year = f" ({r.year})" if r.year else ""
                with ui.row().classes("items-start no-wrap w-full gap-4"):
                    if r.poster_url:
                        ui.image(r.poster_url).classes("w-24 rounded shrink-0")
                    with ui.column().classes("gap-1 min-w-0"):
                        ui.label(f"{r.title}{year}").classes("text-xl font-bold")
                        meta = " · ".join(
                            x
                            for x in (
                                r.network,
                                r.status,
                                f"{r.season_count} seasons" if r.season_count else "",
                                ", ".join(r.genres[:3]),
                            )
                            if x
                        )
                        if meta:
                            ui.label(meta).classes("text-xs muted")
                        if r.overview:
                            ui.label(r.overview[:300]).classes("text-sm muted")

                status_area = ui.column().classes("w-full gap-1")
                plex_area = ui.column().classes("w-full gap-1")
                action_area = ui.column().classes("w-full gap-2")

                def render_statuses() -> None:
                    status_area.clear()
                    with status_area:
                        _section("instances")
                        for status in aggregated.statuses:
                            label, state_class = _badge(status)
                            line = f"{status.instance}: {label}"
                            if (
                                status.state == PresenceState.MONITORED_INCOMPLETE
                                and status.missing_episode_count is not None
                            ):
                                line += f" — missing {status.missing_episode_count}/{status.total_episode_count} eps"
                            ui.label(line).classes(state_class)
                            if status.seasons:
                                chips = "  ".join(
                                    f"{'✓' if s.monitored else '✗'}"
                                    f"{'SP' if s.season_number == 0 else 'S' + str(s.season_number)}"
                                    f" {s.episode_file_count}/{s.total_episode_count or s.episode_count}"
                                    for s in sorted(
                                        status.seasons, key=lambda s: (s.season_number == 0, s.season_number)
                                    )
                                    if s.total_episode_count or s.episode_count or s.monitored
                                )
                                if chips:
                                    ui.label(chips).classes("text-xs muted pl-4")

                    action_area.clear()
                    with action_area:
                        for status in aggregated.statuses:
                            if status.state == PresenceState.NOT_PRESENT:
                                ui.button(
                                    f"add to {status.instance}",
                                    on_click=lambda s=status: do_add(s.instance),
                                ).classes("w-full").props("size=lg color=positive text-color=dark")

                def render_plex() -> None:
                    plex_area.clear()
                    with plex_area:
                        if aggregated.plex:
                            _section("plex")
                        for plex in aggregated.plex:
                            if plex.available:
                                ui.label(f"▶ {plex.server}: watch-ready").classes("state-complete font-bold")
                            elif plex.error:
                                ui.label(f"✗ {plex.server}: unreachable").classes("state-error")
                            else:
                                ui.label(f"· {plex.server}: not in library").classes("state-absent")

                async def do_add(instance_name: str) -> None:
                    add_result = await add_to_instance(aggregated, instance_name, config)
                    ui.notify(
                        add_result.message,
                        color="positive" if add_result.ok else "negative",
                        position="top",
                    )
                    if add_result.ok:
                        refreshed = await refresh_status(aggregated, config, include_plex=False)
                        aggregated.statuses = refreshed.statuses
                        render_statuses()

                render_statuses()
                render_plex()
            dialog.open()

            if any(s.series_id for s in aggregated.statuses):
                await enrich_tv_statuses(aggregated, config)
                render_statuses()
            if config.plex and not aggregated.plex:
                await check_plex_availability(aggregated, config)
                render_plex()

        async def on_toggle(e) -> None:
            state["media_type"] = e.value
            await do_search()

        # --- page layout ---
        with ui.column().classes("w-full max-w-2xl mx-auto p-4 gap-3"):
            with ui.row().classes("items-center w-full no-wrap gap-3"):
                ui.html('<span class="brand-prompt">❯</span> syncplex media').classes("text-2xl font-bold grow")
                ui.toggle(
                    {MediaType.TV: "tv", MediaType.MOVIE: "movies"},
                    value=MediaType.TV,
                    on_change=on_toggle,
                ).props("no-caps toggle-text-color=dark")
            search_box = (
                ui.input(placeholder="search…", on_change=do_search)
                .props('debounce=500 clearable outlined input-class="text-lg"')
                .classes("w-full")
            )
            spinner = ui.spinner(size="lg").classes("self-center")
            spinner.visible = False
            results_area = ui.column().classes("w-full gap-3")

            for warning in config.warnings:
                ui.notify(warning, color="warning", position="top")

    print(f"Syncplex Media web UI on http://{host}:{port}  (bind your Tailscale IP with --host to share)")
    ui.run(host=host, port=port, title="❯ syncplex media", dark=True, reload=False, show=False)
