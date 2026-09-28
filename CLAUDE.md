# Pickwise — project context for Claude Code

Pickwise is an open-source, production-grade, multi-tenant HRMS for Indian companies. It has four modules:

1. **Recruitment (ATS)**: requisitions, jobs, pipelines, interviews and offers. Its differentiator is resume screening with TypeSafe's **Jev** model, driven by recruiter-defined questions.
2. **Core HR**: org structure, employee master data, effective-dated job history, and onboarding/offboarding.
3. **Leave & Attendance**: policies, a leave ledger, holidays, shifts, punches, regularisation, and period summaries that feed payroll.
4. **India Payroll**: salary structures, a statutory engine (EPF/ESI/PT/LWF/TDS), payroll runs, payslips, filings, and full & final settlement.

This is **not an MVP**. Write every piece as if it ships to paying customers: migrations, tests, authorization, audit, observability and docs are part of "done".

Read these before any non-trivial change:
- `docs/DATA_MODEL.md`: the authoritative database design. Change the doc and the migration together.
- `docs/BUILD_PLAN.md`: the phase you're in and its definition of done.
- `docs/REVIEW_RESOLUTIONS.md`: resolved review findings and the owner's standing decisions (license, storage, versions, git workflow).
- `docs/decisions/`: ADRs. Add one whenever you make a design choice someone could reasonably question.

---

## Monorepo layout

```
pickwise/
├── apps/
│   ├── backend/                 # ONE Python project "pickwise" (uv); two entrypoints
│   │   ├── src/pickwise/
│   │   │   ├── api/             # FastAPI app factory, routers, middleware, deps
│   │   │   ├── worker/          # procrastinate app, task registry, periodic jobs
│   │   │   ├── cli/             # the `pickwise` command (db migrate/seed, demo seed, openapi, scan-check)
│   │   │   ├── platform/        # tenancy, auth, rbac, scopes, files, crypto, approvals, notifications, outbox, provisioning, erasure, privacy
│   │   │   ├── core/            # org + employees
│   │   │   ├── leave/
│   │   │   ├── attendance/
│   │   │   ├── payroll/
│   │   │   │   └── engine/      # PURE calculation engine: no I/O, no DB, no clock
│   │   │   ├── recruit/
│   │   │   ├── ai/              # the ONLY place that talks to Jev
│   │   │   └── shared/          # db session, context, errors, pagination, money, dates
│   │   ├── tests/               # unit / integration / e2e (pytest)
│   │   └── pyproject.toml
│   └── web/                     # Next.js (App Router) + TypeScript
├── packages/
│   ├── api-client/              # TS client generated from the backend OpenAPI schema
│   └── ui/                      # shared React components (shadcn/ui based)
├── db/
│   ├── bootstrap/               # roles + extensions (superuser, once per cluster)
│   ├── migrations/              # Alembic (env.py, versions/); revision 0000 = procrastinate `queue` schema
│   ├── pii_classification.yaml  # source of truth for @pii column comments
│   ├── seed/                    # permission catalog, statutory rule sets, demo tenants
│   └── tests/                   # SQL-level tests: RLS isolation, constraints, triggers
├── infra/
│   ├── docker/                  # Dockerfiles (backend, web, tools), postgres initdb, s3 bucket init
│   ├── compose/                 # compose.yml (base), compose.dev.yml, compose.test.yml, compose.prod.yml
│   └── helm/                    # added in the hardening phase
├── eval/                        # Jev screening eval harness + golden sets
├── docs/
├── .github/workflows/
├── Makefile
├── .env.example
└── CLAUDE.md
```

Each domain package (`platform`, `core`, `leave`, …) has the same internal shape:
`models.py` (SQLAlchemy), `schemas.py` (Pydantic I/O), `repository.py` (queries only), `service.py` (business rules and transactions), `router.py` (thin HTTP layer), `permissions.py`, `events.py`, `tasks.py`.

**Module boundaries are enforced with import-linter:**
- Allowed dependency direction: `shared` ← `platform` ← `core` ← `leave` ← `attendance` ← `payroll` ← `recruit`, with `ai` depending only on `platform`/`shared`, and `recruit` also using `ai`.
- A module may call the `service` of a module to its left. Information that flows the other way goes through outbox events. For example, leave's per-days-worked accrual consumes the payload of the `attendance.period_locked` event instead of importing attendance.
- One module never imports another module's `repository` or `models`.
- `payroll.engine` imports nothing from the app except `shared.money` and `shared.dates`.
- `api/`, `worker/` and `cli/` are composition roots: they wire every module together, so they sit outside the import-linter layers. Nothing imports them.

**Extension points (registries).** When a module on the left needs behaviour that a module further right provides, the left module defines a registry and the right module registers into it at startup. Never break the import direction to get at it. The registries are:

| Registry (defined in) | What it provides | Registered by |
|---|---|---|
| `platform.scopes.ScopeResolverRegistry` | resolves DataScopes (department subtree, direct/all reports) | core (Phase 5) |
| `platform.approvals.ApproverResolverRegistry` | resolves approvers (manager, skip-level, dept head) | core (Phase 5) |
| `platform.approvals.ApprovalHandlerRegistry` | on_approved / on_rejected callbacks per entity_type | each module that has approvals |
| `platform.provisioning.ProvisioningHookRegistry` | idempotent per-tenant defaults (roles, templates, policies) | every module. Hooks run in dependency order at tenant creation, and again via `pickwise tenant sync-defaults` so older tenants get defaults added by later modules |
| `platform.erasure.ErasureHandlerRegistry` | per-module purge/anonymise of a data subject | every module that holds personal data |
| `core.calendar.WorkCalendar` (a service, not a registry) | `is_working_day(employee, date)`: holidays + weekly offs + day overrides | used by leave, attendance, payroll |

Until the module that registers into a registry exists, tests use stub registrations.

---

## Stack

| Concern | Choice |
|---|---|
| Backend | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + asyncpg, Alembic. Move to 3.14 only once every dependency (spaCy/Presidio, WeasyPrint, asyncpg) ships wheels for it |
| Package mgmt | `uv` (Python), `pnpm` workspaces + Turborepo (JS) |
| Background jobs | `procrastinate` (Postgres-backed queue, periodic tasks). No Redis required |
| Database | PostgreSQL 18 (native `uuidv7()`) with `pgcrypto`, `citext`, `btree_gist`, `pg_trgm`, `ltree` |
| Object storage | Generic S3 API only (boto3/aioboto3). Dev runs **SeaweedFS**; production can use AWS S3, Cloudflare R2, Ceph or SeaweedFS. **MinIO is not used**: its community images were removed from Docker Hub in Sep 2026. **Don't depend on bucket event notifications.** The client calls an "upload complete" API, and that call enqueues the scan job. Every upload is virus-scanned with ClamAV before use |
| Email | SMTP (Mailpit in dev) with templated emails |
| Frontend | Next.js 16 App Router, TypeScript strict, Tailwind, shadcn/ui, TanStack Query/Table, react-hook-form + zod |
| API contract | OpenAPI generated by FastAPI → `packages/api-client` (openapi-typescript + openapi-fetch). The frontend never hand-writes request types |
| Auth | Local email+password (argon2id), OIDC SSO (Google / Microsoft Entra / generic), TOTP MFA, server-side sessions in httpOnly cookies, scoped API keys |
| Crypto | Envelope encryption (AES-256-GCM): per-tenant keys in `platform.tenant_keys`, wrapped by one master key (`PICKWISE_KEK` env var in dev, cloud KMS in prod). Each tenant has a **data key** and a separate **blind-index key**, and blind indexes are HMAC-SHA256 under the tenant's blind-index key. The KEK is the only global crypto secret. There is no global blind-index key |
| Resume parsing | pdfplumber / pypdf, python-docx, Microsoft Presidio + Indian regexes (PAN, Aadhaar, phone, PIN) |
| Observability | structlog JSON logs, OpenTelemetry traces + metrics, Sentry-compatible error reporting (optional) |
| Quality | ruff, mypy --strict, pytest (+ hypothesis), import-linter, eslint, tsc, Playwright (e2e) |

---

## Non-negotiable engineering rules

### Multi-tenancy (security-critical)
1. **Shared schema, `tenant_id` on every tenant-owned row, enforced by Postgres Row-Level Security.** Every tenant table has `ENABLE` + `FORCE ROW LEVEL SECURITY` and exactly three permissive policies:
   - `tenant_isolation` (all roles): `USING (tenant_id = platform.current_tenant_id()) WITH CHECK (same)`
   - `ops_all TO pickwise_ops USING (true) WITH CHECK (true)`
   - `owner_all TO pickwise_owner USING (true) WITH CHECK (true)`, for migrations and backfills

   **No role has BYPASSRLS.** Cross-tenant access comes only from the role-targeted policies, so the design works on managed Postgres (RDS, Cloud SQL, …) where BYPASSRLS may not be grantable.
   - `platform.current_tenant_id()` / `current_user_id()` are `NULLIF(current_setting('app.tenant_id', true), '')::uuid`, because a pooled connection reads `''` (not NULL) after a transaction-local setting ends. Unset context means zero rows, never a cast error.
   - Partitioned tables: every child partition gets the same ENABLE/FORCE and the same three policies, and `pickwise_app` has **no grants on any child**. The app reads and writes through the parent only.
2. **Primary keys are `(tenant_id, id)`** (id = UUIDv7). **Foreign keys are composite, `(tenant_id, x_id)`,** so a row can never reference another tenant's row, even through a bug.
3. **Every DB transaction sets its context first**, via `set_config('app.tenant_id', …, true)` plus `app.user_id`, `app.actor_type` (`user` / `api_key` / `candidate` / `system` / `worker`), `app.request_id` and `app.client_ip`. Always transaction-local, never session-level: it has to work behind PgBouncer. A single `tenant_session()` context manager in `shared/db.py` does this. Nothing else opens sessions except `ops_session()` (rule 4).
4. **Database roles.** Group roles are NOLOGIN; login users are separate.

   | Group role (NOLOGIN) | Attributes | Purpose |
   |---|---|---|
   | `pickwise_owner` | NOBYPASSRLS | owns all objects; used only by migrations. Sees all rows via the `owner_all` policy |
   | `pickwise_app` | NOBYPASSRLS | all request and job traffic |
   | `pickwise_ops` | NOBYPASSRLS | cross-tenant platform work: provisioning, outbox relay, partitions, usage rollups, erasure, tenant purge. Sees all rows via the `ops_all` policy |

   | Login user | Member of | Notes |
   |---|---|---|
   | `pickwise_migrator` | `pickwise_owner` | `ALTER ROLE … SET role = pickwise_owner`, so objects are owner-owned and `owner_all` applies |
   | `pickwise_api` | `pickwise_app` | the API service |
   | `pickwise_worker` | `pickwise_app`, and `pickwise_ops` WITH INHERIT FALSE, SET TRUE | the worker service |
   | `pickwise_maint` | `pickwise_ops` WITH INHERIT FALSE, SET TRUE | CLI / maintenance only |

   **Policies `TO pickwise_ops` apply only when the current role has that role's privileges, and INHERIT FALSE withholds them.** So `ops_session()` must run `SET LOCAL ROLE pickwise_ops` inside its transaction. That is the only way ops code sees across tenants. `ops_session()` is callable only from `@ops_task` worker tasks or `@ops_command` CLI entry points, and an AST test enforces this. A test proves `pickwise_worker` and `pickwise_maint` see zero rows without context until they `SET LOCAL ROLE`, and that no role has `rolbypassrls`.
   - The `migrate` container verifies every login user can connect with the credentials in `.env`, and fails with "role passwords don't match this database volume: run `make reset`" if not.
4a. **SECURITY DEFINER functions** (owned by `pickwise_owner`, EXECUTE granted to `pickwise_app`, `search_path` pinned) are the only way the app reads across tenants. They run as the owner, so `owner_all` applies. They are narrow, return ids and flags only, and are audited:
   - `platform.list_memberships_for_user(user_id)`: login and the tenant switcher, before any tenant context exists. It also returns the tenant slug and name, the one exception to "ids and flags only" (the switcher must label tenants)
   - `platform.resolve_invite(token_hash)`
   - `platform.resolve_api_key(key_hash)`, `platform.resolve_device_key(key_hash)`: authenticate integrations and biometric devices before the tenant is known
   - `platform.resolve_tenant_by_slug(slug)`: the public careers page
   - `platform.resolve_sso_by_domain(domain)`: SSO discovery at login
   - `audit.log_platform_event(...)`: events with no tenant, e.g. a failed login for an unknown email
   - `platform.ensure_monthly_partitions(parent, …)`: EXECUTE to `pickwise_ops` only, not the app. Creating a partition requires owning the parent, and it reads no tenant data (ADR 0003)
   Adding a new one requires an ADR. There is no global lookup table.
4b. **Ops procedures are SECURITY INVOKER.** `platform.purge_tenant(tenant_id)` and `audit.scrub_subject(entity_table, entity_id)` have EXECUTE granted to `pickwise_ops` only and are called after `SET LOCAL ROLE pickwise_ops`, so `current_user` is `pickwise_ops` inside them.
   - Migrations never hand-write RLS or grants. They call the SQL helpers from revision 0001: `platform.apply_tenant_policies(schema)`, `platform.make_append_only(table)`, `platform.register_global_table(table, privileges)` and `audit.attach(table)`, via `db/migrations/helpers.py`. Table comments carry the markers `@global` / `@append_only` that these helpers and the catalog tests read.
5. **An RLS test exists for every tenant table.** CI generates the list from the catalog, so a new table without its policies fails the build. It also requires the policies on every child partition, and no `pickwise_app` grants on any child.

### Data correctness
6. **Effective-dated facts** (job records, compensation, policy assignments, shift assignments, work schedules, statutory profiles, bank accounts) use a `valid_during daterange` column with a `btree_gist` `EXCLUDE` constraint, so periods can't overlap. Never overwrite history. Close the current period and open a new one.
7. **Ledgers are append-only:** leave ledger, stage transitions, punches, audit. Corrections are reversing entries. A trigger blocks UPDATE/DELETE, with one exception: when `current_user = 'pickwise_ops'` **and** `app.purge_mode = 'on'`, it allows DELETE on any append-only table and UPDATE on `audit.events` only (for `audit.scrub_subject`). Only the erasure and tenant-purge procedures set purge mode. Tenant FKs are **not** `ON DELETE CASCADE`.
   - `platform.purge_tenant(tenant_id)` (ops) first nulls nullable back-references, then deletes in topological FK order generated from `pg_constraint`.
   - The FK graph stays acyclic: a CI test fails on any FK cycle not on its allow-list (initially self-references only, such as `departments.parent_id`). Links point one way only, e.g. `core.employees.membership_id` (not `memberships.employee_id`) and `recruit.applications.hired_employee_id` (not `employees.source_application_id`).
8. **Finalized payroll is immutable.** Fix mistakes with an off-cycle or arrears run, never by editing a finalized run.
9. **Money** is `numeric(14,2)` in the DB and `Decimal` in Python, never float. Rounding rules are explicit per component.
10. **Optimistic concurrency:** mutable aggregates carry `row_version`. Updates include it, and a stale version returns 409.
11. **Idempotency:** every tenant-scoped POST that creates something accepts an `Idempotency-Key` header, stored per `(tenant_id, user_id, key)`. Pre-tenant endpoints (signup, login, password reset) don't take one; rate limits and single-use tokens protect them instead. Every background job is idempotent, using natural keys such as `accrual:{employee}:{leave_type}:{yyyy-mm}`.
12. **Every schema change goes through an Alembic migration** (hand-reviewed; autogenerate is only a starting point). Migrations have to be safe to run on a live database: no long locks, `CREATE INDEX CONCURRENTLY` in a separate migration, and data backfills written as batched jobs.

### Privacy & security
13. **PII is classified** on one four-level scale everywhere: public / internal / confidential / restricted.
    - **`db/pii_classification.yaml` is the source of truth.** Migrations generate the `@pii` column comments from it (`COMMENT ON COLUMN … IS '@pii confidential'` or `'@pii restricted'`), audit redaction and the erasure checks read those comments, a test diffs the YAML against the database, and `make docs-pii` regenerates the classification in `docs/DATA_MODEL.md`. Never hand-edit the markers in the doc.
    - **Restricted fields** (PAN, Aadhaar, bank account, passport, UAN, ESIC IP number) are encrypted at the application level. The DB stores `*_enc bytea`, `*_last4` and `*_bidx` (blind index).
    - **Aadhaar:** store only the last 4 digits by default (`aadhaar_mode = 'last4'`). In that mode **no** `_enc` and **no** `_bidx` are stored: a hash of the full number is still derived data. Aadhaar uniqueness is only enforced when the tenant opts into `aadhaar_mode = 'full'`, which carries a compliance warning.
    - **Blind-index key rotation:** a rotation adds a new tenant blind-index key in `rotating` status. During a rotation, lookups and duplicate checks compute the bidx under **every non-retired key** and query `IN (…)`; the app enforces uniqueness while the rotation runs. A batched job recomputes every `*_bidx` under the new key, then the old key is retired. Data keys rotate by re-wrapping, with lazy re-encryption.
14. **Never log PII, resume text, salary figures or tokens.** Log ids only. Sentry/OTel scrubbers enforce this. The same goes for background-job arguments: procrastinate jobs carry only ids and `tenant_id`, because the queue tables are global. A test inspects the signatures of all registered tasks.
15. **Audit trail:**
    - A generic trigger records every change to audited tables: actor, request id, and a per-column diff.
    - **Both 🟠 confidential and 🔒 restricted columns are redacted.** The diff records only `"[changed]"` for them, never the values. The redaction list comes from the `@pii` column comments at migration time.
    - Erasure also calls the ops-only `audit.scrub_subject(entity_table, entity_id)` to remove residual personal data, such as free-text notes, from audit rows.
    - Semantic events are written explicitly by the app: `pii.reveal`, `export`, `login`, `role.grant`, `payroll.finalize`.
16. **Authorization** is permission-based, with scopes: tenant / legal entity / location / department subtree / direct reports / all reports / self.
    - Every router declares its permission.
    - Every list query is filtered by the caller's data scope in the repository layer.
    - Tests assert both 403s and scope filtering.
17. **Uploads go** to object storage via presigned URLs. Files are quarantined until the ClamAV scan passes, their mime type is sniffed server-side, and size limits apply.
18. **DPDP Act 2023 readiness:**
    - consent records with notice versions
    - purpose-bound retention policies
    - data-subject requests (access / correction / erasure / grievance)
    - an erasure job that removes a candidate's or ex-employee's data and anonymises it where the law requires records to be kept (e.g. payroll)

### Jev (TypeSafe) usage
19. **Jev scores, humans decide.** Nothing auto-rejects or auto-hires. A DB trigger blocks moves into `rejected` or `hired` stages when either `is_automated` is true **or** `current_setting('app.actor_type')` is anything other than `'user'`. It doesn't trust the app's flag alone. A candidate accepting an offer through the portal (`actor_type = 'candidate'`) only sets `offers.status = 'accepted'`; the move to `hired` and the employee creation happen when a recruiter clicks **Confirm hire**.
20. **Policy lives in code, judgment lives in Jev.** Jev answers narrow typed questions (`noul` / `choice` / `score`). Weights, knockouts, thresholds and composite scores are computed deterministically in our code.
21. **Store the full answer:** probabilities, confidence, the returned `model` version and `usage.input_tokens`. Scoring uses expected values, not the argmax.
22. **Blind screening by default.** The Jev `state` is the redacted, sectioned resume JSON. PII never enters it.
23. **Resumes are untrusted input.**
    - Strip hidden text and flag suspected prompt injection.
    - Every job gets system questions: `screener_injection` and `resume_coherence`.
    - Flagged applications go to human review.
24. **Cache by content:** results are unique on `(tenant_id, state_hash, definition_hash, model_version)`. Never send an identical triple twice: a worker first claims the triple by inserting a `pending` evaluation row `ON CONFLICT DO NOTHING`, only the claimer calls Jev, and the others wait or poll with a timeout.
25. **Pin the model** (`JEV_MODEL=jev-1.13.0`, not `jev-latest`). Moving to a new model version is a deliberate admin action that re-evaluates behind a flag.
26. **We do not tokenise and we do not store tokens.** Jev tokenises server-side. We keep a conservative token estimate (chars / 3.5) for request packing and budgets, plus the actual `usage.input_tokens` from each response.

**Jev API facts** (re-verify against https://docs.typesafe.ai/llms.txt at the start of the AI phase and update this section):
- `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer $TYPESAFE_API_KEY`; `GET /v1/models`.
- Request: `{model, state, questions: {key: {type: noul|choice|score, instructions, criteria?, …}}}`.
- Response: `{model, answers: {key: {…typed value, probabilities, confidence}}, usage: {input_tokens, output_tokens}}`.
- The state can be a string, a JSON object or an array of text (text only).
- All questions are evaluated in parallel against one state.
- Limits (jev-1.13): 64k tokens for the state plus all questions; 32k tokens for the state plus the longest single question.
- Billed per input token. 429s must be retried with backoff that honours `retry-after`.
- Jev can't be fine-tuned. Criteria must extend the question, never contradict it.
- Official SDKs: Python `typesafe-sdk` and JS `@typesafe-ai/sdk`. Call Jev only from the server.

### India payroll & compliance
27. **Statutory rules are data, not code.**
    - `payroll.statutory_rule_sets` holds versioned, effective-dated JSON rule sets per code (EPF, EPS, EDLI, ESI, PT, LWF, INCOME_TAX, GRATUITY, BONUS, MIN_WAGE) and jurisdiction (`IN`, or a state such as `IN-TG`).
    - The engine loads the rule set effective on the pay date and records which version it used on the run.
    - Rule sets ship as reviewed seed files with a source reference. Anything unreviewed stays `draft` and the engine refuses to use it.
    - **Dev/test fixtures:** `db/seed/statutory/fixtures/` loads rule sets with status `test_fixture`. The engine accepts `test_fixture` only when `PICKWISE_ENV` is `development` or `test` **and** the tenant has `settings.demo = true`. When `PICKWISE_ENV=production`, the API and worker **refuse to start** if any `test_fixture` rule set exists in the database.
28. **Current legal context to design for** (verify before encoding numbers):
    - The four Labour Codes have been in force since **21 Nov 2025**, including the Code on Wages "wages" definition, where allowances beyond 50% of remuneration are added back into wages. Many state rules are still pending, so the wage definition is configurable per component.
    - The **Income-tax Act, 2025** applies from **1 Apr 2026**. It uses "tax year", salary TDS moves to s.392, and the salary certificate Form 16 becomes Form 130. Section and form codes live in rule data, never in code.
    - **DPDP Rules 2025** were notified on 13 Nov 2025, and substantive obligations apply from **13 May 2027**.
29. **Calculation traceability:** every payroll line stores a `trace` JSON (formula, inputs, rule-set version). The payslip and the "why is my TDS this much" screen are built from it.
30. **Golden tests:** the payroll engine has fixture-based golden tests (input employee-month → expected lines) reviewed by a payroll professional before release. Any change to a golden file needs a written reason in the PR.

---

## Local development (Docker-first)

**Host prerequisites are Docker (Compose v2), make and git. Nothing else.** Anything else a make target needs runs inside the `tools` image (`infra/docker/tools.Dockerfile`: Python, uv, Node, pnpm, openssl, pre-commit, aws-cli). That includes secret generation, pre-commit, codegen and S3 inspection.

- `make dev` is the only command a contributor needs. It:
  - copies `.env.example` to `.env` if missing, and generates dev secrets (`PICKWISE_KEK`, `SESSION_SECRET`) **inside the tools container**
  - builds the images and starts, in order:
    1. `postgres:18`
    2. `migrate`, a one-shot container that verifies the bootstrap roles and that every login user can connect, runs `alembic upgrade head`, then seeds the permission catalog and statutory rule sets (published ones, plus `test_fixture` ones in dev). Each seed step does nothing until its tables exist
    3. `seed-demo`, a one-shot container defined in `compose.dev.yml` and `compose.test.yml` (never in `compose.prod.yml`) that creates the demo tenants
    4. `s3` (SeaweedFS) and `s3-init` (buckets)
    5. `mailpit`
    6. `api` (hot reload), `worker` (hot reload), `web` (Next dev)
  - waits on healthchecks and prints the URLs:
    - web http://localhost:3000
    - API docs http://localhost:8000/docs
    - Mailpit http://localhost:8025
- **ClamAV lives behind a compose profile**, because it needs about 1.5–3 GB of RAM and takes minutes on first start while it downloads signatures.
  - Plain `make dev` uses `SCANNER=stub`. The stub is loudly labelled in logs and the UI, marks the EICAR test string as infected, and marks everything else clean.
  - `make dev-full` and CI run the real `clamd` (`SCANNER=clamav`), with the signature database kept in a named volume.
  - `make scan-check` streams the EICAR string through our scanner adapter to the real clamd and asserts it comes back infected.
  - When `PICKWISE_ENV=production`, the app refuses to start with `SCANNER=stub`.
- **The worker connects to Postgres directly, never through PgBouncer transaction pooling.** Procrastinate needs LISTEN/NOTIFY and session-level advisory locks. The API may go through PgBouncer in transaction mode.
- Other make targets:
  - `make dev-full`, `make down`
  - `make reset` (drops volumes)
  - `make logs s=api`, `make shell s=api`, `make psql`, `make s3-ls`
  - `make migrate`, `make migration m="msg"`, `make seed`, `make seed-demo`
  - `make test`, `make test-db`, `make lint`, `make typecheck`, `make e2e`
  - `make hooks` (installs git hooks that run pre-commit inside the tools container)
  - `make openapi` (regenerates the TS client)
  - `make docs-pii` (regenerates the PII classification in `docs/DATA_MODEL.md` from `db/pii_classification.yaml`)
  - `make eval` / `make eval-live`
- Tests run inside containers against a disposable Postgres (`compose.test.yml`), so they behave the same locally and in CI.
- Demo seed (**dev and test only; `compose.prod.yml` never seeds demo data**): two tenants (`acme`, `globex`, both with `settings.demo = true`) with legal entities, locations, departments, ~50 employees, leave policies, shifts, a salary structure, an open job with questions, and applications. The seeder prints the logins. The e2e tests use it too.
- **CI never calls the live Jev API.** It uses `FakeJevClient` and recorded fixtures. `make eval-live` is opt-in.

## Working agreements for Claude Code
- Work one phase of `docs/BUILD_PLAN.md` at a time. Start in plan mode, list the files you'll touch, then implement.
- A phase isn't done until `make lint typecheck test` passes in Docker and its "Done when" checks are shown passing.
- Update `docs/DATA_MODEL.md`, the OpenAPI examples and the relevant ADR in the same change as the code.
- Ask before adding a new runtime dependency or service to docker compose.
- Git workflow: one branch per phase (`phase-NN-short-name`) with small commits prefixed with the phase (`[P03] …`). Open a PR to `main` with `gh` when the phase is done; the owner reviews and squash-merges. Never push to `main` directly.
- Pin exact image tags and digests in compose files. Never use `:latest`.
- License: AGPL-3.0-only. Every source file carries an `SPDX-License-Identifier: AGPL-3.0-only` header, and a pre-commit check enforces it. Contributions require the CLA (individual or corporate), which CLA Assistant enforces on PRs.
- If this file and the code disagree, stop and flag it. Don't silently pick one.
