"""Web UI user accounts — argon2id hashes in a JSON file.

This is the authentication database that replaced the Authelia forward-auth
layer: same algorithm (argon2id), same single-file model, but owned by the
app so it can also carry roles. Two roles exist:

- ``admin``  — full UI: direct adds, plus the approval queue. Admins approve
  requests and pick which Sonarr/Radarr instance fulfils them.
- ``user``   — search everything, but can only *request* titles; nothing is
  downloaded until an admin approves the request.

The file lives at ``<data dir>/users.json`` (see ``engine.config.get_data_dir``)
and is managed with the ``syncplex users`` CLI — see README "Users & roles".
No plaintext secrets are ever stored; timing-safe verification and a dummy
hash for unknown usernames keep logins from leaking which accounts exist.
"""

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import BaseModel, Field

from ..config import get_data_dir

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")

_hasher = PasswordHasher()  # argon2id, library defaults (64 MiB, t=3, p=4)

# Verified when a login names an unknown account, so the response time does
# not reveal whether the username exists.
_DUMMY_HASH = _hasher.hash("syncplex-no-such-user")

MIN_PASSWORD_LENGTH = 10


class User(BaseModel):
    username: str
    password_hash: str
    role: str = ROLE_USER
    display_name: str = ""
    disabled: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Sessions issued before this instant are rejected, so changing a
    # password (or re-enabling an account) logs out every existing session.
    password_changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


def validate_username(username: str) -> str:
    username = username.strip().lower()
    if not _USERNAME_RE.match(username):
        raise ValueError("Username must be 1-32 chars: lowercase letters, digits, '.', '_' or '-' (starts alnum)")
    return username


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")


class UserStore:
    """All accounts in one JSON file; every mutation is an atomic rewrite.

    The store re-reads the file when its mtime changes, so `syncplex users`
    edits (run on the host or via docker exec) are picked up by the running
    web process without a restart.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or (get_data_dir() / "users.json")
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._loaded_mtime: float | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            self._users = {}
            self._loaded_mtime = None
            return
        raw = json.loads(self.path.read_text())
        self._users = {u["username"]: User.model_validate(u) for u in raw.get("users", [])}
        self._loaded_mtime = self.path.stat().st_mtime

    def _refresh(self) -> None:
        mtime = self.path.stat().st_mtime if self.path.is_file() else None
        if mtime != self._loaded_mtime:
            self._load()

    def _save(self) -> None:
        payload = {"users": [u.model_dump(mode="json") for u in self._users.values()]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        os.chmod(tmp, 0o600)  # hashes only, but no reason to share them
        tmp.replace(self.path)
        self._loaded_mtime = self.path.stat().st_mtime

    # --- queries ---

    def get(self, username: str) -> User | None:
        with self._lock:
            self._refresh()
            return self._users.get(username)

    def list(self) -> list[User]:
        with self._lock:
            self._refresh()
            return sorted(self._users.values(), key=lambda u: u.username)

    def admin_count(self) -> int:
        return sum(1 for u in self.list() if u.is_admin and not u.disabled)

    # --- mutations ---

    def add(self, username: str, password: str, role: str = ROLE_USER, display_name: str = "") -> User:
        username = validate_username(username)
        validate_password(password)
        if role not in ROLES:
            raise ValueError(f"Role must be one of {ROLES}")
        with self._lock:
            self._refresh()
            if username in self._users:
                raise ValueError(f"User '{username}' already exists")
            user = User(
                username=username,
                password_hash=_hasher.hash(password),
                role=role,
                display_name=display_name or username,
            )
            self._users[username] = user
            self._save()
            return user

    def set_password(self, username: str, password: str) -> None:
        validate_password(password)
        self._update(username, password_hash=_hasher.hash(password), password_changed_at=datetime.now(UTC))

    def set_disabled(self, username: str, disabled: bool) -> None:
        # Invalidate sessions in both directions: disabling must lock out
        # live sessions, re-enabling must not resurrect pre-disable cookies.
        self._update(username, disabled=disabled, password_changed_at=datetime.now(UTC))

    def set_role(self, username: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"Role must be one of {ROLES}")
        self._update(username, role=role)

    def remove(self, username: str) -> None:
        with self._lock:
            self._refresh()
            if username not in self._users:
                raise KeyError(f"No such user: {username}")
            del self._users[username]
            self._save()

    def _update(self, username: str, **changes) -> None:
        with self._lock:
            self._refresh()
            user = self._users.get(username)
            if user is None:
                raise KeyError(f"No such user: {username}")
            self._users[username] = user.model_copy(update=changes)
            self._save()

    # --- authentication ---

    def verify(self, username: str, password: str) -> User | None:
        """Timing-safe credential check. Returns the user only when the
        password matches and the account is enabled."""
        user = self.get(username.strip().lower())
        try:
            _hasher.verify(user.password_hash if user else _DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            return None
        if user is None or user.disabled:
            return None
        if _hasher.check_needs_rehash(user.password_hash):
            self._update(user.username, password_hash=_hasher.hash(password))
        return user
