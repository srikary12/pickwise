#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Every source file must carry "SPDX-License-Identifier: AGPL-3.0-only" in its
# first five lines. Generated and vendored files are exempt. Pass file names to
# check only those (pre-commit); otherwise every tracked file is checked.
set -eu

TAG="SPDX-License-Identifier: AGPL-3.0-only"

is_source() {
    case "$1" in
        db/migrations/vendor/*|packages/api-client/src/schema.d.ts|*/next-env.d.ts) return 1 ;;
        *.py|*.ts|*.tsx|*.js|*.mjs|*.cjs|*.css|*.sql|*.sh|*.mako|*Dockerfile|Makefile) return 0 ;;
        *) return 1 ;;
    esac
}

if [ "$#" -gt 0 ]; then
    files="$*"
else
    files=$(git ls-files --cached --others --exclude-standard)
fi

missing=0
for f in $files; do
    [ -f "$f" ] || continue
    is_source "$f" || continue
    if ! head -n 5 "$f" | grep -q "$TAG"; then
        echo "missing SPDX header: $f"
        missing=1
    fi
done

if [ "$missing" -ne 0 ]; then
    echo "add a comment line containing '$TAG' near the top of each file above" >&2
    exit 1
fi
echo "SPDX headers OK"
