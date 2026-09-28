# SPDX-License-Identifier: AGPL-3.0-only
"""Object storage access over the generic S3 API (no vendor-specific features).

Phase 0 only needs a reachability check; presigned uploads, quarantine and
scanning arrive in Phase 3.
"""

from typing import Any

import aioboto3
from botocore.config import Config

from pickwise.shared.settings import Settings


def s3_client(settings: Settings) -> Any:
    """Return an async context manager yielding an S3 client."""
    session = aioboto3.Session()
    return session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value() or None,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value() or None,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=settings.readiness_timeout_seconds,
            read_timeout=settings.readiness_timeout_seconds,
            retries={"max_attempts": 1},
        ),
    )


async def buckets_reachable(settings: Settings) -> bool:
    async with s3_client(settings) as s3:
        for bucket in (settings.s3_bucket_files, settings.s3_bucket_quarantine):
            await s3.head_bucket(Bucket=bucket)
    return True
