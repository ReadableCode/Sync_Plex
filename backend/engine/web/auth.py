"""Sessions and JWT transport; password verification is delegated to the
shared postgrest-auth service.

Login POSTs the credentials to that service, which owns the KDF policy
(argon2id), the per-username + per-IP lockout, and the no-enumeration timing
defense — one copy for the whole fleet instead of one per app. What comes back
is an HS256 JWT signed with the shared PostgREST secret, carrying
role=syncplex_user plus user_id/username/app_role/iat. That single token is
both the session credential and the Bearer token every PostgREST call uses, so
RLS scopes the request queue off the same claims the login produced.

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

import httpx
import jwt

from .. import config
from .users import User, UserStore

SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600


class AuthServiceError(Exception):
    """Login rejected or the service unreachable; carries the HTTP status."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=config.HTTP_TIMEOUT)
    return _client


def session_secret() -> str:
    """Cookie-signing secret for NiceGUI's session id. Without the env var a
    random one is used — fine functionally, but every restart logs everyone
    out."""
    configured = os.environ.get("SYNCPLEX_SESSION_SECRET", "").strip().strip('"').strip("'")
    return configured or secrets.token_urlsafe(32)


def login_via_service(username: str, password: str, ip: str) -> str:
    """Exchange credentials for a session JWT at the shared auth service.

    The viewer's IP is forwarded so the service's per-IP lockout counts the
    browser rather than this container. ttl_hours keeps the 30-day session
    policy this app has always had.
    """
    try:
        resp = _http().post(
            f"{config.AUTH_URL}/token",
            json={
                "schema": config.APP_SCHEMA,
                "username": username,
                "password": password,
                "ttl_hours": SESSION_MAX_AGE_SECONDS // 3600,
            },
            headers={"X-Forwarded-For": ip} if ip else {},
        )
    except httpx.HTTPError as exc:
        raise AuthServiceError(503, "login service unavailable") from exc
    if resp.status_code == 200:
        return resp.json()["token"]
    if resp.status_code == 401:
        raise AuthServiceError(401, "Invalid username or password")
    if resp.status_code == 429:
        try:
            detail = resp.json().get("detail", "Too many failed attempts — try again shortly")
        except ValueError:
            detail = "Too many failed attempts — try again shortly"
        raise AuthServiceError(429, detail)
    raise AuthServiceError(503, "login service unavailable")


def attempt_login(username: str, password: str, ip: str) -> tuple[str | None, str]:
    """One login attempt. Returns (token, "") on success, (None, reason) otherwise."""
    try:
        return login_via_service(username.strip().lower(), password, ip), ""
    except AuthServiceError as exc:
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
