# Seeds

Reference data that `pickwise db seed` loads (the `migrate` container runs it after `alembic upgrade head`):

| Seed | Arrives in | Notes |
|---|---|---|
| Permission catalog | Phase 2 | Synced from `pickwise.platform.permissions` on every migrate |
| Statutory rule sets | Phase 8 | `db/seed/statutory/*.yaml`; everything starts as `draft`. `fixtures/` holds `test_fixture` sets, loaded only in development and test |

Each seed step does nothing until its table exists.

Demo tenants (`acme`, `globex`) come from `pickwise demo seed`, which only the `seed-demo` service in `compose.dev.yml` and `compose.test.yml` runs. Production never seeds demo data.
