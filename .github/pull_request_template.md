## What and why

<!-- What does this change do, and why is it needed? Link the issue or BUILD_PLAN phase. -->

## Checklist

- [ ] `make lint typecheck test` passes locally (in Docker)
- [ ] Tests cover the change, including 403s and data-scope filtering for new endpoints
- [ ] Migrations are hand-reviewed and safe on a live database (no long locks; `CREATE INDEX CONCURRENTLY` in its own migration)
- [ ] New tenant tables have RLS policies, and personal-data columns are classified
- [ ] No PII, salary figures or tokens in logs or job arguments
- [ ] Docs, OpenAPI examples and an ADR are updated where a design choice was made
- [ ] Payroll golden files are unchanged, or the reason for the change is written below
