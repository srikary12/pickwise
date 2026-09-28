# Pickwise

Pickwise is an open-source, multi-tenant HRMS for Indian companies. It has four modules:

- **Recruitment:** an ATS with resume screening that uses TypeSafe's Jev model to answer questions recruiters define. Jev scores; humans decide.
- **Core HR:** org structure, employee records and effective-dated job history.
- **Leave & Attendance:** policies, a leave ledger, shifts, punches and period summaries.
- **India Payroll:** statutory rules (EPF, ESI, PT, LWF, TDS) kept as versioned data, payroll runs, payslips and filings.

> **Status:** early development (Phase 0: the repository skeleton and dev environment). It isn't ready for production use.

## Quick start

You need **Docker** (with Compose v2), **make** and **git**. Nothing else: every other tool runs in containers.

```sh
git clone https://github.com/srikary12/pickwise.git
cd pickwise
make dev
```

`make dev` does four things:
1. Creates `.env` and generates local secrets.
2. Builds the images.
3. Starts the stack.
4. Waits until every service is healthy.

It then prints:

| What | URL |
|---|---|
| Web app | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| Mailpit (dev email) | http://localhost:8025 |

The first run downloads images and dependencies, so it takes a few minutes. Later runs are much faster.

Plain `make dev` uses a **stub virus scanner**. It's labelled loudly in the logs and the UI, flags only the EICAR test file, and isn't allowed in production. To run real ClamAV (it needs about 3 GB of RAM and a few minutes to download signatures on the first start):

```sh
make dev-full     # the stack with clamd
make scan-check   # streams EICAR through the scanner and requires "infected"
```

## Make targets

| Target | What it does |
|---|---|
| `make dev` / `make dev-full` | Start the stack (stub scanner / real ClamAV) |
| `make down` / `make reset` | Stop the stack / stop it and delete all its data |
| `make logs s=api`, `make shell s=api` | Follow a service's logs / open a shell in it |
| `make psql`, `make s3-ls` | Open psql / list the objects in dev storage |
| `make migrate`, `make migration m="…"` | Apply migrations / create a new Alembic revision |
| `make seed`, `make seed-demo` | Reference data / demo tenants (dev only) |
| `make test`, `make test-db` | Backend and database tests in a disposable stack |
| `make lint`, `make typecheck` | ruff, import-linter, SPDX headers, eslint, prettier / mypy --strict, tsc |
| `make e2e` | Playwright against the running dev stack |
| `make openapi` | Regenerate the TypeScript API client |
| `make hooks` | Install git hooks that run pre-commit in a container |

## Repository layout

```
apps/backend     Python 3.13 (FastAPI API, procrastinate worker, CLI), one project, two processes
apps/web         Next.js 16 (App Router, TypeScript strict, Tailwind, shadcn/ui)
packages/        api-client (generated from OpenAPI) and ui (shared components)
db/              bootstrap roles, Alembic migrations, seeds, SQL-level tests
infra/           Dockerfiles, compose stacks (dev, test, prod), scripts
```

Background jobs run on [procrastinate](https://procrastinate.readthedocs.io), a Postgres-backed job-queue library. (The name belongs to the library; it has nothing to do with monitoring people.)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions require signing the [CLA](.github/CLA.md). Please also read the [Code of Conduct](CODE_OF_CONDUCT.md), and report vulnerabilities as described in [SECURITY.md](SECURITY.md).

## License

Copyright (c) 2026 Srikar Yelamanchili. Licensed under the [GNU AGPL v3.0 only](LICENSE).

Commercial licences are available for organisations that can't use AGPL software; see [NOTICE](NOTICE).
