"""Server health for the status banner: liveness, ping, storage, library totals.

Also derives per-instance size averages from each library so the UI can tell
the user roughly how much disk an add would consume before they commit to it.
Like aggregation, everything degrades gracefully — a down server becomes a
ServerHealth with up=False, never an exception.
"""

import asyncio

from .aggregation import _library_index
from .clients import PlexClient, RadarrClient, SonarrClient
from .config import MediaConfig, PlexServer, load_media_config
from .models import AggregatedResult, MediaType, ServerHealth

# Fallbacks for instances whose library has nothing to average over yet
DEFAULT_EPISODE_BYTES = 1_500_000_000  # ~1.5 GB per episode
DEFAULT_MOVIE_BYTES = 5_000_000_000  # ~5 GB per movie

# Assumed season length when no instance knows the real episode count
FALLBACK_EPISODES_PER_SEASON = 10


def format_bytes(n: float | int) -> str:
    """Human size in decimal units, matching how drives (and the arr UIs) are labeled."""
    value = float(n)
    unit = "B"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1000 or unit == "PB":
            break
        value /= 1000
    precision = 0 if unit in ("B", "KB", "MB") else 1
    return f"{value:.{precision}f} {unit}"


def _storage_for_roots(disks: list[dict], root_folders: list[dict]) -> tuple[int | None, int | None]:
    """(free, total) bytes for the mounts that actually back this instance's root folders.

    /api/v3/diskspace lists every mount the server can see (system disk, docker
    overlays, ...); summing all of them would misreport. Each root folder is
    matched to the longest diskspace path that prefixes it, and the matched
    mounts are counted once each. If nothing matches, every mount is used.
    """
    matched: dict[str, dict] = {}
    for folder in root_folders:
        root = folder.get("path") or ""
        best: dict | None = None
        best_len = -1
        for disk in disks:
            mount = (disk.get("path") or "").rstrip("/")
            if root == mount or root.startswith(mount + "/"):
                if len(mount) > best_len:
                    best, best_len = disk, len(mount)
        if best is not None:
            matched[best.get("path", "")] = best
    if not matched:
        matched = {d.get("path", ""): d for d in disks}

    free = sum(d.get("freeSpace") or 0 for d in matched.values())
    total = sum(d.get("totalSpace") or 0 for d in matched.values())
    return (free, total) if total else (None, None)


def apply_library_stats(health: ServerHealth, items: list[dict]) -> None:
    """Fill library totals and size averages from raw library records."""
    if health.kind == "sonarr":
        stats = [item.get("statistics", {}) for item in items]
        health.series_count = len(items)
        health.episode_count = sum(s.get("episodeFileCount") or 0 for s in stats)
        health.library_size_bytes = sum(s.get("sizeOnDisk") or 0 for s in stats)
        if health.episode_count:
            health.avg_episode_bytes = health.library_size_bytes / health.episode_count
    else:
        health.movie_count = len(items)
        health.library_size_bytes = sum(item.get("sizeOnDisk") or 0 for item in items)
        downloaded = sum(1 for item in items if item.get("hasFile"))
        if downloaded:
            health.avg_movie_bytes = health.library_size_bytes / downloaded


async def _ping_twice(ping) -> float:
    """One retry on failure — a transient blip must not flag a healthy server as
    down for a whole banner cycle. Latency reported is the successful attempt's."""
    try:
        return await ping()
    except Exception:  # noqa: BLE001 — second failure propagates to the caller
        return await ping()


async def _arr_health(client: SonarrClient | RadarrClient, kind: str) -> ServerHealth:
    health = ServerHealth(name=client.name, kind=kind)
    try:
        health.ping_ms = await _ping_twice(client.ping_ms)
    except Exception as exc:  # noqa: BLE001 — a down server is a result, not an error
        health.error = str(exc) or type(exc).__name__
        return health
    health.up = True

    # Storage and library totals are best-effort; a failure here still leaves
    # the server marked up (the ping already succeeded).
    try:
        disks, roots, library = await asyncio.gather(
            client.disk_space(), client.root_folders(), _library_index(client)
        )
    except Exception as exc:  # noqa: BLE001
        health.error = str(exc)
        return health
    health.disk_free_bytes, health.disk_total_bytes = _storage_for_roots(disks, roots)
    apply_library_stats(health, list(library.values()))
    return health


async def _plex_health(server: PlexServer) -> ServerHealth:
    health = ServerHealth(name=server.name, kind="plex")
    try:
        health.ping_ms = await _ping_twice(PlexClient(server).ping_ms)
        health.up = True
    except Exception as exc:  # noqa: BLE001
        health.error = str(exc) or type(exc).__name__
    return health


async def check_all_servers(config: MediaConfig | None = None) -> list[ServerHealth]:
    """Probe every configured server concurrently. Order: sonarr, radarr, plex."""
    if config is None:
        config = load_media_config()
    tasks = (
        [_arr_health(SonarrClient(i), "sonarr") for i in config.sonarr]
        + [_arr_health(RadarrClient(i), "radarr") for i in config.radarr]
        + [_plex_health(s) for s in config.plex]
    )
    return list(await asyncio.gather(*tasks))


def known_episode_total(aggregated: AggregatedResult) -> int | None:
    """Best known full-episode count across the instances that carry the series.

    Per-season totals are preferred over the instance's headline count because
    they include unmonitored seasons, which an add (monitored=True) would pull.
    Specials are excluded — Sonarr leaves season 0 unmonitored on add.
    """
    best = 0
    for status in aggregated.statuses:
        by_seasons = sum(
            season.total_episode_count or season.episode_count
            for season in status.seasons
            if season.season_number != 0
        )
        best = max(best, by_seasons, status.total_episode_count or 0)
    return best or None


def estimate_add_bytes(aggregated: AggregatedResult, health: ServerHealth | None) -> int:
    """Rough bytes an add would consume on one instance, from its library averages."""
    result = aggregated.result
    if result.media_type == MediaType.MOVIE:
        per_movie = (health.avg_movie_bytes if health else None) or DEFAULT_MOVIE_BYTES
        return int(per_movie)
    episodes = known_episode_total(aggregated)
    if episodes is None:
        # No instance has the series yet — assume typical season lengths.
        episodes = (result.season_count or 1) * FALLBACK_EPISODES_PER_SEASON
    per_episode = (health.avg_episode_bytes if health else None) or DEFAULT_EPISODE_BYTES
    return int(episodes * per_episode)
