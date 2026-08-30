"""Media request queue — users request titles, an admin approves and picks
the server that fulfils them.

Nothing is sent to Sonarr/Radarr when a request is created; the download
only starts when an admin approves the request AND chooses the instance via
``fulfill_request`` (which calls ``aggregation.add_to_instance``). A denied
or failed add never touches the media servers.

State lives in ``syncplex.requests`` and is reached only through PostgREST,
carrying the caller's session JWT, so row-level security is what decides who
sees what: a user reaches their own rows, an admin reaches the whole queue.
The store is therefore per-session rather than per-process — construct one
with the logged-in user's token.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from .. import store as pgrest  # aliased: `store` is the local name for a RequestStore
from ..web.users import User
from .models import AddResult, AggregatedResult, MediaSearchResult


class RequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class MediaRequest(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    result: MediaSearchResult  # full search hit, so approval can re-run the add by external id
    requested_by: str
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: RequestStatus = RequestStatus.PENDING
    resolved_by: str = ""
    resolved_at: datetime | None = None
    instance: str = ""  # the server an admin picked (approved requests only)
    note: str = ""  # denial reason / add-failure detail shown to the requester

    @property
    def title_line(self) -> str:
        year = f" ({self.result.year})" if self.result.year else ""
        return f"{self.result.title}{year}"


_SELECT = "id,result,requested_by,requested_at,status,resolved_by,resolved_at,instance,note"


def _to_request(row: dict) -> MediaRequest:
    return MediaRequest(
        id=row["id"],
        result=MediaSearchResult.model_validate(row["result"]),
        requested_by=row["requested_by"],
        requested_at=row["requested_at"],
        status=RequestStatus(row["status"]),
        resolved_by=row["resolved_by"],
        resolved_at=row["resolved_at"],
        instance=row["instance"],
        note=row["note"],
    )


class RequestStore:
    """The queue as one logged-in user sees it.

    Every method is an HTTP call to PostgREST with ``user``'s token, so the
    rows that come back are already the ones RLS permits. Nothing here filters
    for security; the database does that.
    """

    def __init__(self, token: str, user: User):
        self.token = token
        self.user = user

    # --- queries ---

    def get(self, request_id: str) -> MediaRequest | None:
        rows = pgrest.select_requests(self.token, {"id": f"eq.{request_id}", "select": _SELECT})
        return _to_request(rows[0]) if rows else None

    def list(self, status: RequestStatus | None = None, requested_by: str | None = None) -> list[MediaRequest]:
        params = {"select": _SELECT, "order": "requested_at.desc"}
        if status is not None:
            params["status"] = f"eq.{status.value}"
        if requested_by is not None:
            params["requested_by"] = f"eq.{requested_by}"
        return [_to_request(r) for r in pgrest.select_requests(self.token, params)]

    def pending_count(self) -> int:
        rows = pgrest.select_requests(self.token, {"select": "id", "status": f"eq.{RequestStatus.PENDING.value}"})
        return len(rows)

    def find_pending(self, result: MediaSearchResult) -> MediaRequest | None:
        """This user's own open request for the same title (matched by
        external id).

        Scoped to the caller, unlike the JSON store, which scanned the whole
        queue. Under RLS a non-admin cannot see anyone else's pending rows, so
        a global check is not something the app can honestly do any more —
        two people wanting the same title now file two requests and the admin
        sees both.
        """
        rows = pgrest.select_requests(
            self.token,
            {
                "select": _SELECT,
                "user_id": f"eq.{self.user.id}",
                "status": f"eq.{RequestStatus.PENDING.value}",
                "external_key": f"eq.{result.external_key}",
                "limit": "1",
            },
        )
        return _to_request(rows[0]) if rows else None

    # --- mutations ---

    def create(self, result: MediaSearchResult, requested_by: str) -> MediaRequest:
        """File a request; returns this user's existing open one instead of a
        duplicate."""
        existing = self.find_pending(result)
        if existing is not None:
            return existing
        row = pgrest.insert_request(
            self.token,
            {
                "id": uuid.uuid4().hex[:12],
                "user_id": self.user.id,
                "requested_by": requested_by,
                "result": result.model_dump(mode="json"),
                "external_key": result.external_key,
                "status": RequestStatus.PENDING.value,
            },
        )
        return _to_request(row)

    def deny(self, request_id: str, admin: str, note: str = "") -> MediaRequest:
        return self._resolve(request_id, RequestStatus.DENIED, admin, note=note)

    def approve(self, request_id: str, admin: str, instance: str, note: str = "") -> MediaRequest:
        return self._resolve(request_id, RequestStatus.APPROVED, admin, note=note, instance=instance)

    def annotate(self, request_id: str, note: str) -> None:
        """Attach a note to a still-pending request (e.g. a failed add)."""
        pgrest.update_request(
            self.token,
            request_id,
            {"note": note},
            extra={"status": f"eq.{RequestStatus.PENDING.value}"},
        )

    def withdraw(self, request_id: str, username: str) -> None:
        """Requester deletes their own pending request."""
        deleted = pgrest.delete_request(
            self.token,
            request_id,
            extra={"status": f"eq.{RequestStatus.PENDING.value}", "requested_by": f"eq.{username}"},
        )
        if deleted:
            return
        if self.get(request_id) is None:
            raise KeyError(f"No such request: {request_id}")
        raise ValueError("Only your own pending requests can be withdrawn")

    def _resolve(
        self, request_id: str, status: RequestStatus, admin: str, note: str = "", instance: str = ""
    ) -> MediaRequest:
        # status=eq.pending is the guard, applied by the database rather than
        # by a read-then-write here, so two admins racing on the same request
        # cannot both win.
        rows = pgrest.update_request(
            self.token,
            request_id,
            {
                "status": status.value,
                "resolved_by": admin,
                "resolved_at": datetime.now(UTC).isoformat(),
                "note": note,
                "instance": instance,
            },
            extra={"status": f"eq.{RequestStatus.PENDING.value}"},
        )
        if rows:
            return _to_request(rows[0])
        current = self.get(request_id)
        if current is None:
            raise KeyError(f"No such request: {request_id}")
        raise ValueError(f"Request already {current.status.value}")


async def fulfill_request(store: RequestStore, request_id: str, instance_name: str, admin: str, config) -> AddResult:
    """Approve a request onto a specific instance — this is the only path
    from a user request to an actual download.

    The add runs first; the request is marked approved only when the
    instance accepted the title (an "already present" add also resolves the
    request — the content exists, nothing further to do). On failure the
    request stays pending with the error recorded, so the admin can retry
    on another server.
    """
    from .aggregation import add_to_instance  # local import to avoid a cycle

    request = store.get(request_id)
    if request is None:
        return AddResult(instance=instance_name, ok=False, message=f"No such request: {request_id}")
    if request.status != RequestStatus.PENDING:
        return AddResult(instance=instance_name, ok=False, message=f"Request already {request.status.value}")

    add_result = await add_to_instance(AggregatedResult(result=request.result), instance_name, config)
    already_present = not add_result.ok and "already present" in add_result.message.lower()
    if add_result.ok or already_present:
        store.approve(request_id, admin, instance=instance_name, note=add_result.message)
    else:
        store.annotate(request_id, f"add failed: {add_result.message}")
    return add_result
