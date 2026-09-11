"""Real auth-service and PostgREST round trip — the production path, end to end.

A throwaway account logs in through the REAL shared auth service, so this
exercises postgrest-auth verify (argon2id) -> JWT claims -> in-app session
validation -> one GET through the REAL PostgREST with the token.

This suite runs against the shared apps Postgres, which is the database the
household's real request queue lives in, so a test may only ever create and
remove its own ztest account and read, never write, the requests table. RLS
(deploy/04_rls.sql, proven row by row in test_db_real.py) hides every other
account's rows from a fresh non-admin, so the read returns nothing.
"""

import time
import uuid

import pytest

from engine import bootstrap
from engine import store as pgrest
from engine.media.requests import RequestStore
from engine.web import auth
from engine.web.users import UserStore, db_reachable

PW = "postgrest-roundtrip-pw"


def _login(users: UserStore, username: str) -> RequestStore:
    try:
        token = auth.login_via_service(username, PW, ip="")
    except auth.AuthServiceError as exc:
        raise AssertionError(f"auth service login failed — red, not skipped: {exc.detail}") from exc
    user = auth.validate_token(token, users)
    assert user is not None, "service token must validate in-app"
    return RequestStore(token, user)


@pytest.fixture(scope="module")
def users() -> UserStore:
    ok, detail = db_reachable()
    assert ok, f"database unreachable — red, not skipped: {detail}"
    bootstrap.apply_schema()
    reachable, detail = pgrest.postgrest_reachable()
    assert reachable, f"postgrest unreachable — red, not skipped: {detail}"
    return UserStore()


def test_postgrest_serves_the_syncplex_schema_to_the_user(users):
    """GET /requests through engine/store.py with Accept-Profile set. A
    PostgREST recreated without the schema in PGRST_DB_SCHEMAS, or a wrong
    APP_SCHEMA, answers 404/406 and RequestStore raises StoreError here
    instead of at the first real request."""
    username = f"ztest{uuid.uuid4().hex[:10]}"
    users.add(username, PW)
    try:
        store = _login(users, username)
        assert store.list() == []  # fresh account: RLS hides every other user's rows
    finally:
        users.remove(username)


def test_disable_revokes_the_live_token(users):
    """password_changed_at moves, so the token issued before it stops
    validating even though it has not expired."""
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
