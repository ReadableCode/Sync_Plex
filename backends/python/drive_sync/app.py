"""The standalone drive-sync app: a thin shell around the screens."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding

from drive_sync.screens import DriveScreen, FolderScreen
from engine.media.tui.theme import TERMINAL_NAVY


class DriveSyncApp(App[None]):
    TITLE = "syncplex drive sync"
    BINDINGS = [Binding("ctrl+q", "quit", "quit", priority=True)]

    def __init__(self, folder: Path | None = None) -> None:
        super().__init__()
        self.folder = folder

    def on_mount(self) -> None:
        self.register_theme(TERMINAL_NAVY)
        self.theme = "terminal-navy"
        if self.folder is not None:
            self.push_screen(DriveScreen(self.folder), lambda _result: self.exit())
        else:
            self.push_screen(FolderScreen(), lambda _result: self.exit())
