#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Wait until every long-running service is healthy and every one-shot service
# has exited 0. Runs on the host, so it uses only POSIX sh and docker compose.
# Usage: wait-healthy.sh <timeout-seconds> <docker compose command...>
set -eu

timeout="$1"; shift
start=$(date +%s)

while :; do
    pending=""
    failed=""
    # One line per container: service, state, health, exit code.
    for line in $("$@" ps --all --format '{{.Service}},{{.State}},{{.Health}},{{.ExitCode}}'); do
        service=$(echo "$line" | cut -d, -f1)
        state=$(echo "$line" | cut -d, -f2)
        health=$(echo "$line" | cut -d, -f3)
        code=$(echo "$line" | cut -d, -f4)
        case "$state" in
            exited)
                [ "$code" = "0" ] || failed="$failed $service(exit $code)" ;;
            running)
                if [ -n "$health" ] && [ "$health" != "healthy" ]; then
                    [ "$health" = "unhealthy" ] && failed="$failed $service(unhealthy)"
                    pending="$pending $service($health)"
                fi ;;
            *)
                pending="$pending $service($state)" ;;
        esac
    done

    if [ -n "$failed" ]; then
        echo "✗ failed:$failed" >&2
        echo "  inspect with: make logs s=<service>" >&2
        exit 1
    fi
    if [ -z "$pending" ]; then
        echo "✓ all services healthy ($(( $(date +%s) - start ))s)"
        exit 0
    fi
    if [ $(( $(date +%s) - start )) -ge "$timeout" ]; then
        echo "✗ timed out after ${timeout}s waiting for:$pending" >&2
        exit 1
    fi
    sleep 3
done
