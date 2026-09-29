#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Fill empty secrets in .env. Runs inside the tools container (needs openssl).
# Never overwrites a value that is already set.
set -eu

ENV_FILE="${1:-.env}"
[ -f "$ENV_FILE" ] || { echo "gen-secrets: $ENV_FILE not found" >&2; exit 1; }

fill() {
    key="$1"; value="$2"
    if grep -qE "^${key}=.+" "$ENV_FILE"; then
        return 0
    fi
    tmp="$(mktemp)"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v} {print}' "$ENV_FILE" > "$tmp"
    else
        cat "$ENV_FILE" > "$tmp"
        printf '%s=%s\n' "$key" "$value" >> "$tmp"
    fi
    cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
    echo "gen-secrets: generated $key"
}

hex() { openssl rand -hex "$1"; }

fill PICKWISE_KEK "$(openssl rand -base64 32)"
fill SESSION_SECRET "$(hex 32)"
fill POSTGRES_PASSWORD "$(hex 24)"
fill PG_MIGRATOR_PASSWORD "$(hex 24)"
fill PG_API_PASSWORD "$(hex 24)"
fill PG_WORKER_PASSWORD "$(hex 24)"
fill PG_MAINT_PASSWORD "$(hex 24)"
fill S3_ACCESS_KEY_ID "pickwise$(hex 8)"
fill S3_SECRET_ACCESS_KEY "$(hex 32)"
# Readable but unguessable, so demo logins are easy to type.
fill DEMO_PASSWORD "demo-$(hex 8)"
