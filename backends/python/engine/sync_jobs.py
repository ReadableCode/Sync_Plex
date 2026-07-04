"""Pure config-reading layer over the legacy root ``sync_config.json``.

The legacy selective-sync script (``src/selective_sync.py``) stays the
execution engine — this module only parses the config so the TUI can list
jobs. Rendering mirrors how the legacy script joins path components.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO_ROOT

DEFAULT_CONFIG_PATH = REPO_ROOT / "sync_config.json"


def render_path(path_type: str, parts: list[str]) -> str:
    """Join path components the way ``src/selective_sync.py`` does."""
    joined = "\\".join(parts)
    if path_type == "Windows Network Mount":
        return "\\\\" + joined
    # "Windows Local Drive" (e.g. I:\Media\...) and unknown types: plain
    # backslash join — the drive letter component already carries its colon.
    return joined


@dataclass
class SyncJob:
    """One entry of ``sync_folders`` in sync_config.json."""

    sync_name: str
    src_path_type: str
    src_path: list[str]
    dest_path_type: str
    dest_path: list[str]
    included_subfolders: list[list[str]] = field(default_factory=list)
    included_files: list[list[str]] = field(default_factory=list)

    @property
    def src_display(self) -> str:
        return render_path(self.src_path_type, self.src_path)

    @property
    def dest_display(self) -> str:
        return render_path(self.dest_path_type, self.dest_path)


def load_sync_jobs(path: Path | None = None) -> list[SyncJob]:
    """Read sync jobs from ``sync_config.json``; missing/invalid file → []."""
    config_path = path if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    folders = raw.get("sync_folders") if isinstance(raw, dict) else None
    if not isinstance(folders, list):
        return []

    jobs: list[SyncJob] = []
    for entry in folders:
        if not isinstance(entry, dict):
            continue
        jobs.append(
            SyncJob(
                sync_name=str(entry.get("sync_name") or "(unnamed)"),
                src_path_type=str(entry.get("src_path_type") or ""),
                src_path=[str(p) for p in entry.get("src_path") or []],
                dest_path_type=str(entry.get("dest_path_type") or ""),
                dest_path=[str(p) for p in entry.get("dest_path") or []],
                included_subfolders=[[str(p) for p in sub] for sub in entry.get("included_subfolders") or []],
                included_files=[[str(p) for p in file] for file in entry.get("included_files") or []],
            )
        )
    return jobs
