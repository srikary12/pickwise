-- SPDX-License-Identifier: AGPL-3.0-only
-- Extensions need a superuser, so they're created here rather than in migrations.
-- uuidv7() is built into PostgreSQL 18; no extension is needed for it.

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS ltree;
