"""A drive's config.yaml: the shows and movies it should hold.

One file per drive at its media root. Loaded into a small dataclass, edited
by the TUI (add, remove, rename, episode counts) and written back in the same
key order the file has always had, so a hand edit and a TUI edit look alike.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_NAME = "config.yaml"
DEFAULT_QUALITY_PREF = ["original", "optimized for mobile"]
DEFAULT_NEXT_EPISODES = 3


class ConfigError(Exception):
    """config.yaml is missing, unreadable, or not shaped like a drive config."""


@dataclass
class ShowEntry:
    name: str
    num_next_episodes: int = DEFAULT_NEXT_EPISODES
    only_get_unwatched: bool = True


@dataclass
class MovieEntry:
    name: str


@dataclass
class DriveConfig:
    path: Path
    shows: list[ShowEntry] = field(default_factory=list)
    movies: list[MovieEntry] = field(default_factory=list)
    quality_profile_pref: list[str] = field(default_factory=lambda: list(DEFAULT_QUALITY_PREF))
    retain_folder_structure: bool = False

    # --- io ---

    @classmethod
    def config_path(cls, folder: Path) -> Path:
        return Path(folder) / CONFIG_NAME

    @classmethod
    def exists(cls, folder: Path) -> bool:
        return cls.config_path(folder).is_file()

    @classmethod
    def starter(cls, folder: Path) -> DriveConfig:
        """A fresh config with nothing wanted yet; the TUI adds titles to it."""
        return cls(path=cls.config_path(folder))

    @classmethod
    def load(cls, folder: Path) -> DriveConfig:
        path = cls.config_path(folder)
        if not path.is_file():
            raise ConfigError(f"no {CONFIG_NAME} in {folder}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must be a mapping with shows: and movies:, got {type(raw).__name__}")
        config = cls(path=path)
        config.shows = [_show_entry(item, path) for item in _entries(raw, "shows", path)]
        config.movies = [_movie_entry(item, path) for item in _entries(raw, "movies", path)]
        pref = raw.get("quality_profile_pref")
        if pref is not None:
            if not isinstance(pref, list):
                raise ConfigError(f"{path}: quality_profile_pref must be a list")
            config.quality_profile_pref = [
                item["quality_profile"] if isinstance(item, dict) else str(item) for item in pref
            ]
        config.retain_folder_structure = bool(raw.get("retain_folder_structure", False))
        return config

    def to_dict(self) -> dict:
        shows = []
        for show in self.shows:
            item = {"name": show.name, "num_next_episodes": show.num_next_episodes}
            if not show.only_get_unwatched:
                item["only_get_unwatched"] = False
            shows.append(item)
        return {
            "shows": shows,
            "movies": [{"name": movie.name} for movie in self.movies],
            "quality_profile_pref": [{"quality_profile": name} for name in self.quality_profile_pref],
            "retain_folder_structure": self.retain_folder_structure,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.dump(self.to_dict(), sort_keys=False, allow_unicode=True), encoding="utf-8")

    # --- edits (the TUI's verbs) ---

    def show_names(self) -> set[str]:
        return {show.name for show in self.shows}

    def movie_names(self) -> set[str]:
        return {movie.name for movie in self.movies}

    def add_show(self, name: str, num_next_episodes: int = DEFAULT_NEXT_EPISODES) -> bool:
        if name in self.show_names():
            return False
        self.shows.append(ShowEntry(name, num_next_episodes))
        return True

    def add_movie(self, name: str) -> bool:
        if name in self.movie_names():
            return False
        self.movies.append(MovieEntry(name))
        return True

    def remove(self, kind: str, name: str) -> bool:
        before = len(self.shows) + len(self.movies)
        if kind == "show":
            self.shows = [show for show in self.shows if show.name != name]
        else:
            self.movies = [movie for movie in self.movies if movie.name != name]
        return len(self.shows) + len(self.movies) < before

    def rename(self, kind: str, old: str, new: str) -> bool:
        """Point an entry at another title (the fix for 'not on plex'); keeps its episode
        count. False when nothing is named `old` or `new` is already configured."""
        entries = self.shows if kind == "show" else self.movies
        if any(entry.name == new for entry in entries):
            return False
        for entry in entries:
            if entry.name == old:
                entry.name = new
                return True
        return False

    def set_episodes(self, name: str, num_next_episodes: int) -> bool:
        for show in self.shows:
            if show.name == name:
                show.num_next_episodes = max(1, num_next_episodes)
                return True
        return False


def _entries(raw: dict, key: str, path: Path) -> list:
    value = raw.get(key) or []
    if not isinstance(value, list):
        raise ConfigError(f"{path}: {key} must be a list of entries with a name")
    return value


def _show_entry(item, path: Path) -> ShowEntry:
    if isinstance(item, str):
        return ShowEntry(item)
    if not isinstance(item, dict) or "name" not in item:
        raise ConfigError(f"{path}: every show needs a name, got {item!r}")
    try:
        episodes = int(item.get("num_next_episodes", DEFAULT_NEXT_EPISODES))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path}: show {item['name']!r} has a non-numeric num_next_episodes") from exc
    return ShowEntry(str(item["name"]), max(1, episodes), bool(item.get("only_get_unwatched", True)))


def _movie_entry(item, path: Path) -> MovieEntry:
    if isinstance(item, str):
        return MovieEntry(item)
    if not isinstance(item, dict) or "name" not in item:
        raise ConfigError(f"{path}: every movie needs a name, got {item!r}")
    return MovieEntry(str(item["name"]))
