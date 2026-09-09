"""Folder picker for the drive-sync destination, navigated like ncdu.

One folder's subfolders at a time: enter opens the highlighted folder,
backspace goes up, space syncs the folder you are standing in. Runs when
`syncplex-drive-sync` gets no path, which is how cmdr's `syncdrive` command
reaches it (cmdr passes a command no arguments). Folders already carrying a
config.yaml are marked, since those are the drives this tool has synced.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

from engine.media.tui.theme import AMBER_BRIGHT, GREEN_BRIGHT, MUTED, TERMINAL_NAVY

DRIVES = None  # the "current folder" on Windows when listing drive letters


@dataclass(frozen=True)
class Editor:
    command: list[str]
    gui: bool  # a window: launch and return; a terminal editor suspends the picker until it exits


def find_editor(environ: dict[str, str] | None = None, which=shutil.which) -> Editor | None:
    """VS Code when its `code` command is on PATH, else the user's own $VISUAL /
    $EDITOR (split like a shell would, so "code --wait" or "emacs -nw" work),
    else nvim, else vim. None when nothing is there."""
    environ = os.environ if environ is None else environ
    if which("code"):
        return Editor([which("code")], gui=True)
    for var in ("VISUAL", "EDITOR"):
        words = shlex.split(environ.get(var, ""))
        if words and which(words[0]):
            return Editor([which(words[0]), *words[1:]], gui=False)
    for name in ("nvim", "vim"):
        if which(name):
            return Editor([which(name)], gui=False)
    return None


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


class DirectoryPicker(App[str | None]):
    """Returns the chosen folder path, or None when cancelled."""

    TITLE = "syncplex drive sync"
    BINDINGS = [
        Binding("right,l", "open", "open"),
        Binding("backspace,left,h", "up", "up"),
        Binding("space,s", "choose", "sync this folder"),
        Binding("e", "edit", "edit config"),
        Binding("slash,r", "roots", "roots"),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("escape,q", "cancel", "cancel"),
    ]
    CSS = f"""
    #where {{ padding: 0 2; color: {GREEN_BRIGHT}; text-style: bold; }}
    #hint {{ padding: 0 2 1 2; color: {MUTED}; }}
    OptionList {{ height: 1fr; border: none; padding: 0 1; }}
    """

    def __init__(self, start: Path) -> None:
        super().__init__()
        self.folder: Path | None = start

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="where")
        yield Static(
            "enter opens the folder, space syncs the one you are in, e edits its config.yaml; "
            "folders already holding one are marked",
            id="hint",
        )
        yield OptionList(id="folders")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(TERMINAL_NAVY)
        self.theme = "terminal-navy"
        self.show(self.folder)

    # --- navigation ---

    def show(self, folder: Path | None, highlight: Path | None = None) -> None:
        self.folder = folder
        options = self.query_one("#folders", OptionList)
        options.clear_options()
        if folder is DRIVES:
            self.sub_title = "drives"
            self.query_one("#where", Static).update("drives")
            children = _roots()
        else:
            self.sub_title = str(folder)
            marker = f"  [{AMBER_BRIGHT}]config.yaml here[/]" if (folder / "config.yaml").is_file() else ""
            self.query_one("#where", Static).update(f"{folder}{marker}")
            children = _subfolders(folder)
        if not children:
            options.add_option(Option(f"[{MUTED}](no subfolders)[/]", disabled=True))
        for child in children:
            label = child.name or str(child)
            if (child / "config.yaml").is_file():
                label += f"/  [{AMBER_BRIGHT}]config.yaml[/]"
            else:
                label += "/"
            options.add_option(Option(label, id=str(child)))
        target = 0
        if highlight is not None:
            for index, child in enumerate(children):
                if child == highlight:
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

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id:
            self.show(Path(event.option.id))

    def action_up(self) -> None:
        if self.folder is DRIVES:
            return
        parent = self.folder.parent
        if parent == self.folder:  # a filesystem root
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

    # --- outcome ---

    def action_choose(self) -> None:
        if self.folder is DRIVES:
            self.notify("open a drive first", severity="warning")
            return
        self.exit(str(self.folder))

    def action_edit(self) -> None:
        """Open this folder's config.yaml in an editor (see find_editor for the order)."""
        if self.folder is DRIVES:
            self.notify("open a drive first", severity="warning")
            return
        config = self.folder / "config.yaml"
        if not config.is_file():
            self.notify(
                "no config.yaml here yet: sync this folder and the tool offers to create a starter one",
                severity="warning",
            )
            return
        editor = find_editor()
        if editor is None:
            self.notify(
                "no editor found: install VS Code (code on PATH), set $EDITOR, or install nvim/vim", severity="error"
            )
            return
        if editor.gui:
            subprocess.Popen([*editor.command, str(config)])
            self.notify(f"opened {config.name} in {Path(editor.command[0]).name}; save it there, then sync")
            return
        with self.suspend():
            subprocess.run([*editor.command, str(config)])
        self.show(self.folder)

    def action_cancel(self) -> None:
        self.exit(None)


def pick_directory(start: Path | None = None) -> str | None:
    """Open the picker on a terminal; the chosen folder, or None when cancelled."""
    return DirectoryPicker(start or default_start()).run()


if __name__ == "__main__":
    # `python -m drive_sync.pick_dir [start]` prints the choice: a way to try the
    # picker on its own, and what the pty test drives.
    chosen = pick_directory(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    if chosen is None:
        sys.exit(1)
    print(chosen)
