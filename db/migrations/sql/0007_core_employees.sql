-- SPDX-License-Identifier: AGPL-3.0-only
-- Migration 0007: employees, personal data, identity documents, bank accounts, effective-dated
-- job records and the reporting hierarchy (Phase 5b, DATA_MODEL §3, ADRs 0022 and 0023).
-- RLS, grants, touch_row and audit triggers are applied by the migration after this file.

-- "Today" for effective dating. Pickwise is built for Indian employers, so the calendar day
-- is the IST one, whatever timezone the database session uses.
CREATE FUNCTION core.today() RETURNS date
    LANGUAGE sql STABLE
    SET search_path = pg_catalog
AS $$ SELECT (now() AT TIME ZONE 'Asia/Kolkata')::date $$;
GRANT EXECUTE ON FUNCTION core.today() TO pickwise_app, pickwise_ops;

CREATE TABLE core.employees (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_code     citext      NOT NULL CHECK (employee_code::text ~ '^[A-Za-z0-9][A-Za-z0-9_/-]{0,29}$'),
    membership_id     uuid,
    status            text        NOT NULL DEFAULT 'draft' CHECK (status IN (
                          'draft', 'pre_boarding', 'active', 'notice_period', 'leave_of_absence', 'exited')),
    first_name        text        NOT NULL CHECK (length(first_name) BETWEEN 1 AND 100),
    middle_name       text        CHECK (length(middle_name) <= 100),
    last_name         text        CHECK (length(last_name) <= 100),
    preferred_name    text        CHECK (length(preferred_name) <= 100),
    display_name      text        GENERATED ALWAYS AS (
                          COALESCE(NULLIF(preferred_name, ''), btrim(first_name || ' ' || COALESCE(last_name, '')))
                      ) STORED,
    work_email        citext      CHECK (length(work_email::text) <= 254),
    work_phone        text        CHECK (length(work_phone) <= 30),
    personal_email    citext      CHECK (length(personal_email::text) <= 254),
    personal_phone    text        CHECK (length(personal_phone) <= 30),
    date_of_joining   date        NOT NULL,
    original_hire_date date,
    probation_end_date date,
    confirmation_date date,
    date_of_exit      date,
    photo_file_id     uuid,
    custom_fields     jsonb       NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, membership_id) REFERENCES platform.memberships (tenant_id, id),
    FOREIGN KEY (tenant_id, photo_file_id) REFERENCES platform.files (tenant_id, id),
    CONSTRAINT employees_exit_after_joining CHECK (date_of_exit IS NULL OR date_of_exit >= date_of_joining),
    CONSTRAINT employees_exited_has_date CHECK (status <> 'exited' OR date_of_exit IS NOT NULL)
);
CREATE UNIQUE INDEX employees_code ON core.employees (tenant_id, employee_code);
CREATE UNIQUE INDEX employees_membership ON core.employees (tenant_id, membership_id)
    WHERE membership_id IS NOT NULL;
CREATE UNIQUE INDEX employees_work_email ON core.employees (tenant_id, work_email)
    WHERE work_email IS NOT NULL;
CREATE INDEX employees_display_name_trgm ON core.employees USING gin (display_name public.gin_trgm_ops);
CREATE INDEX employees_code_trgm ON core.employees USING gin ((employee_code::text) public.gin_trgm_ops);
CREATE INDEX employees_status ON core.employees (tenant_id, status);

ALTER TABLE core.departments
    ADD FOREIGN KEY (tenant_id, head_employee_id) REFERENCES core.employees (tenant_id, id);

CREATE TABLE core.employee_personal (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id         uuid NOT NULL,
    date_of_birth       date CHECK (date_of_birth >= DATE '1900-01-01'),
    gender              text CHECK (gender IN ('female', 'male', 'non_binary', 'undisclosed')),
    marital_status      text CHECK (marital_status IN ('single', 'married', 'divorced', 'widowed', 'undisclosed')),
    blood_group         text CHECK (blood_group IN ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')),
    nationality         text CHECK (length(nationality) <= 60),
    father_or_spouse_name text CHECK (length(father_or_spouse_name) <= 200),
    is_person_with_disability boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id),
    UNIQUE (tenant_id, employee_id)
);

CREATE TABLE core.employee_addresses (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id  uuid NOT NULL,
    address_type text NOT NULL CHECK (address_type IN ('current', 'permanent')),
    line1        text NOT NULL CHECK (length(line1) BETWEEN 1 AND 200),
    line2        text CHECK (length(line2) <= 200),
    city         text NOT NULL CHECK (length(city) BETWEEN 1 AND 100),
    state_code   text CHECK (state_code ~ '^IN-[A-Z]{2}$'),
    pincode      text CHECK (pincode ~ '^[1-9][0-9]{5}$'),
    country_code text NOT NULL DEFAULT 'IN' CHECK (country_code ~ '^[A-Z]{2}$'),
    valid_during daterange NOT NULL CHECK (NOT isempty(valid_during) AND NOT lower_inf(valid_during)),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id),
    CONSTRAINT employee_addresses_no_overlap EXCLUDE USING gist (
        tenant_id WITH =, employee_id WITH =, address_type WITH =, valid_during WITH &&)
);

CREATE TABLE core.emergency_contacts (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id  uuid NOT NULL,
    name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    relationship text NOT NULL CHECK (length(relationship) BETWEEN 1 AND 60),
    phone        text NOT NULL CHECK (length(phone) BETWEEN 5 AND 30),
    email        citext CHECK (length(email::text) <= 254),
    is_primary   boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id)
);
CREATE UNIQUE INDEX emergency_contacts_primary ON core.emergency_contacts (tenant_id, employee_id)
    WHERE is_primary;

CREATE TABLE core.dependents (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id   uuid NOT NULL,
    name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    relationship  text NOT NULL CHECK (relationship IN (
                      'spouse', 'child', 'father', 'mother', 'sibling', 'other')),
    date_of_birth date,
    gender        text CHECK (gender IN ('female', 'male', 'non_binary', 'undisclosed')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id)
);

CREATE TABLE core.nominations (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id   uuid NOT NULL,
    scheme        text NOT NULL CHECK (scheme IN ('pf', 'eps', 'edli', 'gratuity', 'insurance')),
    dependent_id  uuid NOT NULL,
    share_percent numeric(5, 2) NOT NULL CHECK (share_percent > 0 AND share_percent <= 100),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id),
    FOREIGN KEY (tenant_id, dependent_id) REFERENCES core.dependents (tenant_id, id),
    UNIQUE (tenant_id, employee_id, scheme, dependent_id)
);

-- The shares of one scheme must total exactly 100 once the transaction commits. A deferred
-- constraint trigger lets a replacement set be written row by row.
CREATE FUNCTION core.check_nomination_shares() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, core
AS $$
DECLARE
    subject core.nominations;
    total   numeric;
BEGIN
    subject := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    SELECT sum(share_percent) INTO total FROM core.nominations
        WHERE tenant_id = subject.tenant_id AND employee_id = subject.employee_id
          AND scheme = subject.scheme;
    IF total IS NOT NULL AND total <> 100 THEN
        RAISE EXCEPTION 'nomination shares for % must total 100 (got %)', subject.scheme, total
            USING ERRCODE = 'check_violation', CONSTRAINT = 'nominations_shares_total_100';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER nominations_shares AFTER INSERT OR UPDATE OR DELETE ON core.nominations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.check_nomination_shares();

CREATE TABLE core.identity_documents (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id         uuid NOT NULL,
    doc_type            text NOT NULL CHECK (doc_type IN (
                            'pan', 'aadhaar', 'passport', 'uan', 'esic_ip', 'voter_id',
                            'driving_licence', 'visa')),
    value_enc           bytea,
    value_last4         text CHECK (value_last4 ~ '^[A-Z0-9]{1,4}$'),
    value_bidx          bytea,
    bidx_key_version    integer,
    name_as_per_doc     text CHECK (length(name_as_per_doc) <= 200),
    issued_on           date,
    expires_on          date,
    is_current          boolean NOT NULL DEFAULT true,
    superseded_by_id    uuid,
    file_id             uuid,
    verification_status text NOT NULL DEFAULT 'unverified' CHECK (verification_status IN (
                            'unverified', 'verified', 'rejected')),
    verified_by         uuid,
    verified_at         timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id),
    FOREIGN KEY (tenant_id, superseded_by_id) REFERENCES core.identity_documents (tenant_id, id),
    FOREIGN KEY (tenant_id, file_id) REFERENCES platform.files (tenant_id, id),
    CONSTRAINT identity_documents_dates CHECK (expires_on IS NULL OR issued_on IS NULL OR expires_on >= issued_on),
    -- A value without its blind index can't be checked for duplicates. Aadhaar in last4 mode
    -- stores neither (a hash of the full number is still derived data).
    CONSTRAINT identity_documents_bidx_required CHECK (
        doc_type IN ('aadhaar', 'voter_id', 'driving_licence', 'visa') OR
        (value_enc IS NOT NULL AND value_bidx IS NOT NULL)),
    CONSTRAINT identity_documents_enc_with_bidx CHECK ((value_enc IS NULL) = (value_bidx IS NULL))
);
CREATE UNIQUE INDEX identity_documents_value ON core.identity_documents (tenant_id, doc_type, value_bidx)
    WHERE is_current AND value_bidx IS NOT NULL;
CREATE UNIQUE INDEX identity_documents_one_current ON core.identity_documents (tenant_id, employee_id, doc_type)
    WHERE is_current;
CREATE INDEX identity_documents_employee ON core.identity_documents (tenant_id, employee_id);

CREATE TABLE core.bank_accounts (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id         uuid NOT NULL,
    account_holder_name text NOT NULL CHECK (length(account_holder_name) BETWEEN 1 AND 200),
    account_number_enc  bytea NOT NULL,
    account_last4       text NOT NULL CHECK (account_last4 ~ '^[0-9]{1,4}$'),
    account_bidx        bytea NOT NULL,
    bidx_key_version    integer,
    ifsc                text NOT NULL CHECK (ifsc ~ '^[A-Z]{4}0[A-Z0-9]{6}$'),
    bank_name           text NOT NULL CHECK (length(bank_name) BETWEEN 1 AND 200),
    account_type        text NOT NULL DEFAULT 'savings' CHECK (account_type IN ('savings', 'current', 'salary')),
    is_primary          boolean NOT NULL DEFAULT false,
    valid_during        daterange NOT NULL CHECK (NOT isempty(valid_during) AND NOT lower_inf(valid_during)),
    verification_status text NOT NULL DEFAULT 'unverified' CHECK (verification_status IN (
                            'unverified', 'penny_drop_ok', 'failed')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id),
    CONSTRAINT bank_accounts_one_primary EXCLUDE USING gist (
        tenant_id WITH =, employee_id WITH =, valid_during WITH &&) WHERE (is_primary)
);
CREATE INDEX bank_accounts_employee ON core.bank_accounts (tenant_id, employee_id);

CREATE TABLE core.employee_job_records (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id         uuid NOT NULL,
    valid_during        daterange NOT NULL CHECK (NOT isempty(valid_during) AND NOT lower_inf(valid_during)),
    legal_entity_id     uuid NOT NULL,
    location_id         uuid NOT NULL,
    department_id       uuid NOT NULL,
    designation_id      uuid NOT NULL,
    grade_id            uuid,
    cost_center_id      uuid,
    manager_employee_id uuid,
    employment_type     text NOT NULL CHECK (employment_type IN (
                            'full_time', 'part_time', 'fixed_term', 'contract', 'intern',
                            'apprentice', 'consultant')),
    change_reason       text NOT NULL CHECK (change_reason IN (
                            'hire', 'promotion', 'transfer', 'redesignation', 'manager_change',
                            'correction', 'rehire', 'migration')),
    approval_request_id uuid,
    notes               text CHECK (length(notes) <= 1000),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id),
    FOREIGN KEY (tenant_id, legal_entity_id) REFERENCES core.legal_entities (tenant_id, id),
    FOREIGN KEY (tenant_id, location_id) REFERENCES core.locations (tenant_id, id),
    FOREIGN KEY (tenant_id, department_id) REFERENCES core.departments (tenant_id, id),
    FOREIGN KEY (tenant_id, designation_id) REFERENCES core.designations (tenant_id, id),
    FOREIGN KEY (tenant_id, grade_id) REFERENCES core.grades (tenant_id, id),
    FOREIGN KEY (tenant_id, cost_center_id) REFERENCES core.cost_centers (tenant_id, id),
    FOREIGN KEY (tenant_id, manager_employee_id) REFERENCES core.employees (tenant_id, id),
    FOREIGN KEY (tenant_id, approval_request_id) REFERENCES platform.approval_requests (tenant_id, id),
    CONSTRAINT job_records_not_own_manager CHECK (manager_employee_id IS DISTINCT FROM employee_id),
    CONSTRAINT job_records_no_overlap EXCLUDE USING gist (
        tenant_id WITH =, employee_id WITH =, valid_during WITH &&)
);
CREATE INDEX job_records_manager ON core.employee_job_records (tenant_id, manager_employee_id);
CREATE INDEX job_records_department ON core.employee_job_records (tenant_id, department_id);
CREATE INDEX job_records_location ON core.employee_job_records (tenant_id, location_id);
CREATE INDEX job_records_legal_entity ON core.employee_job_records (tenant_id, legal_entity_id);
CREATE INDEX job_records_employee ON core.employee_job_records (tenant_id, employee_id);

-- The record in force today; security_invoker so the caller's row-level security applies.
CREATE VIEW core.employee_job_records_current WITH (security_invoker = true) AS
    SELECT * FROM core.employee_job_records WHERE valid_during @> core.today();
GRANT SELECT ON core.employee_job_records_current TO pickwise_app, pickwise_ops;

-- The current reporting tree as a closure table: one row per (ancestor, descendant), including
-- the employee themselves at depth 0. Kept in step by the job-change service and rebuilt
-- by the daily job; it powers the direct_reports / all_reports scopes.
CREATE TABLE core.employee_hierarchy (
    tenant_id              uuid    NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    ancestor_employee_id   uuid    NOT NULL,
    descendant_employee_id uuid    NOT NULL,
    depth                  integer NOT NULL CHECK (depth >= 0),
    PRIMARY KEY (tenant_id, ancestor_employee_id, descendant_employee_id),
    FOREIGN KEY (tenant_id, ancestor_employee_id) REFERENCES core.employees (tenant_id, id),
    FOREIGN KEY (tenant_id, descendant_employee_id) REFERENCES core.employees (tenant_id, id)
);
CREATE INDEX employee_hierarchy_descendant ON core.employee_hierarchy (tenant_id, descendant_employee_id);

CREATE TABLE core.employee_documents (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id         uuid NOT NULL,
    category            text NOT NULL CHECK (category IN (
                            'offer_letter', 'appointment_letter', 'id_proof', 'address_proof',
                            'education', 'experience', 'policy_ack', 'other')),
    file_id             uuid NOT NULL,
    title               text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    visible_to_employee boolean NOT NULL DEFAULT false,
    expires_on          date,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id),
    FOREIGN KEY (tenant_id, file_id) REFERENCES platform.files (tenant_id, id)
);
CREATE INDEX employee_documents_employee ON core.employee_documents (tenant_id, employee_id);

CREATE TABLE core.education_history (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id  uuid NOT NULL,
    institution  text NOT NULL CHECK (length(institution) BETWEEN 1 AND 200),
    degree       text NOT NULL CHECK (length(degree) BETWEEN 1 AND 200),
    field_of_study text CHECK (length(field_of_study) <= 200),
    start_year   integer CHECK (start_year BETWEEN 1950 AND 2100),
    end_year     integer CHECK (end_year BETWEEN 1950 AND 2100),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id),
    CONSTRAINT education_years CHECK (start_year IS NULL OR end_year IS NULL OR end_year >= start_year)
);

CREATE TABLE core.employment_history (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    employee_id uuid NOT NULL,
    employer    text NOT NULL CHECK (length(employer) BETWEEN 1 AND 200),
    title       text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    from_date   date,
    to_date     date,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES core.employees (tenant_id, id),
    CONSTRAINT employment_dates CHECK (from_date IS NULL OR to_date IS NULL OR to_date >= from_date)
);
