"""Web UI user accounts, backed by syncplex.users in the shared `apps` database.

Two roles exist:

- ``admin``  — full UI: direct adds, plus the approval queue. Admins approve
  requests and pick which Sonarr/Radarr instance fulfils them.
- ``user``   — search everything, but can only *request* titles; nothing is
  downloaded until an admin approves the request.

Password *verification* is not here. It lives in the shared postgrest-auth
service (see ``engine.web.auth``), which owns the KDF policy, the lockout, and
the no-enumeration timing defense for every app at once. This store manages
accounts (the ``syncplex users`` CLI) and answers the per-request session check.

Hashes stay argon2id and are written by this module when an account is created
or its password changed, so nothing about the stored credentials changed in the
move off users.json. The table is REVOKEd from the PostgREST roles
(deploy/03_secure_users.sql); only this process, on the superuser connection,
can read it.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

import psycopg
from argon2 import PasswordHasher
from pydantic import BaseModel

from .. import config

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")

_hasher = PasswordHasher()  # argon2id, library defaults (64 MiB, t=3, p=4)

MIN_PASSWORD_LENGTH = 10

# Session validation runs on every page render; a short cache keeps that from
# being a database round trip per request without letting a disable linger.
_CACHE_TTL_SECONDS = 30.0


class User(BaseModel):
    id: str
    username: str
    password_hash: str
    role: str = ROLE_USER
    display_name: str = ""
    disabled: bool = False
    created_at: datetime
    # Sessions issued before this instant are rejected, so changing a
    # password (or re-enabling an account) logs out every existing session.
    password_changed_at: datetime

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


_COLS = "id, username, password_hash, role, display_name, disabled, created_at, password_changed_at"


def _row_to_user(row) -> User:
    return User(
        id=str(row[0]),
        username=row[1],
        password_hash=row[2],
        role=row[3],
        display_name=row[4],
        disabled=row[5],
        created_at=row[6],
        password_changed_at=row[7],
    )


class UserStore:
    """Account management over the superuser connection.

    Method signatures are the ones the CLI and the web app already used when
    this was a JSON file, so callers did not have to change. The database is
    the concurrency control now — there is no in-process lock and no reload
    check, because there is no longer a file for a second process to edit.
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, User | None]] = {}

    def _conn(self):
        return psycopg.connect(config.superuser_dsn())

    def _invalidate(self, username: str) -> None:
        self._cache.pop(username.strip().lower(), None)

    # --- queries ---

    def get(self, username: str) -> User | None:
        username = username.strip().lower()
        now = time.monotonic()
        hit = self._cache.get(username)
        if hit and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS} FROM {config.APP_SCHEMA}.users WHERE username = %s", (username,))
            row = cur.fetchone()
        user = _row_to_user(row) if row else None
        self._cache[username] = (now, user)
        return user

    def list(self) -> list[User]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS} FROM {config.APP_SCHEMA}.users ORDER BY username")
            return [_row_to_user(r) for r in cur.fetchall()]

    def admin_count(self) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {config.APP_SCHEMA}.users WHERE role = %s AND NOT disabled",
                (ROLE_ADMIN,),
            )
            return cur.fetchone()[0]

    # --- mutations ---

    def add(self, username: str, password: str, role: str = ROLE_USER, display_name: str = "") -> User:
        validate_password(password)
        return self.add_prehashed(username, _hasher.hash(password), role=role, display_name=display_name)

    def add_prehashed(
        self,
        username: str,
        password_hash: str,
        role: str = ROLE_USER,
        display_name: str = "",
        created_at: datetime | None = None,
        password_changed_at: datetime | None = None,
        disabled: bool = False,
    ) -> User:
        """Insert with an already-computed hash.

        The import script uses this: a hash moved from users.json is opaque
        data, never parsed and never recomputed, and its timestamps come across
        with it so no live session is revoked by the move.
        """
        username = validate_username(username)
        if role not in ROLES:
            raise ValueError(f"Role must be one of {ROLES}")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM {config.APP_SCHEMA}.users WHERE username = %s",
                (username,),
            )
            if cur.fetchone() is not None:
                raise ValueError(f"User '{username}' already exists")
            cur.execute(
                f"""INSERT INTO {config.APP_SCHEMA}.users
                    (username, password_hash, role, display_name, disabled, created_at, password_changed_at)
                    VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()))
                    RETURNING {_COLS}""",
                (username, password_hash, role, display_name or username, disabled, created_at, password_changed_at),
            )
            row = cur.fetchone()
        self._invalidate(username)
        return _row_to_user(row)

    def set_password(self, username: str, password: str) -> None:
        validate_password(password)
        self._update(
            username,
            "SET password_hash = %s, password_changed_at = now()",
            (_hasher.hash(password),),
        )

    def set_disabled(self, username: str, disabled: bool) -> None:
        # Invalidate sessions in both directions: disabling must lock out
        # live sessions, re-enabling must not resurrect pre-disable cookies.
        self._update(username, "SET disabled = %s, password_changed_at = now()", (disabled,))

    def set_role(self, username: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"Role must be one of {ROLES}")
        # Bumping password_changed_at here is new, and it matters: the role now
        # rides in the session token as the app_role claim that RLS keys the
        # admin policy on. Without revoking the session, a demoted admin would
        # keep admin reach at the database level until their token expired.
        self._update(username, "SET role = %s, password_changed_at = now()", (role,))

    def remove(self, username: str) -> None:
        username = username.strip().lower()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {config.APP_SCHEMA}.users WHERE username = %s", (username,))
            if cur.rowcount == 0:
                raise KeyError(f"No such user: {username}")
        self._invalidate(username)

    def _update(self, username: str, set_clause: str, params: tuple) -> None:
        username = username.strip().lower()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {config.APP_SCHEMA}.users {set_clause} WHERE username = %s",
                (*params, username),
            )
            if cur.rowcount == 0:
                raise KeyError(f"No such user: {username}")
        self._invalidate(username)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def db_reachable() -> tuple[bool, str]:
    if not config.db_configured():
        return False, "POSTGRES_* env not configured"
    try:
        with psycopg.connect(config.superuser_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True, "ok"
    except psycopg.Error as exc:
        return False, str(exc).strip()
