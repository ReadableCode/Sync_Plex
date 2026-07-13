"""Session handling + brute-force protection for the web UI.

Hardening that lets the app face the internet without Authelia in front:

- argon2id password verification (users.py), dummy-hash on unknown users
- login throttling: 5 failures per username or client IP within 15 minutes
  locks that key for 15 minutes (mirrors Authelia's regulation block)
- sessions live server-side in NiceGUI's user storage; the browser only
  holds a signed session-id cookie (``SYNCPLEX_SESSION_SECRET`` signs it —
  set it in the deployment env or sessions reset on every restart)
- sessions expire after 30 days, and are invalidated early when the account
  is disabled, removed, or its password changes
"""

import math
import os
import secrets
import time
from datetime import UTC, datetime

from .users import User, UserStore

SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600

LOCKOUT_MAX_FAILURES = 5
LOCKOUT_WINDOW_SECONDS = 15 * 60
LOCKOUT_DURATION_SECONDS = 15 * 60


def session_secret() -> str:
    """Cookie-signing secret. Without the env var a random one is used —
    fine functionally, but every restart logs everyone out."""
    configured = os.environ.get("SYNCPLEX_SESSION_SECRET", "").strip().strip('"').strip("'")
    return configured or secrets.token_urlsafe(32)


class LoginRateLimiter:
    """In-memory failure tracker, keyed per username AND per client IP.

    Both keys are checked so one attacker hammering many usernames trips the
    IP lock, and many attackers on one username trip the username lock.
    """

    def __init__(
        self,
        max_failures: int = LOCKOUT_MAX_FAILURES,
        window: float = LOCKOUT_WINDOW_SECONDS,
        duration: float = LOCKOUT_DURATION_SECONDS,
        clock=time.monotonic,
    ):
        self.max_failures = max_failures
        self.window = window
        self.duration = duration
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def _keys(self, username: str, ip: str) -> list[str]:
        return [f"user:{username.strip().lower()}", f"ip:{ip}"]

    def seconds_locked(self, username: str, ip: str) -> int:
        now = self._clock()
        remaining = max(self._locked_until.get(k, 0.0) - now for k in self._keys(username, ip))
        return max(0, math.ceil(remaining))

    def record_failure(self, username: str, ip: str) -> None:
        now = self._clock()
        for key in self._keys(username, ip):
            recent = [t for t in self._failures.get(key, []) if now - t < self.window]
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= self.max_failures:
                self._locked_until[key] = now + self.duration

    def record_success(self, username: str, ip: str) -> None:
        for key in self._keys(username, ip):
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


def attempt_login(
    store: UserStore, limiter: LoginRateLimiter, username: str, password: str, ip: str
) -> tuple[User | None, str]:
    """One login attempt. Returns (user, "") on success, (None, reason) otherwise."""
    username = username.strip().lower()
    locked = limiter.seconds_locked(username, ip)
    if locked:
        return None, f"Too many failed attempts — try again in {max(1, locked // 60)} min"
    user = store.verify(username, password)
    if user is None:
        limiter.record_failure(username, ip)
        return None, "Invalid username or password"
    limiter.record_success(username, ip)
    return user, ""


def issue_session(storage: dict, user: User) -> None:
    storage["username"] = user.username
    storage["issued_at"] = datetime.now(UTC).isoformat()


def clear_session(storage: dict) -> None:
    storage.pop("username", None)
    storage.pop("issued_at", None)


def current_user(storage: dict, store: UserStore) -> User | None:
    """Resolve the session to a live account, or None if it must re-login.

    Rejects sessions that are expired, belong to a deleted/disabled account,
    or predate the account's last password change / disable-toggle.
    """
    username = storage.get("username")
    issued_raw = storage.get("issued_at")
    if not username or not issued_raw:
        return None
    try:
        issued = datetime.fromisoformat(issued_raw)
    except ValueError:
        return None
    now = datetime.now(UTC)
    if (now - issued).total_seconds() > SESSION_MAX_AGE_SECONDS:
        return None
    user = store.get(username)
    if user is None or user.disabled or issued < user.password_changed_at:
        return None
    return user
