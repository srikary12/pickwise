-- SPDX-License-Identifier: AGPL-3.0-only
-- Migration 0001: foundation objects (DATA_MODEL §0, CLAUDE.md rules 1-7, 15; ADRs 0002, 0003).
-- Runs as pickwise_owner, so every object here is owner-owned.

-- Functions get no EXECUTE for PUBLIC by default; each one is granted explicitly.
ALTER DEFAULT PRIVILEGES FOR ROLE pickwise_owner REVOKE EXECUTE ON ROUTINES FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Schemas: one per module.
-- ---------------------------------------------------------------------------
CREATE SCHEMA platform AUTHORIZATION pickwise_owner;
CREATE SCHEMA audit AUTHORIZATION pickwise_owner;
CREATE SCHEMA core AUTHORIZATION pickwise_owner;
CREATE SCHEMA leave AUTHORIZATION pickwise_owner;
CREATE SCHEMA attendance AUTHORIZATION pickwise_owner;
CREATE SCHEMA payroll AUTHORIZATION pickwise_owner;
CREATE SCHEMA ai AUTHORIZATION pickwise_owner;
CREATE SCHEMA recruit AUTHORIZATION pickwise_owner;
GRANT USAGE ON SCHEMA platform, audit, core, leave, attendance, payroll, ai, recruit
    TO pickwise_app, pickwise_ops;

-- ---------------------------------------------------------------------------
-- Request context. NULLIF: a pooled connection reads '' (not NULL) once a
-- transaction-local setting has ended, and ''::uuid would raise. Unset context
-- must mean "no rows", never an error.
-- ---------------------------------------------------------------------------
CREATE FUNCTION platform.current_tenant_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    SET search_path = pg_catalog
AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$;

CREATE FUNCTION platform.current_user_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    SET search_path = pg_catalog
AS $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;

GRANT EXECUTE ON FUNCTION platform.current_tenant_id(), platform.current_user_id()
    TO pickwise_app, pickwise_ops;

-- ---------------------------------------------------------------------------
-- Row maintenance triggers.
-- ---------------------------------------------------------------------------
CREATE FUNCTION platform.touch_row() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, platform
AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
        RAISE EXCEPTION '%.%: tenant_id is immutable', TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION '%.%: created_at/created_by are immutable', TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.updated_at := now();
    NEW.updated_by := platform.current_user_id();
    NEW.row_version := OLD.row_version + 1;
    RETURN NEW;
END
$$;

-- Global tables have no tenant_id or created_by/updated_by.
CREATE FUNCTION platform.touch_row_global() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION '%.%: created_at is immutable', TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.updated_at := now();
    NEW.row_version := OLD.row_version + 1;
    RETURN NEW;
END
$$;

-- Append-only tables (CLAUDE.md rule 7). The only exception is the ops role in
-- purge mode: DELETE anywhere, UPDATE on audit.events only (audit.scrub_subject).
CREATE FUNCTION platform.forbid_mutation() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
DECLARE
    root regclass := coalesce(pg_partition_root(TG_RELID), TG_RELID::regclass);
BEGIN
    IF current_user = 'pickwise_ops' AND current_setting('app.purge_mode', true) = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        IF TG_OP = 'UPDATE' AND root = to_regclass('audit.events') THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION '% is append-only: % is not allowed', root, TG_OP
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'Write a reversing entry instead of changing history.';
END
$$;

-- ---------------------------------------------------------------------------
-- Migration helpers (owner-only). Later module migrations call these, so the
-- RLS/grant rules live in exactly one place.
-- ---------------------------------------------------------------------------

-- ENABLE + FORCE RLS and exactly the three permissive policies (ADR 0002).
CREATE FUNCTION platform.apply_rls(rel regclass, key_column text DEFAULT 'tenant_id') RETURNS void
    LANGUAGE plpgsql
    SET search_path = pg_catalog, platform
AS $$
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', rel);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', rel);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s', rel);
    EXECUTE format('DROP POLICY IF EXISTS ops_all ON %s', rel);
    EXECUTE format('DROP POLICY IF EXISTS owner_all ON %s', rel);
    EXECUTE format(
        'CREATE POLICY tenant_isolation ON %s USING (%I = platform.current_tenant_id()) '
        'WITH CHECK (%I = platform.current_tenant_id())', rel, key_column, key_column);
    EXECUTE format('CREATE POLICY ops_all ON %s TO pickwise_ops USING (true) WITH CHECK (true)', rel);
    EXECUTE format('CREATE POLICY owner_all ON %s TO pickwise_owner USING (true) WITH CHECK (true)', rel);
END
$$;

CREATE FUNCTION platform.has_table_marker(rel regclass, marker text) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path = pg_catalog
AS $$
    SELECT coalesce(obj_description(rel, 'pg_class'), '') ~ ('(^|\s)' || marker || '(\s|$)')
$$;

-- Marks a table append-only: forbid_mutation() trigger + '@append_only' marker,
-- which apply_tenant_policies() reads to grant the app only SELECT and INSERT.
CREATE FUNCTION platform.make_append_only(rel regclass) RETURNS void
    LANGUAGE plpgsql
    SET search_path = pg_catalog, platform
AS $$
BEGIN
    EXECUTE format('COMMENT ON TABLE %s IS %L', rel, '@append_only');
    EXECUTE format(
        'CREATE OR REPLACE TRIGGER forbid_mutation BEFORE UPDATE OR DELETE ON %s '
        'FOR EACH ROW EXECUTE FUNCTION platform.forbid_mutation()', rel);
END
$$;

-- Global (non-tenant) tables: '@global' marker, explicit app privileges, full
-- DML for ops, touch_row_global where the table has row_version.
CREATE FUNCTION platform.register_global_table(rel regclass, app_privileges text) RETURNS void
    LANGUAGE plpgsql
    SET search_path = pg_catalog, platform
AS $$
BEGIN
    IF app_privileges !~ '^(SELECT|INSERT|UPDATE|DELETE)(, (SELECT|INSERT|UPDATE|DELETE))*$' THEN
        RAISE EXCEPTION 'invalid privilege list: %', app_privileges;
    END IF;
    EXECUTE format('COMMENT ON TABLE %s IS %L', rel, '@global');
    EXECUTE format('REVOKE ALL ON %s FROM PUBLIC', rel);
    EXECUTE format('GRANT %s ON %s TO pickwise_app', app_privileges, rel);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO pickwise_ops', rel);
    IF EXISTS (SELECT 1 FROM pg_attribute
               WHERE attrelid = rel AND attname = 'row_version' AND NOT attisdropped) THEN
        EXECUTE format(
            'CREATE OR REPLACE TRIGGER touch_row BEFORE UPDATE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION platform.touch_row_global()', rel);
    END IF;
END
$$;

-- Every tenant table in a schema: RLS + policies, grants, touch_row.
-- Skips '@global' tables and partitions (ensure_monthly_partitions handles those).
CREATE FUNCTION platform.apply_tenant_policies(schema_name text) RETURNS integer
    LANGUAGE plpgsql
    SET search_path = pg_catalog, platform
AS $$
DECLARE
    rel regclass;
    applied integer := 0;
BEGIN
    FOR rel IN
        SELECT c.oid::regclass
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = schema_name
          AND c.relkind IN ('r', 'p')
          AND NOT c.relispartition
          AND EXISTS (SELECT 1 FROM pg_attribute a
                      WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
          AND NOT platform.has_table_marker(c.oid::regclass, '@global')
        ORDER BY c.relname
    LOOP
        PERFORM platform.apply_rls(rel, 'tenant_id');
        EXECUTE format('REVOKE ALL ON %s FROM PUBLIC', rel);
        IF platform.has_table_marker(rel, '@append_only') THEN
            EXECUTE format('GRANT SELECT, INSERT ON %s TO pickwise_app', rel);
        ELSE
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO pickwise_app', rel);
        END IF;
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO pickwise_ops', rel);
        IF EXISTS (SELECT 1 FROM pg_attribute
                   WHERE attrelid = rel AND attname = 'row_version' AND NOT attisdropped) THEN
            EXECUTE format(
                'CREATE OR REPLACE TRIGGER touch_row BEFORE UPDATE ON %s '
                'FOR EACH ROW EXECUTE FUNCTION platform.touch_row()', rel);
        END IF;
        applied := applied + 1;
    END LOOP;
    RETURN applied;
END
$$;

-- ---------------------------------------------------------------------------
-- Monthly partitions (ADR 0003). SECURITY DEFINER because creating a partition
-- requires owning the parent; EXECUTE is granted to pickwise_ops only. It reads
-- no tenant data. Every child gets RLS + the three policies and no app grants.
-- ---------------------------------------------------------------------------
CREATE FUNCTION platform.ensure_monthly_partitions(
    parent regclass, months_ahead integer DEFAULT 3, months_back integer DEFAULT 1
) RETURNS integer
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, platform
AS $$
DECLARE
    parent_schema text;
    parent_name text;
    first_month date;
    month_start date;
    child text;
    qualified text;
    created integer := 0;
BEGIN
    IF months_ahead NOT BETWEEN 0 AND 24 OR months_back NOT BETWEEN 0 AND 24 THEN
        RAISE EXCEPTION 'months_ahead and months_back must be between 0 and 24';
    END IF;
    SELECT n.nspname, c.relname INTO parent_schema, parent_name
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.oid = parent AND c.relkind = 'p';
    IF NOT FOUND THEN
        RAISE EXCEPTION '% is not a partitioned table', parent;
    END IF;
    IF parent_schema NOT IN ('platform', 'audit', 'core', 'leave', 'attendance', 'payroll', 'ai', 'recruit') THEN
        RAISE EXCEPTION '% is not a Pickwise table', parent;
    END IF;

    first_month := (date_trunc('month', now() AT TIME ZONE 'UTC') - make_interval(months => months_back))::date;
    FOR i IN 0 .. months_back + months_ahead LOOP
        month_start := (first_month + make_interval(months => i))::date;
        child := format('%s_y%sm%s', parent_name, to_char(month_start, 'YYYY'), to_char(month_start, 'MM'));
        qualified := format('%I.%I', parent_schema, child);
        CONTINUE WHEN to_regclass(qualified) IS NOT NULL;

        EXECUTE format(
            'CREATE TABLE %s PARTITION OF %s FOR VALUES FROM (%L) TO (%L)',
            qualified, parent,
            (month_start::timestamp AT TIME ZONE 'UTC'),
            ((month_start + interval '1 month')::timestamp AT TIME ZONE 'UTC'));
        PERFORM platform.apply_rls(qualified::regclass, 'tenant_id');
        EXECUTE format('REVOKE ALL ON %s FROM PUBLIC, pickwise_app', qualified);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO pickwise_ops', qualified);
        created := created + 1;
    END LOOP;
    RETURN created;
END
$$;
GRANT EXECUTE ON FUNCTION platform.ensure_monthly_partitions(regclass, integer, integer) TO pickwise_ops;

-- ---------------------------------------------------------------------------
-- Audit (CLAUDE.md rule 15).
-- ---------------------------------------------------------------------------

-- Generic row-change trigger. TG_ARGV lists the columns to redact (every
-- '@pii confidential|restricted' column, from audit.attach()); their values are
-- never written, only "[set]" / "[changed]" / "[removed]".
CREATE FUNCTION audit.capture_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, platform, audit
AS $$
DECLARE
    old_row jsonb := CASE WHEN TG_OP IN ('UPDATE', 'DELETE') THEN to_jsonb(OLD) END;
    new_row jsonb := CASE WHEN TG_OP IN ('INSERT', 'UPDATE') THEN to_jsonb(NEW) END;
    subject jsonb := coalesce(new_row, old_row);
    skipped text[] := ARRAY['tenant_id', 'id', 'created_at', 'created_by', 'updated_at', 'updated_by', 'row_version'];
    diff jsonb := '{}'::jsonb;
    col text;
    old_value jsonb;
    new_value jsonb;
    actor text := NULLIF(current_setting('app.actor_type', true), '');
BEGIN
    -- Tenant purge deletes audited rows; recording them would re-create audit
    -- rows for a tenant that is being removed.
    IF current_user = 'pickwise_ops' AND current_setting('app.purge_mode', true) = 'on' THEN
        RETURN NULL;
    END IF;

    FOR col IN SELECT jsonb_object_keys(subject) LOOP
        CONTINUE WHEN col = ANY (skipped);
        old_value := old_row -> col;
        new_value := new_row -> col;
        IF TG_OP = 'UPDATE' AND old_value IS NOT DISTINCT FROM new_value THEN
            CONTINUE;
        END IF;
        IF TG_OP = 'INSERT' AND new_value = 'null'::jsonb THEN
            CONTINUE;
        END IF;
        IF col = ANY (TG_ARGV) THEN
            diff := diff || jsonb_build_object(col, CASE TG_OP
                WHEN 'INSERT' THEN '[set]' WHEN 'DELETE' THEN '[removed]' ELSE '[changed]' END);
        ELSE
            diff := diff || jsonb_build_object(col, jsonb_build_array(old_value, new_value));
        END IF;
    END LOOP;

    IF TG_OP = 'UPDATE' AND diff = '{}'::jsonb THEN
        RETURN NULL;
    END IF;

    INSERT INTO audit.events (
        tenant_id, actor_user_id, actor_type, action,
        entity_schema, entity_table, entity_id, changes, request_id, ip
    ) VALUES (
        (subject ->> 'tenant_id')::uuid,
        platform.current_user_id(),
        CASE WHEN actor IN ('user', 'api_key', 'candidate', 'system', 'worker') THEN actor ELSE 'system' END,
        lower(TG_OP),
        TG_TABLE_SCHEMA,
        TG_TABLE_NAME,
        (subject ->> 'id')::uuid,
        diff,
        NULLIF(current_setting('app.request_id', true), ''),
        NULLIF(current_setting('app.client_ip', true), '')::inet
    );
    RETURN NULL;
END
$$;

-- Attach the audit trigger to a table; the redaction list is every column whose
-- comment is '@pii confidential' or '@pii restricted' (from db/pii_classification.yaml).
CREATE FUNCTION audit.attach(rel regclass) RETURNS text[]
    LANGUAGE plpgsql
    SET search_path = pg_catalog, platform, audit
AS $$
DECLARE
    redacted text[];
BEGIN
    SELECT coalesce(array_agg(a.attname::text ORDER BY a.attnum), '{}')
    INTO redacted
    FROM pg_attribute a
    WHERE a.attrelid = rel AND a.attnum > 0 AND NOT a.attisdropped
      AND col_description(rel, a.attnum) IN ('@pii confidential', '@pii restricted');

    EXECUTE format(
        'CREATE OR REPLACE TRIGGER audit_capture AFTER INSERT OR UPDATE OR DELETE ON %s '
        'FOR EACH ROW EXECUTE FUNCTION audit.capture_change(%s)',
        rel,
        coalesce((SELECT string_agg(quote_literal(r), ', ') FROM unnest(redacted) r), ''));
    RETURN redacted;
END
$$;

-- Erasure (Phase 12) nulls the diffs of a subject's audit rows. SECURITY INVOKER,
-- ops only, and only in purge mode (forbid_mutation lets this UPDATE through).
CREATE FUNCTION audit.scrub_subject(entity_table text, entity_id uuid) RETURNS bigint
    LANGUAGE plpgsql
    SET search_path = pg_catalog, platform, audit
AS $$
DECLARE
    target_schema text := split_part(entity_table, '.', 1);
    target_table text := split_part(entity_table, '.', 2);
    scrubbed bigint;
BEGIN
    IF current_user <> 'pickwise_ops' OR current_setting('app.purge_mode', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION 'audit.scrub_subject runs only as pickwise_ops in purge mode'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF target_table = '' THEN
        RAISE EXCEPTION 'entity_table must be schema-qualified, got %', entity_table;
    END IF;
    UPDATE audit.events e
    SET changes = NULL
    WHERE e.entity_schema = target_schema
      AND e.entity_table = target_table
      AND e.entity_id = scrub_subject.entity_id
      AND e.changes IS NOT NULL;
    GET DIAGNOSTICS scrubbed = ROW_COUNT;
    RETURN scrubbed;
END
$$;
GRANT EXECUTE ON FUNCTION audit.scrub_subject(text, uuid) TO pickwise_ops;

-- ---------------------------------------------------------------------------
-- Tenant purge (ADR 0002). SECURITY INVOKER, ops only, closed tenants only.
-- 1. null every non-tenant_id FK pointing at the tenant (e.g. sessions.active_tenant_id)
--    and every self-reference inside the tenant's rows;
-- 2. delete from each table with tenant_id, children before parents, in an
--    order computed from pg_constraint (the graph is acyclic by policy);
-- 3. delete the tenant row.
-- Returns one row per table with the number of rows deleted.
-- ---------------------------------------------------------------------------
CREATE FUNCTION platform.purge_tenant(target_tenant_id uuid)
    RETURNS TABLE (table_name text, deleted_rows bigint)
    LANGUAGE plpgsql
    SET search_path = pg_catalog, platform
AS $$
DECLARE
    pickwise_schemas text[] := ARRAY['platform', 'audit', 'core', 'leave', 'attendance', 'payroll', 'ai', 'recruit'];
    tenants_oid oid := 'platform.tenants'::regclass;
    remaining oid[];
    candidate oid;
    progressed boolean;
    fk record;
    n bigint;
BEGIN
    IF current_user <> 'pickwise_ops' THEN
        RAISE EXCEPTION 'purge_tenant must run as pickwise_ops (SET LOCAL ROLE pickwise_ops)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    PERFORM 1 FROM platform.tenants t WHERE t.id = target_tenant_id AND t.status = 'closed';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'tenant % does not exist or is not closed', target_tenant_id;
    END IF;
    PERFORM set_config('app.purge_mode', 'on', true);

    -- 1a. FK columns other than tenant_id that reference platform.tenants.
    FOR fk IN
        SELECT k.conrelid::regclass AS rel, a.attname AS col
        FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid AND NOT c.relispartition
        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
        WHERE k.contype = 'f' AND k.confrelid = tenants_oid
          AND cardinality(k.conkey) = 1 AND a.attname <> 'tenant_id'
    LOOP
        EXECUTE format('UPDATE %s SET %I = NULL WHERE %I = $1', fk.rel, fk.col, fk.col)
            USING target_tenant_id;
    END LOOP;

    -- 1b. Self-references: null the non-tenant_id columns of the FK.
    FOR fk IN
        SELECT k.conrelid::regclass AS rel,
               string_agg(format('%I = NULL', a.attname), ', ') AS set_clause,
               string_agg(format('%I IS NOT NULL', a.attname), ' OR ') AS any_set
        FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid AND NOT c.relispartition
        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = ANY (k.conkey)
        WHERE k.contype = 'f' AND k.conrelid = k.confrelid AND a.attname <> 'tenant_id'
        GROUP BY k.oid, k.conrelid
    LOOP
        EXECUTE format('UPDATE %s SET %s WHERE tenant_id = $1 AND (%s)', fk.rel, fk.set_clause, fk.any_set)
            USING target_tenant_id;
    END LOOP;

    -- 2. Topological delete over every table that has a tenant_id column.
    SELECT array_agg(c.oid) INTO remaining
    FROM pg_class c
    JOIN pg_namespace ns ON ns.oid = c.relnamespace
    WHERE ns.nspname = ANY (pickwise_schemas)
      AND c.relkind IN ('r', 'p') AND NOT c.relispartition
      AND c.oid <> tenants_oid
      AND EXISTS (SELECT 1 FROM pg_attribute a
                  WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped);

    WHILE coalesce(cardinality(remaining), 0) > 0 LOOP
        progressed := false;
        FOREACH candidate IN ARRAY remaining LOOP
            -- A table can go once no other remaining table references it.
            CONTINUE WHEN EXISTS (
                SELECT 1 FROM pg_constraint k
                WHERE k.contype = 'f' AND k.confrelid = candidate
                  AND k.conrelid <> candidate AND k.conrelid = ANY (remaining));
            EXECUTE format('DELETE FROM %s WHERE tenant_id = $1', candidate::regclass)
                USING target_tenant_id;
            GET DIAGNOSTICS n = ROW_COUNT;
            -- Always schema-qualified (regclass::text drops the schema for tables on
            -- this function's search_path).
            SELECT format('%I.%I', ns.nspname, c.relname) INTO table_name
            FROM pg_class c JOIN pg_namespace ns ON ns.oid = c.relnamespace WHERE c.oid = candidate;
            deleted_rows := n;
            RETURN NEXT;
            remaining := array_remove(remaining, candidate);
            progressed := true;
        END LOOP;
        IF NOT progressed THEN
            RAISE EXCEPTION 'foreign-key cycle among %; purge cannot order the deletes',
                (SELECT array_agg(r::regclass) FROM unnest(remaining) r);
        END IF;
    END LOOP;

    DELETE FROM platform.tenants t WHERE t.id = target_tenant_id;
    GET DIAGNOSTICS n = ROW_COUNT;
    table_name := 'platform.tenants';
    deleted_rows := n;
    RETURN NEXT;

    PERFORM set_config('app.purge_mode', 'off', true);
END
$$;
GRANT EXECUTE ON FUNCTION platform.purge_tenant(uuid) TO pickwise_ops;
