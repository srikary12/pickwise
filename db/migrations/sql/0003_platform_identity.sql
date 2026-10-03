-- SPDX-License-Identifier: AGPL-3.0-only
-- Migration 0003: identity and access (Phase 2). ADRs 0004, 0010-0012.

-- Replay protection for TOTP: the last accepted 30-second step.
ALTER TABLE platform.users ADD COLUMN mfa_last_used_step bigint;

-- A session opened through a tenant's identity provider only proves the user to
-- that tenant, so it can't switch into their other tenants (ADR 0010).
ALTER TABLE platform.sessions ADD COLUMN sso_tenant_id uuid REFERENCES platform.tenants (id);

-- Self-serve signup keeps the organisation name the requester typed (ADR 0007).
ALTER TABLE platform.signup_requests ADD COLUMN requested_name text
    CHECK (requested_name IS NULL OR length(requested_name) BETWEEN 1 AND 200);

-- Secret template variables (link tokens) for tenant email, encrypted with the
-- tenant data key and nulled once sent (ADR 0012).
ALTER TABLE platform.email_outbox ADD COLUMN payload_enc bytea;

-- Pre-tenant email (verification, password reset, signup): no tenant exists yet.
CREATE TABLE platform.platform_email_outbox (
    id           uuid        NOT NULL DEFAULT uuidv7() PRIMARY KEY,
    to_address   citext      NOT NULL,
    template_key text        NOT NULL,
    locale       text        NOT NULL DEFAULT 'en-IN',
    payload      jsonb       NOT NULL DEFAULT '{}',
    payload_enc  bytea,
    status       text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'failed')),
    attempts     integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error   text,
    sent_at      timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    row_version  integer     NOT NULL DEFAULT 1
);
CREATE INDEX platform_email_outbox_queued ON platform.platform_email_outbox (created_at)
    WHERE status = 'queued';
SELECT platform.register_global_table('platform.platform_email_outbox', 'SELECT, INSERT');

-- Token buckets for rate limiting (ADR 0011). key_hash = HMAC(SESSION_SECRET, "<rule>:<subject>"),
-- so no raw IP address or email is stored.
CREATE TABLE platform.rate_limit_buckets (
    key_hash    bytea            NOT NULL PRIMARY KEY,
    tokens      double precision NOT NULL,
    refilled_at timestamptz      NOT NULL DEFAULT now()
);
CREATE INDEX rate_limit_buckets_refilled ON platform.rate_limit_buckets (refilled_at);
SELECT platform.register_global_table('platform.rate_limit_buckets', 'SELECT, INSERT, UPDATE, DELETE');

-- ---------------------------------------------------------------------------
-- Pre-tenant SECURITY DEFINER lookups (CLAUDE.md rule 4a, ADR 0004).
-- Owner-owned, search_path pinned, EXECUTE to the app; ids and flags only.
-- ---------------------------------------------------------------------------
CREATE FUNCTION platform.resolve_api_key(api_key_hash bytea)
    RETURNS TABLE (tenant_id uuid, api_key_id uuid, scopes text[], is_usable boolean)
    LANGUAGE sql STABLE
    SECURITY DEFINER
    SET search_path = pg_catalog, platform
AS $$
    SELECT k.tenant_id, k.id, k.scopes,
           k.revoked_at IS NULL
           AND (k.expires_at IS NULL OR k.expires_at > now())
           AND t.status = 'active'
    FROM platform.api_keys k
    JOIN platform.tenants t ON t.id = k.tenant_id
    WHERE k.key_hash = api_key_hash
$$;
GRANT EXECUTE ON FUNCTION platform.resolve_api_key(bytea) TO pickwise_app;

CREATE FUNCTION platform.resolve_tenant_by_slug(tenant_slug citext)
    RETURNS TABLE (tenant_id uuid, status text, careers_enabled boolean)
    LANGUAGE sql STABLE
    SECURITY DEFINER
    SET search_path = pg_catalog, platform
AS $$
    SELECT t.id, t.status, coalesce((t.settings ->> 'careers_enabled')::boolean, false)
    FROM platform.tenants t
    WHERE t.slug = tenant_slug
$$;
GRANT EXECUTE ON FUNCTION platform.resolve_tenant_by_slug(citext) TO pickwise_app;

-- Several tenants may list the same domain; the caller decides (a tenant's
-- enforce_sso only binds users who are members of that tenant, ADR 0010).
CREATE FUNCTION platform.resolve_sso_by_domain(email_domain citext)
    RETURNS TABLE (tenant_id uuid, sso_config_id uuid, enforce_sso boolean)
    LANGUAGE sql STABLE
    SECURITY DEFINER
    SET search_path = pg_catalog, platform
AS $$
    SELECT c.tenant_id, c.id, c.enforce_sso
    FROM platform.tenant_sso_configs c
    JOIN platform.tenants t ON t.id = c.tenant_id
    WHERE email_domain = ANY (c.allowed_domains) AND t.status = 'active'
    ORDER BY c.tenant_id, c.id
$$;
GRANT EXECUTE ON FUNCTION platform.resolve_sso_by_domain(citext) TO pickwise_app;
