from engine.media.health import (
    DEFAULT_EPISODE_BYTES,
    DEFAULT_MOVIE_BYTES,
    _storage_for_roots,
    apply_library_stats,
    estimate_add_bytes,
    format_bytes,
    known_episode_total,
)
from engine.media.models import (
    AggregatedResult,
    InstanceStatus,
    MediaSearchResult,
    MediaType,
    PresenceState,
    SeasonDetail,
    ServerHealth,
)


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(950) == "950 B"
    assert format_bytes(1_500_000) == "2 MB"
    assert format_bytes(48_000_000_000) == "48.0 GB"
    assert format_bytes(1_230_000_000_000) == "1.2 TB"


def test_storage_matches_longest_prefix_mount():
    """A root folder on /data/media must count the /data mount once — not the
    system disk, and not / (which prefixes everything)."""
    disks = [
        {"path": "/", "freeSpace": 10, "totalSpace": 100},
        {"path": "/data", "freeSpace": 500, "totalSpace": 4000},
    ]
    roots = [{"path": "/data/media/tv"}, {"path": "/data/media/anime"}]
    assert _storage_for_roots(disks, roots) == (500, 4000)


def test_storage_falls_back_to_all_mounts():
    disks = [
        {"path": "/x", "freeSpace": 10, "totalSpace": 100},
        {"path": "/y", "freeSpace": 20, "totalSpace": 200},
    ]
    assert _storage_for_roots(disks, []) == (30, 300)
    assert _storage_for_roots([], []) == (None, None)


def test_apply_library_stats_sonarr():
    health = ServerHealth(name="sonarr-a", kind="sonarr")
    apply_library_stats(
        health,
        [
            {"statistics": {"episodeFileCount": 100, "sizeOnDisk": 100_000_000_000}},
            {"statistics": {"episodeFileCount": 50, "sizeOnDisk": 50_000_000_000}},
            {"statistics": {}},
        ],
    )
    assert health.series_count == 3
    assert health.episode_count == 150
    assert health.library_size_bytes == 150_000_000_000
    assert health.avg_episode_bytes == 1_000_000_000


def test_apply_library_stats_radarr():
    health = ServerHealth(name="radarr-a", kind="radarr")
    apply_library_stats(
        health,
        [
            {"hasFile": True, "sizeOnDisk": 8_000_000_000},
            {"hasFile": True, "sizeOnDisk": 4_000_000_000},
            {"hasFile": False, "sizeOnDisk": 0},
        ],
    )
    assert health.movie_count == 3
    assert health.library_size_bytes == 12_000_000_000
    assert health.avg_movie_bytes == 6_000_000_000  # only downloaded movies average


def _tv_result(**kwargs) -> MediaSearchResult:
    return MediaSearchResult(media_type=MediaType.TV, title="Severance", **kwargs)


def test_known_episode_total_prefers_season_totals_over_monitored_count():
    """Unmonitored seasons still get pulled by an add — count them, skip specials."""
    aggregated = AggregatedResult(
        result=_tv_result(),
        statuses=[
            InstanceStatus(
                instance="sonarr-a",
                state=PresenceState.MONITORED_INCOMPLETE,
                total_episode_count=10,  # monitored-only headline count
                seasons=[
                    SeasonDetail(season_number=0, total_episode_count=3),
                    SeasonDetail(season_number=1, total_episode_count=10),
                    SeasonDetail(season_number=2, total_episode_count=22),
                ],
            ),
            InstanceStatus(instance="sonarr-b", state=PresenceState.NOT_PRESENT),
        ],
    )
    assert known_episode_total(aggregated) == 32


def test_known_episode_total_unknown():
    aggregated = AggregatedResult(
        result=_tv_result(),
        statuses=[InstanceStatus(instance="sonarr-a", state=PresenceState.NOT_PRESENT)],
    )
    assert known_episode_total(aggregated) is None


def test_estimate_add_bytes_tv_uses_instance_average():
    aggregated = AggregatedResult(
        result=_tv_result(),
        statuses=[
            InstanceStatus(
                instance="sonarr-a",
                state=PresenceState.MONITORED_COMPLETE,
                total_episode_count=20,
            )
        ],
    )
    health = ServerHealth(name="sonarr-b", kind="sonarr", avg_episode_bytes=2_000_000_000)
    assert estimate_add_bytes(aggregated, health) == 40_000_000_000


def test_estimate_add_bytes_tv_fallbacks():
    """No instance has it and no health data — season-count guess at default size."""
    aggregated = AggregatedResult(result=_tv_result(season_count=3))
    assert estimate_add_bytes(aggregated, None) == 30 * DEFAULT_EPISODE_BYTES


def test_estimate_add_bytes_movie():
    aggregated = AggregatedResult(
        result=MediaSearchResult(media_type=MediaType.MOVIE, title="Dune: Part Two")
    )
    health = ServerHealth(name="radarr-a", kind="radarr", avg_movie_bytes=7_000_000_000)
    assert estimate_add_bytes(aggregated, health) == 7_000_000_000
    assert estimate_add_bytes(aggregated, None) == DEFAULT_MOVIE_BYTES
