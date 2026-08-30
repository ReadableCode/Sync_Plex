"""Request-queue persistence — every read/write goes through PostgREST.

Same client shape as Solitaire_Associations/app/store.py: the schema is pinned
per-request with Accept-Profile/Content-Profile, and the caller's session JWT
is forwarded verbatim as the Bearer token, so RLS (deploy/04_rls.sql) scopes
every query. A normal user's token reaches only their own rows; an admin's
token carries app_role=admin and reaches the whole queue.

This is the only module in the package that builds a PostgREST URL.
"""

from __future__ import annotations

import httpx

from . import config


class StoreError(Exception):
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


def _headers(token: str, write: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept-Profile": config.APP_SCHEMA,
    }
    if write:
        headers["Content-Profile"] = config.APP_SCHEMA
        headers["Content-Type"] = "application/json"
    return headers


def _check(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise StoreError(resp.status_code, f"postgrest {resp.status_code}: {resp.text[:300]}")


def select_requests(token: str, params: dict[str, str]) -> list[dict]:
    resp = _http().get(f"{config.POSTGREST_URL}/requests", params=params, headers=_headers(token))
    _check(resp)
    return resp.json()


def insert_request(token: str, row: dict) -> dict:
    resp = _http().post(
        f"{config.POSTGREST_URL}/requests",
        headers={**_headers(token, write=True), "Prefer": "return=representation"},
        json=row,
    )
    _check(resp)
    rows = resp.json()
    if not rows:
        raise StoreError(500, "insert returned no row")
    return rows[0]


def update_request(token: str, request_id: str, changes: dict, extra: dict[str, str] | None = None) -> list[dict]:
    """PATCH one request. `extra` adds filters (e.g. status=eq.pending) so the
    guard is applied by the database rather than read-then-write."""
    params = {"id": f"eq.{request_id}", **(extra or {})}
    resp = _http().patch(
        f"{config.POSTGREST_URL}/requests",
        params=params,
        headers={**_headers(token, write=True), "Prefer": "return=representation"},
        json=changes,
    )
    _check(resp)
    return resp.json()


def delete_request(token: str, request_id: str, extra: dict[str, str] | None = None) -> list[dict]:
    params = {"id": f"eq.{request_id}", **(extra or {})}
    resp = _http().delete(
        f"{config.POSTGREST_URL}/requests",
        params=params,
        headers={**_headers(token, write=True), "Prefer": "return=representation"},
    )
    _check(resp)
    return resp.json()


def postgrest_reachable() -> tuple[bool, str]:
    try:
        resp = _http().get(f"{config.POSTGREST_URL}/", headers={"Accept-Profile": config.APP_SCHEMA})
        return resp.status_code < 500, f"http {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, str(exc)
