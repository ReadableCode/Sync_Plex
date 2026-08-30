-- Core schema. Idempotent; applied by engine/bootstrap.py at startup
-- (version-gated via syncplex.deploy_meta).
--
-- These files live under backends/python/ rather than the repo-root deploy/
-- because that directory is the docker build context — bootstrap has to be
-- able to read them from inside the image.

CREATE SCHEMA IF NOT EXISTS syncplex;

-- Credentials read ONLY by the app process (superuser);
-- 03_secure_users.sql revokes it from the PostgREST-facing roles.
CREATE TABLE IF NOT EXISTS syncplex.users (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username            text UNIQUE NOT NULL,
    password_hash       text NOT NULL,
    role                text NOT NULL DEFAULT 'user',
    display_name        text NOT NULL DEFAULT '',
    disabled            boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    password_changed_at timestamptz NOT NULL DEFAULT now()
);

-- The request queue. `id` is the app's 12-hex-char identifier, kept as text so
-- the ids already in requests.json survive the import unchanged.
--
-- `result` holds the whole MediaSearchResult verbatim: approval re-runs the add
-- from the original search hit, and the cards render poster/overview/genres
-- straight out of it. Flattening it to columns would drop fields the UI reads.
--
-- `requested_by` and `resolved_by` are denormalized usernames, not joins.
-- syncplex.users is unreachable through PostgREST by design (I4), so a join is
-- not available to the queue pages; the names have to travel with the row.
-- They are also what keeps a removed account's history readable, which is the
-- behaviour `syncplex users remove` has always documented — hence
-- ON DELETE SET NULL rather than CASCADE on user_id.
--
-- `external_key` is MediaSearchResult.external_key, written by the app. It is
-- the dedupe key for "you already requested this", so it has to be filterable
-- server-side rather than recomputed from `result` in SQL.
CREATE TABLE IF NOT EXISTS syncplex.requests (
    id           text PRIMARY KEY,
    user_id      uuid REFERENCES syncplex.users (id) ON DELETE SET NULL,
    requested_by text NOT NULL,
    result       jsonb NOT NULL,
    external_key text NOT NULL,
    status       text NOT NULL DEFAULT 'pending',  -- pending | approved | denied
    requested_at timestamptz NOT NULL DEFAULT now(),
    resolved_by  text NOT NULL DEFAULT '',
    resolved_at  timestamptz,
    instance     text NOT NULL DEFAULT '',
    note         text NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_syncplex_requests_user   ON syncplex.requests (user_id);
CREATE INDEX IF NOT EXISTS idx_syncplex_requests_status ON syncplex.requests (status);
-- Serves the per-user "already requested?" lookup on the search cards.
CREATE INDEX IF NOT EXISTS idx_syncplex_requests_dedupe
    ON syncplex.requests (user_id, status, external_key);

GRANT USAGE ON SCHEMA syncplex TO syncplex_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON syncplex.requests TO syncplex_user;
