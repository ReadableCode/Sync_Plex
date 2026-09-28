"""Who the TUI acts as: the holder of this .env is the admin, and nobody
signs in.

The request queue is reached through PostgREST, which authorises every call
by the JWT it carries. The web UI gets that token from the shared auth
service in exchange for a password; the TUI mints the same token itself,
signed with POSTGREST_JWT_SECRET from .env, the secret postgrest-auth signs
with. The claims are the ones the service would issue for an admin (the
admin policy in deploy/04_rls.sql keys on app_role) minus user_id, which
only a requester's own rows need. PostgREST and row-level security therefore
see a logged-in admin, and the queue code is the code the web runs.

SYNCPLEX_OPERATOR names that admin: approvals and denials are recorded under
it, the way the web records the account that clicked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from ... import config
from ...web.users import ROLE_ADMIN
from ..requests import RequestStore

TOKEN_TTL = timedelta(hours=1)  # minted per store, so this only bounds a leaked token


@dataclass(frozen=True)
class Operator:
    username: str

    def token(self) -> str:
        now = datetime.now(UTC)
        claims = {
            "role": f"{config.APP_SCHEMA}_user",
            "username": self.username,
            "app_role": ROLE_ADMIN,
            "iat": now,
            "exp": now + TOKEN_TTL,
        }
        return jwt.encode(claims, config.JWT_SECRET, algorithm="HS256")

    def store(self) -> RequestStore:
        """A fresh token each time: the store is built per action, and the token is cheap."""
        return RequestStore(self.token(), user_id="")


def load() -> tuple[Operator | None, str]:
    """The operator this .env names, or (None, what is missing).

    Missing pieces disable the queue screens, never the media screen: search
    and add need only the inventory and the service keys.
    """
    missing = [
        name
        for name, value in (
            ("POSTGREST_URL", config.POSTGREST_URL),
            ("POSTGREST_JWT_SECRET", config.JWT_SECRET),
            ("SYNCPLEX_OPERATOR", config.OPERATOR),
        )
        if not value
    ]
    if missing:
        return None, f"{', '.join(missing)} not set in .env"
    return Operator(config.OPERATOR), ""
