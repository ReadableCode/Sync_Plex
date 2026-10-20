"""Pure text helpers behind the web UI (engine/web/app) — the lines both the
search dialog and the admin request queue print about size and headroom."""

from engine.media.models import InstanceStatus, MediaSearchResult, MediaType, PresenceState
from engine.web.app import _headroom_line, _meta_line, _server_choice


def test_meta_line_skips_blanks():
    result = MediaSearchResult(
        media_type=MediaType.TV, title="Severance", network="Apple TV+", status="continuing", season_count=3
    )
    assert _meta_line(result) == "Apple TV+ · continuing · 3 seasons"
    assert _meta_line(MediaSearchResult(media_type=MediaType.MOVIE, title="Zootopia")) == ""


def test_headroom_line_warns_when_it_will_not_fit():
    text, css = _headroom_line(40_000_000_000, 5_000_000_000, "sonarr-behemoth")
    assert text == "⚠ needs ~40.0 GB but only 5.0 GB free on behemoth"
    assert css == "text-xs state-partial"


def test_headroom_line_states_free_space_when_it_fits():
    text, css = _headroom_line(40_000_000_000, 1_200_000_000_000, "sonarr-behemoth")
    assert text == "1.2 TB free on behemoth"
    assert css == "text-xs muted"


def test_headroom_line_is_silent_without_a_disk_reading():
    assert _headroom_line(40_000_000_000, None, "sonarr-behemoth") is None


def test_server_choice_prices_an_absent_server():
    absent = InstanceStatus(instance="sonarr-behemoth", state=PresenceState.NOT_PRESENT)
    assert _server_choice(absent, 40_000_000_000, 1_200_000_000_000) == (
        "~40.0 GB to add · 1.2 TB free",
        "muted",
        True,
    )
    assert _server_choice(None, 40_000_000_000, None) == ("~40.0 GB to add", "muted", True)


def test_server_choice_warns_when_it_will_not_fit():
    absent = InstanceStatus(instance="sonarr-behemoth", state=PresenceState.NOT_PRESENT)
    assert _server_choice(absent, 40_000_000_000, 5_000_000_000) == (
        "⚠ needs ~40.0 GB, 5.0 GB free",
        "state-partial",
        True,
    )


def test_server_choice_rules_out_a_server_that_already_has_it():
    present = InstanceStatus(instance="sonarr-behemoth", state=PresenceState.MONITORED_COMPLETE)
    assert _server_choice(present, 40_000_000_000, 1_000) == ("● complete · already here", "state-complete", False)
