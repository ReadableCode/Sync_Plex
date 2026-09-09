"""syncplex TUI — the media remote and the request queue, over engine.media.

Two working screens on one app: the media screen (search every Sonarr,
Radarr and Plex; add, or request when not an admin) and the requests screen
(the approval queue, with the same server picture the web queue shows). A
health strip sits under the header on both. Login is the web UI's: the
shared auth service issues the JWT the queue calls carry (tui/session.py).
ctrl+s pushes the drive-sync screens (drive_sync.screens) onto this app.
Styling follows the readablecode "terminal navy" design system (dotfiles
design/STYLE.md).
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from ..aggregation import (
    add_to_instance,
    check_plex_availability,
    enrich_tv_statuses,
    lookup_status,
    refresh_status,
    search_everywhere,
)
from ..config import load_media_config
from ..health import check_all_servers, estimate_add_bytes, format_bytes
from ..models import AggregatedResult, MediaType, PresenceState, ServerHealth
from ..notifications import notify_new_request
from ..present import badge, meta_line, short, stats_line
from ..requests import MediaRequest, RequestStatus, fulfill_request
from . import session as sessions
from .theme import AMBER_BRIGHT, BG, GREEN, GREEN_BRIGHT, HAIRLINE, INK_2, MUTED, RED, SURFACE, TERMINAL_NAVY

STATE_GLYPHS = {
    PresenceState.MONITORED_COMPLETE: f"[{GREEN_BRIGHT}]●[/]",
    PresenceState.MONITORED_INCOMPLETE: f"[{AMBER_BRIGHT}]◐[/]",
    PresenceState.NOT_PRESENT: f"[{MUTED}]○[/]",
    PresenceState.UNREACHABLE: f"[{RED}]✗[/]",
}

STATE_COLOURS = {
    PresenceState.MONITORED_COMPLETE: GREEN_BRIGHT,
    PresenceState.MONITORED_INCOMPLETE: AMBER_BRIGHT,
    PresenceState.NOT_PRESENT: MUTED,
    PresenceState.UNREACHABLE: RED,
}

REQUEST_COLOURS = {
    RequestStatus.PENDING: AMBER_BRIGHT,
    RequestStatus.APPROVED: GREEN_BRIGHT,
    RequestStatus.DENIED: RED,
}
REQUEST_LABELS = {
    RequestStatus.PENDING: "⏳ pending",
    RequestStatus.APPROVED: "✓ approved",
    RequestStatus.DENIED: "✗ denied",
}


def section(title: str) -> str:
    return f"[{GREEN_BRIGHT}]//[/] [bold]{title}[/]"


def status_lines(aggregated: AggregatedResult, health: dict[str, ServerHealth]) -> list[str]:
    """The per-instance picture: presence, missing counts, size, seasons, plex, and what an add would cost."""
    lines = [section("instances")]
    for status in aggregated.statuses:
        label, _css = badge(status)
        colour = STATE_COLOURS[status.state]
        line = f"[{colour}]{label}[/]  {status.instance}"
        if status.state == PresenceState.MONITORED_INCOMPLETE and status.missing_episode_count is not None:
            line += f"  [{MUTED}]missing {status.missing_episode_count}/{status.total_episode_count} eps[/]"
        if status.size_on_disk:
            line += f"  [{MUTED}]{format_bytes(status.size_on_disk)}[/]"
        if status.error:
            line += f"  [{RED}]{status.error[:60]}[/]"
        lines.append(line)
        for season in sorted(status.seasons, key=lambda s: (s.season_number == 0, s.season_number)):
            tag = "SP" if season.season_number == 0 else f"S{season.season_number}"
            mark = f"[{GREEN_BRIGHT}]✓[/]" if season.monitored else f"[{MUTED}]✗[/]"
            denominator = season.total_episode_count or season.episode_count
            counts = f"{season.episode_file_count}/{denominator}" if denominator else "—"
            size = f"  [{MUTED}]{format_bytes(season.size_on_disk)}[/]" if season.size_on_disk else ""
            lines.append(f"      {mark} {tag:<4} {counts}{size}")
        if status.state == PresenceState.NOT_PRESENT:
            server = health.get(status.instance)
            estimate = estimate_add_bytes(aggregated, server)
            free = server.disk_free_bytes if server else None
            cost = f"      [{MUTED}]an add here: ~{format_bytes(estimate)}[/]"
            if free is not None and estimate > free:
                cost = (
                    f"      [{AMBER_BRIGHT}]⚠ an add needs ~{format_bytes(estimate)}, only {format_bytes(free)} free[/]"
                )
            elif free is not None:
                cost += f"  [{MUTED}]· {format_bytes(free)} free[/]"
            lines.append(cost)
    if not aggregated.statuses:
        lines.append(f"[{RED}]no server could look this title up[/]")
    if aggregated.plex:
        lines.append("")
        lines.append(section("plex"))
        for plex in aggregated.plex:
            glyph = f"[{GREEN_BRIGHT}]▶[/]" if plex.available else f"[{MUTED}]·[/]"
            note = "watch-ready" if plex.available else ("error: " + plex.error[:40] if plex.error else "not in plex")
            lines.append(f"{glyph} {plex.server}: {note}")
    return lines


def best_server(aggregated: AggregatedResult, health: dict[str, ServerHealth], names: list[str]) -> str | None:
    """The absent server with the most room after the add, else the first absent one, else None."""
    best: tuple[int, str] | None = None
    fallback = None
    for name in names:
        status = aggregated.status_for(name)
        if status is not None and status.state != PresenceState.NOT_PRESENT:
            continue
        fallback = fallback or name
        server = health.get(name)
        free = server.disk_free_bytes if server else None
        estimate = estimate_add_bytes(aggregated, server)
        if free is not None and estimate <= free and (best is None or free > best[0]):
            best = (free, name)
    return best[1] if best else fallback


# --- modals ---


class LoginScreen(ModalScreen["sessions.Session | None"]):
    """Username and password, straight to the shared auth service."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]
    CSS = f"""
    LoginScreen {{ align: center middle; }}
    LoginScreen > Vertical {{
        width: 60; height: auto;
        border: solid {HAIRLINE}; background: {SURFACE}; padding: 1 2;
    }}
    LoginScreen #title {{ color: {GREEN_BRIGHT}; text-style: bold; margin-bottom: 1; }}
    LoginScreen Input {{ margin-bottom: 1; }}
    LoginScreen #error {{ color: {RED}; }}
    LoginScreen #hint {{ color: {MUTED}; }}
    """

    def __init__(self, why: str = "") -> None:
        super().__init__()
        self.why = why

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("sign in" + (f"  [{MUTED}]{self.why}[/]" if self.why else ""), id="title")
            yield Input(placeholder="username", id="username")
            yield Input(placeholder="password", password=True, id="password")
            yield Static("", id="error")
            yield Static("enter signs in · escape cancels · the same accounts as the web ui", id="hint")

    def on_mount(self) -> None:
        self.query_one("#username", Input).focus()

    @on(Input.Submitted, "#username")
    def to_password(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one("#password", Input).focus()

    @on(Input.Submitted, "#password")
    def submit(self, event: Input.Submitted) -> None:
        event.stop()
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        if not username or not password:
            self.query_one("#error", Static).update("both fields, please")
            return
        self.query_one("#error", Static).update(f"[{MUTED}]signing in…[/]")
        self.attempt(username, password)

    @work(thread=True, exclusive=True)
    def attempt(self, username: str, password: str) -> None:
        session, reason = sessions.login(username, password)
        self.app.call_from_thread(self.finish, session, reason)

    def finish(self, session: sessions.Session | None, reason: str) -> None:
        if session is None:
            self.query_one("#error", Static).update(reason)
            self.query_one("#password", Input).value = ""
            self.query_one("#password", Input).focus()
            return
        self.dismiss(session)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PickScreen(ModalScreen[str | None]):
    """Choose one option (a server to add to); enter picks, escape cancels."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]
    CSS = f"""
    PickScreen {{ align: center middle; }}
    PickScreen > Vertical {{
        width: 80; height: auto; max-height: 80%;
        border: solid {HAIRLINE}; background: {SURFACE}; padding: 1 2;
    }}
    PickScreen #title {{ color: {GREEN_BRIGHT}; text-style: bold; margin-bottom: 1; }}
    PickScreen OptionList {{ height: auto; max-height: 16; border: none; }}
    PickScreen #hint {{ color: {MUTED}; margin-top: 1; }}
    """

    def __init__(self, title: str, options: list[tuple[str, str]], preselect: str | None = None) -> None:
        super().__init__()
        self.title_text = title
        self.options = options  # (id, label)
        self.preselect = preselect

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.title_text, id="title")
            yield OptionList(*[Option(label, id=key) for key, label in self.options], id="choices")
            yield Static("enter picks · escape cancels", id="hint")

    def on_mount(self) -> None:
        choices = self.query_one("#choices", OptionList)
        index = 0
        for i, (key, _label) in enumerate(self.options):
            if key == self.preselect:
                index = i
        if self.options:
            choices.highlighted = index
        choices.focus()

    @on(OptionList.OptionSelected, "#choices")
    def picked(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NoteScreen(ModalScreen[str | None]):
    """One line of text (a denial reason); enter accepts, escape cancels."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]
    CSS = f"""
    NoteScreen {{ align: center middle; }}
    NoteScreen > Vertical {{
        width: 70; height: auto;
        border: solid {HAIRLINE}; background: {SURFACE}; padding: 1 2;
    }}
    NoteScreen #title {{ color: {AMBER_BRIGHT}; text-style: bold; margin-bottom: 1; }}
    NoteScreen #hint {{ color: {MUTED}; margin-top: 1; }}
    """

    def __init__(self, title: str, placeholder: str) -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.title_text, id="title")
            yield Input(placeholder=self.placeholder, id="note")
            yield Static("enter confirms · escape cancels", id="hint")

    def on_mount(self) -> None:
        self.query_one("#note", Input).focus()

    @on(Input.Submitted, "#note")
    def submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "close", "close")]
    CSS = f"""
    HelpScreen {{ align: center middle; }}
    HelpScreen > VerticalScroll {{
        width: 84; height: auto; max-height: 90%;
        border: solid {HAIRLINE}; background: {SURFACE}; padding: 1 2;
    }}
    """

    TEXT = f"""[{GREEN_BRIGHT}]//[/] [bold]media[/]
  type to search · [bold]escape[/] hops to the results, where the letter keys work · [bold]/[/] back to typing
  [bold]t[/] tv / movies · [bold]↑↓[/] pick a result · [bold]r[/] refresh it
  [bold]a[/] add the title to a server (a picker when several could take it)
  [bold]w[/] request it / withdraw the request  [{MUTED}](signed in as a non-admin)[/]

[{GREEN_BRIGHT}]//[/] [bold]requests[/]  [{MUTED}](ctrl+r from anywhere)[/]
  [bold]↑↓[/] pick a request · [bold]a[/] / [bold]enter[/] approve onto a server · [bold]d[/] deny with a reason
  [bold]r[/] refresh · [bold]escape[/] back to media

[{GREEN_BRIGHT}]//[/] [bold]everywhere[/]
  [bold]ctrl+l[/] sign in / out · [bold]ctrl+s[/] drive sync · [bold]f1[/] or [bold]?[/] help · [bold]ctrl+q[/] quit

[{MUTED}]the strip under the header is every server: up, latency, disk, library; it refreshes each minute[/]
"""

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self.TEXT)

    def action_close(self) -> None:
        self.dismiss(None)


# --- the health strip ---


class HealthStrip(Static):
    """One line per server family: up/down, latency, disk, library totals."""

    DEFAULT_CSS = f"""
    HealthStrip {{ padding: 0 1; height: auto; border-bottom: solid {HAIRLINE}; color: {INK_2}; }}
    """

    def render_health(self, health: dict[str, ServerHealth], pending: int | None, user: str) -> None:
        if not health:
            line = f"[{MUTED}]servers: checking…[/]"
        else:
            chips = []
            for server in health.values():
                if not server.up:
                    chips.append(f"[{RED}]✗ {short(server.name)}[/]")
                    continue
                chip = f"[{GREEN_BRIGHT}]●[/] {short(server.name)}"
                if server.ping_ms is not None:
                    chip += f" [{MUTED}]{server.ping_ms:.0f}ms[/]"
                if server.disk_total_bytes:
                    used = server.disk_total_bytes - (server.disk_free_bytes or 0)
                    pct = round(100 * used / server.disk_total_bytes)
                    colour = RED if pct >= 90 else MUTED
                    chip += f" [{colour}]{format_bytes(server.disk_free_bytes or 0)} free[/]"
                chips.append(chip)
            line = "   ".join(chips)
        who = f"[{MUTED}]not signed in · ctrl+l[/]" if not user else f"[{MUTED}]{user}[/]"
        if pending:
            who = f"[{AMBER_BRIGHT}]⏳ {pending} request(s) · ctrl+r[/]  " + who
        self.update(f"{line}    {who}")


# --- the media screen ---


class MediaScreen(Screen[None]):
    BINDINGS = [
        Binding("slash", "focus_search", "search"),
        Binding("t", "toggle_type", "tv/movie"),
        Binding("a", "add", "add"),
        Binding("w", "request", "request"),
        Binding("r", "refresh_selected", "refresh"),
        Binding("escape", "hop", "results/search", show=False),
    ]
    CSS = f"""
    MediaScreen #search {{ margin: 0 1; }}
    MediaScreen #body {{ height: 1fr; }}
    MediaScreen #results {{ width: 2fr; }}
    MediaScreen #detail-pane {{ width: 1fr; border-left: solid {HAIRLINE}; padding: 0 1; background: {SURFACE}; }}
    MediaScreen #detail {{ height: auto; }}
    """

    def __init__(self) -> None:
        super().__init__()
        self.media_type = MediaType.TV
        self.results: dict[str, AggregatedResult] = {}
        self._search_timer: Timer | None = None

    @property
    def remote(self) -> MediaRemote:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield HealthStrip(id="health")
        yield Input(placeholder="search… (escape hops to the results, f1 for help)", id="search")
        with Horizontal(id="body"):
            yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="detail-pane"):
                yield Static("type to search.", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self._update_subtitle()
        self._rebuild_columns()
        for warning in self.remote.config.warnings:
            self.notify(warning, severity="warning", timeout=8)
        self.query_one("#search", Input).focus()
        self.remote.refresh_strip()

    def on_screen_resume(self) -> None:
        self.remote.refresh_strip()

    def _update_subtitle(self) -> None:
        instances = self.remote.config.arr_instances(self.media_type.value)
        self.sub_title = f"{self.media_type.value} — {len(instances)} instances, {len(self.remote.config.plex)} plex"

    def _rebuild_columns(self) -> None:
        table = self.query_one("#results", DataTable)
        table.clear(columns=True)
        table.add_column("title", key="title", width=40)
        table.add_column("year", key="year", width=6)
        for instance in self.remote.config.arr_instances(self.media_type.value):
            table.add_column(short(instance.name), key=instance.name)

    # --- search ---

    @on(Input.Changed, "#search")
    def debounce_search(self, event: Input.Changed) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        query = event.value.strip()
        if len(query) < 2:
            return
        self._search_timer = self.set_timer(0.5, lambda: self.run_search(query))

    @on(Input.Submitted, "#search")
    def submit_search(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self.run_search(query)
        self.query_one("#results", DataTable).focus()

    @work(exclusive=True, group="search")
    async def run_search(self, query: str) -> None:
        self.query_one("#detail", Static).update(f"[{MUTED}]searching…[/]")
        results = await search_everywhere(query, self.media_type, self.remote.config)
        self.results = {r.result.external_key: r for r in results[:20]}
        table = self.query_one("#results", DataTable)
        table.clear()
        for key, aggregated in self.results.items():
            row: list[str | Text] = [aggregated.result.title, str(aggregated.result.year or "")]
            for instance in self.remote.config.arr_instances(self.media_type.value):
                status = aggregated.status_for(instance.name)
                row.append(Text.from_markup(STATE_GLYPHS[status.state]) if status else "?")
            table.add_row(*row, key=key)
        if self.results:
            table.focus()
        else:
            self.query_one("#detail", Static).update("no results.")

    # --- detail ---

    def _selected(self) -> AggregatedResult | None:
        table = self.query_one("#results", DataTable)
        if table.cursor_row is None or not self.results or table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        return self.results.get(key) if key is not None else None

    @on(DataTable.RowHighlighted, "#results")
    def show_detail(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None or event.row_key.value not in self.results:
            return
        aggregated = self.results[event.row_key.value]
        self._render_detail(aggregated)
        self.enrich_detail(aggregated)
        self.check_plex(aggregated)

    @work(exclusive=True, group="enrich")
    async def enrich_detail(self, aggregated: AggregatedResult) -> None:
        if any(s.series_id for s in aggregated.statuses):
            await enrich_tv_statuses(aggregated, self.remote.config)
            self._render_detail(aggregated)

    @work(exclusive=True, group="plex")
    async def check_plex(self, aggregated: AggregatedResult) -> None:
        if not self.remote.config.plex or aggregated.plex:
            return
        await check_plex_availability(aggregated, self.remote.config)
        self._render_detail(aggregated)

    def _render_detail(self, aggregated: AggregatedResult) -> None:
        if self._selected() is not aggregated:
            return
        r = aggregated.result
        lines = [f"[bold]{r.title}[/] ({r.year or '?'})"]
        meta = meta_line(r)
        if meta:
            lines.append(f"[{MUTED}]{meta}[/]")
        if r.runtime:
            lines.append(f"[{MUTED}]{r.runtime} min[/]")
        lines.append("")
        lines.extend(status_lines(aggregated, self.remote.health))
        if self.remote.config.plex and not aggregated.plex:
            lines.append(f"[{MUTED}]checking plex…[/]")
        lines.append("")
        lines.extend(self._action_lines(aggregated))
        if r.overview:
            lines.append(f"\n[{MUTED}]{r.overview[:400]}[/]")
        self.query_one("#detail", Static).update("\n".join(lines))

    def _action_lines(self, aggregated: AggregatedResult) -> list[str]:
        absent = [s.instance for s in aggregated.statuses if s.state == PresenceState.NOT_PRESENT]
        session = self.remote.session
        if session is not None and not session.user.is_admin:
            pending = self.remote.own_pending.get(aggregated.result.external_key)
            if pending:
                return [f"[{AMBER_BRIGHT}]⏳ requested by you[/] · [bold]w[/] withdraws it"]
            if not absent:
                return [f"[{MUTED}]every server has it[/]"]
            return ["[bold]w[/] requests it · an admin approves and picks the server"]
        if not absent:
            return [f"[{MUTED}]every server has it[/]"]
        if len(absent) == 1:
            return [f"[bold]a[/] adds it to [bold]{absent[0]}[/]"]
        return [f"[bold]a[/] adds it · picks between {', '.join(short(name) for name in absent)}"]

    # --- actions ---

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_hop(self) -> None:
        """Escape leaves the search box for the results (where the letter keys work), and comes back."""
        search = self.query_one("#search", Input)
        if search.has_focus:
            self.query_one("#results", DataTable).focus()
        else:
            search.focus()

    def action_toggle_type(self) -> None:
        self.media_type = MediaType.MOVIE if self.media_type == MediaType.TV else MediaType.TV
        self._update_subtitle()
        self.results = {}
        self._rebuild_columns()
        self.query_one("#detail", Static).update("type to search.")
        query = self.query_one("#search", Input).value.strip()
        if len(query) >= 2:
            self.run_search(query)

    def action_add(self) -> None:
        aggregated = self._selected()
        if aggregated is None:
            return
        session = self.remote.session
        if session is not None and not session.user.is_admin:
            self.notify("signed in as a user: w requests it instead", severity="warning")
            return
        absent = [s.instance for s in aggregated.statuses if s.state == PresenceState.NOT_PRESENT]
        if not absent:
            self.notify("every server has it already")
            return
        if len(absent) == 1:
            self.do_add(aggregated, absent[0])
            return
        options = [(name, self.remote.server_label(aggregated, name)) for name in absent]
        preselect = best_server(aggregated, self.remote.health, absent)

        def picked(name: str | None) -> None:
            if name:
                self.do_add(aggregated, name)

        self.app.push_screen(PickScreen(f"add {aggregated.result.title} to…", options, preselect), picked)

    @work(group="add")
    async def do_add(self, aggregated: AggregatedResult, instance: str) -> None:
        self.notify(f"adding to {instance}…")
        result = await add_to_instance(aggregated, instance, self.remote.config)
        if result.ok:
            self.notify(result.message)
            await self._refresh_result(aggregated)
        else:
            self.notify(result.message, severity="error", timeout=8)

    def action_request(self) -> None:
        aggregated = self._selected()
        if aggregated is None:
            return
        if not self.remote.require_session("to file a request"):
            return
        self.file_or_withdraw(aggregated)

    @work(group="request")
    async def file_or_withdraw(self, aggregated: AggregatedResult) -> None:
        session = self.remote.session
        assert session is not None
        store = session.store()
        key = aggregated.result.external_key
        try:
            pending = self.remote.own_pending.get(key)
            if pending is not None:
                await asyncio.to_thread(store.withdraw, pending.id, session.user.username)
                self.notify(f"withdrew the request for {aggregated.result.title}")
            else:
                request = await asyncio.to_thread(store.create, aggregated.result, session.user.username)
                estimate = max(
                    (estimate_add_bytes(aggregated, self.remote.health.get(s.instance)) for s in aggregated.statuses),
                    default=None,
                )
                notify_new_request(request, estimate_bytes=estimate)
                self.notify(f"requested {aggregated.result.title} — waiting for an admin")
        except Exception as exc:  # noqa: BLE001 — the queue failing must not take the screen down
            self.notify(str(exc)[:160], severity="error", timeout=8)
            return
        await self.remote.refresh_queue_counts()
        self._render_detail(aggregated)

    def action_refresh_selected(self) -> None:
        aggregated = self._selected()
        if aggregated is not None:
            self.refresh_row(aggregated)

    @work(exclusive=True, group="refresh")
    async def refresh_row(self, aggregated: AggregatedResult) -> None:
        await self._refresh_result(aggregated)
        self.notify("refreshed.")

    async def _refresh_result(self, aggregated: AggregatedResult) -> None:
        updated = await refresh_status(aggregated, self.remote.config, include_plex=bool(self.remote.config.plex))
        key = updated.result.external_key
        self.results[key] = updated
        table = self.query_one("#results", DataTable)
        for instance in self.remote.config.arr_instances(self.media_type.value):
            status = updated.status_for(instance.name)
            if status:
                try:
                    table.update_cell(key, instance.name, Text.from_markup(STATE_GLYPHS[status.state]))
                except Exception:  # noqa: BLE001 — row may be gone after a new search
                    pass
        self._render_detail(updated)


# --- the requests screen ---


class RequestsScreen(Screen[None]):
    """The approval queue: pending requests with the live server picture, and the history below."""

    BINDINGS = [
        Binding("enter,a", "approve", "approve"),
        Binding("d", "deny", "deny"),
        Binding("w", "withdraw", "withdraw"),
        Binding("r", "reload", "refresh"),
        Binding("escape", "back", "media"),
    ]
    CSS = f"""
    RequestsScreen #body {{ height: 1fr; }}
    RequestsScreen #left {{ width: 3fr; }}
    RequestsScreen #pending {{ height: 2fr; }}
    RequestsScreen #history-title {{ padding: 0 1; border-top: solid {HAIRLINE}; }}
    RequestsScreen #history {{ height: 1fr; }}
    RequestsScreen #detail-pane {{ width: 2fr; border-left: solid {HAIRLINE}; padding: 0 1; background: {SURFACE}; }}
    RequestsScreen #detail {{ height: auto; }}
    """

    def __init__(self) -> None:
        super().__init__()
        self.pending: dict[str, MediaRequest] = {}
        self.pictures: dict[str, AggregatedResult] = {}

    @property
    def remote(self) -> MediaRemote:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield HealthStrip(id="health")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield DataTable(id="pending", cursor_type="row", zebra_stripes=True)
                yield Static(section("history"), id="history-title")
                yield DataTable(id="history", cursor_type="none", zebra_stripes=True)
            with VerticalScroll(id="detail-pane"):
                yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "requests"
        pending = self.query_one("#pending", DataTable)
        for name, width in (("title", 34), ("kind", 6), ("by", 12), ("when", 12), ("note", 30)):
            pending.add_column(name, key=name, width=width)
        history = self.query_one("#history", DataTable)
        for name, width in (("status", 11), ("title", 34), ("by", 12), ("outcome", 30)):
            history.add_column(name, key=name, width=width)
        self.remote.refresh_strip()
        self.reload()

    def on_screen_resume(self) -> None:
        self.remote.refresh_strip()

    def action_reload(self) -> None:
        self.reload()

    @work(exclusive=True, group="queue")
    async def reload(self) -> None:
        session = self.remote.session
        if session is None:
            return
        store = session.store()
        self.query_one("#detail", Static).update(f"[{MUTED}]loading the queue…[/]")
        try:
            if session.user.is_admin:
                everything = await asyncio.to_thread(store.list)
            else:
                everything = await asyncio.to_thread(store.list, None, session.user.username)
        except Exception as exc:  # noqa: BLE001
            self.query_one("#detail", Static).update(f"[{RED}]could not load the queue: {str(exc)[:200]}[/]")
            return
        pending_rows = [r for r in everything if r.status == RequestStatus.PENDING]
        history_rows = [r for r in everything if r.status != RequestStatus.PENDING][:30]
        self.pending = {r.id: r for r in pending_rows}
        table = self.query_one("#pending", DataTable)
        table.clear()
        for request in pending_rows:
            kind = "show" if request.result.media_type == MediaType.TV else "movie"
            table.add_row(
                request.title_line,
                kind,
                request.requested_by,
                self._when(request.requested_at),
                Text(request.note[:60], style=RED) if request.note else "",
                key=request.id,
            )
        history = self.query_one("#history", DataTable)
        history.clear()
        for request in history_rows:
            outcome = f"→ {request.instance}" if request.instance else request.note
            history.add_row(
                Text(REQUEST_LABELS[request.status], style=REQUEST_COLOURS[request.status]),
                request.title_line,
                request.requested_by,
                outcome[:60],
                key=request.id,
            )
        who = "pending" if session.user.is_admin else "your pending"
        self.sub_title = f"requests — {len(pending_rows)} {who}, {len(history_rows)} in history"
        self.remote.pending_count = len(pending_rows) if session.user.is_admin else None
        self.remote.refresh_strip()
        if pending_rows:
            table.focus()
            self.show_request(pending_rows[0])
        else:
            empty = (
                "queue is empty." if session.user.is_admin else "nothing yet — search on the media screen and hit w."
            )
            self.query_one("#detail", Static).update(f"[{MUTED}]{empty}[/]")

    @staticmethod
    def _when(stamp: datetime) -> str:
        return stamp.astimezone().strftime("%Y-%m-%d %H:%M")

    def _selected(self) -> MediaRequest | None:
        table = self.query_one("#pending", DataTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        return self.pending.get(key) if key is not None else None

    @on(DataTable.RowHighlighted, "#pending")
    def highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None and event.row_key.value in self.pending:
            self.show_request(self.pending[event.row_key.value])

    def show_request(self, request: MediaRequest) -> None:
        self._render_request(request)
        if request.id not in self.pictures:
            self.fetch_picture(request)

    @work(group="picture")
    async def fetch_picture(self, request: MediaRequest) -> None:
        try:
            aggregated = await lookup_status(request.result, self.remote.config)
            await asyncio.gather(
                enrich_tv_statuses(aggregated, self.remote.config),
                check_plex_availability(aggregated, self.remote.config)
                if self.remote.config.plex
                else asyncio.sleep(0),
            )
        except Exception as exc:  # noqa: BLE001 — the queue must stay usable with servers down
            self.pictures[request.id] = AggregatedResult(result=request.result)
            self._render_request(request, error=str(exc)[:160])
            return
        self.pictures[request.id] = aggregated
        self._render_request(request)

    def _render_request(self, request: MediaRequest, error: str = "") -> None:
        if self._selected() is not request:
            return
        r = request.result
        kind = "show" if r.media_type == MediaType.TV else "movie"
        lines = [
            f"[bold]{request.title_line}[/]",
            f"[{MUTED}]{kind} · requested by [bold]{request.requested_by}[/] · {self._when(request.requested_at)}[/]",
        ]
        meta = meta_line(r)
        if meta:
            lines.append(f"[{MUTED}]{meta}[/]")
        if request.note:
            lines.append(f"[{RED}]{request.note}[/]")
        lines.append("")
        picture = self.pictures.get(request.id)
        if error:
            lines.append(f"[{RED}]couldn't reach the servers: {error}[/]")
        elif picture is None:
            lines.append(f"[{MUTED}]checking servers…[/]")
        else:
            lines.extend(status_lines(picture, self.remote.health))
            names = [i.name for i in self.remote.config.arr_instances(r.media_type.value)]
            best = best_server(picture, self.remote.health, names)
            lines.append("")
            if self.remote.session and self.remote.session.user.is_admin:
                if best:
                    lines.append(f"[bold]enter[/] approves onto [bold]{best}[/] (or picks) · [bold]d[/] denies")
                else:
                    lines.append(f"[{MUTED}]every server has it: approve resolves the request, d denies[/]")
            else:
                lines.append("[bold]w[/] withdraws this request")
        if r.overview:
            lines.append(f"\n[{MUTED}]{r.overview[:300]}[/]")
        self.query_one("#detail", Static).update("\n".join(lines))

    # --- verbs ---

    def action_approve(self) -> None:
        request = self._selected()
        session = self.remote.session
        if request is None or session is None:
            return
        if not session.user.is_admin:
            self.notify("only an admin approves; w withdraws your own", severity="warning")
            return
        names = [i.name for i in self.remote.config.arr_instances(request.result.media_type.value)]
        if not names:
            self.notify("no instances configured for this media type", severity="error")
            return
        picture = self.pictures.get(request.id) or AggregatedResult(result=request.result)
        options = [(name, self.remote.server_label(picture, name)) for name in names]
        preselect = best_server(picture, self.remote.health, names) or names[0]

        def picked(name: str | None) -> None:
            if name:
                self.do_approve(request, name)

        self.app.push_screen(PickScreen(f"approve {request.title_line} onto…", options, preselect), picked)

    @work(group="resolve")
    async def do_approve(self, request: MediaRequest, instance: str) -> None:
        session = self.remote.session
        assert session is not None
        self.notify(f"adding {request.result.title} to {instance}…")
        result = await fulfill_request(session.store(), request.id, instance, session.user.username, self.remote.config)
        self.notify(result.message, severity="information" if result.ok else "error", timeout=8)
        self.pictures.pop(request.id, None)
        self.reload()

    def action_deny(self) -> None:
        request = self._selected()
        session = self.remote.session
        if request is None or session is None:
            return
        if not session.user.is_admin:
            self.notify("only an admin denies; w withdraws your own", severity="warning")
            return

        def noted(note: str | None) -> None:
            if note is not None:
                self.do_deny(request, note)

        self.app.push_screen(
            NoteScreen(f"deny {request.title_line}?", "reason, shown to the requester (optional)"), noted
        )

    @work(group="resolve")
    async def do_deny(self, request: MediaRequest, note: str) -> None:
        session = self.remote.session
        assert session is not None
        try:
            await asyncio.to_thread(session.store().deny, request.id, session.user.username, note)
        except (KeyError, ValueError) as exc:
            self.notify(str(exc), severity="error")
        else:
            self.notify(f"denied {request.result.title}")
        self.reload()

    def action_withdraw(self) -> None:
        request = self._selected()
        session = self.remote.session
        if request is None or session is None:
            return
        self.do_withdraw(request)

    @work(group="resolve")
    async def do_withdraw(self, request: MediaRequest) -> None:
        session = self.remote.session
        assert session is not None
        try:
            await asyncio.to_thread(session.store().withdraw, request.id, session.user.username)
        except (KeyError, ValueError) as exc:
            self.notify(str(exc), severity="error")
        else:
            self.notify(f"withdrew {request.result.title}")
        await self.remote.refresh_queue_counts()
        self.reload()

    def action_back(self) -> None:
        self.dismiss(None)


# --- the app ---


class MediaRemote(App[None]):
    TITLE = "❯ syncplex"
    CSS = f"""
    Screen {{ background: {BG}; }}
    DataTable > .datatable--cursor {{ background: {GREEN} 35%; }}
    """
    BINDINGS = [
        Binding("ctrl+r", "requests", "requests", priority=True),
        Binding("ctrl+l", "login", "sign in/out", priority=True),
        Binding("ctrl+s", "show_sync", "drive sync", priority=True),
        Binding("f1,question_mark", "help", "help", priority=True),
        Binding("ctrl+q,ctrl+c", "quit", "quit", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(TERMINAL_NAVY)
        self.theme = "terminal-navy"
        self.config = load_media_config()
        self.health: dict[str, ServerHealth] = {}
        self.session: sessions.Session | None = sessions.load_saved() if sessions.configured() else None
        self.pending_count: int | None = None
        self.own_pending: dict[str, MediaRequest] = {}  # a user's open requests by external key

    def on_mount(self) -> None:
        self.push_screen(MediaScreen())
        self.set_interval(60.0, self.refresh_health)
        self.refresh_health()
        if self.session is not None:
            self.refresh_queue_counts_worker()

    # --- shared state ---

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {"login", "requests", "show_sync", "help"} and isinstance(self.screen, ModalScreen):
            return False
        return True

    @work(exclusive=True, group="health")
    async def refresh_health(self) -> None:
        try:
            healths = await check_all_servers(self.config)
        except Exception:  # noqa: BLE001 — a strip that says "checking" beats a crash
            return
        self.health = {h.name: h for h in healths}
        self.refresh_strip()

    def refresh_strip(self) -> None:
        # App.query walks the default screen, so this asks the ACTIVE screen;
        # the others redraw their strip when they resume.
        for strip in self.screen.query(HealthStrip):
            strip.render_health(self.health, self.pending_count, self.session.user.username if self.session else "")

    @work(group="counts")
    async def refresh_queue_counts_worker(self) -> None:
        await self.refresh_queue_counts()

    async def refresh_queue_counts(self) -> None:
        """The strip's pending badge (admins) and the user's own open requests (for the media detail)."""
        if self.session is None:
            self.pending_count = None
            self.own_pending = {}
            self.refresh_strip()
            return
        store = self.session.store()
        try:
            if self.session.user.is_admin:
                self.pending_count = await asyncio.to_thread(store.pending_count)
            else:
                mine = await asyncio.to_thread(store.list, RequestStatus.PENDING, self.session.user.username)
                self.own_pending = {r.result.external_key: r for r in mine}
        except Exception as exc:  # noqa: BLE001
            self.notify(f"queue: {str(exc)[:120]}", severity="warning")
        self.refresh_strip()

    def server_label(self, aggregated: AggregatedResult, name: str) -> str:
        """Picker text for one server: presence if it has the title, else the add's cost and the room."""
        status = aggregated.status_for(name)
        server = self.health.get(name)
        if status is not None and status.state != PresenceState.NOT_PRESENT:
            return f"{name} · {badge(status)[0]}"
        estimate = estimate_add_bytes(aggregated, server)
        label = f"{name} · ~{format_bytes(estimate)}"
        if server and server.disk_free_bytes is not None:
            room = format_bytes(server.disk_free_bytes)
            label += f" · {room} free" if estimate <= server.disk_free_bytes else f" · ⚠ only {room} free"
        if server:
            stats = stats_line(server)
            if stats:
                label += f"  ({stats})"
        return label

    # --- login ---

    def require_session(self, why: str) -> bool:
        """True when signed in; otherwise opens the login and returns False (retry after)."""
        if self.session is not None:
            return True
        if not sessions.configured():
            self.notify("no request queue configured (POSTGREST_URL / AUTH_URL)", severity="warning")
            return False
        self.push_screen(LoginScreen(why), self._signed_in)
        return False

    def _signed_in(self, session: sessions.Session | None) -> None:
        if session is None:
            return
        self.session = session
        self.notify(f"signed in as {session.user.username}" + (" (admin)" if session.user.is_admin else ""))
        self.refresh_queue_counts_worker()

    def action_login(self) -> None:
        if self.session is not None:
            sessions.clear()
            name = self.session.user.username
            self.session = None
            self.pending_count = None
            self.own_pending = {}
            self.refresh_strip()
            self.notify(f"signed out {name}")
            return
        self.require_session("")

    # --- navigation ---

    def action_requests(self) -> None:
        self.open_requests()

    def open_requests(self) -> None:
        if isinstance(self.screen, RequestsScreen):
            return
        if not self.require_session("to see the request queue"):
            return

        def _refresh_counts(_result: None) -> None:
            self.refresh_queue_counts_worker()

        self.push_screen(RequestsScreen(), _refresh_counts)

    def action_show_sync(self) -> None:
        from drive_sync.screens import FolderScreen  # drive-sync deps stay out of the web image

        self.push_screen(FolderScreen())

    def action_help(self) -> None:
        self.push_screen(HelpScreen())


def run_tui() -> None:
    MediaRemote().run()
