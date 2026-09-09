"""The TUI's login: the same auth service and JWT the web UI uses, kept in
~/.config/syncplex/tui_session.json so a terminal session survives restarts
for the token's 30 days. The claims name the user and role; PostgREST's
row-level security enforces them, so the TUI only has to carry the token.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import jwt

from ... import config
from ...web.auth import SESSION_MAX_AGE_SECONDS, AuthServiceError, login_via_service
from ...web.users import ROLE_ADMIN
from ..requests import RequestStore

SESSION_FILE = "tui_session.json"


@dataclass(frozen=True)
class SessionUser:
    """What RequestStore needs from a user: the id its rows carry and the name."""

    id: str
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


@dataclass
class Session:
    token: str
    user: SessionUser

    def store(self) -> RequestStore:
        return RequestStore(self.token, self.user)  # type: ignore[arg-type]


def _session_path() -> Path:
    return config.get_data_dir() / SESSION_FILE


def decode(token: str) -> SessionUser | None:
    """The user a token names, or None when it is malformed or older than a session may be."""
    try:
        if config.JWT_SECRET:
            claims = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
        else:
            claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return None
    issued = claims.get("iat")
    if issued is None or (datetime.now(UTC) - datetime.fromtimestamp(float(issued), tz=UTC)).total_seconds() > (
        SESSION_MAX_AGE_SECONDS
    ):
        return None
    username = claims.get("username", "")
    user_id = claims.get("user_id", "")
    if not username or not user_id:
        return None
    return SessionUser(id=str(user_id), username=username, role=claims.get("app_role", ""))


def load_saved() -> Session | None:
    path = _session_path()
    try:
        token = json.loads(path.read_text(encoding="utf-8")).get("token", "")
    except OSError, ValueError:
        return None
    user = decode(token)
    if user is None:
        return None
    return Session(token, user)


def save(session: Session) -> None:
    path = _session_path()
    path.write_text(json.dumps({"token": session.token}), encoding="utf-8")
    path.chmod(0o600)


def clear() -> None:
    try:
        _session_path().unlink()
    except OSError:
        pass


def configured() -> bool:
    """The queue needs PostgREST and the auth service; without them the TUI is search-and-add only."""
    return bool(config.POSTGREST_URL and config.AUTH_URL)


def login(username: str, password: str) -> tuple[Session | None, str]:
    """Trade credentials for a session; (None, reason) when the service says no."""
    try:
        token = login_via_service(username.strip().lower(), password, ip="")
    except AuthServiceError as exc:
        return None, exc.detail
    user = decode(token)
    if user is None:
        return None, "the login service returned a token this client could not read"
    session = Session(token, user)
    save(session)
    return session, ""
