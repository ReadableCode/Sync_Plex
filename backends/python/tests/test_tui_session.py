"""The TUI's login token handling (engine/media/tui/session): what a token
must carry, how old it may be, and that the saved file round-trips. No
network: tokens are minted locally with a test secret."""

import time

import jwt
import pytest

from engine import config
from engine.media.tui import session as sessions


def _token(secret: str, **over) -> str:
    claims = {"username": "jo", "user_id": "u-1", "app_role": "admin", "iat": int(time.time())}
    claims.update(over)
    return jwt.encode(claims, secret, algorithm="HS256")


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(config, "JWT_SECRET", "test-secret")
    return "test-secret"


def test_decode_reads_the_user_and_role(secret):
    user = sessions.decode(_token(secret))
    assert user == sessions.SessionUser(id="u-1", username="jo", role="admin")
    assert user.is_admin
    assert not sessions.decode(_token(secret, app_role="user")).is_admin


def test_decode_rejects_the_wrong_signature_and_stale_or_partial_tokens(secret):
    assert sessions.decode(_token("other-secret")) is None
    assert sessions.decode("not-a-token") is None
    assert sessions.decode(_token(secret, iat=int(time.time()) - sessions.SESSION_MAX_AGE_SECONDS - 60)) is None
    assert sessions.decode(_token(secret, username="")) is None


def test_decode_without_a_secret_trusts_the_claims(monkeypatch):
    # A workstation without the PostgREST secret still reads the token; the
    # server enforces it on every queue call anyway.
    monkeypatch.setattr(config, "JWT_SECRET", "")
    assert sessions.decode(_token("whatever")).username == "jo"


def test_saved_session_round_trips_and_clears(secret, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    assert sessions.load_saved() is None
    session = sessions.Session(_token(secret), sessions.decode(_token(secret)))
    sessions.save(session)
    assert (tmp_path / sessions.SESSION_FILE).stat().st_mode & 0o777 == 0o600
    loaded = sessions.load_saved()
    assert loaded is not None and loaded.user.username == "jo"
    sessions.clear()
    assert sessions.load_saved() is None
