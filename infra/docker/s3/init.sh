#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Create the buckets and CORS rules for presigned browser uploads. Idempotent.
# Runs in the tools image (aws-cli) against the SeaweedFS S3 gateway.
set -eu
export AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$S3_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="${S3_REGION:-us-east-1}"
endpoint="${S3_ENDPOINT_URL:-http://s3:8333}"
s3api() { aws --endpoint-url "$endpoint" s3api "$@"; }

cors=$(cat <<JSON
{
  "CORSRules": [
    {
      "AllowedOrigins": ["${WEB_ORIGIN:-http://localhost:3000}"],
      "AllowedMethods": ["PUT", "GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "ExposeHeaders": ["ETag"],
      "MaxAgeSeconds": 3000
    }
  ]
}
JSON
)

for bucket in "$S3_BUCKET_FILES" "$S3_BUCKET_QUARANTINE"; do
    if s3api head-bucket --bucket "$bucket" >/dev/null 2>&1; then
        echo "s3-init: bucket $bucket exists"
    else
        s3api create-bucket --bucket "$bucket" >/dev/null
        echo "s3-init: created bucket $bucket"
    fi
    s3api put-bucket-cors --bucket "$bucket" --cors-configuration "$cors"
    echo "s3-init: CORS set on $bucket"
done
