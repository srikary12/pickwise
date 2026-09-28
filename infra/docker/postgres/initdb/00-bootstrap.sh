#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Runs once, when the postgres image initialises an empty data volume.
# Applies db/bootstrap/*.sql (roles + extensions) to the pickwise database.
set -eu

: "${PG_MIGRATOR_PASSWORD:?PG_MIGRATOR_PASSWORD must be set (run make dev to generate .env)}"
: "${PG_API_PASSWORD:?PG_API_PASSWORD must be set}"
: "${PG_WORKER_PASSWORD:?PG_WORKER_PASSWORD must be set}"
: "${PG_MAINT_PASSWORD:?PG_MAINT_PASSWORD must be set}"

for file in /pickwise/bootstrap/*.sql; do
    echo "pickwise bootstrap: applying $(basename "$file")"
    psql --no-psqlrc -v ON_ERROR_STOP=1 \
        --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -v migrator_password="$PG_MIGRATOR_PASSWORD" \
        -v api_password="$PG_API_PASSWORD" \
        -v worker_password="$PG_WORKER_PASSWORD" \
        -v maint_password="$PG_MAINT_PASSWORD" \
        -f "$file"
done
