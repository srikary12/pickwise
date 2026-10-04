-- SPDX-License-Identifier: AGPL-3.0-only
-- Migration 0005: approval deadlines (Phase 3b, ADR 0016) and the erasure procedure's
-- purge-mode switch (ADR 0018).

-- When a task is overdue. The step's escalate_after_hours decides it; a task with no
-- deadline (a fallback approver's task, or a step without escalation) never escalates.
ALTER TABLE platform.approval_tasks ADD COLUMN due_at timestamptz;
-- When the half-way reminder went out, so it is sent once.
ALTER TABLE platform.approval_tasks ADD COLUMN reminded_at timestamptz;

-- What the policy conditions were evaluated against and what the approver resolvers need
-- at later steps (department id, requester id, amounts). Set once when the request starts.
-- Classified confidential because a module may put an amount in it: audited as "[changed]".
ALTER TABLE platform.approval_requests ADD COLUMN attributes jsonb NOT NULL DEFAULT '{}'
    CHECK (jsonb_typeof(attributes) = 'object');

CREATE INDEX approval_tasks_due ON platform.approval_tasks (due_at)
    WHERE status = 'pending' AND due_at IS NOT NULL;

-- Erasure deletes from append-only tables and scrubs audit rows, which is allowed only
-- for pickwise_ops in purge mode. These two functions are how erasure enters and leaves
-- that mode inside its transaction (the tenant purge does the same inline). SECURITY
-- INVOKER and ops-only, like purge_tenant.
CREATE FUNCTION platform.begin_erasure() RETURNS void
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
BEGIN
    IF current_user <> 'pickwise_ops' THEN
        RAISE EXCEPTION 'begin_erasure must run as pickwise_ops (SET LOCAL ROLE pickwise_ops)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    PERFORM set_config('app.purge_mode', 'on', true);
END
$$;

CREATE FUNCTION platform.end_erasure() RETURNS void
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
BEGIN
    IF current_user <> 'pickwise_ops' THEN
        RAISE EXCEPTION 'end_erasure must run as pickwise_ops (SET LOCAL ROLE pickwise_ops)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    PERFORM set_config('app.purge_mode', 'off', true);
END
$$;

GRANT EXECUTE ON FUNCTION platform.begin_erasure(), platform.end_erasure() TO pickwise_ops;
