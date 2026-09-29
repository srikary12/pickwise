-- SPDX-License-Identifier: AGPL-3.0-only
-- Migration 0002: platform + audit tables (DATA_MODEL §1-2).
-- Tenant tables follow the template in DATA_MODEL §0: PK (tenant_id, id), composite
-- FKs without ON DELETE CASCADE, created_*/updated_*/row_version. RLS, grants and
-- touch_row come from platform.apply_tenant_policies() at the end.

-- ===========================================================================
-- Tenancy root and global identity tables
-- ===========================================================================
CREATE TABLE platform.tenants (
    id               uuid        NOT NULL DEFAULT uuidv7() PRIMARY KEY,
    slug             citext      NOT NULL UNIQUE CHECK (slug::text ~ '^[a-z0-9-]{3,40}$'),
    name             text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    status           text        NOT NULL DEFAULT 'provisioning'
                                 CHECK (status IN ('provisioning', 'active', 'suspended', 'closed')),
    plan             text        NOT NULL DEFAULT 'standard',
    country_code     text        NOT NULL DEFAULT 'IN' CHECK (country_code ~ '^[A-Z]{2}$'),
    default_timezone text        NOT NULL DEFAULT 'Asia/Kolkata',
    default_currency text        NOT NULL DEFAULT 'INR' CHECK (default_currency ~ '^[A-Z]{3}$'),
    settings         jsonb       NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(settings) = 'object'),
    data_region      text        NOT NULL DEFAULT 'in',
    closed_at        timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    row_version      integer     NOT NULL DEFAULT 1,
    CHECK (status <> 'closed' OR closed_at IS NOT NULL)
);

CREATE TABLE platform.users (
    id                  uuid        NOT NULL DEFAULT uuidv7() PRIMARY KEY,
    email               citext      NOT NULL UNIQUE,
    email_verified_at   timestamptz,
    display_name        text        NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
    password_hash       text,
    mfa_totp_secret_enc bytea,
    mfa_enabled         boolean     NOT NULL DEFAULT false,
    status              text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'locked', 'disabled')),
    failed_login_count  integer     NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    locked_until        timestamptz,
    last_login_at       timestamptz,
    locale              text        NOT NULL DEFAULT 'en-IN',
    settings            jsonb       NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(settings) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    row_version         integer     NOT NULL DEFAULT 1,
    CHECK (NOT mfa_enabled OR mfa_totp_secret_enc IS NOT NULL)
);

CREATE TABLE platform.platform_keys (
    id          uuid        NOT NULL DEFAULT uuidv7() PRIMARY KEY,
    purpose     text        NOT NULL CHECK (purpose IN ('data')),
    version     integer     NOT NULL CHECK (version > 0),
    wrapped_key bytea       NOT NULL,
    kek_id      text        NOT NULL,
    status      text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rotating', 'retired')),
    rotated_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    row_version integer     NOT NULL DEFAULT 1,
    UNIQUE (purpose, version)
);
CREATE UNIQUE INDEX platform_keys_one_active ON platform.platform_keys (purpose) WHERE status = 'active';

CREATE TABLE platform.auth_tokens (
    id         uuid        NOT NULL DEFAULT uuidv7() PRIMARY KEY,
    purpose    text        NOT NULL CHECK (purpose IN ('verify_email', 'reset_password', 'invite', 'signup')),
    token_hash bytea       NOT NULL UNIQUE,
    user_id    uuid,
    email      citext,
    tenant_id  uuid        REFERENCES platform.tenants (id),
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (purpose <> 'reset_password' OR user_id IS NOT NULL),
    CHECK (purpose = 'reset_password' OR email IS NOT NULL),
    CHECK (purpose <> 'invite' OR tenant_id IS NOT NULL)
);
CREATE INDEX auth_tokens_expires ON platform.auth_tokens (expires_at);

CREATE TABLE platform.mfa_recovery_codes (
    id         uuid        NOT NULL DEFAULT uuidv7() PRIMARY KEY,
    user_id    uuid        NOT NULL REFERENCES platform.users (id),
    code_hash  bytea       NOT NULL,
    used_at    timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, code_hash)
);

CREATE TABLE platform.signup_requests (
    id             uuid        NOT NULL DEFAULT uuidv7() PRIMARY KEY,
    email          citext      NOT NULL,
    requested_slug citext      NOT NULL CHECK (requested_slug::text ~ '^[a-z0-9-]{3,40}$'),
    status         text        NOT NULL DEFAULT 'pending_verification'
                               CHECK (status IN ('pending_verification', 'queued', 'provisioning', 'completed', 'failed')),
    tenant_id      uuid        REFERENCES platform.tenants (id),
    error_code     text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    completed_at   timestamptz,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    row_version    integer     NOT NULL DEFAULT 1
);
CREATE INDEX signup_requests_email ON platform.signup_requests (email);

CREATE TABLE platform.user_identities (
    id         uuid        NOT NULL DEFAULT uuidv7() PRIMARY KEY,
    user_id    uuid        NOT NULL REFERENCES platform.users (id),
    provider   text        NOT NULL CHECK (provider ~ '^oidc:[a-z0-9-]+$'),
    subject    text        NOT NULL,
    tenant_id  uuid        REFERENCES platform.tenants (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, subject)
);
CREATE INDEX user_identities_user ON platform.user_identities (user_id);

CREATE TABLE platform.sessions (
    id               uuid        NOT NULL DEFAULT uuidv7() PRIMARY KEY,
    user_id          uuid        NOT NULL REFERENCES platform.users (id),
    active_tenant_id uuid        REFERENCES platform.tenants (id),
    token_hash       bytea       NOT NULL UNIQUE,
    mfa_verified     boolean     NOT NULL DEFAULT false,
    ip               inet,
    user_agent       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL,
    revoked_at       timestamptz
);
CREATE INDEX sessions_user ON platform.sessions (user_id);

CREATE TABLE platform.permissions (
    code         text        NOT NULL PRIMARY KEY CHECK (code ~ '^[a-z][a-z_]*(\.[a-z][a-z_]*)+$'),
    module       text        NOT NULL,
    description  text        NOT NULL,
    is_sensitive boolean     NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    row_version  integer     NOT NULL DEFAULT 1
);

-- ===========================================================================
-- Tenant tables
-- ===========================================================================
CREATE TABLE platform.tenant_keys (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    purpose     text        NOT NULL CHECK (purpose IN ('data', 'blind_index')),
    version     integer     NOT NULL CHECK (version > 0),
    wrapped_key bytea       NOT NULL,
    kek_id      text        NOT NULL,
    status      text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rotating', 'retired')),
    rotated_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, purpose, version)
);
CREATE UNIQUE INDEX tenant_keys_one_active ON platform.tenant_keys (tenant_id, purpose) WHERE status = 'active';

CREATE TABLE platform.memberships (
    tenant_id                uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id                       uuid        NOT NULL DEFAULT uuidv7(),
    user_id                  uuid        NOT NULL REFERENCES platform.users (id),
    status                   text        NOT NULL DEFAULT 'invited'
                                         CHECK (status IN ('invited', 'active', 'suspended', 'removed')),
    invited_by               uuid,
    invited_at               timestamptz,
    joined_at                timestamptz,
    notification_preferences jsonb       NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(notification_preferences) = 'object'),
    created_at               timestamptz NOT NULL DEFAULT now(),
    created_by               uuid                 DEFAULT platform.current_user_id(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    updated_by               uuid,
    row_version              integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, user_id)
);
CREATE INDEX memberships_user ON platform.memberships (user_id);

CREATE TABLE platform.roles (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    key         citext      NOT NULL CHECK (key::text ~ '^[a-z][a-z0-9_]{1,62}$'),
    name        text        NOT NULL,
    description text,
    is_system   boolean     NOT NULL DEFAULT false,
    archived_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, key)
);

CREATE TABLE platform.role_permissions (
    tenant_id       uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    role_id         uuid        NOT NULL,
    permission_code text        NOT NULL REFERENCES platform.permissions (code),
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      uuid                 DEFAULT platform.current_user_id(),
    PRIMARY KEY (tenant_id, role_id, permission_code),
    FOREIGN KEY (tenant_id, role_id) REFERENCES platform.roles (tenant_id, id)
);

CREATE TABLE platform.role_assignments (
    tenant_id     uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id            uuid        NOT NULL DEFAULT uuidv7(),
    membership_id uuid        NOT NULL,
    role_id       uuid        NOT NULL,
    scope_type    text        NOT NULL CHECK (scope_type IN (
                      'tenant', 'legal_entity', 'location', 'department', 'department_subtree',
                      'direct_reports', 'all_reports', 'self')),
    scope_id      uuid,
    valid_during  tstzrange,
    granted_by    uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    uuid                 DEFAULT platform.current_user_id(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    uuid,
    row_version   integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, membership_id) REFERENCES platform.memberships (tenant_id, id),
    FOREIGN KEY (tenant_id, role_id) REFERENCES platform.roles (tenant_id, id),
    -- Entity-typed scopes name their entity; relative scopes don't.
    CHECK ((scope_type IN ('legal_entity', 'location', 'department', 'department_subtree')) = (scope_id IS NOT NULL))
);
CREATE INDEX role_assignments_membership ON platform.role_assignments (tenant_id, membership_id);

CREATE TABLE platform.tenant_sso_configs (
    tenant_id         uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id                uuid        NOT NULL DEFAULT uuidv7(),
    protocol          text        NOT NULL DEFAULT 'oidc' CHECK (protocol IN ('oidc')),
    issuer            text        NOT NULL CHECK (issuer ~ '^https://'),
    client_id         text        NOT NULL,
    client_secret_enc bytea       NOT NULL,
    allowed_domains   citext[]    NOT NULL DEFAULT '{}',
    enforce_sso       boolean     NOT NULL DEFAULT false,
    jit_provisioning  boolean     NOT NULL DEFAULT false,
    default_role_id   uuid,
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by        uuid                 DEFAULT platform.current_user_id(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        uuid,
    row_version       integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, issuer),
    FOREIGN KEY (tenant_id, default_role_id) REFERENCES platform.roles (tenant_id, id)
);
CREATE INDEX tenant_sso_configs_domains ON platform.tenant_sso_configs USING gin (allowed_domains);

CREATE TABLE platform.api_keys (
    tenant_id    uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id           uuid        NOT NULL DEFAULT uuidv7(),
    name         text        NOT NULL,
    prefix       text        NOT NULL,
    key_hash     bytea       NOT NULL UNIQUE,
    scopes       text[]      NOT NULL DEFAULT '{}',
    expires_at   timestamptz,
    last_used_at timestamptz,
    revoked_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid                 DEFAULT platform.current_user_id(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid,
    row_version  integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE platform.files (
    tenant_id         uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id                uuid        NOT NULL DEFAULT uuidv7(),
    storage_key       text        NOT NULL,
    bucket            text        NOT NULL,
    original_name     text        NOT NULL,
    mime_type         text,
    size_bytes        bigint      CHECK (size_bytes >= 0),
    sha256            bytea       CHECK (sha256 IS NULL OR length(sha256) = 32),
    scan_status       text        NOT NULL DEFAULT 'pending' CHECK (scan_status IN ('pending', 'clean', 'infected', 'error')),
    scanned_at        timestamptz,
    classification    text        NOT NULL DEFAULT 'confidential'
                                  CHECK (classification IN ('public', 'internal', 'confidential', 'restricted')),
    owner_entity_type text,
    owner_entity_id   uuid,
    retention_until   timestamptz,
    purged_at         timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by        uuid                 DEFAULT platform.current_user_id(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        uuid,
    row_version       integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, storage_key)
);

CREATE TABLE platform.custom_field_definitions (
    tenant_id    uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id           uuid        NOT NULL DEFAULT uuidv7(),
    entity_type  text        NOT NULL CHECK (entity_type IN ('employee', 'candidate', 'job', 'application', 'requisition')),
    key          text        NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{0,62}$'),
    label        text        NOT NULL,
    field_type   text        NOT NULL CHECK (field_type IN (
                     'text', 'number', 'date', 'boolean', 'select', 'multiselect', 'user', 'file')),
    options      jsonb       NOT NULL DEFAULT '{}',
    required     boolean     NOT NULL DEFAULT false,
    is_sensitive boolean     NOT NULL DEFAULT false,
    position     integer     NOT NULL DEFAULT 0,
    archived_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid                 DEFAULT platform.current_user_id(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid,
    row_version  integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id)
);
CREATE UNIQUE INDEX custom_field_definitions_key ON platform.custom_field_definitions (tenant_id, entity_type, key)
    WHERE archived_at IS NULL;

CREATE TABLE platform.number_sequences (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    key         text        NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{0,62}$'),
    prefix      text        NOT NULL DEFAULT '',
    next_value  bigint      NOT NULL DEFAULT 1 CHECK (next_value > 0),
    padding     integer     NOT NULL DEFAULT 5 CHECK (padding BETWEEN 0 AND 12),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, key)
);

-- --- approvals --------------------------------------------------------------
CREATE TABLE platform.approval_policies (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    key         citext      NOT NULL,
    entity_type text        NOT NULL CHECK (entity_type IN (
                    'leave_request', 'regularization', 'requisition', 'offer', 'payroll_run', 'compensation', 'separation')),
    name        text        NOT NULL,
    priority    integer     NOT NULL DEFAULT 0,
    conditions  jsonb       NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(conditions) = 'object'),
    steps       jsonb       NOT NULL CHECK (jsonb_typeof(steps) = 'array' AND jsonb_array_length(steps) > 0),
    is_active   boolean     NOT NULL DEFAULT true,
    archived_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id)
);
CREATE UNIQUE INDEX approval_policies_key ON platform.approval_policies (tenant_id, key) WHERE archived_at IS NULL;

CREATE TABLE platform.approval_requests (
    tenant_id       uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id              uuid        NOT NULL DEFAULT uuidv7(),
    entity_type     text        NOT NULL CHECK (entity_type IN (
                        'leave_request', 'regularization', 'requisition', 'offer', 'payroll_run', 'compensation', 'separation')),
    entity_id       uuid        NOT NULL,
    policy_id       uuid        NOT NULL,
    policy_snapshot jsonb       NOT NULL,
    status          text        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')),
    current_step    integer     NOT NULL DEFAULT 1 CHECK (current_step > 0),
    requested_by    uuid,
    completed_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      uuid                 DEFAULT platform.current_user_id(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      uuid,
    row_version     integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, policy_id) REFERENCES platform.approval_policies (tenant_id, id)
);
CREATE UNIQUE INDEX approval_requests_one_pending ON platform.approval_requests (tenant_id, entity_type, entity_id)
    WHERE status = 'pending';

CREATE TABLE platform.approval_tasks (
    tenant_id              uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id                     uuid        NOT NULL DEFAULT uuidv7(),
    approval_request_id    uuid        NOT NULL,
    step_no                integer     NOT NULL CHECK (step_no > 0),
    assignee_user_id       uuid        NOT NULL,
    status                 text        NOT NULL DEFAULT 'pending' CHECK (status IN (
                               'pending', 'approved', 'rejected', 'skipped', 'delegated', 'escalated')),
    acted_at               timestamptz,
    comment                text,
    delegated_from_user_id uuid,
    created_at             timestamptz NOT NULL DEFAULT now(),
    created_by             uuid                 DEFAULT platform.current_user_id(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    updated_by             uuid,
    row_version            integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, approval_request_id) REFERENCES platform.approval_requests (tenant_id, id)
);
CREATE INDEX approval_tasks_inbox ON platform.approval_tasks (tenant_id, assignee_user_id, status);
CREATE INDEX approval_tasks_request ON platform.approval_tasks (tenant_id, approval_request_id);

CREATE TABLE platform.delegations (
    tenant_id    uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id           uuid        NOT NULL DEFAULT uuidv7(),
    from_user_id uuid        NOT NULL,
    to_user_id   uuid        NOT NULL,
    valid_during tstzrange   NOT NULL,
    entity_types text[]      NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid                 DEFAULT platform.current_user_id(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid,
    row_version  integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    CHECK (from_user_id <> to_user_id)
);
CREATE INDEX delegations_from ON platform.delegations (tenant_id, from_user_id);

-- --- notifications, email, outbox, webhooks ----------------------------------
CREATE TABLE platform.notifications (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    user_id     uuid        NOT NULL,
    type        text        NOT NULL,
    title       text        NOT NULL,
    body        text,
    link        text,
    entity_type text,
    entity_id   uuid,
    read_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX notifications_inbox ON platform.notifications (tenant_id, user_id, read_at);

CREATE TABLE platform.email_outbox (
    tenant_id    uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id           uuid        NOT NULL DEFAULT uuidv7(),
    to_address   citext      NOT NULL,
    template_key text        NOT NULL,
    locale       text        NOT NULL DEFAULT 'en-IN',
    payload      jsonb       NOT NULL DEFAULT '{}',
    status       text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'failed')),
    attempts     integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error   text,
    sent_at      timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid                 DEFAULT platform.current_user_id(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid,
    row_version  integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX email_outbox_queued ON platform.email_outbox (created_at) WHERE status = 'queued';

CREATE TABLE platform.outbox_events (
    tenant_id      uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id             uuid        NOT NULL DEFAULT uuidv7(),
    aggregate_type text        NOT NULL,
    aggregate_id   uuid        NOT NULL,
    event_type     text        NOT NULL CHECK (event_type ~ '^[a-z_]+(\.[a-z_]+)+$'),
    payload        jsonb       NOT NULL DEFAULT '{}',
    occurred_at    timestamptz NOT NULL DEFAULT now(),
    published_at   timestamptz,
    attempts       integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     uuid                 DEFAULT platform.current_user_id(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    updated_by     uuid,
    row_version    integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX outbox_events_unpublished ON platform.outbox_events (occurred_at) WHERE published_at IS NULL;

CREATE TABLE platform.webhook_endpoints (
    tenant_id   uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id          uuid        NOT NULL DEFAULT uuidv7(),
    url         text        NOT NULL CHECK (url ~ '^https?://'),
    secret_enc  bytea       NOT NULL,
    event_types text[]      NOT NULL DEFAULT '{}',
    is_active   boolean     NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid                 DEFAULT platform.current_user_id(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    row_version integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE platform.webhook_deliveries (
    tenant_id       uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id              uuid        NOT NULL DEFAULT uuidv7(),
    endpoint_id     uuid        NOT NULL,
    event_id        uuid        NOT NULL,
    status          text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'succeeded', 'failed', 'dead')),
    attempt         integer     NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    response_code   integer,
    next_attempt_at timestamptz,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      uuid                 DEFAULT platform.current_user_id(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      uuid,
    row_version     integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, endpoint_id) REFERENCES platform.webhook_endpoints (tenant_id, id),
    FOREIGN KEY (tenant_id, event_id) REFERENCES platform.outbox_events (tenant_id, id)
);
CREATE INDEX webhook_deliveries_due ON platform.webhook_deliveries (next_attempt_at) WHERE status = 'pending';

CREATE TABLE platform.idempotency_keys (
    tenant_id       uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    user_id         uuid        NOT NULL,
    key             text        NOT NULL CHECK (length(key) BETWEEN 1 AND 255),
    request_hash    bytea       NOT NULL,
    response_status integer,
    response_body   jsonb,
    expires_at      timestamptz NOT NULL DEFAULT now() + interval '24 hours',
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, key)
);
CREATE INDEX idempotency_keys_expires ON platform.idempotency_keys (expires_at);

-- --- imports ------------------------------------------------------------------
CREATE TABLE platform.imports (
    tenant_id     uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id            uuid        NOT NULL DEFAULT uuidv7(),
    import_type   text        NOT NULL CHECK (import_type ~ '^[a-z][a-z_]{1,62}$'),
    file_id       uuid        NOT NULL,
    status        text        NOT NULL DEFAULT 'pending' CHECK (status IN (
                      'pending', 'validating', 'validated', 'committing', 'committed', 'failed', 'cancelled')),
    stats         jsonb       NOT NULL DEFAULT '{}',
    error_file_id uuid,
    dry_run       boolean     NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    uuid                 DEFAULT platform.current_user_id(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    uuid,
    row_version   integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, file_id) REFERENCES platform.files (tenant_id, id),
    FOREIGN KEY (tenant_id, error_file_id) REFERENCES platform.files (tenant_id, id)
);

-- --- privacy (DPDP) -----------------------------------------------------------
CREATE TABLE platform.privacy_notices (
    tenant_id    uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id           uuid        NOT NULL DEFAULT uuidv7(),
    purpose      text        NOT NULL CHECK (purpose IN ('recruitment', 'employment', 'payroll')),
    version      integer     NOT NULL CHECK (version > 0),
    body_md      text        NOT NULL,
    published_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid                 DEFAULT platform.current_user_id(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid,
    row_version  integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, purpose, version)
);

CREATE TABLE platform.consents (
    tenant_id    uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id           uuid        NOT NULL DEFAULT uuidv7(),
    subject_type text        NOT NULL CHECK (subject_type IN ('candidate', 'employee')),
    subject_id   uuid        NOT NULL,
    purpose      text        NOT NULL CHECK (purpose IN ('recruitment', 'employment', 'payroll')),
    notice_id    uuid        NOT NULL,
    granted_at   timestamptz NOT NULL DEFAULT now(),
    withdrawn_at timestamptz,
    method       text        NOT NULL,
    evidence     jsonb       NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid                 DEFAULT platform.current_user_id(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid,
    row_version  integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, notice_id) REFERENCES platform.privacy_notices (tenant_id, id),
    CHECK (withdrawn_at IS NULL OR withdrawn_at >= granted_at)
);
CREATE INDEX consents_subject ON platform.consents (tenant_id, subject_type, subject_id);

CREATE TABLE platform.data_subject_requests (
    tenant_id    uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id           uuid        NOT NULL DEFAULT uuidv7(),
    subject_type text        NOT NULL CHECK (subject_type IN ('candidate', 'employee')),
    subject_id   uuid        NOT NULL,
    request_type text        NOT NULL CHECK (request_type IN ('access', 'correction', 'erasure', 'grievance', 'nomination')),
    status       text        NOT NULL DEFAULT 'received' CHECK (status IN ('received', 'in_progress', 'completed', 'rejected')),
    received_at  timestamptz NOT NULL DEFAULT now(),
    due_at       timestamptz NOT NULL,
    completed_at timestamptz,
    handled_by   uuid,
    notes        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid                 DEFAULT platform.current_user_id(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid,
    row_version  integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE platform.retention_policies (
    tenant_id     uuid        NOT NULL DEFAULT platform.current_tenant_id() REFERENCES platform.tenants (id),
    id            uuid        NOT NULL DEFAULT uuidv7(),
    entity_type   text        NOT NULL,
    trigger_event text        NOT NULL CHECK (trigger_event IN ('job_closed', 'application_rejected', 'employee_exit')),
    retain_days   integer     NOT NULL CHECK (retain_days >= 0),
    action        text        NOT NULL CHECK (action IN ('anonymise', 'delete')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    uuid                 DEFAULT platform.current_user_id(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    uuid,
    row_version   integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, entity_type)
);

-- ===========================================================================
-- audit.events: append-only, partitioned monthly by occurred_at. tenant_id is
-- NULL for tenant-less platform events (e.g. a failed login for an unknown
-- email), which only ops/owner can read.
-- ===========================================================================
CREATE TABLE audit.events (
    tenant_id     uuid        REFERENCES platform.tenants (id),
    id            uuid        NOT NULL DEFAULT uuidv7(),
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    actor_user_id uuid,
    actor_type    text        NOT NULL CHECK (actor_type IN ('user', 'api_key', 'candidate', 'system', 'worker')),
    action        text        NOT NULL CHECK (action ~ '^[a-z_]+(\.[a-z_]+)*$'),
    entity_schema text,
    entity_table  text,
    entity_id     uuid,
    changes       jsonb,
    request_id    text,
    ip            inet,
    user_agent    text,
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);
CREATE INDEX events_entity ON audit.events (tenant_id, entity_table, entity_id, occurred_at DESC);
CREATE INDEX events_actor ON audit.events (tenant_id, actor_user_id, occurred_at DESC);

SELECT platform.make_append_only('audit.events');
SELECT platform.ensure_monthly_partitions('audit.events', 3, 1);

-- ===========================================================================
-- Global tables: marker, grants, touch_row_global.
-- ===========================================================================
SELECT platform.register_global_table('platform.users', 'SELECT, INSERT, UPDATE');
SELECT platform.register_global_table('platform.user_identities', 'SELECT, INSERT, DELETE');
SELECT platform.register_global_table('platform.sessions', 'SELECT, INSERT, UPDATE, DELETE');
SELECT platform.register_global_table('platform.permissions', 'SELECT');
SELECT platform.register_global_table('platform.auth_tokens', 'SELECT, INSERT, UPDATE');
SELECT platform.register_global_table('platform.mfa_recovery_codes', 'SELECT, INSERT, UPDATE, DELETE');
SELECT platform.register_global_table('platform.platform_keys', 'SELECT');
SELECT platform.register_global_table('platform.signup_requests', 'SELECT, INSERT, UPDATE');

-- The tenancy root: RLS on its own id; the app may read and update its tenant,
-- never create or delete one (that is provisioning / purge, ops only).
COMMENT ON TABLE platform.tenants IS '@tenant_root';
SELECT platform.apply_rls('platform.tenants', 'id');
REVOKE ALL ON platform.tenants FROM PUBLIC;
GRANT SELECT, UPDATE ON platform.tenants TO pickwise_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.tenants TO pickwise_ops;
CREATE TRIGGER touch_row BEFORE UPDATE ON platform.tenants
    FOR EACH ROW EXECUTE FUNCTION platform.touch_row_global();

-- ===========================================================================
-- Pre-tenant SECURITY DEFINER functions (CLAUDE.md rule 4a). Owner-owned, so the
-- owner_all policy applies inside them; search_path pinned; EXECUTE to the app.
-- ===========================================================================

-- Login and the tenant switcher. Returns the tenant slug and name as well as ids:
-- the one recorded exception to "ids and flags only" (ADR 0002), because the
-- switcher must label tenants and tenant names are not personal data.
CREATE FUNCTION platform.list_memberships_for_user(target_user_id uuid)
    RETURNS TABLE (
        membership_id uuid, tenant_id uuid, tenant_slug text, tenant_name text,
        tenant_status text, membership_status text)
    LANGUAGE sql STABLE
    SECURITY DEFINER
    SET search_path = pg_catalog, platform
AS $$
    SELECT m.id, m.tenant_id, t.slug::text, t.name, t.status, m.status
    FROM platform.memberships m
    JOIN platform.tenants t ON t.id = m.tenant_id
    WHERE m.user_id = target_user_id
    ORDER BY t.name
$$;
GRANT EXECUTE ON FUNCTION platform.list_memberships_for_user(uuid) TO pickwise_app;

CREATE FUNCTION platform.resolve_invite(invite_token_hash bytea)
    RETURNS TABLE (
        token_id uuid, tenant_id uuid, user_id uuid, has_email boolean,
        is_expired boolean, is_used boolean)
    LANGUAGE sql STABLE
    SECURITY DEFINER
    SET search_path = pg_catalog, platform
AS $$
    SELECT a.id, a.tenant_id, a.user_id, a.email IS NOT NULL,
           a.expires_at <= now(), a.used_at IS NOT NULL
    FROM platform.auth_tokens a
    WHERE a.token_hash = invite_token_hash AND a.purpose = 'invite'
$$;
GRANT EXECUTE ON FUNCTION platform.resolve_invite(bytea) TO pickwise_app;

-- Tenant-less semantic events (e.g. a failed login for an unknown email).
-- Writes ids and the action only; callers pass no free text.
CREATE FUNCTION audit.log_platform_event(
    event_action text, event_actor_user_id uuid, event_request_id text, event_ip inet
) RETURNS uuid
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, platform, audit
AS $$
DECLARE
    event_id uuid;
BEGIN
    IF event_action !~ '^[a-z_]+(\.[a-z_]+)+$' THEN
        RAISE EXCEPTION 'invalid platform event action %', event_action;
    END IF;
    INSERT INTO audit.events (tenant_id, actor_user_id, actor_type, action, request_id, ip)
    VALUES (NULL, event_actor_user_id,
            CASE WHEN event_actor_user_id IS NULL THEN 'system' ELSE 'user' END,
            event_action, event_request_id, event_ip)
    RETURNING id INTO event_id;
    RETURN event_id;
END
$$;
GRANT EXECUTE ON FUNCTION audit.log_platform_event(text, uuid, text, inet) TO pickwise_app;
