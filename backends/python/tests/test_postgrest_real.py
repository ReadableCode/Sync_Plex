"""Real-PostgREST round trip — the production path, end to end.

Two throwaway accounts (one user, one admin) log in through the REAL shared
auth service, so this exercises postgrest-auth verify (argon2id) -> JWT claims
-> in-app session validation -> RLS -> the requests table.

The isolation checks are the point of the migration: a non-admin must not be
able to read or touch another user's rows THROUGH POSTGREST, not merely
through the UI.
"""

import asyncio
import time
import uuid

import pytest

from engine import bootstrap
from engine import store as pgrest
from engine.media.config import ArrInstance, MediaConfig
from engine.media.models import AddResult, MediaSearchResult, MediaType
from engine.media.requests import RequestStatus, RequestStore, fulfill_request
from engine.web import auth
from engine.web.users import UserStore, db_reachable

PW = "postgrest-roundtrip-pw"


def _result(title="Severance", tvdb=371980) -> MediaSearchResult:
    return MediaSearchResult(media_type=MediaType.TV, title=title, year=2022, tvdb_id=tvdb)


def _login(users: UserStore, username: str) -> RequestStore:
    try:
        token = auth.login_via_service(username, PW, ip="")
    except auth.AuthServiceError as exc:
        raise AssertionError(f"auth service login failed — red, not skipped: {exc.detail}") from exc
    user = auth.validate_token(token, users)
    assert user is not None, "service token must validate in-app"
    return RequestStore(token, user)


@pytest.fixture(scope="module")
def stores():
    ok, detail = db_reachable()
    assert ok, f"database unreachable — red, not skipped: {detail}"
    bootstrap.apply_schema()
    reachable, detail = pgrest.postgrest_reachable()
    assert reachable, f"postgrest unreachable — red, not skipped: {detail}"

    users = UserStore()
    requester = f"ztest{uuid.uuid4().hex[:10]}"
    admin = f"ztest{uuid.uuid4().hex[:10]}"
    users.add(requester, PW)
    users.add(admin, PW, role="admin")
    try:
        yield users, _login(users, requester), _login(users, admin)
    finally:
        # ON DELETE SET NULL keeps the rows, so clear them explicitly first.
        for username in (requester, admin):
            try:
                users.remove(username)
            except KeyError:
                pass


@pytest.fixture()
def clean(stores):
    """Drop every request either account can see, before and after."""
    _, user_store, admin_store = stores

    def _wipe():
        for request in admin_store.list():
            pgrest.delete_request(admin_store.token, request.id)

    _wipe()
    yield stores
    _wipe()


def test_create_and_read_back(clean):
    _, user_store, _ = clean
    request = user_store.create(_result(), user_store.user.username)
    assert request.status == RequestStatus.PENDING
    assert request.requested_by == user_store.user.username

    mine = user_store.list()
    assert [r.id for r in mine] == [request.id]
    assert mine[0].result.tvdb_id == 371980


def test_duplicate_request_collapses_per_user(clean):
    _, user_store, _ = clean
    first = user_store.create(_result(), user_store.user.username)
    second = user_store.create(_result(), user_store.user.username)
    assert second.id == first.id
    assert user_store.pending_count() == 1


def test_rls_hides_other_users_rows(clean):
    """The definition-of-done check: isolation enforced at PostgREST, not the UI."""
    _, user_store, admin_store = clean
    mine = user_store.create(_result(), user_store.user.username)

    # The admin's own view is scoped to them for dedupe, but the admin policy
    # lets them see the whole queue — that is what the approval page needs.
    assert mine.id in {r.id for r in admin_store.list()}

    other = admin_store.create(_result("Andor", 507150), admin_store.user.username)
    visible = {r.id for r in user_store.list()}
    assert other.id not in visible, "a non-admin must not see another account's request"
    assert user_store.get(other.id) is None


def test_rls_blocks_writing_rows_for_someone_else(clean):
    _, user_store, admin_store = clean
    with pytest.raises(pgrest.StoreError):
        pgrest.insert_request(
            user_store.token,
            {
                "id": uuid.uuid4().hex[:12],
                "user_id": admin_store.user.id,  # not the caller
                "requested_by": admin_store.user.username,
                "result": _result().model_dump(mode="json"),
                "external_key": _result().external_key,
                "status": "pending",
            },
        )


def test_non_admin_cannot_resolve_someone_elses_request(clean):
    _, user_store, admin_store = clean
    theirs = admin_store.create(_result("Andor", 507150), admin_store.user.username)
    with pytest.raises(KeyError):
        user_store.deny(theirs.id, user_store.user.username, note="not yours")
    assert admin_store.get(theirs.id).status == RequestStatus.PENDING


def test_users_table_unreachable_through_postgrest(clean):
    """I4 from the client side: no PostgREST path to a password hash."""
    _, user_store, _ = clean
    with pytest.raises(pgrest.StoreError) as exc:
        pgrest._check(
            pgrest._http().get(
                f"{pgrest.config.POSTGREST_URL}/users",
                headers={"Authorization": f"Bearer {user_store.token}", "Accept-Profile": "syncplex"},
            )
        )
    assert exc.value.status_code in (401, 403, 404)


def test_admin_denies_and_the_note_reaches_the_requester(clean):
    _, user_store, admin_store = clean
    request = user_store.create(_result(), user_store.user.username)

    denied = admin_store.deny(request.id, admin_store.user.username, note="not this one")
    assert denied.status == RequestStatus.DENIED
    assert denied.resolved_by == admin_store.user.username
    assert denied.resolved_at is not None
    assert user_store.get(request.id).note == "not this one"

    with pytest.raises(ValueError):
        admin_store.deny(request.id, admin_store.user.username)


def test_denied_title_can_be_rerequested(clean):
    _, user_store, admin_store = clean
    request = user_store.create(_result(), user_store.user.username)
    admin_store.deny(request.id, admin_store.user.username)
    again = user_store.create(_result(), user_store.user.username)
    assert again.id != request.id
    assert again.status == RequestStatus.PENDING


def test_withdraw_own_pending_only(clean):
    _, user_store, admin_store = clean
    request = user_store.create(_result(), user_store.user.username)
    with pytest.raises(ValueError):
        admin_store.withdraw(request.id, admin_store.user.username)
    user_store.withdraw(request.id, user_store.user.username)
    assert user_store.get(request.id) is None
    with pytest.raises(KeyError):
        user_store.withdraw(request.id, user_store.user.username)


def test_fulfill_approves_only_when_add_succeeds(clean, monkeypatch):
    """Approval is the ONLY path to a download, and it must name a server."""
    from engine.media import aggregation

    _, user_store, admin_store = clean
    request = user_store.create(_result(), user_store.user.username)
    calls = []

    async def fake_add(aggregated, instance_name, config, quality_profile=""):
        calls.append((aggregated.result.title, instance_name))
        return AddResult(instance=instance_name, ok=True, message="Added")

    monkeypatch.setattr(aggregation, "add_to_instance", fake_add)
    config = MediaConfig(sonarr=[ArrInstance(name="sonarr-elitedesk", base_url="http://x", api_key="k")])

    add_result = asyncio.run(
        fulfill_request(admin_store, request.id, "sonarr-elitedesk", admin_store.user.username, config)
    )
    assert add_result.ok
    assert calls == [("Severance", "sonarr-elitedesk")]
    approved = admin_store.get(request.id)
    assert approved.status == RequestStatus.APPROVED
    assert approved.instance == "sonarr-elitedesk"
    assert approved.resolved_by == admin_store.user.username


def test_fulfill_failure_keeps_request_pending(clean, monkeypatch):
    from engine.media import aggregation

    _, user_store, admin_store = clean
    request = user_store.create(_result(), user_store.user.username)

    async def fake_add(aggregated, instance_name, config, quality_profile=""):
        return AddResult(instance=instance_name, ok=False, message="root folder missing")

    monkeypatch.setattr(aggregation, "add_to_instance", fake_add)
    add_result = asyncio.run(
        fulfill_request(admin_store, request.id, "sonarr-elitedesk", admin_store.user.username, MediaConfig())
    )
    assert not add_result.ok
    pending = admin_store.get(request.id)
    assert pending.status == RequestStatus.PENDING  # admin can retry on another server
    assert "root folder missing" in pending.note


def test_fulfill_already_present_resolves_request(clean, monkeypatch):
    from engine.media import aggregation

    _, user_store, admin_store = clean
    request = user_store.create(_result(), user_store.user.username)

    async def fake_add(aggregated, instance_name, config, quality_profile=""):
        return AddResult(instance=instance_name, ok=False, message="Already present on this instance")

    monkeypatch.setattr(aggregation, "add_to_instance", fake_add)
    asyncio.run(fulfill_request(admin_store, request.id, "sonarr-elitedesk", admin_store.user.username, MediaConfig()))
    assert admin_store.get(request.id).status == RequestStatus.APPROVED


def test_fulfill_unknown_request(clean):
    _, _, admin_store = clean
    add_result = asyncio.run(fulfill_request(admin_store, "nope", "sonarr-elitedesk", "admin", MediaConfig()))
    assert not add_result.ok


def test_disable_revokes_the_live_token(stores):
    """password_changed_at moves, so the token issued before it stops
    validating even though it has not expired.

    Uses its own account rather than the module fixture's — the point of the
    test is to invalidate a token, which would poison a shared one.
    """
    users, _, _ = stores
    username = f"ztest{uuid.uuid4().hex[:10]}"
    users.add(username, PW)
    try:
        store = _login(users, username)
        assert auth.validate_token(store.token, users) is not None
        # iat is second-granular and validate_token grants it a 1s grace, so
        # the re-enable's password_changed_at bump must land in a later second
        # than the login or the old token slips through.
        time.sleep(1.1)
        users.set_disabled(username, True)
        assert auth.validate_token(store.token, users) is None
        users.set_disabled(username, False)
        # re-enabling must not resurrect the pre-disable token either
        assert auth.validate_token(store.token, users) is None
    finally:
        users.remove(username)
