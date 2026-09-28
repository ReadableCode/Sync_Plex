"""What the Plex server has: show and movie titles for matching and fuzzy
search, plus the per-show episode walk the planner needs. A thin layer over
plex_api_wrapper (which caches every response for the process lifetime) so
the planner and the TUI never touch raw JSON."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from drive_sync import plex_api_wrapper as plex


@dataclass(frozen=True)
class LibraryTitle:
    kind: str  # "show" | "movie"
    title: str  # as Plex names it ("Zootopia (2016)" for movies)
    match_name: str  # what a config names: movies drop the " (year)" suffix
    rating_key: str
    raw: dict  # the Plex metadata record (Media parts, viewCount ...)


class PlexLibrary:
    """Titles come from the two library sections; episodes are fetched per show on demand."""

    def __init__(self) -> None:
        self._shows: list[LibraryTitle] | None = None
        self._movies: list[LibraryTitle] | None = None

    def shows(self) -> list[LibraryTitle]:
        if self._shows is None:
            data = plex.get_dict_plex_show_data()
            records = data.get("MediaContainer", {}).get("Metadata", []) if isinstance(data, dict) else []
            self._shows = [LibraryTitle("show", r["title"], r["title"], str(r["ratingKey"]), r) for r in records]
        return self._shows

    def movies(self) -> list[LibraryTitle]:
        if self._movies is None:
            data = plex.get_dict_plex_movie_data()
            records = data.get("MediaContainer", {}).get("Metadata", []) if isinstance(data, dict) else []
            self._movies = [
                LibraryTitle("movie", r["title"], r["title"].split(" (")[0], str(r["ratingKey"]), r) for r in records
            ]
        return self._movies

    def titles(self, kind: str) -> list[LibraryTitle]:
        return self.shows() if kind == "show" else self.movies()

    def find(self, kind: str, name: str) -> LibraryTitle | None:
        """Exact match on the config's naming (a movie's name is its title without the year)."""
        for item in self.titles(kind):
            if item.match_name == name:
                return item
        return None

    def episodes(self, show: LibraryTitle) -> list[tuple[str, dict]]:
        """Every episode of a show in season order as (season title, episode record)."""
        out = []
        for season in plex.get_seasons_data_for_show_id(show.rating_key):
            for episode in plex.get_episode_data_for_season_key(season["ratingKey"]):
                out.append((season["title"], episode))
        return out


def fuzzy_score(query: str, title: str) -> float:
    """Substring and prefix hits rank first, then difflib similarity; 0 means no relation."""
    q, t = query.lower().strip(), title.lower()
    if not q:
        return 0.0
    if t.startswith(q):
        return 2.0 + len(q) / max(len(t), 1)
    if q in t:
        return 1.0 + len(q) / max(len(t), 1)
    ratio = SequenceMatcher(None, q, t).ratio()
    return ratio if ratio >= 0.5 else 0.0


def fuzzy_find(query: str, titles: list[LibraryTitle], limit: int = 30) -> list[LibraryTitle]:
    """Best matches for a query, most likely first; everything when the query is empty."""
    if not query.strip():
        return sorted(titles, key=lambda item: item.title.lower())[:limit]
    scored = [(fuzzy_score(query, item.match_name), item) for item in titles]
    ranked = sorted((pair for pair in scored if pair[0] > 0), key=lambda pair: (-pair[0], pair[1].title.lower()))
    return [item for _, item in ranked[:limit]]
