#!/usr/bin/env python
"""One-shot import of users.json / requests.json into the syncplex schema.

Deliberately NOT part of bootstrap. Bootstrap converges schema; this moves
data, once, and a human should read its output. Run it inside the container so
it gets the deployment's environment and therefore the real database:

    docker compose -f docker_compose_projects.yaml exec syncplex-web \\
        python -m scripts.import_json_stores

Properties that matter:

- ``password_hash`` is copied as an opaque string. Never parsed, never
  rehashed. Whatever verified a login before verifies it after.
- Before writing anything, every stored hash is checked to be one the shared
  auth service can actually read (``preflight_hashes``). Nobody gets a
  password reset, so a hash the verifier cannot parse would mean a locked-out
  account, and that must stop the import rather than be discovered at login.
- ``created_at`` and ``password_changed_at`` come from the JSON, not from
  now(). Overwriting password_changed_at would invalidate every live session.
- Request ids, timestamps, notes, resolver names and the full search result
  are preserved verbatim, so the approval history reads the same afterwards.
- Idempotent: existing usernames and request ids are skipped, so a partial run
  can simply be re-run. Start with ``--dry-run`` to see the plan first; it
  reads the JSON and the current row counts and writes nothing.
- The source files are renamed to ``*.json.migrated`` on success, never
  deleted — they are the only copy of the pre-migration state.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from engine import bootstrap, config
from engine.web.users import UserStore, db_reachable

_hasher = PasswordHasher()  # same construction as engine.web.users and postgrest-auth


def _load(path: Path, key: str) -> list[dict]:
    if not path.is_file():
        print(f"  {path} not found — nothing to import")
        return []
    return json.loads(path.read_text()).get(key, [])


def preflight_hashes(rows: list[dict]) -> list[str]:
    """Which accounts, if any, the auth service would fail to verify.

    No passwords are needed. Verifying against a deliberately wrong password
    separates the two outcomes that matter: VerifyMismatchError means the hash
    parsed and the KDF ran, so the real password will work; InvalidHashError
    means the verifier cannot read it and that account would be locked out.

    Mirrors postgrest-auth/security.py:verify_password — bcrypt hashes are
    dispatched by their ``$2`` prefix and rehashed to argon2id on first
    successful login, so they are fine here too and are only reported.
    """
    unreadable = []
    for row in rows:
        stored = row["password_hash"]
        username = row["username"].strip().lower()
        if stored.startswith("$2"):
            print(f"  ~ {username}: bcrypt — verified by prefix, rehashed to argon2id on first login")
            continue
        try:
            _hasher.verify(stored, "a-deliberately-wrong-password-for-preflight")
            unreadable.append(f"{username}: wrong password verified — refusing to trust this hash")
        except VerifyMismatchError:
            print(f"  ok {username}: argon2id, readable by the auth service")
        except (InvalidHashError, VerificationError) as exc:
            unreadable.append(f"{username}: {type(exc).__name__} — the verifier cannot read this hash")
    return unreadable


def import_users(store: UserStore, rows: list[dict], dry_run: bool = False) -> int:
    existing = {u.username for u in store.list()}
    added = 0
    for row in rows:
        username = row["username"].strip().lower()
        if username in existing:
            print(f"  = {username} (already present, skipped)")
            continue
        if dry_run:
            print(f"  ~ {username} ({row.get('role', 'user')}) would be created")
            added += 1
            continue
        store.add_prehashed(
            username,
            row["password_hash"],  # opaque; not parsed, not rehashed
            role=row.get("role", "user"),
            display_name=row.get("display_name", ""),
            created_at=row.get("created_at"),
            password_changed_at=row.get("password_changed_at"),
            disabled=row.get("disabled", False),
        )
        print(f"  + {username} ({row.get('role', 'user')})")
        added += 1
    return added


def _external_key(result: dict) -> str:
    """Same rule as MediaSearchResult.external_key, computed from raw JSON so
    the import does not depend on the model accepting every historical field."""
    if result.get("media_type") == "tv" and result.get("tvdb_id"):
        return f"tvdb:{result['tvdb_id']}"
    if result.get("media_type") == "movie" and result.get("tmdb_id"):
        return f"tmdb:{result['tmdb_id']}"
    return f"title:{result.get('title', '').casefold()}:{result.get('year') or 0}"


def import_requests(rows: list[dict], user_ids: dict[str, str], dry_run: bool = False) -> tuple[int, list[str]]:
    """Insert over the superuser connection, not PostgREST.

    RLS would otherwise force this to impersonate each requester in turn, and a
    data import is exactly the kind of one-off the superuser connection is for
    (I2 governs application traffic, not a migration script).
    """
    added = 0
    orphans: list[str] = []
    with psycopg.connect(config.superuser_dsn()) as conn, conn.cursor() as cur:
        for row in rows:
            requested_by = row["requested_by"].strip().lower()
            user_id = user_ids.get(requested_by)
            if user_id is None:
                # Keep the row anyway: the username is what the history shows,
                # and a since-deleted account is the ON DELETE SET NULL case.
                orphans.append(requested_by)
            result = row["result"]
            if dry_run:
                print(f"  ~ {row['id']} {result.get('title', '?')} [{row['status']}] would be inserted")
                added += 1
                continue
            cur.execute(
                f"""INSERT INTO {config.APP_SCHEMA}.requests
                    (id, user_id, requested_by, result, external_key, status,
                     requested_at, resolved_by, resolved_at, instance, note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING""",
                (
                    row["id"],
                    user_id,
                    requested_by,
                    json.dumps(result),
                    _external_key(result),
                    row["status"],
                    row["requested_at"],
                    row.get("resolved_by", ""),
                    row.get("resolved_at"),
                    row.get("instance", ""),
                    row.get("note", ""),
                ),
            )
            if cur.rowcount:
                added += 1
                print(f"  + {row['id']} {result.get('title', '?')} [{row['status']}]")
            else:
                print(f"  = {row['id']} (already present, skipped)")
        if not dry_run:
            conn.commit()
    return added, orphans


def _count(table: str) -> int:
    with psycopg.connect(config.superuser_dsn()) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {config.APP_SCHEMA}.{table}")
        return cur.fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Import the JSON stores into Postgres (one-shot).")
    parser.add_argument("--data-dir", type=Path, default=None, help="defaults to engine.config.get_data_dir()")
    parser.add_argument("--keep-sources", action="store_true", help="do not rename the JSON files afterwards")
    parser.add_argument("--dry-run", action="store_true", help="show what would be imported; write nothing")
    args = parser.parse_args()

    data_dir = args.data_dir or config.get_data_dir()
    users_path = data_dir / "users.json"
    requests_path = data_dir / "requests.json"

    ok, detail = db_reachable()
    if not ok:
        print(f"database unreachable: {detail}", file=sys.stderr)
        return 1
    bootstrap.apply_schema()

    user_rows = _load(users_path, "users")
    request_rows = _load(requests_path, "requests")

    users_before, requests_before = _count("users"), _count("requests")

    print("\npreflight — can the auth service read every stored hash?")
    unreadable = preflight_hashes(user_rows)
    if unreadable:
        print("\nABORTING — these accounts would be locked out, and nobody gets a reset:", file=sys.stderr)
        for line in unreadable:
            print(f"  {line}", file=sys.stderr)
        return 1

    print(f"\nusers ({len(user_rows)} in {users_path.name}):")
    store = UserStore()
    import_users(store, user_rows, dry_run=args.dry_run)
    user_ids = {u.username: u.id for u in store.list()}
    if args.dry_run:
        # The accounts were not really created, so stand in for them —
        # otherwise every request would be reported as an orphan.
        for row in user_rows:
            user_ids.setdefault(row["username"].strip().lower(), "(would be created)")

    print(f"\nrequests ({len(request_rows)} in {requests_path.name}):")
    _, orphans = import_requests(request_rows, user_ids, dry_run=args.dry_run)

    if args.dry_run:
        print("\n--dry-run: nothing was written and the JSON files were left alone")
        print(f"  users:    {users_before} rows now, {len(user_rows)} in json")
        print(f"  requests: {requests_before} rows now, {len(request_rows)} in json")
        if orphans:
            print(f"  requests with no matching account (user_id would be NULL): {sorted(set(orphans))}")
        return 0

    users_after, requests_after = _count("users"), _count("requests")

    print("\ncounts:")
    print(f"  users:    {users_before} -> {users_after}  (json had {len(user_rows)})")
    print(f"  requests: {requests_before} -> {requests_after}  (json had {len(request_rows)})")
    if orphans:
        print(f"  requests with no matching account (user_id NULL): {sorted(set(orphans))}")

    failures = []
    if users_after < len(user_rows):
        failures.append(f"users: {users_after} rows for {len(user_rows)} json entries")
    if requests_after < len(request_rows):
        failures.append(f"requests: {requests_after} rows for {len(request_rows)} json entries")
    if failures:
        print("\nMISMATCH — not renaming sources:", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1

    if args.keep_sources:
        print("\n--keep-sources: leaving the JSON files in place")
        return 0
    for path in (users_path, requests_path):
        if path.is_file():
            target = path.with_suffix(".json.migrated")
            path.rename(target)
            print(f"\nrenamed {path.name} -> {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
