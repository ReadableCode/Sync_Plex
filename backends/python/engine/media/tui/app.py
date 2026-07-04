"""syncplex TUI — a thin Textual layer over the engine package.

The media screen wires user actions to engine/media core functions; the sync
screen lists the legacy selective-sync jobs (engine.sync_jobs) and can hand
off to src/selective_sync.py. Styling follows the readablecode
"terminal navy" design system (dotfiles design/STYLE.md).
"""

import asyncio

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static, Tree

from ...config import REPO_ROOT
from ...sync_jobs import load_sync_jobs
from ..aggregation import (
    add_to_instance,
    check_plex_availability,
    enrich_tv_statuses,
    search_everywhere,
)
from ..config import load_media_config
from ..health import format_bytes
from ..models import AggregatedResult, MediaType, PresenceState

# terminal-navy tokens (dotfiles design/tokens.css)
BG = "#0d1420"
SURFACE = "#121b2a"
SURFACE_2 = "#182333"
HAIRLINE = "#273141"  # --border rgba(148,163,184,.16) flattened onto --surface
GRID = "#1c2739"
INK = "#dbe4f0"
INK_2 = "#9fb0c3"
MUTED = "#7d8b9e"
GREEN = "#2ea043"
GREEN_BRIGHT = "#56d364"
AMBER = "#b8860b"
AMBER_BRIGHT = "#e3b341"
RED = "#f87171"

TERMINAL_NAVY = Theme(
    name="terminal-navy",
    primary=GREEN,
    secondary=AMBER,
    accent=GREEN_BRIGHT,
    warning=AMBER_BRIGHT,
    error=RED,
    success=GREEN,
    foreground=INK,
    background=BG,
    surface=SURFACE,
    panel=SURFACE_2,
    dark=True,
    variables={
        "border": GREEN,
        "border-blurred": HAIRLINE,
        "footer-key-foreground": GREEN_BRIGHT,
        "block-cursor-foreground": INK,
        "block-cursor-background": GRID,
        "block-cursor-blurred-foreground": INK_2,
        "block-cursor-blurred-background": SURFACE_2,
        "block-hover-background": SURFACE_2,
        "input-selection-background": f"{GREEN} 35%",
    },
)

STATE_GLYPHS = {
    PresenceState.MONITORED_COMPLETE: f"[{GREEN_BRIGHT}]●[/]",
    PresenceState.MONITORED_INCOMPLETE: f"[{AMBER_BRIGHT}]◐[/]",
    PresenceState.NOT_PRESENT: f"[{MUTED}]○[/]",
    PresenceState.UNREACHABLE: f"[{RED}]✗[/]",
}

STATE_LABELS = {
    PresenceState.MONITORED_COMPLETE: "complete",
    PresenceState.MONITORED_INCOMPLETE: "incomplete",
    PresenceState.NOT_PRESENT: "not present",
    PresenceState.UNREACHABLE: "unreachable",
}


class ConfirmSyncScreen(ModalScreen[bool]):
    """Explicit confirmation before handing off to the destructive legacy script."""

    BINDINGS = [("escape", "dismiss(False)", "cancel")]

    CSS = f"""
    ConfirmSyncScreen {{
        align: center middle;
        background: {BG} 60%;
    }}
    #confirm-box {{
        width: 70;
        height: auto;
        padding: 1 2;
        background: {SURFACE};
        border: solid {AMBER_BRIGHT};
    }}
    #confirm-box Button {{
        margin-top: 1;
        margin-right: 2;
    }}
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(
                f"[bold {AMBER_BRIGHT}]run ALL sync jobs?[/]\n\n"
                f"this hands off to [bold]src/selective_sync.py[/], which mirrors every "
                f"configured source (robocopy /MIR) and then [bold {RED}]prunes[/] anything "
                f"not in the configuration from the destinations. every job in "
                f"sync_config.json runs — there is no per-job selection."
            )
            with Horizontal():
                yield Button("run all jobs", variant="error", id="confirm-run")
                yield Button("cancel", id="confirm-cancel")

    @on(Button.Pressed, "#confirm-run")
    def confirm_run(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-cancel")
    def confirm_cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(False)


class SyncScreen(Screen):
    """Selective-sync jobs from the root sync_config.json (legacy script stays the engine)."""

    BINDINGS = [
        ("escape", "back", "media"),
        ("R", "run_sync", "run all jobs"),
    ]

    CSS = f"""
    SyncScreen #sync-jobs {{
        height: 1fr;
        padding: 0 1;
        background: $background;
    }}
    SyncScreen #sync-empty {{
        height: 1fr;
        padding: 1 2;
        color: {MUTED};
    }}
    SyncScreen #sync-log {{
        height: 12;
        border-top: solid {HAIRLINE};
        background: $surface;
        padding: 0 1;
    }}
    """

    def __init__(self) -> None:
        super().__init__()
        self.jobs = load_sync_jobs()
        self._sync_running = False

    def compose(self) -> ComposeResult:
        yield Header()
        if self.jobs:
            yield Tree("sync jobs", id="sync-jobs")
        else:
            yield Static("no sync_config.json found", id="sync-empty")
        yield RichLog(id="sync-log", wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        count = len(self.jobs)
        self.sub_title = f"sync — {count} job{'s' if count != 1 else ''}" if self.jobs else "sync"
        log = self.query_one(RichLog)
        log.write(Text("R runs ALL jobs via src/selective_sync.py (mirror + prune).", style=MUTED))
        if not self.jobs:
            return
        tree = self.query_one(Tree)
        tree.show_root = False
        for job in self.jobs:
            node = tree.root.add(
                Text.assemble(
                    (job.sync_name, "bold"),
                    "  ",
                    (f"{job.src_display} → {job.dest_display}", MUTED),
                ),
                expand=True,
            )
            for subfolder in job.included_subfolders:
                node.add_leaf(Text("\\".join(subfolder)))
            for file_parts in job.included_files:
                node.add_leaf(Text.assemble(("file  ", MUTED), "\\".join(file_parts)))
            if not job.included_subfolders and not job.included_files:
                node.add_leaf(Text("(nothing included)", style=MUTED))
        tree.focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_run_sync(self) -> None:
        if self._sync_running:
            self.notify("sync already running.", severity="warning")
            return

        def _decided(confirmed: bool | None) -> None:
            if confirmed:
                self.run_all_jobs()

        self.app.push_screen(ConfirmSyncScreen(), _decided)

    @work(exclusive=True, group="sync-run")
    async def run_all_jobs(self) -> None:
        self._sync_running = True
        log = self.query_one(RichLog)
        cmd = ["uv", "run", "--project", str(REPO_ROOT), "python", "src/selective_sync.py"]
        log.write(Text("$ " + " ".join(cmd), style=f"bold {GREEN_BRIGHT}"))
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(REPO_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert process.stdout is not None
            while line := await process.stdout.readline():
                log.write(line.decode(errors="replace").rstrip())
            code = await process.wait()
        except OSError as exc:  # e.g. uv missing from PATH
            log.write(Text(f"✗ failed to launch: {exc}", style=RED))
        else:
            style, mark = (GREEN_BRIGHT, "✓") if code == 0 else (RED, "✗")
            log.write(Text(f"{mark} exited with status {code}", style=style))
        finally:
            self._sync_running = False


class MediaRemote(App):
    """Search once, see status across every instance, add where you choose."""

    TITLE = "❯ syncplex"

    CSS = f"""
    Header {{
        background: $panel;
        color: $text;
    }}
    #search {{
        /* not docked: docking overlaps the Header, hiding the title bar */
        margin: 0 1;
    }}
    #body {{
        height: 1fr;
    }}
    #results {{
        width: 2fr;
    }}
    #detail-pane {{
        width: 1fr;
        border-left: solid {HAIRLINE};
        padding: 0 1;
        background: $surface;
    }}
    #detail {{
        height: auto;
    }}
    #actions Button {{
        width: 100%;
        margin-top: 1;
    }}
    #actions Button.-success {{
        border: none;
        background: {GREEN};
        color: {BG};
        text-style: bold;
    }}
    #actions Button.-success:hover {{
        background: {GREEN_BRIGHT};
        color: {BG};
    }}
    """

    BINDINGS = [
        ("q", "quit", "quit"),
        ("t", "toggle_type", "tv/movie"),
        ("r", "refresh_selected", "refresh"),
        ("ctrl+s", "show_sync", "sync"),
        ("escape", "focus_search", "search"),
    ]

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # media-screen actions (and opening sync) only apply on the base screen
        if action in {"toggle_type", "refresh_selected", "focus_search", "show_sync"}:
            return self.screen is self.screen_stack[0]
        return True

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(TERMINAL_NAVY)
        self.theme = "terminal-navy"
        self.media_type = MediaType.TV
        self.config = load_media_config()
        self.results: dict[str, AggregatedResult] = {}
        self._search_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="search shows… (t toggles tv/movie)", id="search")
        with Horizontal(id="body"):
            yield DataTable(id="results", cursor_type="row")
            with VerticalScroll(id="detail-pane"):
                yield Static("type to search.", id="detail")
                yield Vertical(id="actions")
        yield Footer()

    def on_mount(self) -> None:
        self._update_subtitle()
        table = self.query_one(DataTable)
        table.add_column("title", key="title", width=40)
        table.add_column("year", key="year", width=6)
        for instance in self.config.arr_instances(self.media_type.value):
            table.add_column(instance.name, key=instance.name)
        for warning in self.config.warnings:
            self.notify(warning, severity="warning", timeout=8)
        self.query_one(Input).focus()

    def _update_subtitle(self) -> None:
        instances = self.config.arr_instances(self.media_type.value)
        self.sub_title = f"{self.media_type.value} — {len(instances)} instances, {len(self.config.plex)} plex"

    def _rebuild_columns(self) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_column("title", key="title", width=40)
        table.add_column("year", key="year", width=6)
        for instance in self.config.arr_instances(self.media_type.value):
            table.add_column(instance.name, key=instance.name)

    # --- search -------------------------------------------------------------

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
        self.query_one(DataTable).focus()

    @work(exclusive=True, group="search")
    async def run_search(self, query: str) -> None:
        results = await search_everywhere(query, self.media_type, self.config)
        self.results = {r.result.external_key: r for r in results[:20]}

        table = self.query_one(DataTable)
        table.clear()
        for key, aggregated in self.results.items():
            row = [aggregated.result.title, str(aggregated.result.year or "")]
            for instance in self.config.arr_instances(self.media_type.value):
                status = aggregated.status_for(instance.name)
                row.append(STATE_GLYPHS[status.state] if status else "?")
            table.add_row(*row, key=key)
        if self.results:
            table.focus()
        else:
            self.query_one("#detail", Static).update("no results.")

    # --- detail / plex ------------------------------------------------------

    @on(DataTable.RowHighlighted)
    def show_detail(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None or event.row_key.value not in self.results:
            return
        aggregated = self.results[event.row_key.value]
        self._render_detail(aggregated)
        self.enrich_detail(aggregated)
        self.check_plex(aggregated)

    @work(exclusive=True, group="enrich")
    async def enrich_detail(self, aggregated: AggregatedResult) -> None:
        """Lookup responses lack per-season statistics — refetch the real series records."""
        if any(s.series_id for s in aggregated.statuses):
            await enrich_tv_statuses(aggregated, self.config)
            self._render_detail(aggregated)

    @work(exclusive=True, group="plex")
    async def check_plex(self, aggregated: AggregatedResult) -> None:
        if not self.config.plex or aggregated.plex:
            return
        await check_plex_availability(aggregated, self.config)
        self._render_detail(aggregated)

    def _render_detail(self, aggregated: AggregatedResult) -> None:
        r = aggregated.result
        lines = [f"[bold]{r.title}[/] ({r.year or '?'})"]
        meta = " · ".join(
            x
            for x in (
                r.network,
                r.status,
                f"{r.season_count} seasons" if r.season_count else "",
                f"{r.runtime}m" if r.runtime else "",
            )
            if x
        )
        if meta:
            lines.append(f"[{MUTED}]{meta}[/]")
        if r.genres:
            lines.append(f"[{MUTED}]{', '.join(r.genres[:4])}[/]")
        lines.append("")
        lines.append(f"[{GREEN_BRIGHT}]//[/] instances")
        for status in aggregated.statuses:
            glyph = STATE_GLYPHS[status.state]
            label = STATE_LABELS[status.state]
            if status.state == PresenceState.MONITORED_INCOMPLETE and status.missing_episode_count is None:
                label = "not downloaded"  # movie without a file
            line = f"{glyph} {status.instance}: {label}"
            if status.state == PresenceState.MONITORED_INCOMPLETE and status.missing_episode_count is not None:
                line += f" — missing {status.missing_episode_count}/{status.total_episode_count} eps"
            if status.size_on_disk:
                line += f" · {format_bytes(status.size_on_disk)}"
            lines.append(line)
            for season in sorted(status.seasons, key=lambda s: (s.season_number == 0, s.season_number)):
                label = "SP" if season.season_number == 0 else f"S{season.season_number}"
                mark = f"[{GREEN_BRIGHT}]✓[/]" if season.monitored else f"[{MUTED}]✗[/]"
                denominator = season.total_episode_count or season.episode_count
                counts = f"{season.episode_file_count}/{denominator}" if denominator else "—"
                size = f"  [{MUTED}]{format_bytes(season.size_on_disk)}[/]" if season.size_on_disk else ""
                lines.append(f"    {mark} {label:<4} {counts}{size}")
        if aggregated.plex:
            lines.append("")
            lines.append(f"[{GREEN_BRIGHT}]//[/] plex")
            for plex in aggregated.plex:
                glyph = f"[{GREEN_BRIGHT}]▶[/]" if plex.available else f"[{MUTED}]·[/]"
                note = "watch-ready" if plex.available else ("error" if plex.error else "not in Plex")
                lines.append(f"{glyph} {plex.server}: {note}")
        elif self.config.plex:
            lines.append(f"\n[{MUTED}]checking plex…[/]")
        if r.overview:
            lines.append(f"\n[{MUTED}]{r.overview[:400]}[/]")
        self.query_one("#detail", Static).update("\n".join(lines))
        self._render_actions(aggregated)

    def _render_actions(self, aggregated: AggregatedResult) -> None:
        actions = self.query_one("#actions", Vertical)
        actions.remove_children()
        for status in aggregated.statuses:
            if status.state == PresenceState.NOT_PRESENT:
                button = Button(f"add to {status.instance}", variant="success")
                button.instance_name = status.instance  # type: ignore[attr-defined]
                button.result_key = aggregated.result.external_key  # type: ignore[attr-defined]
                actions.mount(button)

    # --- actions ------------------------------------------------------------

    @on(Button.Pressed)
    def add_pressed(self, event: Button.Pressed) -> None:
        instance = getattr(event.button, "instance_name", None)
        key = getattr(event.button, "result_key", None)
        if instance and key and key in self.results:
            event.button.disabled = True
            self.do_add(self.results[key], instance)

    @work(group="add")
    async def do_add(self, aggregated: AggregatedResult, instance: str) -> None:
        result = await add_to_instance(aggregated, instance, self.config)
        if result.ok:
            self.notify(result.message)
            await self._refresh_result(aggregated)
        else:
            self.notify(result.message, severity="error", timeout=8)

    def action_toggle_type(self) -> None:
        self.media_type = MediaType.MOVIE if self.media_type == MediaType.TV else MediaType.TV
        self._update_subtitle()
        self.results = {}
        self._rebuild_columns()
        self.query_one("#detail", Static).update("type to search.")
        self.query_one("#actions", Vertical).remove_children()
        query = self.query_one(Input).value.strip()
        if len(query) >= 2:
            self.run_search(query)

    def action_refresh_selected(self) -> None:
        table = self.query_one(DataTable)
        if table.cursor_row is None or not self.results:
            return
        row_key = list(self.results)[table.cursor_row] if table.cursor_row < len(self.results) else None
        if row_key:
            self.refresh_row(self.results[row_key])

    @work(exclusive=True, group="refresh")
    async def refresh_row(self, aggregated: AggregatedResult) -> None:
        await self._refresh_result(aggregated)
        self.notify("refreshed.")

    async def _refresh_result(self, aggregated: AggregatedResult) -> None:
        from ..aggregation import refresh_status

        updated = await refresh_status(aggregated, self.config, include_plex=bool(self.config.plex))
        key = updated.result.external_key
        self.results[key] = updated

        table = self.query_one(DataTable)
        for instance in self.config.arr_instances(self.media_type.value):
            status = updated.status_for(instance.name)
            if status:
                try:
                    table.update_cell(key, instance.name, STATE_GLYPHS[status.state])
                except Exception:  # noqa: BLE001 — row may be gone after a new search
                    pass
        self._render_detail(updated)

    def action_focus_search(self) -> None:
        self.query_one(Input).focus()

    def action_show_sync(self) -> None:
        self.push_screen(SyncScreen())


def run_tui() -> None:
    MediaRemote().run()
