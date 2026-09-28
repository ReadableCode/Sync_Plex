"""drive_sync core: config, fuzzy search, planning, progress parsing. No Plex, no disk beyond tmp."""

import pytest

from drive_sync import plan as planning
from drive_sync.drive_config import ConfigError, DriveConfig, ShowEntry
from drive_sync.library import LibraryTitle, PlexLibrary, fuzzy_find
from drive_sync.transfer import parse_progress

# --- config ---


def test_config_roundtrip_keeps_key_order_and_edits(tmp_path):
    config = DriveConfig.starter(tmp_path)
    assert config.add_show("Severance", 2) and not config.add_show("Severance")
    assert config.add_movie("Zootopia")
    config.save()
    text = (tmp_path / "config.yaml").read_text()
    assert text.index("shows:") < text.index("movies:") < text.index("quality_profile_pref:")
    loaded = DriveConfig.load(tmp_path)
    assert loaded.shows == [ShowEntry("Severance", 2)]
    assert loaded.movie_names() == {"Zootopia"}
    assert loaded.rename("show", "Severance", "Severance (2022)") and loaded.set_episodes("Severance (2022)", 5)
    assert loaded.add_show("Other") and not loaded.rename("show", "Other", "Severance (2022)")  # no duplicates
    assert loaded.remove("show", "Other")
    assert loaded.remove("movie", "Zootopia") and not loaded.remove("movie", "Zootopia")
    assert loaded.shows == [ShowEntry("Severance (2022)", 5)] and loaded.movies == []


def test_config_errors_name_the_problem(tmp_path):
    (tmp_path / "config.yaml").write_text("shows: [\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        DriveConfig.load(tmp_path)
    (tmp_path / "config.yaml").write_text("shows:\n  - num_next_episodes: 3\n")
    with pytest.raises(ConfigError, match="needs a name"):
        DriveConfig.load(tmp_path)
    with pytest.raises(ConfigError, match="no config.yaml"):
        DriveConfig.load(tmp_path / "nope")


def test_legacy_only_get_unwatched_and_bare_strings_load(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "shows:\n  - name: A\n    num_next_episodes: 4\n    only_get_unwatched: false\nmovies:\n  - B\n"
    )
    config = DriveConfig.load(tmp_path)
    assert config.shows == [ShowEntry("A", 4, only_get_unwatched=False)]
    assert config.movie_names() == {"B"}
    assert config.quality_profile_pref == ["original", "optimized for mobile"]


# --- fuzzy ---


def _title(kind, title):
    name = title.split(" (")[0] if kind == "movie" else title
    return LibraryTitle(kind, title, name, title, {})


def test_fuzzy_prefers_prefix_then_substring_then_similarity():
    titles = [_title("show", t) for t in ("American Dad!", "The Americans", "Family Guy", "Amercan Dad")]
    found = [t.title for t in fuzzy_find("americ", titles)]
    assert found[:2] == ["American Dad!", "The Americans"]
    assert "Family Guy" not in found
    assert [t.title for t in fuzzy_find("american dad", titles)][0] == "American Dad!"
    assert "Amercan Dad" in [t.title for t in fuzzy_find("american dad", titles)]


# --- planning ---


class FakeLibrary(PlexLibrary):
    def __init__(self, shows, movies, episodes):
        super().__init__()
        self._shows = shows
        self._movies = movies
        self._episodes = episodes

    def episodes(self, show):
        return self._episodes[show.title]


def _part(file, size_gb, quality):
    return {"Part": [{"file": file, "size": int(size_gb * 1e9), "title": quality}]}


@pytest.fixture
def library():
    shows = [
        LibraryTitle("show", "Severance", "Severance", "1", {}),
    ]
    movies = [
        LibraryTitle(
            "movie",
            "Zootopia (2016)",
            "Zootopia",
            "9",
            {"Media": [_part("/data/Movies/Zootopia (2016)/Zootopia.mkv", 3.0, "original")], "viewCount": 0},
        ),
    ]
    episodes = {
        "Severance": [
            (
                "Season 1",
                {
                    "index": 1,
                    "title": "Good News",
                    "viewCount": 2,
                    "Media": [_part("/data/TV/Severance/Season 1/S01E01.mkv", 1.0, "original")],
                },
            ),
            (
                "Season 1",
                {
                    "index": 2,
                    "title": "Half Loop",
                    "viewCount": 0,
                    "Media": [
                        _part("/data/TV/Severance/Season 1/S01E02.mkv", 1.0, "original"),
                        _part("/data/TV/Severance/Season 1/S01E02.mobile.mp4", 0.4, "optimized for mobile"),
                    ],
                },
            ),
            (
                "Season 1",
                {
                    "index": 3,
                    "title": "In Perpetuity",
                    "viewCount": 0,
                    "Media": [_part("/data/TV/Severance/Season 1/S01E03.mkv", 1.0, "4k")],
                },
            ),
            (
                "Season 2",
                {
                    "index": 1,
                    "title": "Hello",
                    "viewCount": 0,
                    "Media": [_part("/data/TV/Severance/Season 2/S02E01.mkv", 1.0, "original")],
                },
            ),
        ]
    }
    return FakeLibrary(shows, movies, episodes)


def test_desired_files_next_unwatched_quality_and_layout(library, monkeypatch, tmp_path):
    monkeypatch.setenv("PLEX_SERVER_MEDIA_PATH", "/Media")
    config = DriveConfig.starter(tmp_path)
    config.add_show("Severance", 2)
    config.add_movie("Zootopia")
    config.add_movie("Not A Movie")
    config.quality_profile_pref = ["optimized for mobile", "original"]
    wanted, missing = planning.desired_files(config, library, str(tmp_path), os_name="Darwin")
    labels = [(w.title, w.label.split(" · ")[1] if w.kind == "show" else w.label, w.quality) for w in wanted]
    # episode 1 is watched and skipped, 2 takes the mobile part, 3 falls back to its only part
    assert labels == [
        ("Zootopia", "Zootopia (2016)", "original"),
        ("Severance", "2", "optimized for mobile"),
        ("Severance", "3", "4k"),
    ]
    assert wanted[1].server_path == "/Media/TV/Severance/Season 1/S01E02.mobile.mp4"
    assert wanted[1].dest_path == str(tmp_path / "TV" / "Severance" / "S01E02.mobile.mp4")  # season folder dropped
    assert [m.name for m in missing] == ["Not A Movie"]


def test_dest_path_windows_keeps_drive_letter():
    root = "\\\\plex\\Media"
    server = root + "\\TV\\Show\\Season 1\\ep.mkv"
    assert planning.dest_path_for(server, root, "E:\\Media", False, "Windows") == "E:\\Media\\TV\\Show\\ep.mkv"


def test_build_plan_groups_per_title_and_orders_deletes_first(library, monkeypatch, tmp_path):
    monkeypatch.setenv("PLEX_SERVER_MEDIA_PATH", "/Media")
    config = DriveConfig.starter(tmp_path)
    config.add_show("Severance", 2)
    config.add_show("Ghost Show")
    wanted, missing = planning.desired_files(config, library, str(tmp_path), os_name="Darwin")
    existing = {
        str(tmp_path / "TV" / "Severance" / "S01E02.mkv"): 1.0,  # wanted: synced
        str(tmp_path / "TV" / "Severance" / "S01E01.mkv"): 1.0,  # watched: delete, owned by Severance
        str(tmp_path / "Movies" / "Old (1999)" / "Old.mkv"): 2.0,  # no entry: other files
    }
    plan = planning.build_plan(config, wanted, missing, str(tmp_path), existing, free=10.0)
    assert [a.op for a in plan.actions] == ["delete", "delete", "download", "synced"]
    by_name = {t.name: t for t in plan.titles}
    assert by_name["Severance"].on_drive == 1 and len(by_name["Severance"].downloads) == 1
    assert [a.label for a in by_name["Severance"].deletes] == ["TV/Severance/S01E01.mkv"]
    assert by_name["Ghost Show"].missing.name == "Ghost Show"
    assert by_name["Ghost Show"].missing.suggestions == ()
    assert by_name["other files on drive"].delete_gb == 2.0
    assert plan.download_gb == 1.0 and plan.delete_gb == 3.0 and plan.net_gb == -2.0 and plan.fits
    assert not plan.is_clean and [m.name for m in plan.missing] == ["Ghost Show"]


# --- transfer ---


def test_parse_progress_reads_rsync_and_openrsync_lines():
    assert parse_progress("       30000000 100%  169.59MB/s   00:00:00 (xfer#1, to-check=0/1)") == (30000000, 100)
    assert parse_progress("  1,234,567  45%   12.34MB/s    0:00:10") == (1234567, 45)
    assert parse_progress("sending incremental file list") is None
