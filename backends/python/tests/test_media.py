from engine.media.aggregation import merge_lookups
from engine.media.clients import RadarrClient, SonarrClient
from engine.media.config import ArrInstance, MediaConfig, load_media_config
from engine.media.models import MediaType, PresenceState
from engine.models import Machine, Service


def _machine_with_services() -> Machine:
    return Machine(
        id="behemoth",
        name="behemoth",
        hostname="192.168.86.31",
        user="root",
        services=[
            Service(type="sonarr", name="sonarr-behemoth", port=8989, api_key_env="TEST_SONARR_KEY"),
            Service(type="radarr", name="radarr-behemoth", port=7878, api_key_env="TEST_RADARR_KEY"),
            Service(type="plex", name="plex-behemoth", port=32400, api_key_env="TEST_PLEX_TOKEN"),
        ],
    )


def test_load_media_config_from_services(monkeypatch):
    # quoted value mimics docker-compose v1 env_file, which keeps quotes literally
    monkeypatch.setenv("TEST_SONARR_KEY", '"abc"')
    monkeypatch.setenv("TEST_RADARR_KEY", "def")
    monkeypatch.setenv("TEST_PLEX_TOKEN", "ghi")

    config = load_media_config([_machine_with_services()])
    assert [i.name for i in config.sonarr] == ["sonarr-behemoth"]
    assert config.sonarr[0].base_url == "http://192.168.86.31:8989"
    assert config.sonarr[0].api_key == "abc"
    assert [i.name for i in config.radarr] == ["radarr-behemoth"]
    assert [s.name for s in config.plex] == ["plex-behemoth"]
    assert config.plex[0].token == "ghi"
    assert config.warnings == []


def test_load_media_config_skips_missing_keys(monkeypatch):
    monkeypatch.delenv("TEST_SONARR_KEY", raising=False)
    monkeypatch.setenv("TEST_RADARR_KEY", "def")
    monkeypatch.delenv("TEST_PLEX_TOKEN", raising=False)

    config = load_media_config([_machine_with_services()])
    assert config.sonarr == []
    assert len(config.radarr) == 1
    assert config.plex == []
    assert len(config.warnings) == 2
    assert any("TEST_SONARR_KEY" in w for w in config.warnings)


def _tv_config() -> MediaConfig:
    return MediaConfig(
        sonarr=[
            ArrInstance(name="sonarr-a", base_url="http://a:8989", api_key="k"),
            ArrInstance(name="sonarr-b", base_url="http://b:8989", api_key="k"),
        ]
    )


def test_merge_lookups_by_tvdb_id():
    # Lookup hit (may lack presence detail) and the authoritative library record
    lookup_item = {"title": "Severance", "year": 2022, "tvdbId": 371980}
    library_record = {
        "title": "Severance",
        "tvdbId": 371980,
        "id": 42,
        "monitored": True,
        "statistics": {"episodeCount": 19, "episodeFileCount": 19},
    }
    # Similarly-named different show, only in b's search results
    other_show = {"title": "Severance Package", "year": 2010, "tvdbId": 99999}

    merged = merge_lookups(
        {
            "sonarr-a": {"results": [lookup_item], "library": {371980: library_record}},
            "sonarr-b": {"results": [lookup_item, other_show], "library": {}},
        },
        MediaType.TV,
        _tv_config(),
    )

    assert len(merged) == 2  # merged by tvdb id, not title
    severance = next(m for m in merged if m.result.tvdb_id == 371980)
    assert severance.status_for("sonarr-a").state == PresenceState.MONITORED_COMPLETE
    assert severance.status_for("sonarr-b").state == PresenceState.NOT_PRESENT

    other = next(m for m in merged if m.result.tvdb_id == 99999)
    assert other.status_for("sonarr-a").state == PresenceState.NOT_PRESENT


def test_merge_lookups_marks_unreachable_instance():
    item = {"title": "Severance", "year": 2022, "tvdbId": 371980}
    merged = merge_lookups(
        {
            "sonarr-a": {"results": [item], "library": {}},
            "sonarr-b": ConnectionError("boom"),
        },
        MediaType.TV,
        _tv_config(),
    )
    assert len(merged) == 1
    status_b = merged[0].status_for("sonarr-b")
    assert status_b.state == PresenceState.UNREACHABLE
    assert "boom" in status_b.error


def test_merge_movie_status_comes_from_library_not_lookup():
    """Radarr lookup leaves hasFile empty even for downloaded movies — the
    library record must win, or every downloaded movie renders as partial."""
    config = MediaConfig(radarr=[ArrInstance(name="radarr-a", base_url="http://a", api_key="k")])
    lookup_item = {"title": "Dune: Part Two", "year": 2024, "tmdbId": 693134, "id": 1828, "hasFile": None}
    library_record = {"id": 1828, "tmdbId": 693134, "hasFile": True, "monitored": True}

    merged = merge_lookups(
        {"radarr-a": {"results": [lookup_item], "library": {693134: library_record}}},
        MediaType.MOVIE,
        config,
    )
    assert merged[0].status_for("radarr-a").state == PresenceState.MONITORED_COMPLETE

    # and a movie in nobody's library is simply not present
    merged = merge_lookups(
        {"radarr-a": {"results": [lookup_item], "library": {}}},
        MediaType.MOVIE,
        config,
    )
    assert merged[0].status_for("radarr-a").state == PresenceState.NOT_PRESENT


def test_sonarr_status_missing_episodes():
    client = SonarrClient(ArrInstance(name="sonarr-a", base_url="http://a", api_key="k"))
    status = client.to_status(
        {"id": 7, "monitored": True, "statistics": {"episodeCount": 10, "episodeFileCount": 6}}
    )
    assert status.state == PresenceState.MONITORED_INCOMPLETE
    assert status.missing_episode_count == 4
    assert status.total_episode_count == 10
    assert status.series_id == 7


def test_sonarr_status_extracts_seasons():
    client = SonarrClient(ArrInstance(name="sonarr-a", base_url="http://a", api_key="k"))
    status = client.to_status(
        {
            "id": 7,
            "monitored": True,
            "statistics": {"episodeCount": 10, "episodeFileCount": 10},
            "seasons": [
                {"seasonNumber": 0, "monitored": False, "statistics": {"episodeFileCount": 0, "episodeCount": 3}},
                {
                    "seasonNumber": 1,
                    "monitored": True,
                    "statistics": {"episodeFileCount": 10, "episodeCount": 10, "totalEpisodeCount": 10},
                },
                {
                    "seasonNumber": 2,
                    "monitored": False,
                    "statistics": {"episodeFileCount": 0, "episodeCount": 0, "totalEpisodeCount": 22},
                },
            ],
        }
    )
    assert len(status.seasons) == 3
    s1 = next(s for s in status.seasons if s.season_number == 1)
    assert s1.monitored and s1.episode_file_count == 10 and s1.episode_count == 10
    s2 = next(s for s in status.seasons if s.season_number == 2)
    assert not s2.monitored and s2.episode_file_count == 0
    # unmonitored seasons report 0 monitored eps but keep the real total
    assert s2.total_episode_count == 22


def test_sonarr_status_nothing_monitored_is_not_complete():
    client = SonarrClient(ArrInstance(name="sonarr-a", base_url="http://a", api_key="k"))
    status = client.to_status({"id": 7, "monitored": False, "statistics": {"episodeCount": 0, "episodeFileCount": 0}})
    assert status.state == PresenceState.MONITORED_INCOMPLETE
    assert status.total_episode_count == 0


def test_sonarr_search_result_metadata():
    result = SonarrClient.to_search_result(
        {
            "title": "48 Hours",
            "year": 1988,
            "tvdbId": 138551,
            "network": "CBS",
            "status": "continuing",
            "genres": ["Crime", "Documentary"],
            "runtime": 45,
            "statistics": {"seasonCount": 36},
        }
    )
    assert result.network == "CBS"
    assert result.status == "continuing"
    assert result.genres == ["Crime", "Documentary"]
    assert result.runtime == 45
    assert result.season_count == 36


def test_radarr_status_has_file():
    client = RadarrClient(ArrInstance(name="radarr-a", base_url="http://a", api_key="k"))
    assert client.to_status({"id": 7, "hasFile": True}).state == PresenceState.MONITORED_COMPLETE
    assert client.to_status({"id": 7, "hasFile": False}).state == PresenceState.MONITORED_INCOMPLETE
    assert client.to_status(None).state == PresenceState.NOT_PRESENT
    assert client.to_status({"title": "x"}).state == PresenceState.NOT_PRESENT
