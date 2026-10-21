"""Sessions and JWT transport; password verification runs in-process through
the postgrest-auth library.

Login checks the password against syncplex.users over the superuser connection
with postgrest_auth.login, which owns the KDF policy (argon2id, legacy bcrypt
rehashed on login), the per-username + per-IP lockout, and the no-enumeration
timing defense. What comes back is an HS256 JWT signed with the shared
PostgREST secret, carrying role=syncplex_user plus
user_id/username/app_role/iat. That single token is both the session
credential and the Bearer token every PostgREST call uses, so RLS scopes the
request queue off the same claims the login produced.

The token is held in NiceGUI's server-side session storage, exactly where the
username used to live, so nothing about the browser-side cookie changed.

Sessions expire after 30 days and die early when password_changed_at moves past
their issue time — password change, disable, re-enable, and role change all
revoke.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta

import jwt
import postgrest_auth
import psycopg

from .. import config
from .users import User, UserStore

SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600


class LoginError(Exception):
    """Login rejected or unavailable; carries the HTTP status."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def session_secret() -> str:
    """Cookie-signing secret for NiceGUI's session id. Without the env var a
    random one is used — fine functionally, but every restart logs everyone
    out."""
    configured = os.environ.get("SYNCPLEX_SESSION_SECRET", "").strip().strip('"').strip("'")
    return configured or secrets.token_urlsafe(32)


def login(username: str, password: str, ip: str) -> str:
    """Exchange credentials for a session JWT.

    The viewer's IP feeds the per-IP lockout. ttl_hours keeps the 30-day
    session policy this app has always had.
    """
    try:
        return postgrest_auth.login(
            lambda: psycopg.connect(config.superuser_dsn()),
            config.APP_SCHEMA,
            username,
            password,
            jwt_secret=config.JWT_SECRET,
            client_ip=ip or None,
            ttl_hours=SESSION_MAX_AGE_SECONDS // 3600,
        )
    except postgrest_auth.InvalidCredentials:
        raise LoginError(401, "Invalid username or password") from None
    except postgrest_auth.LoginLocked as exc:
        raise LoginError(429, str(exc)) from None
    except psycopg.Error as exc:
        raise LoginError(503, "login unavailable") from exc


def attempt_login(username: str, password: str, ip: str) -> tuple[str | None, str]:
    """One login attempt. Returns (token, "") on success, (None, reason) otherwise."""
    try:
        return login(username.strip().lower(), password, ip), ""
    except LoginError as exc:
        return None, exc.detail


def issue_session(storage: dict, token: str) -> None:
    storage["token"] = token


def clear_session(storage: dict) -> None:
    storage.pop("token", None)


def session_token(storage: dict) -> str:
    return storage.get("token", "")


def validate_token(token: str, store: UserStore) -> User | None:
    """Resolve a token to a live account, or None if it must re-login.

    Rejects tokens that are expired or malformed, belong to a deleted or
    disabled account, or predate the account's last password change, disable
    toggle, or role change.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    username = payload.get("username", "")
    issued_ts = payload.get("iat")
    if not username or issued_ts is None:
        return None
    issued = datetime.fromtimestamp(float(issued_ts), tz=UTC)
    if (datetime.now(UTC) - issued).total_seconds() > SESSION_MAX_AGE_SECONDS:
        return None
    user = store.get(username)
    if user is None or user.disabled:
        return None
    # Small grace: iat is second-granular, password_changed_at is not.
    if issued + timedelta(seconds=1) < user.password_changed_at:
        return None
    return user


def current_user(storage: dict, store: UserStore) -> User | None:
    return validate_token(session_token(storage), store)
