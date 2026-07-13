"""User store + login hardening (engine/web/users, engine/web/auth)."""

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from engine.web.auth import LoginRateLimiter, attempt_login, clear_session, current_user, issue_session
from engine.web.users import ROLE_ADMIN, ROLE_USER, UserStore

PW = "correct horse battery"


@pytest.fixture()
def store(tmp_path):
    return UserStore(tmp_path / "users.json")


def test_add_and_verify(store):
    user = store.add("Jason", PW, role=ROLE_ADMIN)
    assert user.username == "jason"  # normalized to lowercase
    assert user.is_admin
    assert "$argon2id$" in user.password_hash
    assert PW not in user.password_hash

    assert store.verify("jason", PW).username == "jason"
    assert store.verify("JASON", PW).username == "jason"
    assert store.verify("jason", "wrong password!") is None
    assert store.verify("nobody", PW) is None


def test_persistence_and_external_edits(store, tmp_path):
    store.add("friend", PW)
    # a second store (fresh process) sees the same accounts
    again = UserStore(tmp_path / "users.json")
    assert again.get("friend") is not None
    # and edits made by that process are picked up by the first (mtime check)
    again.add("other", PW, role=ROLE_ADMIN)
    os.utime(again.path, (time.time() + 5, time.time() + 5))
    assert store.get("other") is not None


def test_validation(store):
    with pytest.raises(ValueError):
        store.add("bad name!", PW)
    with pytest.raises(ValueError):
        store.add("shortpw", "tiny")
    store.add("dupe", PW)
    with pytest.raises(ValueError):
        store.add("dupe", PW)
    with pytest.raises(ValueError):
        store.add("x", PW, role="superuser")


def test_disable_blocks_login(store):
    store.add("friend", PW)
    store.set_disabled("friend", True)
    assert store.verify("friend", PW) is None
    store.set_disabled("friend", False)
    assert store.verify("friend", PW) is not None


def test_role_change_and_remove(store):
    store.add("friend", PW)
    store.set_role("friend", ROLE_ADMIN)
    assert store.get("friend").is_admin
    store.remove("friend")
    assert store.get("friend") is None
    with pytest.raises(KeyError):
        store.remove("friend")


def test_users_file_is_private(store):
    store.add("jason", PW)
    assert (store.path.stat().st_mode & 0o777) == 0o600


# --- rate limiting ---


def test_lockout_after_failures(store):
    store.add("jason", PW)
    clock = {"now": 0.0}
    limiter = LoginRateLimiter(clock=lambda: clock["now"])

    for _ in range(5):
        user, error = attempt_login(store, limiter, "jason", "wrong password!", "1.2.3.4")
        assert user is None
    # correct password is now rejected too — locked
    user, error = attempt_login(store, limiter, "jason", PW, "1.2.3.4")
    assert user is None
    assert "try again" in error
    # different IP but same username is still locked (username key)
    user, _ = attempt_login(store, limiter, "jason", PW, "5.6.7.8")
    assert user is None
    # lock expires
    clock["now"] += 16 * 60
    user, error = attempt_login(store, limiter, "jason", PW, "1.2.3.4")
    assert user is not None and error == ""


def test_ip_lock_covers_many_usernames(store):
    store.add("jason", PW)
    clock = {"now": 0.0}
    limiter = LoginRateLimiter(clock=lambda: clock["now"])
    for i in range(5):
        attempt_login(store, limiter, f"guess{i}", "wrong password!", "9.9.9.9")
    user, error = attempt_login(store, limiter, "jason", PW, "9.9.9.9")
    assert user is None and "try again" in error


def test_success_resets_counters(store):
    store.add("jason", PW)
    limiter = LoginRateLimiter()
    for _ in range(4):
        attempt_login(store, limiter, "jason", "wrong password!", "1.1.1.1")
    user, _ = attempt_login(store, limiter, "jason", PW, "1.1.1.1")
    assert user is not None
    for _ in range(4):
        attempt_login(store, limiter, "jason", "wrong password!", "1.1.1.1")
    user, _ = attempt_login(store, limiter, "jason", PW, "1.1.1.1")
    assert user is not None


# --- sessions ---


def test_session_roundtrip(store):
    user = store.add("jason", PW, role=ROLE_ADMIN)
    storage: dict = {}
    issue_session(storage, user)
    assert current_user(storage, store).username == "jason"
    clear_session(storage)
    assert current_user(storage, store) is None


def test_password_change_invalidates_sessions(store):
    user = store.add("jason", PW)
    storage: dict = {}
    issue_session(storage, user)
    store.set_password("jason", "a whole new password")
    assert current_user(storage, store) is None


def test_disable_invalidates_sessions(store):
    user = store.add("jason", PW)
    storage: dict = {}
    issue_session(storage, user)
    store.set_disabled("jason", True)
    assert current_user(storage, store) is None
    # re-enabling does not resurrect the old session
    store.set_disabled("jason", False)
    assert current_user(storage, store) is None


def test_expired_session_rejected(store):
    user = store.add("jason", PW)
    storage: dict = {}
    issue_session(storage, user)
    storage["issued_at"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    assert current_user(storage, store) is None


def test_garbage_session_rejected(store):
    store.add("jason", PW)
    assert current_user({"username": "jason", "issued_at": "not-a-date"}, store) is None
    assert current_user({"username": "ghost", "issued_at": datetime.now(UTC).isoformat()}, store) is None
    assert current_user({}, store) is None


def test_role_default_is_user(store):
    assert store.add("friend", PW).role == ROLE_USER
