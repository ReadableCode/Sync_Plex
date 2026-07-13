"""Request queue: users file requests, admins approve/deny (engine/media/requests)."""

import asyncio

import pytest

from engine.media.config import ArrInstance, MediaConfig
from engine.media.models import MediaSearchResult, MediaType
from engine.media.requests import RequestStatus, RequestStore, fulfill_request


def _result(title="Severance", tvdb=371980) -> MediaSearchResult:
    return MediaSearchResult(media_type=MediaType.TV, title=title, year=2022, tvdb_id=tvdb)


@pytest.fixture()
def store(tmp_path):
    return RequestStore(tmp_path / "requests.json")


def test_create_and_list(store):
    request = store.create(_result(), "friend")
    assert request.status == RequestStatus.PENDING
    assert store.pending_count() == 1
    assert store.list(requested_by="friend")[0].id == request.id
    assert store.list(requested_by="somebody-else") == []


def test_duplicate_pending_requests_collapse(store):
    first = store.create(_result(), "friend")
    second = store.create(_result(), "other")  # same title, different user
    assert second.id == first.id
    assert store.pending_count() == 1


def test_deny_records_admin_and_note(store):
    request = store.create(_result(), "friend")
    store.deny(request.id, "jason", note="not this one")
    denied = store.get(request.id)
    assert denied.status == RequestStatus.DENIED
    assert denied.resolved_by == "jason"
    assert denied.note == "not this one"
    assert store.pending_count() == 0
    # resolved requests cannot be re-resolved
    with pytest.raises(ValueError):
        store.deny(request.id, "jason")


def test_denied_title_can_be_rerequested(store):
    request = store.create(_result(), "friend")
    store.deny(request.id, "jason")
    again = store.create(_result(), "friend")
    assert again.id != request.id
    assert again.status == RequestStatus.PENDING


def test_withdraw_own_pending_only(store):
    request = store.create(_result(), "friend")
    with pytest.raises(ValueError):
        store.withdraw(request.id, "other")
    store.withdraw(request.id, "friend")
    assert store.get(request.id) is None


def test_persistence(store, tmp_path):
    store.create(_result(), "friend")
    again = RequestStore(tmp_path / "requests.json")
    assert again.pending_count() == 1
    assert again.list()[0].result.tvdb_id == 371980


def test_fulfill_approves_only_when_add_succeeds(store, monkeypatch):
    """Approval is the ONLY path to a download, and it must name a server."""
    from engine.media import aggregation
    from engine.media.models import AddResult

    request = store.create(_result(), "friend")
    calls = []

    async def fake_add(aggregated, instance_name, config, quality_profile=""):
        calls.append((aggregated.result.title, instance_name))
        return AddResult(instance=instance_name, ok=True, message="Added")

    monkeypatch.setattr(aggregation, "add_to_instance", fake_add)
    config = MediaConfig(sonarr=[ArrInstance(name="sonarr-elitedesk", base_url="http://x", api_key="k")])

    add_result = asyncio.run(fulfill_request(store, request.id, "sonarr-elitedesk", "jason", config))
    assert add_result.ok
    assert calls == [("Severance", "sonarr-elitedesk")]
    approved = store.get(request.id)
    assert approved.status == RequestStatus.APPROVED
    assert approved.instance == "sonarr-elitedesk"
    assert approved.resolved_by == "jason"


def test_fulfill_failure_keeps_request_pending(store, monkeypatch):
    from engine.media import aggregation
    from engine.media.models import AddResult

    request = store.create(_result(), "friend")

    async def fake_add(aggregated, instance_name, config, quality_profile=""):
        return AddResult(instance=instance_name, ok=False, message="root folder missing")

    monkeypatch.setattr(aggregation, "add_to_instance", fake_add)
    add_result = asyncio.run(fulfill_request(store, request.id, "sonarr-elitedesk", "jason", MediaConfig()))
    assert not add_result.ok
    pending = store.get(request.id)
    assert pending.status == RequestStatus.PENDING  # admin can retry on another server
    assert "root folder missing" in pending.note


def test_fulfill_already_present_resolves_request(store, monkeypatch):
    from engine.media import aggregation
    from engine.media.models import AddResult

    request = store.create(_result(), "friend")

    async def fake_add(aggregated, instance_name, config, quality_profile=""):
        return AddResult(instance=instance_name, ok=False, message="Already present on this instance")

    monkeypatch.setattr(aggregation, "add_to_instance", fake_add)
    asyncio.run(fulfill_request(store, request.id, "sonarr-elitedesk", "jason", MediaConfig()))
    assert store.get(request.id).status == RequestStatus.APPROVED


def test_fulfill_unknown_request(store):
    add_result = asyncio.run(fulfill_request(store, "nope", "sonarr-elitedesk", "jason", MediaConfig()))
    assert not add_result.ok
