-- SPDX-License-Identifier: AGPL-3.0-only
-- Database role model (CLAUDE.md rule 4). Run by a superuser once per cluster;
-- idempotent, so it's safe to re-run. Passwords arrive as psql variables:
--   psql -v migrator_password=… -v api_password=… -v worker_password=… -v maint_password=…
--
-- No role has BYPASSRLS. Cross-tenant access comes from role-targeted RLS policies
-- (ops_all / owner_all, from Phase 1) that apply only after SET LOCAL ROLE, because
-- login users hold pickwise_ops WITH INHERIT FALSE. This also keeps us portable to
-- managed Postgres services where BYPASSRLS may not be grantable.

\set ON_ERROR_STOP on

-- Group roles (NOLOGIN) -------------------------------------------------------
SELECT format('CREATE ROLE %I NOLOGIN', r)
FROM unnest(ARRAY['pickwise_owner', 'pickwise_app', 'pickwise_ops']) AS r
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)
\gexec

ALTER ROLE pickwise_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE pickwise_app   NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE pickwise_ops   NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

-- Login users -----------------------------------------------------------------
SELECT format('CREATE ROLE %I LOGIN', r)
FROM unnest(ARRAY['pickwise_migrator', 'pickwise_api', 'pickwise_worker', 'pickwise_maint']) AS r
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)
\gexec

ALTER ROLE pickwise_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'migrator_password';
ALTER ROLE pickwise_api      LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'api_password';
ALTER ROLE pickwise_worker   LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'worker_password';
ALTER ROLE pickwise_maint    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'maint_password';

-- Memberships -----------------------------------------------------------------
GRANT pickwise_owner TO pickwise_migrator WITH INHERIT TRUE, SET TRUE;
GRANT pickwise_app   TO pickwise_api      WITH INHERIT TRUE, SET TRUE;
GRANT pickwise_app   TO pickwise_worker   WITH INHERIT TRUE, SET TRUE;
GRANT pickwise_ops   TO pickwise_worker   WITH INHERIT FALSE, SET TRUE;
GRANT pickwise_ops   TO pickwise_maint    WITH INHERIT FALSE, SET TRUE;

-- Objects created by migrations are owned by pickwise_owner.
ALTER ROLE pickwise_migrator SET role = pickwise_owner;

-- Database privileges -----------------------------------------------------------
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %s', current_database(),
              'pickwise_migrator, pickwise_api, pickwise_worker, pickwise_maint') \gexec
SELECT format('GRANT CREATE ON DATABASE %I TO pickwise_owner', current_database()) \gexec

-- public holds only alembic_version; nobody else may create objects in it.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO pickwise_owner;
GRANT USAGE ON SCHEMA public TO pickwise_app, pickwise_ops;
