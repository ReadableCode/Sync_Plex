# backlog

Known issues in this repo that are real, understood, and deliberately not
fixed yet. One file per issue, this file is the index.

An entry earns its place by being actionable: it says how to re-check that the
problem still exists, what the fix is, and what the fix would disturb.

Re-run an entry's `verify:` command before acting on it.

| Issue | Found | Status |
|-------|-------|--------|
| [tests: `uv run pytest` deletes every row in the live request queue](tests-wipe-live-request-queue.md) | 2026-09-04 | open |
