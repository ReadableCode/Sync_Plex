"""Login error mapping (engine/web/auth) — no DB, no network: postgrest_auth.login
is faked. The real verify against syncplex.users is in test_postgrest_real.py."""

import postgrest_auth
import psycopg
import pytest

from engine import config
from engine.web import auth


def _fake_login(monkeypatch, outcome):
    seen = {}

    def fake(connect, schema, username, password, **kwargs):
        seen.update(schema=schema, username=username, password=password, **kwargs)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(auth.postgrest_auth, "login", fake)
    return seen


def test_login_success(monkeypatch):
    seen = _fake_login(monkeypatch, "the-token")
    assert auth.login("jason", "pw", "1.2.3.4") == "the-token"
    assert seen["schema"] == config.APP_SCHEMA
    assert seen["client_ip"] == "1.2.3.4"
    assert seen["ttl_hours"] == 720
    assert seen["jwt_secret"] == config.JWT_SECRET


def test_login_without_ip_passes_none(monkeypatch):
    seen = _fake_login(monkeypatch, "the-token")
    auth.login("jason", "pw", "")
    assert seen["client_ip"] is None


def test_login_maps_errors(monkeypatch):
    for outcome, status, detail in (
        (postgrest_auth.InvalidCredentials("x"), 401, "Invalid username or password"),
        (postgrest_auth.LoginLocked(150), 429, "too many attempts — locked for 3 more minute(s)"),
        (psycopg.OperationalError("boom"), 503, "login unavailable"),
    ):
        _fake_login(monkeypatch, outcome)
        with pytest.raises(auth.LoginError) as exc:
            auth.login("jason", "pw", "1.2.3.4")
        assert exc.value.status_code == status
        assert exc.value.detail == detail


def test_attempt_login_normalizes_and_reports(monkeypatch):
    seen = _fake_login(monkeypatch, "the-token")
    assert auth.attempt_login("  Jason ", "pw", "1.2.3.4") == ("the-token", "")
    assert seen["username"] == "jason"

    _fake_login(monkeypatch, postgrest_auth.InvalidCredentials("x"))
    assert auth.attempt_login("jason", "pw", "1.2.3.4") == (None, "Invalid username or password")
