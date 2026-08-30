"""Request-model mapping that needs no database (engine/media/requests).

The queue itself is exercised end to end against real PostgREST in
test_postgrest_real.py — including fulfill_request, whose approve-only-on-
success rule is the part that decides whether anything downloads.
"""

from engine.media.models import MediaSearchResult, MediaType
from engine.media.requests import RequestStatus, _to_request


def _row(**over) -> dict:
    base = {
        "id": "d86f307b5ba9",
        "result": {"media_type": "tv", "title": "Severance", "year": 2022, "tvdb_id": 371980},
        "requested_by": "victoria",
        "requested_at": "2026-07-13T12:35:48.431392Z",
        "status": "approved",
        "resolved_by": "jason",
        "resolved_at": "2026-07-13T13:00:00Z",
        "instance": "sonarr-behemoth",
        "note": "Added",
    }
    return {**base, **over}


def test_row_maps_to_request():
    request = _to_request(_row())
    assert request.id == "d86f307b5ba9"
    assert request.status == RequestStatus.APPROVED
    assert request.result.tvdb_id == 371980
    assert request.result.media_type == MediaType.TV
    assert request.instance == "sonarr-behemoth"
    assert request.title_line == "Severance (2022)"


def test_pending_row_has_no_resolution():
    request = _to_request(_row(status="pending", resolved_by="", resolved_at=None, instance="", note=""))
    assert request.status == RequestStatus.PENDING
    assert request.resolved_at is None
    assert request.resolved_by == ""


def test_external_key_is_the_dedupe_key():
    """What create()/find_pending() filter on server-side."""
    tv = MediaSearchResult(media_type=MediaType.TV, title="Severance", year=2022, tvdb_id=371980)
    movie = MediaSearchResult(media_type=MediaType.MOVIE, title="Dune", year=2021, tmdb_id=438631)
    untagged = MediaSearchResult(media_type=MediaType.MOVIE, title="Dune", year=2021)
    assert tv.external_key == "tvdb:371980"
    assert movie.external_key == "tmdb:438631"
    assert untagged.external_key == "title:dune:2021"
