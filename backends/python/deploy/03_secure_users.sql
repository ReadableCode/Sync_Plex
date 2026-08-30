-- The credentials table must never be reachable through PostgREST.
-- (Same posture as Solitaire_Associations/deploy/03_secure_users.sql.)

REVOKE ALL ON syncplex.users FROM syncplex_user;
REVOKE ALL ON syncplex.users FROM web_anon;
