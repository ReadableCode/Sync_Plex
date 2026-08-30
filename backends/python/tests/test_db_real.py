"""Real-database tests — hit the actual shared Postgres, red if unreachable.

Creates throwaway accounts prefixed ztest and removes them. Runs the same
version-gated bootstrap the app runs at startup (a no-op once converged).
"""

import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from argon2 import PasswordHasher

from engine import bootstrap, config
from engine.web.users import UserStore, db_reachable, hash_password

PW = "correct-horse-battery"


@pytest.fixture(scope="module")
def store():
    ok, detail = db_reachable()
    assert ok, f"database unreachable — this test must be red, not skipped: {detail}"
    bootstrap.apply_schema()
    return UserStore()


@pytest.fixture()
def temp_username(store):
    username = f"ztest{uuid.uuid4().hex[:10]}"
    yield username
    try:
        store.remove(username)
    except KeyError:
        pass


def test_bootstrap_is_a_no_op_when_converged(store):
    assert bootstrap.apply_schema() is False


def test_account_lifecycle(store, temp_username):
    user = store.add(temp_username, PW, display_name="Temp")
    assert user.username == temp_username
    assert user.role == "user"
    assert user.id
    # argon2id at rest — the same string the shared auth service verifies
    PasswordHasher().verify(store.get(temp_username).password_hash, PW)

    before = store.get(temp_username).password_changed_at
    store.set_password(temp_username, "another-good-password")
    after = store.get(temp_username).password_changed_at
    assert after > before, "password change must bump password_changed_at"

    store.set_disabled(temp_username, True)
    mid = store.get(temp_username)
    assert mid.disabled and mid.password_changed_at > after, "disable must revoke sessions"
    store.set_disabled(temp_username, False)
    final = store.get(temp_username)
    assert not final.disabled
    assert final.password_changed_at > mid.password_changed_at, "re-enable must not resurrect them"


def test_role_change_revokes_sessions(store, temp_username):
    """The role rides in the token as app_role, so a demotion has to end the
    session or the old token keeps admin reach through RLS."""
    store.add(temp_username, PW)
    before = store.get(temp_username).password_changed_at
    store.set_role(temp_username, "admin")
    promoted = store.get(temp_username)
    assert promoted.is_admin
    assert promoted.password_changed_at > before


def test_validation_rules(store):
    with pytest.raises(ValueError):
        store.add("Bad Username!", "long-enough-password")
    with pytest.raises(ValueError):
        store.add("ztestshortpw", "tiny")
    with pytest.raises(ValueError):
        store.add("ztestbadrole", "long-enough-password", role="superuser")


def test_duplicate_username_rejected(store, temp_username):
    store.add(temp_username, PW)
    with pytest.raises(ValueError):
        store.add(temp_username, PW)


def test_remove_unknown_user_raises(store):
    with pytest.raises(KeyError):
        store.remove(f"ztest{uuid.uuid4().hex[:10]}")


def test_prehashed_import_preserves_hash_and_timestamps(store, temp_username):
    """The migration path: a hash and its timestamps move verbatim, so no live
    session is revoked and no password has to be reset."""
    password = "imported-from-users-json"
    original_hash = hash_password(password)
    created = datetime(2025, 7, 13, 12, 35, 48, tzinfo=UTC)
    changed = datetime(2025, 8, 1, 9, 0, 0, tzinfo=UTC)

    store.add_prehashed(
        temp_username,
        original_hash,
        role="admin",
        display_name="Imported",
        created_at=created,
        password_changed_at=changed,
    )
    user = store.get(temp_username)
    assert user.password_hash == original_hash, "hash must be stored byte-for-byte"
    PasswordHasher().verify(user.password_hash, password)
    assert user.created_at == created
    assert user.password_changed_at == changed, "overwriting this would log everyone out"
    assert user.role == "admin" and user.display_name == "Imported"


def test_users_table_hidden_from_postgrest_roles(store):
    """I4: the credentials table must not be readable by the PostgREST roles."""
    with psycopg.connect(config.superuser_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT grantee, privilege_type FROM information_schema.role_table_grants
               WHERE table_schema = %s AND table_name = 'users'
                 AND grantee IN ('syncplex_user', 'web_anon')""",
            (config.APP_SCHEMA,),
        )
        assert cur.fetchall() == []


def test_app_role_is_nologin(store):
    """I3: PostgREST SET ROLEs into it; it must never be able to log in."""
    with psycopg.connect(config.superuser_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'syncplex_user'")
        row = cur.fetchone()
    assert row is not None, "syncplex_user role missing — bootstrap did not run"
    assert row[0] is False


def test_requests_table_has_rls_enabled(store):
    with psycopg.connect(config.superuser_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = 'requests'",
            (config.APP_SCHEMA,),
        )
        assert cur.fetchone()[0] is True


# --- RLS, verified straight against Postgres ---------------------------------
# PostgREST enforces the policies by SET ROLE-ing into syncplex_user and setting
# request.jwt.claims. Doing exactly that here proves the policies themselves,
# without depending on PostgREST being recreated with the schema exposed.
# test_postgrest_real.py covers the same ground over real HTTP.


def _as_role(cur, claims: str) -> None:
    cur.execute("SELECT set_config('request.jwt.claims', %s, true)", (claims,))
    cur.execute("SET LOCAL ROLE syncplex_user")


def _claims(user_id: str, app_role: str = "user") -> str:
    import json

    return json.dumps({"user_id": user_id, "app_role": app_role, "role": "syncplex_user"})


@pytest.fixture()
def two_accounts(store):
    requester = f"ztest{uuid.uuid4().hex[:10]}"
    admin = f"ztest{uuid.uuid4().hex[:10]}"
    a = store.add(requester, PW)
    b = store.add(admin, PW, role="admin")
    yield a, b
    for username in (requester, admin):
        try:
            store.remove(username)
        except KeyError:
            pass


def _seed(cur, owner_id: str, owner_name: str, request_id: str) -> None:
    cur.execute(
        f"""INSERT INTO {config.APP_SCHEMA}.requests
            (id, user_id, requested_by, result, external_key, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')""",
        (request_id, owner_id, owner_name, '{"media_type": "tv", "title": "T"}', f"key:{request_id}"),
    )


def test_rls_scopes_reads_to_the_claim(two_accounts):
    user, admin = two_accounts
    mine, theirs = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    with psycopg.connect(config.superuser_dsn()) as conn:
        with conn.cursor() as cur:  # seeded as superuser, which bypasses RLS
            _seed(cur, user.id, user.username, mine)
            _seed(cur, admin.id, admin.username, theirs)
        conn.commit()

        with conn.cursor() as cur:
            _as_role(cur, _claims(user.id))
            cur.execute(f"SELECT id FROM {config.APP_SCHEMA}.requests ORDER BY id")
            assert [r[0] for r in cur.fetchall()] == sorted([mine]), "user must see only their own row"
        conn.rollback()

        with conn.cursor() as cur:
            _as_role(cur, _claims(admin.id, "admin"))
            cur.execute(f"SELECT id FROM {config.APP_SCHEMA}.requests WHERE id IN (%s, %s)", (mine, theirs))
            assert sorted(r[0] for r in cur.fetchall()) == sorted([mine, theirs]), "admin must see the whole queue"
        conn.rollback()

        with conn.cursor() as cur:  # cleanup happens via the account removal cascade
            cur.execute(f"DELETE FROM {config.APP_SCHEMA}.requests WHERE id IN (%s, %s)", (mine, theirs))
        conn.commit()


def test_rls_rejects_writing_a_row_for_someone_else(two_accounts):
    user, admin = two_accounts
    with psycopg.connect(config.superuser_dsn()) as conn, conn.cursor() as cur:
        _as_role(cur, _claims(user.id))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _seed(cur, admin.id, admin.username, uuid.uuid4().hex[:12])
        conn.rollback()


def test_rls_rejects_a_null_owner(two_accounts):
    """WITH CHECK compares against the claim, so an unowned insert cannot slip
    past — NULL = <uuid> is NULL, not true."""
    user, _ = two_accounts
    with psycopg.connect(config.superuser_dsn()) as conn, conn.cursor() as cur:
        _as_role(cur, _claims(user.id))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _seed(cur, None, user.username, uuid.uuid4().hex[:12])
        conn.rollback()


def test_rls_denies_everything_without_claims(two_accounts):
    user, _ = two_accounts
    request_id = uuid.uuid4().hex[:12]
    with psycopg.connect(config.superuser_dsn()) as conn:
        with conn.cursor() as cur:
            _seed(cur, user.id, user.username, request_id)
        conn.commit()
        with conn.cursor() as cur:
            _as_role(cur, "")  # no token at all
            cur.execute(f"SELECT count(*) FROM {config.APP_SCHEMA}.requests")
            assert cur.fetchone()[0] == 0
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {config.APP_SCHEMA}.requests WHERE id = %s", (request_id,))
        conn.commit()


def test_stored_hash_stays_verifiable_by_the_shared_auth_service(store, temp_username):
    """Nobody gets a password reset, so a hash the auth service cannot read
    means a locked-out account.

    postgrest-auth verifies with a plain ``argon2.PasswordHasher()`` after
    dispatching on the ``$`` prefix (postgrest-auth/security.py). Construct one
    the same way and check it can both read the stored hash and match the real
    password — and that check_needs_rehash is False, so the parameters agree
    and first login does not rewrite the row.
    """
    from argon2.exceptions import VerifyMismatchError

    password = "the-password-a-friend-already-has"
    original = hash_password(password)
    store.add_prehashed(temp_username, original)

    stored = store.get(temp_username).password_hash
    assert stored == original, "hash must survive the round trip byte-for-byte"
    assert stored.startswith("$argon2id$"), "prefix is how the service picks the verifier"

    service_hasher = PasswordHasher()  # exactly how postgrest-auth builds its own
    assert service_hasher.verify(stored, password) is True
    with pytest.raises(VerifyMismatchError):
        # Not InvalidHashError: the hash parses, it is only the password that is wrong.
        service_hasher.verify(stored, "a-deliberately-wrong-password")
    assert service_hasher.check_needs_rehash(stored) is False
