"""
MinIO / S3-compatible storage service.

All file I/O in TextLens goes through this module.
Swap MINIO_ENDPOINT + credentials for an AWS S3 endpoint and nothing
else in the codebase changes.
"""
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

# One session for the whole process lifetime — thread-safe, connection-pooled.
_session = aioboto3.Session()


def _client_kwargs() -> dict:
    """Build boto3 client kwargs pointing at MinIO."""
    return {
        "service_name": "s3",
        "endpoint_url": settings.MINIO_ENDPOINT,
        "aws_access_key_id": settings.MINIO_ACCESS_KEY,
        "aws_secret_access_key": settings.MINIO_SECRET_KEY,
        "config": Config(
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "adaptive"},
            # Required for MinIO path-style addressing
            s3={"addressing_style": "path"},
        ),
    }


def _public_client_kwargs() -> dict:
    """
    For presigned URL generation we need the PUBLIC endpoint URL so the
    browser can reach MinIO directly (not via Docker's internal hostname).
    """
    kw = _client_kwargs()
    kw["endpoint_url"] = settings.MINIO_PUBLIC_URL
    return kw


# Bucket lifecycle 
async def ensure_bucket() -> None:
    """
    Create the configured bucket if it doesn't exist.
    Safe to call on every startup — idempotent.
    """
    async with _session.client(**_client_kwargs()) as s3:
        try:
            await s3.head_bucket(Bucket=settings.MINIO_BUCKET)
            logger.info(f"[storage] bucket '{settings.MINIO_BUCKET}' already exists")
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("404", "NoSuchBucket"):
                await s3.create_bucket(Bucket=settings.MINIO_BUCKET)
                logger.info(f"[storage] created bucket '{settings.MINIO_BUCKET}'")
            else:
                raise


# Upload 
async def upload_bytes(
    data: bytes,
    object_key: str,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload raw bytes under `object_key`.
    Returns the key — useful for chaining.
    """
    async with _session.client(**_client_kwargs()) as s3:
        await s3.put_object(
            Bucket=settings.MINIO_BUCKET,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )
    logger.debug(f"[storage] uploaded {len(data):,}B → {object_key}")
    return object_key


async def upload_file(
    local_path: str,
    object_key: str,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Stream-upload a local file to object storage.
    Returns the object key.
    """
    async with _session.client(**_client_kwargs()) as s3:
        await s3.upload_file(
            Filename=local_path,
            Bucket=settings.MINIO_BUCKET,
            Key=object_key,
            ExtraArgs={"ContentType": content_type},
        )
    logger.debug(f"[storage] uploaded file {local_path} → {object_key}")
    return object_key


# Download 
async def download_to_temp(object_key: str, suffix: str = "") -> str:
    """
    Download an object to a named temp file.

    Returns the local file path.
    IMPORTANT: Caller is responsible for deleting the file after use
    (`os.unlink(path)` or `Path(path).unlink(missing_ok=True)`).
    """
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()

    async with _session.client(**_client_kwargs()) as s3:
        await s3.download_file(
            Bucket=settings.MINIO_BUCKET,
            Key=object_key,
            Filename=tmp.name,
        )

    logger.debug(f"[storage] downloaded {object_key} → {tmp.name}")
    return tmp.name


# Presigned URL 
async def get_presigned_url(
    object_key: str,
    expires_in: int = 3600,
    filename: Optional[str] = None,
) -> str:
    """
    Generate a presigned GET URL valid for `expires_in` seconds.
    Set `filename` to add a Content-Disposition header for browser downloads.
    """
    params: dict = {"Bucket": settings.MINIO_BUCKET, "Key": object_key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'

    # Use the PUBLIC endpoint so the browser can resolve the hostname.
    async with _session.client(**_public_client_kwargs()) as s3:
        url: str = await s3.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in,
        )

    return url


# Delete
async def delete_object(object_key: str) -> None:
    """Delete an object. Silently ignores missing keys."""
    try:
        async with _session.client(**_client_kwargs()) as s3:
            await s3.delete_object(Bucket=settings.MINIO_BUCKET, Key=object_key)
        logger.debug(f"[storage] deleted {object_key}")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code not in ("404", "NoSuchKey"):
            raise


async def delete_objects(keys: list[str]) -> None:
    """Batch-delete multiple objects in a single API call (max 1000)."""
    if not keys:
        return
    objects = [{"Key": k} for k in keys]
    async with _session.client(**_client_kwargs()) as s3:
        await s3.delete_objects(
            Bucket=settings.MINIO_BUCKET,
            Delete={"Objects": objects, "Quiet": True},
        )
    logger.debug(f"[storage] batch-deleted {len(keys)} objects")


# Existence check 
async def object_exists(object_key: str) -> bool:
    """Return True if the object exists in the bucket."""
    try:
        async with _session.client(**_client_kwargs()) as s3:
            await s3.head_object(Bucket=settings.MINIO_BUCKET, Key=object_key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


# Object key builders 
def build_upload_key(user_id: str, filename: str) -> str:
    """uploads/{user_id}/{uuid}{ext}"""
    ext = Path(filename).suffix or ".bin"
    return f"uploads/{user_id}/{uuid.uuid4()}{ext}"


def build_result_key(user_id: str, job_id: str, filename: str) -> str:
    """results/{user_id}/{job_id}/{filename}"""
    return f"results/{user_id}/{job_id}/{filename}"


def build_batch_key(batch_id: str, item_id: str, filename: str) -> str:
    """batch/{batch_id}/{item_id}/{uuid}{ext}"""
    ext = Path(filename).suffix or ".bin"
    return f"batch/{batch_id}/{item_id}/{uuid.uuid4()}{ext}"


# Content-type helpers 

_EXT_TO_MIME: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
    ".bmp":  "image/bmp",
    ".webp": "image/webp",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv":  "text/csv",
}


def content_type_for(filename: str) -> str:
    """Infer MIME type from file extension."""
    return _EXT_TO_MIME.get(Path(filename).suffix.lower(), "application/octet-stream")