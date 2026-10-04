# SPDX-License-Identifier: AGPL-3.0-only
"""Object storage access over the generic S3 API (no vendor-specific features).

Uploads go to the quarantine bucket through presigned POSTs; the worker scans them
and moves clean files to the files bucket (ADR 0013). Browsers only ever see
presigned URLs, built against ``s3_public_endpoint_url``.
"""

from typing import IO, Any

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from pickwise.shared.settings import Settings


def s3_client(settings: Settings, *, public: bool = False) -> Any:
    """An async context manager yielding an S3 client.

    ``public=True`` signs against the browser-facing endpoint; use it only to presign.
    """
    session = aioboto3.Session()
    endpoint = (settings.s3_public_endpoint_url if public else None) or settings.s3_endpoint_url
    return session.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value() or None,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value() or None,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=settings.readiness_timeout_seconds,
            read_timeout=max(settings.readiness_timeout_seconds, 30),
            retries={"max_attempts": 2},
        ),
    )


async def buckets_reachable(settings: Settings) -> bool:
    async with s3_client(settings) as s3:
        for bucket in (settings.s3_bucket_files, settings.s3_bucket_quarantine):
            await s3.head_bucket(Bucket=bucket)
    return True


async def presign_upload(
    settings: Settings, key: str, *, content_type: str, max_bytes: int, expires_in: int
) -> dict[str, Any]:
    """A presigned POST into the quarantine bucket. The store itself enforces the size cap."""
    async with s3_client(settings, public=True) as s3:
        post: dict[str, Any] = await s3.generate_presigned_post(
            settings.s3_bucket_quarantine,
            key,
            Fields={"Content-Type": content_type},
            Conditions=[
                ["content-length-range", 1, max_bytes],
                {"Content-Type": content_type},
            ],
            ExpiresIn=expires_in,
        )
    return post


async def presign_download(
    settings: Settings, bucket: str, key: str, *, filename: str, content_type: str, expires_in: int
) -> str:
    """A presigned GET that forces a download with the sniffed type (never inline)."""
    quoted = filename.replace('"', "").replace("\r", "").replace("\n", "")
    async with s3_client(settings, public=True) as s3:
        url: str = await s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": f'attachment; filename="{quoted}"',
            },
            ExpiresIn=expires_in,
        )
    return url


async def head_object(s3: Any, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        result: dict[str, Any] = await s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return result


async def download_to(s3: Any, bucket: str, key: str, target: IO[bytes]) -> None:
    response = await s3.get_object(Bucket=bucket, Key=key)
    async for chunk in response["Body"].iter_chunks(1024 * 1024):
        target.write(chunk)


async def delete_object(s3: Any, bucket: str, key: str) -> None:
    await s3.delete_object(Bucket=bucket, Key=key)
