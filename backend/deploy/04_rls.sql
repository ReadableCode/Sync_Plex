-- Row-level security on the request queue, keyed on the JWT claims that
-- postgrest-auth mints (postgrest-auth/main.py): `user_id` for identity and
-- `app_role` for the app's own admin/user role. (`role` is not usable here —
-- PostgREST has already consumed it for SET ROLE, so every caller's `role` is
-- syncplex_user.) Idempotent: DROP/CREATE pairs throughout.

CREATE OR REPLACE FUNCTION syncplex.jwt_user_id() RETURNS uuid
LANGUAGE plpgsql STABLE AS $$
DECLARE
    claims text := NULLIF(current_setting('request.jwt.claims', true), '');
    uid text;
BEGIN
    IF claims IS NOT NULL THEN
        uid := COALESCE(claims::jsonb ->> 'user_id', claims::jsonb ->> 'sub');
    END IF;
    -- pre-v9 PostgREST exposes claims as individual settings
    uid := COALESCE(uid, NULLIF(current_setting('request.jwt.claim.user_id', true), ''));
    RETURN uid::uuid;
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION syncplex.jwt_is_admin() RETURNS boolean
LANGUAGE plpgsql STABLE AS $$
DECLARE
    claims text := NULLIF(current_setting('request.jwt.claims', true), '');
    app_role text;
BEGIN
    IF claims IS NOT NULL THEN
        app_role := claims::jsonb ->> 'app_role';
    END IF;
    app_role := COALESCE(app_role, NULLIF(current_setting('request.jwt.claim.app_role', true), ''));
    RETURN app_role = 'admin';
EXCEPTION WHEN others THEN
    RETURN false;
END $$;

-- Two permissive policies, so they OR together: a user reaches their own rows,
-- an admin reaches every row (the approval queue is the whole point of the
-- admin role). Admin is expressed as a policy rather than a second Postgres
-- role on purpose — PostgREST picks the role straight out of the token, so a
-- forged claim would escalate at the database level instead of being contained
-- here.
ALTER TABLE syncplex.requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_isolation ON syncplex.requests;
CREATE POLICY user_isolation ON syncplex.requests
    USING (user_id = syncplex.jwt_user_id())
    WITH CHECK (user_id = syncplex.jwt_user_id());

DROP POLICY IF EXISTS admin_all ON syncplex.requests;
CREATE POLICY admin_all ON syncplex.requests
    USING (syncplex.jwt_is_admin())
    WITH CHECK (syncplex.jwt_is_admin());
