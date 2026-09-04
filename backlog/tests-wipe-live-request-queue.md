# tests: `uv run pytest` deletes every row in the live request queue

    found:  2026-09-04
    status: open
    verify: cd backends/python && grep -n "_wipe\|admin_store.list()" tests/test_postgrest_real.py && uv run python -c "from engine import config; print(config.POSTGRES_URL, config.POSTGRES_DB, config.APP_SCHEMA, config.POSTGREST_URL)"

The real-PostgREST tests have no database of their own. They read the same
`.env` the web app does, so they run against the shared apps Postgres on
elitedesk (`192.168.86.179`, db `apps`, schema `syncplex`, PostgREST at
`https://pgrest.tinkernet.me`) — the database production requests live in.
The `clean` fixture then deletes every request the throwaway admin can see,
and under RLS an admin sees the whole queue:

```python
@pytest.fixture()
def clean(stores):
    """Drop every request either account can see, before and after."""
    _, user_store, admin_store = stores

    def _wipe():
        for request in admin_store.list():
            pgrest.delete_request(admin_store.token, request.id)

    _wipe()
    yield stores
    _wipe()
```

Five tests use it (`test_create_and_read_back`, `test_duplicate_request_collapses_per_user`,
`test_rls_hides_other_users_rows`, `test_rls_blocks_writing_rows_for_someone_else`,
`test_non_admin_cannot_resolve_someone_elses_request`). A plain `uv run pytest`
from `backends/python` therefore empties the household's pending/approved/denied
request history every time it runs. Found on 2026-09-04 while running the suite
for the admin-queue work; the queue read 0 rows afterwards and there is no way
to tell what was in it before.

The throwaway `ztest*` accounts are cleaned up properly (`users.remove` in the
module fixture); only the request rows are the problem.

**fix** — scope the wipe to rows the test accounts created, so real rows are
never touched:

```python
def _wipe():
    for request in admin_store.list():
        if request.requested_by.startswith("ztest"):
            pgrest.delete_request(admin_store.token, request.id)
```

and make the assertions that count rows filter the same way (`user_store.list()`
already only returns the requester's own rows under RLS, so only the admin-side
lists in `test_rls_hides_other_users_rows` need the filter). Alternatively point
the suite at a scratch schema via `APP_SCHEMA` in a test-only env, but that
needs bootstrap to create the schema and PostgREST to expose it.

**blast radius** — tests only. Production rows stop being deleted; the tests
still pass with unrelated rows present because they assert on the throwaway
users' rows.

**not doing yet** — out of scope of the admin-queue change it was found
during. Decide whether the filter-by-username approach is enough or whether a
separate test schema is wanted (that is a PostgREST config change on elitedesk,
not just a test edit).
