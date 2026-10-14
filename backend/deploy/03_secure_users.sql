-- The credentials table must never be reachable through PostgREST.
-- (Same posture as Solitaire_Associations/deploy/03_secure_users.sql.)
-- Applied by engine/bootstrap.py at startup.
-- Running this file by hand with psql might work, but it is not the intended
-- use: change it here and let the app apply it.

REVOKE ALL ON syncplex.users FROM syncplex_user;
REVOKE ALL ON syncplex.users FROM web_anon;
