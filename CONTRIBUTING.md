# Contributing to Pickwise

Thanks for helping. Pickwise handles people's personal data and pay, so the bar for correctness, security and privacy is high. This guide explains how to get a change merged.

## Before you start

- **Sign the CLA.** On your first pull request, the CLA Assistant bot asks you to accept the [Individual CLA](.github/CLA.md) by posting a comment. If you contribute on behalf of your employer, your employer should also sign the [Corporate CLA](.github/CCLA.md).
- **Discuss larger changes first:** open an issue before you start a new feature, a schema change or a new dependency.

### Why a CLA?

Pickwise is licensed under the **AGPL-3.0-only**. The CLA also lets the copyright holder offer Pickwise under a **commercial licence** to organisations that can't accept the AGPL, and that revenue funds the project. You keep the copyright in your work. Pickwise stays available under an OSI-approved open-source licence.

## Development environment

You need only **Docker**, **make** and **git**.

```sh
make dev      # start everything, wait until healthy
make hooks    # install git hooks (pre-commit runs inside the tools container)
```

Never install project tooling on your host. If a task needs a tool, it belongs in `infra/docker/tools.Dockerfile` and a `make` target.

## Making a change

1. Branch from `main`. Phase work uses `phase-NN-short-name`; other work uses a short descriptive name.
2. Keep commits small and focused. Phase work prefixes commit subjects with the phase, e.g. `[P03] Add presigned upload endpoint`.
3. Before pushing, run:

   ```sh
   make lint typecheck test
   ```

4. Open a pull request against `main`. The maintainer reviews and squash-merges. Nobody pushes to `main` directly.

A change is done when it includes, as applicable:
- tests (including authorization and data-scope tests)
- migrations
- docs and OpenAPI examples
- an Architecture Decision Record for any design choice someone could reasonably question

## Rules that CI enforces

- **License headers:** every source file starts with an `SPDX-License-Identifier: AGPL-3.0-only` comment. Pre-commit checks this.
- **Module boundaries:** import-linter enforces the dependency direction `shared ← platform ← core ← leave ← attendance ← payroll ← recruit`.
- **Types:** `mypy --strict` for Python and `tsc` in strict mode for TypeScript.
- **No live AI calls:** tests and CI use a fake Jev client and never call the live TypeSafe API.
- **No PII in logs or job arguments:** log ids only, never names, emails, salaries or tokens.

## Dependencies and services

Ask in an issue before adding a new runtime dependency or a new service to the compose files. Pin exact versions, and pin container images by tag **and** digest.

## Reporting security issues

Don't open a public issue. Follow [SECURITY.md](SECURITY.md).
