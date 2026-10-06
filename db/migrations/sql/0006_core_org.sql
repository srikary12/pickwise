-- SPDX-License-Identifier: AGPL-3.0-only
-- Migration 0006: core organisation structure (Phase 5a, DATA_MODEL §3, ADR 0021).
-- RLS, grants, touch_row and audit triggers are applied by the migration after this file.
-- departments.head_employee_id gets its foreign key in 0007, once core.employees exists.

CREATE TABLE core.legal_entities (
    tenant_id             uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id                    uuid        NOT NULL DEFAULT uuidv7(),
    name                  text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    legal_name            text        NOT NULL CHECK (length(legal_name) BETWEEN 1 AND 300),
    country_code          text        NOT NULL DEFAULT 'IN' CHECK (country_code ~ '^[A-Z]{2}$'),
    pan                   text        CHECK (pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'),
    tan                   text        CHECK (tan ~ '^[A-Z]{4}[0-9]{5}[A-Z]$'),
    gstin                 text        CHECK (gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$'),
    cin                   text        CHECK (cin ~ '^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$'),
    registered_address    jsonb       NOT NULL DEFAULT '{}',
    pf_establishment_code text        CHECK (length(pf_establishment_code) <= 40),
    esi_employer_code     text        CHECK (length(esi_employer_code) <= 40),
    logo_file_id          uuid,
    archived_at           timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    created_by            uuid                 DEFAULT platform.current_user_id(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    updated_by            uuid,
    row_version           integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, logo_file_id) REFERENCES platform.files (tenant_id, id)
);
CREATE UNIQUE INDEX legal_entities_name ON core.legal_entities (tenant_id, lower(name))
    WHERE archived_at IS NULL;

CREATE TABLE core.legal_entity_registrations (
    tenant_id         uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id                uuid        NOT NULL DEFAULT uuidv7(),
    legal_entity_id   uuid        NOT NULL,
    registration_type text        NOT NULL CHECK (registration_type IN ('PT', 'LWF', 'SHOPS_ESTABLISHMENT', 'FACTORY')),
    state_code        text        NOT NULL CHECK (state_code ~ '^IN-[A-Z]{2}$'),
    registration_no   text        NOT NULL CHECK (length(registration_no) BETWEEN 1 AND 60),
    valid_during      daterange   NOT NULL CHECK (NOT isempty(valid_during) AND NOT lower_inf(valid_during)),
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by        uuid                 DEFAULT platform.current_user_id(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        uuid,
    row_version       integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, legal_entity_id) REFERENCES core.legal_entities (tenant_id, id),
    CONSTRAINT legal_entity_registrations_no_overlap EXCLUDE USING gist (
        tenant_id WITH =, legal_entity_id WITH =, registration_type WITH =, state_code WITH =,
        valid_during WITH &&)
);

CREATE TABLE core.locations (
    tenant_id          uuid          NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id                 uuid          NOT NULL DEFAULT uuidv7(),
    legal_entity_id    uuid          NOT NULL,
    code               text          NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,29}$'),
    name               text          NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    address            jsonb         NOT NULL DEFAULT '{}',
    state_code         text          NOT NULL CHECK (state_code ~ '^IN-[A-Z]{2}$'),
    city               text          CHECK (length(city) <= 100),
    pincode            text          CHECK (pincode ~ '^[1-9][0-9]{5}$'),
    timezone           text          CHECK (length(timezone) <= 64),
    latitude           numeric(9, 6) CHECK (latitude BETWEEN -90 AND 90),
    longitude          numeric(9, 6) CHECK (longitude BETWEEN -180 AND 180),
    geofence_radius_m  integer       CHECK (geofence_radius_m BETWEEN 10 AND 100000),
    archived_at        timestamptz,
    created_at         timestamptz   NOT NULL DEFAULT now(),
    created_by         uuid                   DEFAULT platform.current_user_id(),
    updated_at         timestamptz   NOT NULL DEFAULT now(),
    updated_by         uuid,
    row_version        integer       NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, legal_entity_id) REFERENCES core.legal_entities (tenant_id, id),
    CONSTRAINT locations_geofence_needs_point CHECK (
        geofence_radius_m IS NULL OR (latitude IS NOT NULL AND longitude IS NOT NULL))
);
CREATE UNIQUE INDEX locations_code ON core.locations (tenant_id, lower(code));
CREATE INDEX locations_legal_entity ON core.locations (tenant_id, legal_entity_id);

CREATE TABLE core.cost_centers (
    tenant_id       uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id              uuid        NOT NULL DEFAULT uuidv7(),
    legal_entity_id uuid        NOT NULL,
    code            text        NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,29}$'),
    name            text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    archived_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      uuid                 DEFAULT platform.current_user_id(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      uuid,
    row_version     integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, legal_entity_id) REFERENCES core.legal_entities (tenant_id, id)
);
CREATE UNIQUE INDEX cost_centers_code ON core.cost_centers (tenant_id, lower(code));

CREATE TABLE core.designations (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    code        text        NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,29}$'),
    name        text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    job_family  text        CHECK (length(job_family) <= 100),
    archived_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id)
);
CREATE UNIQUE INDEX designations_code ON core.designations (tenant_id, lower(code));

CREATE TABLE core.grades (
    tenant_id   uuid          NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid          NOT NULL DEFAULT uuidv7(),
    code        text          NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,29}$'),
    name        text          NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    rank        integer       NOT NULL CHECK (rank >= 0),
    ctc_min     numeric(14, 2) CHECK (ctc_min >= 0),
    ctc_max     numeric(14, 2) CHECK (ctc_max >= 0),
    archived_at timestamptz,
    created_at  timestamptz   NOT NULL DEFAULT now(),
    created_by  uuid                   DEFAULT platform.current_user_id(),
    updated_at  timestamptz   NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer       NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT grades_ctc_range CHECK (ctc_min IS NULL OR ctc_max IS NULL OR ctc_min <= ctc_max)
);
CREATE UNIQUE INDEX grades_code ON core.grades (tenant_id, lower(code));

CREATE TABLE core.departments (
    tenant_id        uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id               uuid        NOT NULL DEFAULT uuidv7(),
    code             text        NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,29}$'),
    name             text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    parent_id        uuid,
    path             public.ltree NOT NULL,
    head_employee_id uuid,
    cost_center_id   uuid,
    archived_at      timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       uuid                 DEFAULT platform.current_user_id(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    updated_by       uuid,
    row_version      integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, parent_id) REFERENCES core.departments (tenant_id, id),
    FOREIGN KEY (tenant_id, cost_center_id) REFERENCES core.cost_centers (tenant_id, id),
    CONSTRAINT departments_not_own_parent CHECK (parent_id IS DISTINCT FROM id)
);
CREATE UNIQUE INDEX departments_code ON core.departments (tenant_id, lower(code));
CREATE INDEX departments_path ON core.departments USING gist (path);
CREATE INDEX departments_parent ON core.departments (tenant_id, parent_id);

-- The materialised path (label = the id without hyphens) is owned by this trigger:
--   * on insert and on a parent change it is derived from the parent's path;
--   * a move that would put a department under itself or one of its descendants is rejected;
--   * the descendants' paths are rewritten after a move (AFTER trigger, below);
--   * nobody else may write it.
-- Moves in a tenant are serialised with an advisory lock, so two concurrent moves cannot each
-- look acyclic and together create a cycle.
CREATE FUNCTION core.department_path_before() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, core
AS $$
DECLARE
    parent_path public.ltree;
    own_label   text := replace(NEW.id::text, '-', '');
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.parent_id IS NOT DISTINCT FROM OLD.parent_id THEN
        -- Descendant rewrites arrive from the AFTER trigger (nested, depth > 1).
        IF NEW.path IS DISTINCT FROM OLD.path AND pg_trigger_depth() = 1 THEN
            RAISE EXCEPTION 'core.departments.path is maintained by trigger'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext('core.departments'), hashtext(NEW.tenant_id::text));

    IF NEW.parent_id IS NULL THEN
        NEW.path := own_label::public.ltree;
        RETURN NEW;
    END IF;

    SELECT path INTO parent_path FROM core.departments
        WHERE tenant_id = NEW.tenant_id AND id = NEW.parent_id;
    IF parent_path IS NULL THEN
        RAISE EXCEPTION 'parent department does not exist'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND parent_path OPERATOR(public.<@) OLD.path THEN
        RAISE EXCEPTION 'a department cannot be moved under itself or its descendants'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'departments_no_cycle';
    END IF;
    NEW.path := parent_path OPERATOR(public.||) own_label::public.ltree;
    RETURN NEW;
END
$$;

CREATE FUNCTION core.department_path_after() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, core
AS $$
BEGIN
    -- The rewrites below fire this trigger again for each descendant; they are already done.
    IF pg_trigger_depth() > 1 THEN
        RETURN NULL;
    END IF;
    UPDATE core.departments d
       SET path = NEW.path OPERATOR(public.||) public.subpath(d.path, public.nlevel(OLD.path))
     WHERE d.tenant_id = NEW.tenant_id
       AND d.path OPERATOR(public.<@) OLD.path
       AND d.id <> NEW.id;
    RETURN NULL;
END
$$;

CREATE TRIGGER department_path_before BEFORE INSERT OR UPDATE ON core.departments
    FOR EACH ROW EXECUTE FUNCTION core.department_path_before();
CREATE TRIGGER department_path_after AFTER UPDATE ON core.departments
    FOR EACH ROW WHEN (OLD.path IS DISTINCT FROM NEW.path)
    EXECUTE FUNCTION core.department_path_after();
