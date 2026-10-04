-- SPDX-License-Identifier: AGPL-3.0-only
-- Migration 0004: upload bookkeeping for the file pipeline (Phase 3a, ADR 0013).

-- What the client declared when it asked for an upload slot. The worker compares
-- these with what it sniffs and measures once the object has arrived.
ALTER TABLE platform.files ADD COLUMN declared_mime_type text;
ALTER TABLE platform.files ADD COLUMN declared_size_bytes bigint CHECK (declared_size_bytes > 0);
-- The upload slot expires if the client never calls /complete.
ALTER TABLE platform.files ADD COLUMN upload_expires_at timestamptz;
-- When the client reported the upload as finished (the scan is queued from here).
ALTER TABLE platform.files ADD COLUMN uploaded_at timestamptz;
-- Why a file is infected or rejected (signature name, mime mismatch, size, …); never content.
ALTER TABLE platform.files ADD COLUMN scan_detail text CHECK (length(scan_detail) <= 500);

-- Unfinished uploads and files due for purge are found by the periodic jobs.
CREATE INDEX files_pending_expiry ON platform.files (upload_expires_at)
    WHERE scan_status = 'pending' AND uploaded_at IS NULL;
CREATE INDEX files_retention ON platform.files (retention_until)
    WHERE retention_until IS NOT NULL AND purged_at IS NULL;
