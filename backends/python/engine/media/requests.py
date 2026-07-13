"""Media request queue — users request titles, an admin approves and picks
the server that fulfils them.

Nothing is sent to Sonarr/Radarr when a request is created; the download
only starts when an admin approves the request AND chooses the instance via
``fulfill_request`` (which calls ``aggregation.add_to_instance``). A denied
or failed add never touches the media servers.

State is one JSON file at ``<data dir>/requests.json`` — same single-file,
atomic-rewrite pattern as the user store.
"""

import json
import os
import threading
import uuid
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import get_data_dir
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


class RequestStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (get_data_dir() / "requests.json")
        self._lock = threading.Lock()
        self._requests: dict[str, MediaRequest] = {}
        self._loaded_mtime: float | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            self._requests = {}
            self._loaded_mtime = None
            return
        raw = json.loads(self.path.read_text())
        self._requests = {r["id"]: MediaRequest.model_validate(r) for r in raw.get("requests", [])}
        self._loaded_mtime = self.path.stat().st_mtime

    def _refresh(self) -> None:
        mtime = self.path.stat().st_mtime if self.path.is_file() else None
        if mtime != self._loaded_mtime:
            self._load()

    def _save(self) -> None:
        payload = {"requests": [r.model_dump(mode="json") for r in self._requests.values()]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        self._loaded_mtime = self.path.stat().st_mtime

    # --- queries ---

    def get(self, request_id: str) -> MediaRequest | None:
        with self._lock:
            self._refresh()
            return self._requests.get(request_id)

    def list(self, status: RequestStatus | None = None, requested_by: str | None = None) -> list[MediaRequest]:
        with self._lock:
            self._refresh()
            items = list(self._requests.values())
        if status is not None:
            items = [r for r in items if r.status == status]
        if requested_by is not None:
            items = [r for r in items if r.requested_by == requested_by]
        return sorted(items, key=lambda r: r.requested_at, reverse=True)

    def pending_count(self) -> int:
        return len(self.list(status=RequestStatus.PENDING))

    def find_pending(self, result: MediaSearchResult) -> MediaRequest | None:
        """An open request for the same title (matched by external id)."""
        key = result.external_key
        for request in self.list(status=RequestStatus.PENDING):
            if request.result.external_key == key:
                return request
        return None

    # --- mutations ---

    def create(self, result: MediaSearchResult, requested_by: str) -> MediaRequest:
        """File a request; returns the existing open one instead of a duplicate."""
        existing = self.find_pending(result)
        if existing is not None:
            return existing
        request = MediaRequest(result=result, requested_by=requested_by)
        with self._lock:
            self._refresh()
            self._requests[request.id] = request
            self._save()
        return request

    def deny(self, request_id: str, admin: str, note: str = "") -> MediaRequest:
        return self._resolve(request_id, RequestStatus.DENIED, admin, note=note)

    def approve(self, request_id: str, admin: str, instance: str, note: str = "") -> MediaRequest:
        return self._resolve(request_id, RequestStatus.APPROVED, admin, note=note, instance=instance)

    def annotate(self, request_id: str, note: str) -> None:
        """Attach a note to a still-pending request (e.g. a failed add)."""
        with self._lock:
            self._refresh()
            request = self._requests.get(request_id)
            if request is not None and request.status == RequestStatus.PENDING:
                self._requests[request_id] = request.model_copy(update={"note": note})
                self._save()

    def withdraw(self, request_id: str, username: str) -> None:
        """Requester deletes their own pending request."""
        with self._lock:
            self._refresh()
            request = self._requests.get(request_id)
            if request is None:
                raise KeyError(f"No such request: {request_id}")
            if request.requested_by != username or request.status != RequestStatus.PENDING:
                raise ValueError("Only your own pending requests can be withdrawn")
            del self._requests[request_id]
            self._save()

    def _resolve(
        self, request_id: str, status: RequestStatus, admin: str, note: str = "", instance: str = ""
    ) -> MediaRequest:
        with self._lock:
            self._refresh()
            request = self._requests.get(request_id)
            if request is None:
                raise KeyError(f"No such request: {request_id}")
            if request.status != RequestStatus.PENDING:
                raise ValueError(f"Request already {request.status.value}")
            resolved = request.model_copy(
                update={
                    "status": status,
                    "resolved_by": admin,
                    "resolved_at": datetime.now(UTC),
                    "note": note,
                    "instance": instance,
                }
            )
            self._requests[request_id] = resolved
            self._save()
            return resolved


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
