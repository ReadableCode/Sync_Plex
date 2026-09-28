"""The ntfy text an admin reads on their phone (engine/media/notifications)."""

from engine.media.models import MediaSearchResult, MediaType
from engine.media.notifications import request_message
from engine.media.requests import MediaRequest


def _request(**over) -> MediaRequest:
    result = MediaSearchResult(media_type=MediaType.TV, title="Severance", year=2022, tvdb_id=371980, season_count=3)
    return MediaRequest(result=result, requested_by="victoria", **over)


def test_message_carries_kind_seasons_and_estimate():
    text = request_message(_request(), estimate_bytes=40_000_000_000)
    assert text == "Sync_Plex: victoria requested Severance (2022) — show, 3 seasons, ~40.0 GB — awaiting approval"


def test_message_without_estimate_still_says_what_it_is():
    movie = MediaRequest(
        result=MediaSearchResult(media_type=MediaType.MOVIE, title="Zootopia", year=2016, tmdb_id=269149),
        requested_by="victoria",
    )
    assert request_message(movie) == "Sync_Plex: victoria requested Zootopia (2016) — movie — awaiting approval"
