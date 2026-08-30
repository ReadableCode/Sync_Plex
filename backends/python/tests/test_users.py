"""Account-model rules that need no database (engine/web/users).

Everything that touches syncplex.users for real lives in test_db_real.py, and
the session/RLS path in test_postgrest_real.py.
"""

import pytest

from engine.web.users import ROLE_ADMIN, ROLE_USER, User, validate_password, validate_username


def _user(**over) -> User:
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "username": "jason",
        "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$abc$def",
        "role": ROLE_USER,
        "created_at": "2026-01-01T00:00:00Z",
        "password_changed_at": "2026-01-01T00:00:00Z",
    }
    return User(**{**base, **over})


def test_username_normalized_and_validated():
    assert validate_username("  Jason  ") == "jason"
    assert validate_username("a.b_c-1") == "a.b_c-1"
    for bad in ("bad name!", "", "-leading", "A" * 33, "ünïcode"):
        with pytest.raises(ValueError):
            validate_username(bad)


def test_password_length_rule():
    validate_password("long-enough-password")
    with pytest.raises(ValueError):
        validate_password("tiny")


def test_is_admin_reads_the_role():
    assert _user(role=ROLE_ADMIN).is_admin
    assert not _user().is_admin


def test_role_default_is_user():
    assert _user().role == ROLE_USER
