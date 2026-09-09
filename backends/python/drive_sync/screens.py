"""The drive-sync TUI: pick a drive folder, see what its config wants against
what it holds, fix or extend the config (fuzzy search against Plex), then run
the sync with per-file progress. Screens, not an App, so the media remote can
push them onto its own app and the standalone entry point wraps them thinly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Input, OptionList, ProgressBar, RichLog, Static
from textual.widgets.option_list import Option

from drive_sync.drive_config import ConfigError, DriveConfig
from drive_sync.editor import find_editor
from drive_sync.library import LibraryTitle, PlexLibrary, fuzzy_find
from drive_sync.plan import Action, Plan, TitlePlan, make_plan
from drive_sync.transfer import SyncRunner
from engine.media.tui.theme import AMBER_BRIGHT, GREEN_BRIGHT, HAIRLINE, MUTED, RED

DRIVES = None  # the "current folder" on Windows when listing drive letters


def default_start() -> Path:
    """~/Media when it exists (the syncdrive shell function's default), else home."""
    media = Path.home() / "Media"
    return media if media.is_dir() else Path.home()


def _roots() -> list[Path]:
    if sys.platform == "win32":
        return [Path(drive) for drive in os.listdrives()]
    return [Path("/")]


def _subfolders(folder: Path) -> list[Path]:
    """Visible subfolders, case-insensitive order; unreadable entries are skipped, not fatal."""
    try:
        entries = sorted(os.scandir(folder), key=lambda entry: entry.name.lower())
    except OSError:
        return []
    found = []
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=True) and not entry.name.startswith("."):
                found.append(Path(entry.path))
        except OSError:
            continue
    return found


def gb(value: float) -> str:
    return f"{value:.2f} GB"


# --- small modals ---


class ConfirmScreen(ModalScreen[bool]):
    """A question with enter/y for yes, escape/n for no."""

    BINDINGS = [
        Binding("enter,y", "yes", "yes"),
        Binding("escape,n", "no", "no"),
    ]
    CSS = f"""
    ConfirmScreen {{ align: center middle; }}
    ConfirmScreen > Vertical {{
        width: 70; height: auto; border: solid {HAIRLINE}; background: $surface; padding: 1 2;
    }}
    ConfirmScreen #question {{ margin-bottom: 1; }}
    ConfirmScreen #keys {{ color: {MUTED}; }}
    """

    def __init__(self, question: str, danger: bool = False) -> None:
        super().__init__()
        self.question = question
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Vertical():
            colour = RED if self.danger else AMBER_BRIGHT
            yield Static(f"[{colour}]{self.question}[/]", id="question")
            yield Static("enter / y  yes      escape / n  no", id="keys")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class FuzzyPickScreen(ModalScreen[str | None]):
    """Type part of a title, pick the Plex match. Returns the config name for it."""

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("down", "move(1)", show=False),
        Binding("up", "move(-1)", show=False),
    ]
    CSS = f"""
    FuzzyPickScreen {{ align: center middle; }}
    FuzzyPickScreen > Vertical {{
        width: 80%; height: 80%; border: solid {HAIRLINE}; background: $surface; padding: 0 1;
    }}
    FuzzyPickScreen #title {{ padding: 1 1 0 1; color: {GREEN_BRIGHT}; text-style: bold; }}
    FuzzyPickScreen #matches {{ height: 1fr; border: none; }}
    FuzzyPickScreen #hint {{ color: {MUTED}; padding: 0 1; }}
    """

    def __init__(self, kind: str, titles: list[LibraryTitle], taken: set[str], query: str = "") -> None:
        super().__init__()
        self.kind = kind
        self.titles = titles
        self.taken = taken
        self.initial_query = query
        self.matches: list[LibraryTitle] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            what = "show" if self.kind == "show" else "movie"
            yield Static(f"which {what}? ({len(self.titles)} on plex)", id="title")
            yield Input(value=self.initial_query, placeholder="type part of the title…", id="query")
            yield OptionList(id="matches")
            yield Static("enter picks the highlighted match · escape cancels", id="hint")

    def on_mount(self) -> None:
        self.refilter(self.initial_query)
        self.query_one("#query", Input).focus()

    @on(Input.Changed, "#query")
    def query_changed(self, event: Input.Changed) -> None:
        self.refilter(event.value)

    def refilter(self, query: str) -> None:
        self.matches = fuzzy_find(query, self.titles)
        options = self.query_one("#matches", OptionList)
        options.clear_options()
        for item in self.matches:
            label = item.title
            if item.match_name in self.taken:
                label += f"  [{MUTED}]already on this drive[/]"
            options.add_option(Option(label, id=item.rating_key))
        if not self.matches:
            options.add_option(Option(f"[{MUTED}]nothing on plex matches[/]", disabled=True))
        else:
            options.highlighted = 0

    @on(Input.Submitted, "#query")
    def query_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.pick_highlighted()

    @on(OptionList.OptionSelected, "#matches")
    def option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.pick(event.option.id)

    def action_move(self, step: int) -> None:
        options = self.query_one("#matches", OptionList)
        if options.option_count == 0:
            return
        current = options.highlighted or 0
        options.highlighted = max(0, min(options.option_count - 1, current + step))

    def pick_highlighted(self) -> None:
        options = self.query_one("#matches", OptionList)
        if options.highlighted is None or not self.matches:
            return
        self.pick(options.get_option_at_index(options.highlighted).id)

    def pick(self, rating_key: str | None) -> None:
        for item in self.matches:
            if item.rating_key == rating_key:
                self.dismiss(item.match_name)
                return

    def action_cancel(self) -> None:
        self.dismiss(None)


# --- the folder browser ---


class FolderScreen(Screen[None]):
    """ncdu-style: one folder's subfolders at a time; space opens the drive screen for the folder you are in."""

    BINDINGS = [
        Binding("right,l", "open", "open"),
        Binding("backspace,left,h", "up", "up"),
        Binding("space,s", "choose", "sync this folder"),
        Binding("slash,r", "roots", "roots"),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("escape,q", "cancel", "back"),
    ]
    CSS = f"""
    FolderScreen #where {{ padding: 0 2; color: {GREEN_BRIGHT}; text-style: bold; }}
    FolderScreen #hint {{ padding: 0 2 1 2; color: {MUTED}; }}
    FolderScreen OptionList {{ height: 1fr; border: none; padding: 0 1; }}
    """

    def __init__(self, start: Path | None = None) -> None:
        super().__init__()
        self.folder: Path | None = start or default_start()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="where")
        yield Static(
            "enter opens the folder, space syncs the one you are in; folders already holding a config.yaml are marked",
            id="hint",
        )
        yield OptionList(id="folders")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "pick the drive's media folder"
        self.show(self.folder)

    def show(self, folder: Path | None, highlight: Path | None = None) -> None:
        self.folder = folder
        options = self.query_one("#folders", OptionList)
        options.clear_options()
        if folder is DRIVES:
            self.query_one("#where", Static).update("drives")
            children = _roots()
        else:
            marker = f"  [{AMBER_BRIGHT}]config.yaml here[/]" if DriveConfig.exists(folder) else ""
            self.query_one("#where", Static).update(f"{folder}{marker}")
            children = _subfolders(folder)
        if not children:
            options.add_option(Option(f"[{MUTED}](no subfolders)[/]", disabled=True))
        for child in children:
            label = (child.name or str(child)) + "/"
            if DriveConfig.exists(child):
                label += f"  [{AMBER_BRIGHT}]config.yaml[/]"
            options.add_option(Option(label, id=str(child)))
        target = 0
        for index, child in enumerate(children):
            if highlight is not None and child == highlight:
                target = index
        if children:
            options.highlighted = target
        options.focus()

    def _highlighted(self) -> Path | None:
        options = self.query_one("#folders", OptionList)
        if options.highlighted is None:
            return None
        option = options.get_option_at_index(options.highlighted)
        return Path(option.id) if option.id else None

    def action_open(self) -> None:
        child = self._highlighted()
        if child is not None:
            self.show(child)

    @on(OptionList.OptionSelected, "#folders")
    def option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id:
            self.show(Path(event.option.id))

    def action_up(self) -> None:
        if self.folder is DRIVES:
            return
        parent = self.folder.parent
        if parent == self.folder:
            if sys.platform == "win32":
                self.show(DRIVES, highlight=self.folder)
            return
        self.show(parent, highlight=self.folder)

    def action_roots(self) -> None:
        self.show(DRIVES if sys.platform == "win32" else Path("/"))

    def action_cursor_down(self) -> None:
        self.query_one("#folders", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#folders", OptionList).action_cursor_up()

    def action_choose(self) -> None:
        if self.folder is DRIVES:
            self.notify("open a drive first", severity="warning")
            return
        self.app.push_screen(DriveScreen(self.folder))

    def action_cancel(self) -> None:
        self.dismiss(None)


# --- the drive: config against contents ---


class DriveScreen(Screen[None]):
    """One drive: every configured title with what Plex has, what the drive has, and what a sync would do."""

    BINDINGS = [
        Binding("enter,s", "sync", "sync"),
        Binding("a", "add_show", "add show"),
        Binding("m", "add_movie", "add movie"),
        Binding("f", "fix", "fix title"),
        Binding("d", "remove", "remove"),
        Binding("plus,equals_sign", "episodes(1)", "+episodes"),
        Binding("minus", "episodes(-1)", "-episodes"),
        Binding("e", "edit", "edit config"),
        Binding("c", "create", show=False),
        Binding("r", "reload", "reload"),
        Binding("j", "row(1)", show=False),
        Binding("k", "row(-1)", show=False),
        Binding("escape,q", "back", "back"),
    ]
    CSS = f"""
    DriveScreen #summary {{ padding: 0 2; }}
    DriveScreen #problem {{ padding: 0 2; color: {RED}; }}
    DriveScreen #titles {{ height: 1fr; }}
    DriveScreen #hint {{ padding: 0 2; color: {MUTED}; }}
    """

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.folder = Path(folder)
        self.config: DriveConfig | None = None
        self.library = PlexLibrary()
        self.plan: Plan | None = None
        self.problem: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("loading…", id="summary")
        yield Static("", id="problem")
        table: DataTable = DataTable(id="titles", cursor_type="row", zebra_stripes=True)
        yield table
        yield Static("", id="hint")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = str(self.folder)
        table = self.query_one("#titles", DataTable)
        for name, width in (
            ("kind", 6),
            ("title", 40),
            ("keep", 5),
            ("plex", 12),
            ("on drive", 9),
            ("get", 16),
            ("remove", 16),
        ):
            table.add_column(name, key=name, width=width)
        self.reload()

    # --- loading ---

    def action_reload(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.query_one("#summary", Static).update(f"[{MUTED}]reading config, asking plex, scanning the drive…[/]")
        self.query_one("#problem", Static).update("")
        self.load_plan()

    @work(thread=True, exclusive=True, group="plan")
    def load_plan(self) -> None:
        try:
            config = DriveConfig.load(self.folder)
        except ConfigError as exc:
            self.app.call_from_thread(self.show_problem, str(exc), missing_config=not DriveConfig.exists(self.folder))
            return
        try:
            plan = make_plan(config, self.library, self.folder)
        except Exception as exc:  # plex unreachable, env missing: show it, keep the screen usable
            self.app.call_from_thread(self.show_problem, f"could not plan: {exc}", config=config)
            return
        self.app.call_from_thread(self.show_plan, config, plan)

    def show_problem(self, message: str, missing_config: bool = False, config: DriveConfig | None = None) -> None:
        self.config = config
        self.plan = None
        self.problem = message
        self.query_one("#titles", DataTable).clear()
        self.query_one("#summary", Static).update("")
        self.query_one("#problem", Static).update(message)
        if missing_config:
            self.query_one("#hint", Static).update("c creates an empty config.yaml here · escape picks another folder")
        else:
            self.query_one("#hint", Static).update("e opens the config in an editor · r retries · escape goes back")

    def show_plan(self, config: DriveConfig, plan: Plan) -> None:
        self.config = config
        self.plan = plan
        self.problem = ""
        table = self.query_one("#titles", DataTable)
        table.clear()
        for title in plan.titles:
            table.add_row(*self._row(title), key=f"{title.kind}|{title.name}")
        self.query_one("#summary", Static).update(self._summary(plan))
        missing = len(plan.missing)
        hint = "enter syncs · a/m add a show/movie · d removes · +/- episodes to keep · e edits the file"
        if missing:
            hint = f"[{RED}]{missing} title(s) are not on plex: f on the row picks the right one[/] · " + hint
        self.query_one("#hint", Static).update(hint)
        table.focus()

    def _row(self, title: TitlePlan) -> list:
        if title.kind == "other":
            return [
                "",
                Text(title.name, style=MUTED),
                "",
                "",
                str(title.on_drive),
                "",
                f"{len(title.deletes)} · {gb(title.delete_gb)}",
            ]
        keep = ""
        if title.kind == "show" and self.config is not None:
            keep = next((str(s.num_next_episodes) for s in self.config.shows if s.name == title.name), "")
        if title.missing:
            suggestion = title.missing.suggestions[0] if title.missing.suggestions else ""
            plex = Text("not on plex", style=f"bold {RED}")
            name = Text(title.name, style=RED)
            if suggestion:
                name.append(f"  → {suggestion}?", style=MUTED)
            return [title.kind, name, keep, plex, "", "", ""]
        get = f"{len(title.downloads)} · {gb(title.download_gb)}" if title.downloads else ""
        remove = f"{len(title.deletes)} · {gb(title.delete_gb)}" if title.deletes else ""
        plex = Text(f"{title.wanted} file(s)", style=GREEN_BRIGHT)
        return [title.kind, title.name, keep, plex, str(title.on_drive), get, remove]

    def _summary(self, plan: Plan) -> str:
        free = "free space unknown" if plan.free_gb is None else f"free {gb(plan.free_gb)}"
        if plan.is_clean:
            return f"[{GREEN_BRIGHT}]drive matches its config[/] · {free}"
        parts = [
            f"get [bold]{len(plan.downloads)}[/] ({gb(plan.download_gb)})",
            f"remove [bold]{len(plan.deletes)}[/] ({gb(plan.delete_gb)})",
            f"net {gb(plan.net_gb)}",
            free,
        ]
        if plan.fits is False:
            parts.append(f"[bold {RED}]does not fit[/]")
        return " · ".join(parts)

    # --- selection helpers ---

    def _selected(self) -> TitlePlan | None:
        if self.plan is None:
            return None
        table = self.query_one("#titles", DataTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        for title in self.plan.titles:
            if f"{title.kind}|{title.name}" == key:
                return title
        return None

    def action_row(self, step: int) -> None:
        table = self.query_one("#titles", DataTable)
        if table.row_count:
            table.move_cursor(row=max(0, min(table.row_count - 1, table.cursor_row + step)))

    def _save_and_reload(self, note: str) -> None:
        assert self.config is not None
        self.config.save()
        self.notify(note)
        self.reload()

    # --- config verbs ---

    def action_add_show(self) -> None:
        self._add("show")

    def action_add_movie(self) -> None:
        self._add("movie")

    def _add(self, kind: str) -> None:
        if self.config is None:
            self.notify("no config loaded", severity="warning")
            return
        taken = self.config.show_names() if kind == "show" else self.config.movie_names()

        def picked(name: str | None) -> None:
            if not name or self.config is None:
                return
            added = self.config.add_show(name) if kind == "show" else self.config.add_movie(name)
            if not added:
                self.notify(f"{name} is already on this drive's config", severity="warning")
                return
            self._save_and_reload(f"added {name}")

        self.app.push_screen(FuzzyPickScreen(kind, self.library.titles(kind), taken), picked)

    def action_fix(self) -> None:
        title = self._selected()
        if title is None or self.config is None:
            return
        if not title.missing:
            self.notify(f"{title.name} is on plex already", severity="information")
            return
        taken = self.config.show_names() if title.kind == "show" else self.config.movie_names()
        query = title.missing.suggestions[0] if title.missing.suggestions else title.name

        def picked(name: str | None) -> None:
            if not name or self.config is None:
                return
            if self.config.rename(title.kind, title.name, name):
                self._save_and_reload(f"{title.name} → {name}")
            else:
                self.config.remove(title.kind, title.name)
                self._save_and_reload(f"{name} was already configured; dropped {title.name}")

        self.app.push_screen(FuzzyPickScreen(title.kind, self.library.titles(title.kind), taken, query), picked)

    def action_remove(self) -> None:
        title = self._selected()
        if title is None or self.config is None or title.kind == "other":
            return

        def decided(yes: bool | None) -> None:
            if yes and self.config is not None and self.config.remove(title.kind, title.name):
                self._save_and_reload(f"removed {title.name}; its files go on the next sync")

        self.app.push_screen(ConfirmScreen(f"remove {title.name} from this drive's config?"), decided)

    def action_episodes(self, step: int) -> None:
        title = self._selected()
        if title is None or self.config is None or title.kind != "show":
            return
        current = next((s.num_next_episodes for s in self.config.shows if s.name == title.name), None)
        if current is None:
            return
        self.config.set_episodes(title.name, current + step)
        self._save_and_reload(f"{title.name}: keep {max(1, current + step)} episode(s)")

    def action_create(self) -> None:
        if DriveConfig.exists(self.folder):
            return
        DriveConfig.starter(self.folder).save()
        self.notify("created config.yaml; a and m add titles")
        self.reload()

    def action_edit(self) -> None:
        if not DriveConfig.exists(self.folder):
            self.notify("no config.yaml here yet: c creates one", severity="warning")
            return
        editor = find_editor()
        if editor is None:
            self.notify(
                "no editor found: install VS Code (code on PATH), set $EDITOR, or install nvim/vim", severity="error"
            )
            return
        path = str(DriveConfig.config_path(self.folder))
        if editor.gui:
            subprocess.Popen([*editor.command, path])
            self.notify(f"opened config.yaml in {Path(editor.command[0]).name}; save there, then r reloads")
            return
        with self.app.suspend():
            subprocess.run([*editor.command, path])
        self.reload()

    # --- sync ---

    def action_sync(self) -> None:
        if self.plan is None:
            return
        if not self.plan.actions_needed:
            self.notify("nothing to do: the drive matches its config")
            return
        warnings = []
        if self.plan.missing:
            warnings.append(f"{len(self.plan.missing)} title(s) are not on plex and will be skipped")
        if self.plan.fits is False:
            warnings.append(f"the downloads need {gb(self.plan.net_gb)} more than the drive has free")
        plan = self.plan

        def start(yes: bool | None) -> None:
            if yes:
                self.app.push_screen(SyncScreen(plan), lambda _result: self.reload())

        question = (
            f"remove {len(plan.deletes)} file(s) ({gb(plan.delete_gb)}) and download {len(plan.downloads)} "
            f"({gb(plan.download_gb)})?"
        )
        if warnings:
            question = " · ".join(warnings) + "\n\n" + question
        self.app.push_screen(ConfirmScreen(question, danger=bool(plan.deletes)), start)

    def action_back(self) -> None:
        self.dismiss(None)


# --- the sync run ---


class SyncScreen(Screen[None]):
    """Deletes then downloads, one row per file with live progress; escape asks to stop."""

    BINDINGS = [Binding("escape,q", "leave", "stop / back")]
    CSS = f"""
    SyncScreen #overall {{ padding: 0 2; }}
    SyncScreen ProgressBar {{ padding: 0 2; width: 100%; }}
    SyncScreen #files {{ height: 2fr; }}
    SyncScreen #log {{ height: 1fr; border-top: solid {HAIRLINE}; padding: 0 1; }}
    """

    def __init__(self, plan: Plan) -> None:
        super().__init__()
        self.plan = plan
        self.runner: SyncRunner | None = None
        self.finished = False
        self.total_bytes = max(1, int(plan.download_gb * 1e9))
        self.done_bytes: dict[str, int] = {}
        self._lock = threading.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="overall")
        yield ProgressBar(total=self.total_bytes, show_eta=True)
        yield DataTable(id="files", cursor_type="row", zebra_stripes=True)
        yield RichLog(id="log", wrap=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f"syncing {self.plan.folder}"
        table = self.query_one("#files", DataTable)
        for name, width in (("op", 8), ("title", 30), ("file", 40), ("size", 10), ("status", 14)):
            table.add_column(name, key=name, width=width)
        for action in self.plan.actions_needed:
            table.add_row(action.op, action.title, action.label, gb(action.size_gb), "pending", key=action.dest_path)
        self.update_overall()
        self.run_sync()

    @work(thread=True, exclusive=True, group="sync")
    def run_sync(self) -> None:
        self.runner = SyncRunner(self.plan, self.report_progress, self.report_event)
        self.runner.run()
        self.app.call_from_thread(self.finish)

    # called from the worker thread
    def report_progress(self, action: Action, done: int, total: int) -> None:
        with self._lock:
            self.done_bytes[action.dest_path] = done
        percent = int(done * 100 / total) if total else 0
        self.app.call_from_thread(self.set_status, action, f"{percent}%")
        self.app.call_from_thread(self.update_overall)

    def report_event(self, action: Action, state: str) -> None:
        if state == "done" and action.op == "download":
            with self._lock:
                self.done_bytes[action.dest_path] = int(action.size_gb * 1e9)
        label = {"running": "running…", "done": "done", "failed": "failed", "cancelled": "cancelled"}[state]
        self.app.call_from_thread(self.set_status, action, label)
        self.app.call_from_thread(self.update_overall)
        if state == "failed":
            self.app.call_from_thread(self.write_log, f"[{RED}]{action.op} {action.label} failed: {action.error}[/]")
        elif state == "done":
            self.app.call_from_thread(self.write_log, f"[{GREEN_BRIGHT}]{action.op}[/] {action.label}")

    # on the UI thread
    def set_status(self, action: Action, text: str) -> None:
        style = {"done": GREEN_BRIGHT, "failed": RED, "cancelled": AMBER_BRIGHT}.get(text, "")
        self.query_one("#files", DataTable).update_cell(action.dest_path, "status", Text(text, style=style))

    def write_log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def update_overall(self) -> None:
        actions = self.plan.actions_needed
        finished = sum(1 for a in actions if a.status in ("done", "failed", "cancelled"))
        with self._lock:
            done = sum(self.done_bytes.values())
        self.query_one(ProgressBar).update(progress=min(done, self.total_bytes))
        failed = sum(1 for a in actions if a.status == "failed")
        text = f"{finished}/{len(actions)} files · {gb(done / 1e9)} of {gb(self.total_bytes / 1e9)} downloaded"
        if failed:
            text += f" · [{RED}]{failed} failed[/]"
        self.query_one("#overall", Static).update(text)

    def finish(self) -> None:
        self.finished = True
        actions = self.plan.actions_needed
        done = sum(1 for a in actions if a.status == "done")
        failed = sum(1 for a in actions if a.status == "failed")
        cancelled = sum(1 for a in actions if a.status == "cancelled")
        colour = RED if failed else (AMBER_BRIGHT if cancelled else GREEN_BRIGHT)
        self.write_log(f"[{colour}]finished: {done} done, {failed} failed, {cancelled} cancelled[/] · escape goes back")
        self.sub_title = f"finished {self.plan.folder}"

    def action_leave(self) -> None:
        if self.finished or self.runner is None:
            self.dismiss(None)
            return

        def decided(yes: bool | None) -> None:
            if yes and self.runner is not None:
                self.runner.cancel()
                self.write_log(f"[{AMBER_BRIGHT}]stopping after the current file…[/]")

        self.app.push_screen(ConfirmScreen("stop the sync? the file in flight is abandoned", danger=True), decided)
