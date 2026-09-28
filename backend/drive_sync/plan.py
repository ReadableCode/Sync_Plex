"""What a drive should hold versus what it holds, as a plan of downloads and
deletes grouped per title. Pure: no printing, no prompting, no transfers.

The rules carried over from the original scraper:
- a show contributes its next N episodes (unwatched only unless the entry
  says otherwise), walked in season order;
- a movie contributes its one file;
- the file chosen per episode/movie is the first Plex media part whose
  quality label matches the config's preference order, else the first part;
- the drive layout is <root>/<TV|Movies>/<title>/<file> with the "Season NN"
  folder dropped unless retain_folder_structure is set;
- anything under TV/ or Movies/ that no entry wants is deleted.
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path

from drive_sync.drive_config import DriveConfig
from drive_sync.library import PlexLibrary, fuzzy_find


def plex_server_host() -> str:
    """The host part of PLEX_SERVER (http://host:32400 -> host)."""
    server = os.environ["PLEX_SERVER"]
    match = re.search(r"https?://([^:/]+)", server)
    if not match:
        raise ValueError(f"could not parse a host from PLEX_SERVER={server!r}")
    return match.group(1)


def source_root(os_name: str | None = None) -> str:
    """Where the server's /data media root is reachable from here: the SMB share
    on Windows, the server-side path rsync-over-ssh reads elsewhere."""
    os_name = os_name or platform.system()
    if os_name == "Windows":
        return f"\\\\{plex_server_host()}\\Media"
    return os.environ.get("PLEX_SERVER_MEDIA_PATH", "/Media")


def server_mapped_path(part_file: str, root: str) -> str:
    """A Plex part's file path (under the server's /data) as a path under `root`."""
    normalized = os.path.normpath(part_file)
    return normalized.replace("\\data", root).replace("/data", root)


def dest_path_for(server_path: str, root: str, folder: str, retain_folder_structure: bool, os_name: str) -> str:
    """The drive path for a server file: swap the root, drop the Season folder."""
    dest = server_path.replace(root, folder)
    if retain_folder_structure:
        return dest
    separator = "\\" if os_name == "Windows" else os.sep
    parts = dest.split(separator)
    for index, part in enumerate(parts):
        if part.startswith("Season "):
            parts.pop(index)
            break
    if os_name == "Windows":
        return "\\".join(parts)
    return os.sep + os.path.join(*parts)


def best_part(media_items: list[dict], quality_pref: list[str]) -> tuple[str, float, str]:
    """(part file, size in GB, quality label) of the preferred part, else the first one."""
    for quality in quality_pref:
        for media in media_items:
            for part in media.get("Part", []):
                if part.get("title", "").lower() == quality.lower():
                    return part.get("file", ""), part.get("size", 0) / 1e9, part.get("title", "")
    for media in media_items:
        for part in media.get("Part", []):
            return part.get("file", ""), part.get("size", 0) / 1e9, part.get("title", "")
    raise ValueError("a Plex item with no media parts")


@dataclass(frozen=True)
class DesiredFile:
    kind: str  # show | movie
    title: str  # the config's name for it
    label: str  # "S01 · 3 · Pilot" for an episode, the Plex title for a movie
    server_path: str
    dest_path: str
    size_gb: float
    quality: str
    view_count: int


@dataclass(frozen=True)
class Missing:
    kind: str
    name: str
    suggestions: tuple[str, ...]  # closest Plex titles, best first


@dataclass
class Action:
    op: str  # download | delete | synced
    dest_path: str
    size_gb: float
    kind: str = ""
    title: str = ""
    label: str = ""
    server_path: str = ""
    status: str = "pending"  # pending | running | done | failed | cancelled
    error: str = ""


@dataclass
class TitlePlan:
    kind: str
    name: str
    wanted: int  # files the entry asks for (0 when missing on Plex)
    on_drive: int
    downloads: list[Action] = field(default_factory=list)
    deletes: list[Action] = field(default_factory=list)
    missing: Missing | None = None

    @property
    def download_gb(self) -> float:
        return sum(a.size_gb for a in self.downloads)

    @property
    def delete_gb(self) -> float:
        return sum(a.size_gb for a in self.deletes)


@dataclass
class Plan:
    folder: str
    titles: list[TitlePlan]
    actions: list[Action]  # deletes first, then downloads: the run order
    free_gb: float | None

    @property
    def downloads(self) -> list[Action]:
        return [a for a in self.actions if a.op == "download"]

    @property
    def deletes(self) -> list[Action]:
        return [a for a in self.actions if a.op == "delete"]

    @property
    def download_gb(self) -> float:
        return sum(a.size_gb for a in self.downloads)

    @property
    def delete_gb(self) -> float:
        return sum(a.size_gb for a in self.deletes)

    @property
    def net_gb(self) -> float:
        return self.download_gb - self.delete_gb

    @property
    def missing(self) -> list[Missing]:
        return [t.missing for t in self.titles if t.missing]

    @property
    def fits(self) -> bool | None:
        return None if self.free_gb is None else self.net_gb <= self.free_gb

    @property
    def is_clean(self) -> bool:
        return not self.actions_needed and not self.missing

    @property
    def actions_needed(self) -> list[Action]:
        return [a for a in self.actions if a.op != "synced"]


# --- desired side ---


def desired_files(
    config: DriveConfig, library: PlexLibrary, folder: str, os_name: str | None = None
) -> tuple[list[DesiredFile], list[Missing]]:
    os_name = os_name or platform.system()
    root = source_root(os_name)
    wanted: list[DesiredFile] = []
    missing: list[Missing] = []

    for movie in config.movies:
        item = library.find("movie", movie.name)
        if item is None:
            missing.append(Missing("movie", movie.name, _suggest("movie", movie.name, library)))
            continue
        file, size_gb, quality = best_part(item.raw.get("Media", []), config.quality_profile_pref)
        server_path = server_mapped_path(file, root)
        wanted.append(
            DesiredFile(
                "movie",
                movie.name,
                item.title,
                server_path,
                dest_path_for(server_path, root, folder, config.retain_folder_structure, os_name),
                size_gb,
                quality,
                int(item.raw.get("viewCount", 0)),
            )
        )

    for show in config.shows:
        item = library.find("show", show.name)
        if item is None:
            missing.append(Missing("show", show.name, _suggest("show", show.name, library)))
            continue
        added = 0
        for season_title, episode in library.episodes(item):
            if added >= show.num_next_episodes:
                break
            views = int(episode.get("viewCount", 0))
            if views > 0 and show.only_get_unwatched:
                continue
            file, size_gb, quality = best_part(episode.get("Media", []), config.quality_profile_pref)
            server_path = server_mapped_path(file, root)
            wanted.append(
                DesiredFile(
                    "show",
                    show.name,
                    f"{season_title} · {episode.get('index', '?')} · {episode.get('title', '')}",
                    server_path,
                    dest_path_for(server_path, root, folder, config.retain_folder_structure, os_name),
                    size_gb,
                    quality,
                    views,
                )
            )
            added += 1
    return wanted, missing


def _suggest(kind: str, name: str, library: PlexLibrary, limit: int = 3) -> tuple[str, ...]:
    return tuple(item.match_name for item in fuzzy_find(name, library.titles(kind), limit))


# --- drive side ---


def existing_files(folder: str) -> dict[str, float]:
    """{path: size in GB} for every file under TV/ and Movies/ (macOS ._ sidecars skipped)."""
    found: dict[str, float] = {}
    for sub in ("TV", "Movies"):
        for root_dir, _dirs, files in os.walk(os.path.join(folder, sub)):
            for name in files:
                if name.startswith("._"):
                    continue
                path = os.path.join(root_dir, name)
                try:
                    found[path] = os.path.getsize(path) / 1e9
                except OSError:
                    continue
    return found


def free_gb(folder: str) -> float | None:
    try:
        import shutil

        return shutil.disk_usage(folder).free / 1e9
    except OSError:
        return None


# --- the plan ---


def build_plan(
    config: DriveConfig,
    wanted: list[DesiredFile],
    missing: list[Missing],
    folder: str,
    existing: dict[str, float],
    free: float | None,
) -> Plan:
    by_title: dict[tuple[str, str], TitlePlan] = {}
    for show in config.shows:
        by_title[("show", show.name)] = TitlePlan("show", show.name, 0, 0)
    for movie in config.movies:
        by_title[("movie", movie.name)] = TitlePlan("movie", movie.name, 0, 0)
    for miss in missing:
        by_title[(miss.kind, miss.name)].missing = miss

    actions: list[Action] = []
    wanted_paths = {w.dest_path for w in wanted}
    for w in wanted:
        title = by_title[(w.kind, w.title)]
        title.wanted += 1
        if w.dest_path in existing:
            title.on_drive += 1
            actions.append(
                Action("synced", w.dest_path, existing[w.dest_path], w.kind, w.title, w.label, w.server_path)
            )
        else:
            action = Action("download", w.dest_path, w.size_gb, w.kind, w.title, w.label, w.server_path)
            title.downloads.append(action)
            actions.append(action)
    strays: list[Action] = []
    for path, size in sorted(existing.items()):
        if path in wanted_paths:
            continue
        action = Action("delete", path, size, label=os.path.relpath(path, folder))
        owner = _owner_title(path, folder, by_title)
        if owner is not None:
            action.kind, action.title = owner.kind, owner.name
            owner.deletes.append(action)
        strays.append(action)
    if strays:
        # files no entry accounts for: shown under one "other files on drive" row
        unowned = [a for a in strays if not a.title]
        if unowned:
            other = TitlePlan("other", "other files on drive", 0, len(unowned))
            other.deletes = unowned
            by_title[("other", "other files on drive")] = other
    downloads = [a for a in actions if a.op == "download"]
    synced = [a for a in actions if a.op == "synced"]
    return Plan(folder, list(by_title.values()), strays + downloads + synced, free)


def _owner_title(path: str, folder: str, by_title: dict[tuple[str, str], TitlePlan]) -> TitlePlan | None:
    """A stray file belongs to the entry whose title folder holds it (TV/<title>/...)."""
    rel = os.path.relpath(path, folder).split(os.sep)
    if len(rel) < 3:
        return None
    kind = "show" if rel[0] == "TV" else "movie"
    for (entry_kind, name), title in by_title.items():
        if entry_kind == kind and rel[1] == name:
            return title
    for (entry_kind, name), title in by_title.items():
        if entry_kind == kind and rel[1].split(" (")[0] == name:
            return title
    return None


def make_plan(config: DriveConfig, library: PlexLibrary, folder: str | Path, os_name: str | None = None) -> Plan:
    """Config + Plex + the drive's contents -> the plan. The one call the UI and --check make."""
    folder = str(folder)
    wanted, missing = desired_files(config, library, folder, os_name)
    return build_plan(config, wanted, missing, folder, existing_files(folder), free_gb(folder))
