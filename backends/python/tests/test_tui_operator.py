"""The TUI's identity (engine/media/tui/operator): the token it mints is the
one postgrest-auth would issue for an admin, signed with the same secret, and
the queue stays off with a reason when .env lacks a piece. No network."""

import jwt
import pytest

from engine import config
from engine.media.tui import operator as operators

SECRET = "test-secret-long-enough-for-hs256-to-like"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "POSTGREST_URL", "https://pgrest.example")
    monkeypatch.setattr(config, "JWT_SECRET", SECRET)
    monkeypatch.setattr(config, "OPERATOR", "jo")


def test_loads_the_operator_named_in_env(env):
    operator, reason = operators.load()
    assert operator == operators.Operator("jo") and reason == ""


def test_the_token_is_an_admin_token_postgrest_accepts(env):
    operator, _ = operators.load()
    claims = jwt.decode(operator.token(), SECRET, algorithms=["HS256"])
    assert claims["role"] == f"{config.APP_SCHEMA}_user"  # the SET ROLE PostgREST performs
    assert claims["app_role"] == "admin"  # what 04_rls.sql's admin policy reads
    assert claims["username"] == "jo"
    assert "user_id" not in claims  # only a requester's own rows key on it
    assert claims["exp"] - claims["iat"] == operators.TOKEN_TTL.total_seconds()
    assert operator.store().user_id == ""


def test_each_store_carries_a_fresh_token(env):
    operator, _ = operators.load()
    assert operator.store().token  # minted, not cached from an earlier call
    assert jwt.decode(operator.store().token, SECRET, algorithms=["HS256"])["username"] == "jo"


def test_a_missing_piece_names_itself(env, monkeypatch):
    monkeypatch.setattr(config, "OPERATOR", "")
    operator, reason = operators.load()
    assert operator is None and reason == "SYNCPLEX_OPERATOR not set in .env"
    monkeypatch.setattr(config, "JWT_SECRET", "")
    assert operators.load()[1] == "POSTGREST_JWT_SECRET, SYNCPLEX_OPERATOR not set in .env"
