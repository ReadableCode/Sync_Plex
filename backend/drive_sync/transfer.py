"""Moving the files: deletes, then downloads with byte progress, in the
plan's order. rsync over ssh on macOS/Linux (parsing its --progress lines,
which both rsync and macOS openrsync print), a chunked copy off the SMB
share on Windows. Cancellable between files and mid-transfer."""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass

from drive_sync.plan import Action, Plan, plex_server_host

Progress = Callable[[Action, int, int], None]  # (action, bytes done, bytes total)
Event = Callable[[Action, str], None]  # (action, "running" | "done" | "failed" | "cancelled")

PROGRESS_LINE = re.compile(r"(\d[\d,]*)\s+(\d{1,3})%")


def parse_progress(line: str) -> tuple[int, int] | None:
    """(bytes so far, percent) from an rsync --progress line, else None."""
    match = PROGRESS_LINE.search(line)
    if not match:
        return None
    return int(match.group(1).replace(",", "")), int(match.group(2))


@dataclass
class TransferError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class SyncRunner:
    """Runs a plan's pending actions; `cancel()` stops after the current byte."""

    def __init__(self, plan: Plan, on_progress: Progress, on_event: Event, os_name: str | None = None) -> None:
        self.plan = plan
        self.on_progress = on_progress
        self.on_event = on_event
        self.os_name = os_name or platform.system()
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        self._cancel.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        for action in self.plan.actions_needed:
            if self._cancel.is_set():
                action.status = "cancelled"
                self.on_event(action, "cancelled")
                continue
            action.status = "running"
            self.on_event(action, "running")
            try:
                if action.op == "delete":
                    os.remove(action.dest_path)
                else:
                    self.copy(action)
            except Exception as exc:  # one failed file must not stop the rest
                if self._cancel.is_set():
                    action.status = "cancelled"
                    self.on_event(action, "cancelled")
                    continue
                action.status, action.error = "failed", str(exc)
                self.on_event(action, "failed")
                continue
            action.status = "done"
            self.on_event(action, "done")
        for sub in ("TV", "Movies"):
            path = os.path.join(self.plan.folder, sub)
            if os.path.isdir(path):
                remove_empty_dirs(path)

    def copy(self, action: Action) -> None:
        os.makedirs(os.path.dirname(action.dest_path), exist_ok=True)
        total = int(action.size_gb * 1e9)
        if self.os_name == "Windows":
            self._copy_share(action, total)
        else:
            self._copy_rsync(action, total)

    def _copy_share(self, action: Action, total: int) -> None:
        done = 0
        with open(action.server_path, "rb") as src, open(action.dest_path, "wb") as dest:
            while True:
                if self._cancel.is_set():
                    raise TransferError("cancelled")
                chunk = src.read(8 * 1024 * 1024)
                if not chunk:
                    break
                dest.write(chunk)
                done += len(chunk)
                self.on_progress(action, done, total)

    def _copy_rsync(self, action: Action, total: int) -> None:
        user = os.environ.get("PLEX_SSH_USER")
        if not user:
            raise TransferError("PLEX_SSH_USER is not set: rsync over ssh needs the server login")
        # The remote shell word-splits the path (spaces, apostrophes), so it is
        # quoted; RSYNC_OLD_ARGS keeps rsync >= 3.2.4 from escaping it again.
        remote = f"{user}@{plex_server_host()}:{shlex.quote(action.server_path)}"
        self._proc = subprocess.Popen(
            ["rsync", "-a", "--progress", "-e", "ssh", remote, action.dest_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, "RSYNC_OLD_ARGS": "1"},
            text=True,
            bufsize=1,
        )
        tail: list[str] = []
        assert self._proc.stdout is not None
        for raw in _lines(self._proc.stdout):
            tail = (tail + [raw.strip()])[-5:]
            parsed = parse_progress(raw)
            if parsed:
                done, percent = parsed
                self.on_progress(action, done, total or int(done * 100 / max(percent, 1)))
        code = self._proc.wait()
        self._proc = None
        if code != 0:
            raise TransferError(f"rsync exited {code}: " + " | ".join(line for line in tail if line))
        self.on_progress(action, total, total)


def _lines(stream):
    """rsync updates progress with \\r, not \\n: split on both."""
    buffer = ""
    while True:
        char = stream.read(1)
        if not char:
            if buffer:
                yield buffer
            return
        if char in "\r\n":
            if buffer:
                yield buffer
            buffer = ""
        else:
            buffer += char


def remove_empty_dirs(root: str) -> None:
    for dirpath, _dirs, _files in os.walk(root, topdown=False):
        if dirpath != root and not os.listdir(dirpath):
            os.rmdir(dirpath)


def disk_free_gb(folder: str) -> float | None:
    try:
        return shutil.disk_usage(folder).free / 1e9
    except OSError:
        return None
