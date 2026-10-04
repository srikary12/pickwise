#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Start SeaweedFS (master + volume + filer + S3 gateway in one process) with a
# single S3 identity taken from the environment.
set -eu
: "${S3_ACCESS_KEY_ID:?S3_ACCESS_KEY_ID must be set}"
: "${S3_SECRET_ACCESS_KEY:?S3_SECRET_ACCESS_KEY must be set}"

umask 077
cat > /tmp/s3.json <<JSON
{
  "identities": [
    {
      "name": "pickwise",
      "credentials": [{"accessKey": "${S3_ACCESS_KEY_ID}", "secretKey": "${S3_SECRET_ACCESS_KEY}"}],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }
  ]
}
JSON

exec weed server \
    -dir=/data \
    -ip=s3 -ip.bind=0.0.0.0 \
    -volume.max=0 \
    -master.volumeSizeLimitMB="${S3_VOLUME_SIZE_LIMIT_MB:-256}" \
    -s3 -s3.port=8333 -s3.config=/tmp/s3.json
