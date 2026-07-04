from enum import Enum

from pydantic import BaseModel, Field


class MediaType(str, Enum):
    TV = "tv"
    MOVIE = "movie"


class PresenceState(str, Enum):
    NOT_PRESENT = "not_present"
    MONITORED_INCOMPLETE = "monitored_incomplete"
    MONITORED_COMPLETE = "monitored_complete"
    UNREACHABLE = "unreachable"


class MediaSearchResult(BaseModel):
    """Normalized search hit from a Sonarr/Radarr lookup."""

    media_type: MediaType
    title: str
    year: int | None = None
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    overview: str = ""
    poster_url: str = ""
    network: str = ""  # TV network (sonarr) or studio (radarr)
    status: str = ""  # continuing / ended / released / ...
    genres: list[str] = Field(default_factory=list)
    runtime: int | None = None  # minutes
    season_count: int | None = None

    @property
    def external_key(self) -> str:
        """Stable merge key — external ID when available, title/year otherwise."""
        if self.media_type == MediaType.TV and self.tvdb_id:
            return f"tvdb:{self.tvdb_id}"
        if self.media_type == MediaType.MOVIE and self.tmdb_id:
            return f"tmdb:{self.tmdb_id}"
        return f"title:{self.title.casefold()}:{self.year or 0}"


class SeasonDetail(BaseModel):
    """Per-season monitoring/availability on one instance (TV only)."""

    season_number: int
    monitored: bool = False
    episode_file_count: int = 0
    episode_count: int = 0  # monitored + aired episodes (sonarr semantics)
    total_episode_count: int = 0  # all episodes incl. unmonitored/unaired


class EpisodeDetail(BaseModel):
    """One episode's state on one instance (TV only)."""

    season_number: int
    episode_number: int
    title: str = ""
    monitored: bool = False
    has_file: bool = False
    air_date: str = ""


class InstanceStatus(BaseModel):
    """Presence of one title on one Sonarr/Radarr instance."""

    instance: str
    state: PresenceState
    monitored: bool = False
    missing_episode_count: int | None = None
    total_episode_count: int | None = None
    series_id: int | None = None  # instance-internal id when present (needed for episode queries)
    seasons: list[SeasonDetail] = Field(default_factory=list)
    error: str = ""


class ServerHealth(BaseModel):
    """Liveness, storage, and library totals for one configured server."""

    name: str
    kind: str  # sonarr | radarr | plex
    up: bool = False
    ping_ms: float | None = None
    error: str = ""
    disk_free_bytes: int | None = None
    disk_total_bytes: int | None = None
    series_count: int | None = None
    episode_count: int | None = None  # downloaded episode files, not aired totals
    movie_count: int | None = None
    library_size_bytes: int | None = None
    avg_episode_bytes: float | None = None  # library size / episode files — feeds add estimates
    avg_movie_bytes: float | None = None


class PlexAvailability(BaseModel):
    """Watch-readiness of one title on one Plex server."""

    server: str
    available: bool = False
    error: str = ""


class AggregatedResult(BaseModel):
    """One title with its status across every configured instance — the object the UIs render."""

    result: MediaSearchResult
    statuses: list[InstanceStatus] = Field(default_factory=list)
    plex: list[PlexAvailability] = Field(default_factory=list)

    def status_for(self, instance: str) -> InstanceStatus | None:
        return next((s for s in self.statuses if s.instance == instance), None)


class AddResult(BaseModel):
    instance: str
    ok: bool
    message: str = ""
