"""Media remote web UI — a thin NiceGUI layer over engine.media.

Single process, server-rendered, calls core functions in-process (no internal
REST API). All business logic lives in engine/media; this file only renders.
Styling follows the readablecode "terminal navy" design system (dotfiles
design/STYLE.md) — tokens live in _TOKENS_CSS, Quasar brand colors are mapped
onto them per page.

Authentication is built in (this app used to sit behind Authelia; it no
longer needs to): argon2id accounts from `syncplex users` (engine/web/users),
server-side sessions + login throttling (engine/web/auth), and two roles —
admins add titles directly and work the approval queue at /requests; users
can only file requests there (engine/media/requests).
"""

import os

from ..config import get_data_dir
from ..media.aggregation import (
    add_to_instance,
    check_plex_availability,
    enrich_tv_statuses,
    refresh_status,
    search_everywhere,
)
from ..media.config import MediaConfig, load_media_config
from ..media.health import check_all_servers, estimate_add_bytes, format_bytes
from ..media.models import AggregatedResult, MediaType, PresenceState, ServerHealth
from ..media.requests import MediaRequest, RequestStatus, RequestStore, fulfill_request
from .auth import (
    LoginRateLimiter,
    attempt_login,
    clear_session,
    current_user,
    issue_session,
    session_secret,
)
from .users import User, UserStore

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

/* storage meter: hairline track, green fill, red when nearly full */
.meter {
  width: 100%;
  height: 6px;
  border-radius: 3px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  overflow: hidden;
}
.meter > i {
  display: block;
  height: 100%;
  background: var(--green);
}
.meter > i.meter-hot { background: var(--dot-red); }

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

/* nav links + request status badges */
a.nav-link { color: var(--ink-2); text-decoration: none; }
a.nav-link:hover { color: var(--green-bright); }
.req-pending { color: var(--amber-bright); }
.req-approved { color: var(--green-bright); }
.req-denied { color: var(--dot-red); }
"""

REQUEST_BADGE = {
    RequestStatus.PENDING: ("⏳ pending", "req-pending"),
    RequestStatus.APPROVED: ("✓ approved", "req-approved"),
    RequestStatus.DENIED: ("✗ denied", "req-denied"),
}


def _badge(status) -> tuple[str, str]:
    label, color = STATE_BADGE[status.state]
    # A movie is either downloaded or not — "partial" only makes sense for TV
    if status.state == PresenceState.MONITORED_INCOMPLETE and status.missing_episode_count is None:
        label = "◐ not downloaded"
    return label, color


def _short(instance_name: str) -> str:
    """Compact instance label for badges: 'sonarr-behemoth' -> 'behemoth'."""
    return instance_name.split("-", 1)[-1]


def _season_chip(season) -> str:
    """'✓S2 8/10 · 19.1 GB' — monitoring, have/total, and the season folder size."""
    mark = "✓" if season.monitored else "✗"
    label = "SP" if season.season_number == 0 else f"S{season.season_number}"
    chip = f"{mark}{label} {season.episode_file_count}/{season.total_episode_count or season.episode_count}"
    if season.size_on_disk:
        chip += f" · {format_bytes(season.size_on_disk)}"
    return chip


def _stats_line(health: ServerHealth) -> str:
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


def run_web(host: str = "127.0.0.1", port: int = 8788) -> None:  # noqa: C901 — wires every page
    from fastapi import Request
    from fastapi.responses import RedirectResponse
    from nicegui import Client, app, run, ui
    from nicegui.storage import Storage
    from starlette.middleware.base import BaseHTTPMiddleware

    # Server-side session storage goes in the data dir (not CWD), so logins
    # survive container rebuilds alongside users.json/requests.json.
    if not os.environ.get("NICEGUI_STORAGE_PATH"):
        Storage.path = get_data_dir() / ".nicegui"

    config: MediaConfig = load_media_config()
    users = UserStore()
    requests_store = RequestStore()
    limiter = LoginRateLimiter()

    def _user() -> User | None:
        return current_user(app.storage.user, users)

    def _client_ip(request: Request) -> str:
        # First X-Forwarded-For hop when behind the reverse proxy. Spoofable
        # on direct hits, but the per-username lockout doesn't depend on it.
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    class AuthMiddleware(BaseHTTPMiddleware):
        """Every page except /login requires a valid session."""

        async def dispatch(self, request: Request, call_next):
            if request.url.path in Client.page_routes.values() and request.url.path != "/login":
                if _user() is None:
                    app.storage.user["referrer_path"] = request.url.path
                    return RedirectResponse("/login")
            return await call_next(request)

    app.add_middleware(AuthMiddleware)

    def _theme() -> None:
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

    def _section(title: str) -> None:
        ui.html(f'<span class="sh-slash">//</span> {title}').classes("section-h")

    def _logout() -> None:
        clear_session(app.storage.user)
        ui.navigate.to("/login")

    def _nav(user: User) -> None:
        """Shared header: brand, requests link (pending count), user, logout."""
        with ui.row().classes("items-center w-full no-wrap gap-3"):
            with ui.link(target="/").classes("nav-link grow"):
                ui.html('<span class="brand-prompt">❯</span> syncplex media').classes("text-2xl font-bold")
            count = (
                requests_store.pending_count()
                if user.is_admin
                else len(requests_store.list(status=RequestStatus.PENDING, requested_by=user.username))
            )
            ui.link(f"requests ({count})" if count else "requests", "/requests").classes("nav-link text-sm shrink-0")
            ui.label(f"{user.username} · {user.role}").classes("text-xs muted shrink-0")
            ui.button("logout", on_click=_logout).props("flat dense no-caps size=sm color=info")

    @ui.page("/login")
    def login_page(request: Request) -> None:
        _theme()
        if _user() is not None:
            ui.navigate.to("/")
            return

        async def try_login() -> None:
            # io_bound keeps the ~100ms argon2 verify off the event loop
            user, error = await run.io_bound(
                attempt_login, users, limiter, username_box.value or "", password_box.value or "", _client_ip(request)
            )
            if user is None:
                ui.notify(error, color="negative", position="top")
                return
            issue_session(app.storage.user, user)
            ui.navigate.to(app.storage.user.pop("referrer_path", "/") or "/")

        with ui.column().classes("absolute-center items-center gap-4 w-80"):
            ui.html('<span class="brand-prompt">❯</span> syncplex media').classes("text-2xl font-bold")
            with ui.card().classes("w-full gap-3 p-5"):
                username_box = (
                    ui.input(placeholder="username").props("outlined autofocus autocomplete=username").classes("w-full")
                )
                password_box = (
                    ui.input(placeholder="password", password=True, password_toggle_button=True)
                    .props("outlined autocomplete=current-password")
                    .classes("w-full")
                )
                password_box.on("keydown.enter", try_login)
                ui.button("log in", on_click=try_login).classes("w-full").props(
                    "color=positive text-color=dark no-caps size=lg"
                )
            if not users.list():
                ui.label("no accounts yet — create the first admin on the server:").classes("text-xs muted")
                ui.label("syncplex users add <name> --role admin").classes("text-xs")

    @ui.page("/")
    def index() -> None:  # noqa: C901 — page builder wires the whole UI
        _theme()
        user = _user()
        if user is None:  # middleware already redirects; belt and braces
            ui.navigate.to("/login")
            return
        state: dict = {"media_type": MediaType.TV, "health": {}}

        def _health_card(health: ServerHealth) -> None:
            with ui.card().classes("grow basis-52 gap-1 p-3"):
                with ui.row().classes("items-center w-full no-wrap gap-2"):
                    ui.label(health.name).classes("text-sm font-bold grow truncate")
                    if health.up:
                        ping = f" {health.ping_ms:.0f}ms" if health.ping_ms is not None else ""
                        ui.label(f"● up{ping}").classes("state-complete text-xs shrink-0")
                    else:
                        ui.label("✗ down").classes("state-error text-xs shrink-0")
                if health.disk_total_bytes:
                    used = health.disk_total_bytes - (health.disk_free_bytes or 0)
                    pct = round(100 * used / health.disk_total_bytes)
                    hot = " meter-hot" if pct >= 90 else ""
                    ui.html(f'<div class="meter"><i class="{hot}" style="width:{pct}%"></i></div>')
                    ui.label(
                        f"{format_bytes(used)} used · {format_bytes(health.disk_free_bytes or 0)} free"
                        f" of {format_bytes(health.disk_total_bytes)}"
                    ).classes("text-xs muted")
                stats = _stats_line(health)
                if stats:
                    ui.label(stats).classes("text-xs muted")
                if not health.up and health.error:
                    ui.label(health.error[:100]).classes("text-xs state-error")

        def render_health() -> None:
            health_row.clear()
            with health_row:
                if not state["health"]:
                    ui.label("checking servers…").classes("text-xs muted")
                for health in state["health"].values():
                    _health_card(health)

        async def refresh_health() -> None:
            healths = await check_all_servers(config)
            state["health"] = {h.name: h for h in healths}
            render_health()

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
                            if status.size_on_disk:
                                line += f" · {format_bytes(status.size_on_disk)}"
                            ui.label(line).classes(state_class)
                            if status.seasons:
                                chips = "  ".join(
                                    _season_chip(s)
                                    for s in sorted(
                                        status.seasons, key=lambda s: (s.season_number == 0, s.season_number)
                                    )
                                    if s.total_episode_count or s.episode_count or s.monitored
                                )
                                if chips:
                                    ui.label(chips).classes("text-xs muted pl-4")

                    render_actions()

                def render_actions() -> None:
                    action_area.clear()
                    with action_area:
                        if user.is_admin:
                            _admin_actions()
                        else:
                            _request_actions()

                def _admin_actions() -> None:
                    for status in aggregated.statuses:
                        if status.state != PresenceState.NOT_PRESENT:
                            continue
                        health = state["health"].get(status.instance)
                        estimate = estimate_add_bytes(aggregated, health)
                        ui.button(
                            f"add to {status.instance} · ~{format_bytes(estimate)}",
                            on_click=lambda s=status: do_add(s.instance),
                        ).classes("w-full").props("size=lg color=positive text-color=dark")
                        free = health.disk_free_bytes if health else None
                        if free is not None:
                            if estimate > free:
                                ui.label(
                                    f"⚠ needs ~{format_bytes(estimate)} but only "
                                    f"{format_bytes(free)} free on {_short(status.instance)}"
                                ).classes("text-xs state-partial")
                            else:
                                ui.label(
                                    f"{format_bytes(free)} free on {_short(status.instance)}"
                                ).classes("text-xs muted")

                def _request_actions() -> None:
                    """Non-admins never add directly — they file a request
                    that an admin approves (and targets) at /requests."""
                    pending = requests_store.find_pending(r)
                    if pending is not None:
                        who = "you" if pending.requested_by == user.username else pending.requested_by
                        ui.label(f"⏳ requested by {who} — waiting for admin approval").classes("req-pending")
                        if pending.requested_by == user.username:
                            ui.button("withdraw request", on_click=lambda: do_withdraw(pending.id)).props(
                                "flat no-caps"
                            ).classes("w-full")
                        return
                    if all(s.state != PresenceState.NOT_PRESENT for s in aggregated.statuses):
                        return  # nothing to request — every server has it (or is down)
                    ui.button("request this title", on_click=do_request).classes("w-full").props(
                        "size=lg color=positive text-color=dark"
                    )
                    ui.label("an admin approves it and picks the server before anything downloads").classes(
                        "text-xs muted"
                    )

                def do_request() -> None:
                    request = requests_store.create(r, user.username)
                    ui.notify(f"requested '{request.result.title}' — waiting for admin approval", position="top")
                    render_actions()

                def do_withdraw(request_id: str) -> None:
                    try:
                        requests_store.withdraw(request_id, user.username)
                    except (KeyError, ValueError) as exc:
                        ui.notify(str(exc), color="negative", position="top")
                    render_actions()

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
            _nav(user)
            health_row = ui.row().classes("w-full gap-3 items-stretch")
            if user.is_admin:
                # server health powers the add-size estimates — admin-only,
                # requesters don't pick servers so they don't need the noise
                render_health()
                ui.timer(60.0, refresh_health)  # fires immediately, then every minute
            with ui.row().classes("items-center w-full no-wrap gap-3"):
                search_box = (
                    ui.input(placeholder="search…", on_change=do_search)
                    .props('debounce=500 clearable outlined input-class="text-lg"')
                    .classes("grow")
                )
                ui.toggle(
                    {MediaType.TV: "tv", MediaType.MOVIE: "movies"},
                    value=MediaType.TV,
                    on_change=on_toggle,
                ).props("no-caps toggle-text-color=dark").classes("shrink-0")
            spinner = ui.spinner(size="lg").classes("self-center")
            spinner.visible = False
            results_area = ui.column().classes("w-full gap-3")

            if user.is_admin:
                for warning in config.warnings:
                    ui.notify(warning, color="warning", position="top")

    @ui.page("/requests")
    def requests_page() -> None:  # noqa: C901 — page builder wires the whole queue UI
        _theme()
        user = _user()
        if user is None:
            ui.navigate.to("/login")
            return

        def _request_meta(request: MediaRequest) -> str:
            kind = "show" if request.result.media_type == MediaType.TV else "movie"
            return f"{kind} · requested by {request.requested_by} · {request.requested_at:%Y-%m-%d %H:%M}"

        def _history_row(request: MediaRequest) -> None:
            label, badge_class = REQUEST_BADGE[request.status]
            with ui.row().classes("items-center w-full no-wrap gap-2"):
                ui.label(label).classes(f"{badge_class} text-xs shrink-0")
                ui.label(request.title_line).classes("text-sm grow truncate")
                detail = f"→ {request.instance}" if request.instance else (request.note or "")
                ui.label(f"{request.requested_by} {detail}".strip()).classes("text-xs muted shrink-0")

        def _admin_card(request: MediaRequest) -> None:
            with ui.card().classes("w-full gap-2"):
                with ui.row().classes("items-start no-wrap w-full gap-4"):
                    if request.result.poster_url:
                        ui.image(request.result.poster_url).classes("w-16 rounded shrink-0")
                    with ui.column().classes("gap-1 min-w-0 grow"):
                        ui.label(request.title_line).classes("text-lg font-bold")
                        ui.label(_request_meta(request)).classes("text-xs muted")
                        if request.result.overview:
                            ui.label(request.result.overview[:200]).classes("text-xs muted")
                        if request.note:
                            ui.label(request.note).classes("text-xs state-error")

                instance_names = [i.name for i in config.arr_instances(request.result.media_type.value)]

                async def do_approve() -> None:
                    if not target.value:
                        ui.notify("pick a server first", color="warning", position="top")
                        return
                    add_result = await fulfill_request(requests_store, request.id, target.value, user.username, config)
                    ui.notify(
                        add_result.message,
                        color="positive" if add_result.ok else "negative",
                        position="top",
                    )
                    render()

                def do_deny() -> None:
                    with ui.dialog() as deny_dialog, ui.card().classes("w-80 gap-3"):
                        ui.label(f"deny '{request.result.title}'?").classes("font-bold")
                        note_box = ui.input(placeholder="reason (shown to requester, optional)").props(
                            "outlined dense"
                        ).classes("w-full")

                        def confirm() -> None:
                            try:
                                requests_store.deny(request.id, user.username, note=note_box.value or "")
                            except (KeyError, ValueError) as exc:
                                ui.notify(str(exc), color="negative", position="top")
                            deny_dialog.close()
                            render()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("cancel", on_click=deny_dialog.close).props("flat no-caps")
                            ui.button("deny", on_click=confirm).props("color=negative no-caps")
                    deny_dialog.open()

                with ui.row().classes("items-center w-full no-wrap gap-2"):
                    target = (
                        ui.select(instance_names, value=instance_names[0] if instance_names else None, label="server")
                        .props("outlined dense options-dense")
                        .classes("grow")
                    )
                    ui.button("approve", on_click=do_approve).props("color=positive text-color=dark no-caps")
                    ui.button("deny", on_click=do_deny).props("flat no-caps color=info")
                if not instance_names:
                    ui.label("no instances configured for this media type").classes("text-xs state-error")

        def _own_card(request: MediaRequest) -> None:
            label, badge_class = REQUEST_BADGE[request.status]
            with ui.card().classes("w-full gap-1"):
                with ui.row().classes("items-center w-full no-wrap gap-2"):
                    ui.label(request.title_line).classes("text-base font-bold grow truncate")
                    ui.label(label).classes(f"{badge_class} text-sm shrink-0")
                detail = f"requested {request.requested_at:%Y-%m-%d}"
                if request.status == RequestStatus.APPROVED and request.instance:
                    detail += f" · added to {_short(request.instance)}"
                if request.note and request.status != RequestStatus.APPROVED:
                    detail += f" · {request.note}"
                ui.label(detail).classes("text-xs muted")
                if request.status == RequestStatus.PENDING:

                    def withdraw(request_id: str = request.id) -> None:
                        try:
                            requests_store.withdraw(request_id, user.username)
                        except (KeyError, ValueError) as exc:
                            ui.notify(str(exc), color="negative", position="top")
                        render()

                    ui.button("withdraw", on_click=withdraw).props("flat dense no-caps size=sm color=info")

        def render() -> None:
            area.clear()
            with area:
                if user.is_admin:
                    pending = requests_store.list(status=RequestStatus.PENDING)
                    _section(f"pending requests ({len(pending)})")
                    if not pending:
                        ui.label("queue is empty.").classes("muted")
                    for request in pending:
                        _admin_card(request)
                    history = [r for r in requests_store.list() if r.status != RequestStatus.PENDING][:30]
                    if history:
                        _section("history")
                        for request in history:
                            _history_row(request)
                else:
                    mine = requests_store.list(requested_by=user.username)
                    _section("your requests")
                    if not mine:
                        ui.label("nothing yet — search on the main page and hit request.").classes("muted")
                    for request in mine:
                        _own_card(request)

        with ui.column().classes("w-full max-w-2xl mx-auto p-4 gap-3"):
            _nav(user)
            area = ui.column().classes("w-full gap-3")
            render()

    if not users.list():
        print("No accounts exist yet — nobody can log in. Create the first admin:")
        print("  syncplex users add <name> --role admin")
    if not os.environ.get("SYNCPLEX_SESSION_SECRET"):
        print("SYNCPLEX_SESSION_SECRET is not set — sessions will not survive a restart.")
    print(f"Syncplex Media web UI on http://{host}:{port}")
    ui.run(
        host=host,
        port=port,
        title="❯ syncplex media",
        dark=True,
        reload=False,
        show=False,
        storage_secret=session_secret(),
    )
