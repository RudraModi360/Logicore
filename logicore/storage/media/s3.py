"""
S3 media backend for binary file storage.

Requires: boto3 (pip install boto3)

Swap from local storage by changing media config:
    MediaConfig(root="s3://my-bucket/prefix")
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from .base import MediaBackend, MediaInfo

try:
    import boto3
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


def _guess_extension(mime: str) -> str:
    mime_map = {
        "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
        "image/webp": ".webp", "image/svg+xml": ".svg", "application/pdf": ".pdf",
        "text/plain": ".txt", "text/csv": ".csv", "application/json": ".json",
        "text/markdown": ".md", "text/html": ".html", "text/css": ".css",
        "text/javascript": ".js", "application/zip": ".zip",
        "application/octet-stream": ".bin",
    }
    return mime_map.get(mime, ".bin")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class S3MediaBackend(MediaBackend):
    """S3-compatible media storage backend."""

    def __init__(self, bucket: str, prefix: str = "", region: str = "us-east-1",
                 endpoint_url: str = None, aws_access_key_id: str = None,
                 aws_secret_access_key: str = None):
        if not HAS_BOTO3:
            raise ImportError(
                "boto3 is required for S3 backend. "
                "Install with: pip install boto3"
            )
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = None
        self._region = region
        self._endpoint_url = endpoint_url
        self._access_key = aws_access_key_id
        self._secret_key = aws_secret_access_key

    def initialize(self) -> None:
        kwargs = {"region_name": self._region}
        if self._endpoint_url:
            kwargs["endpoint_url"] = self._endpoint_url
        if self._access_key:
            kwargs["aws_access_key_id"] = self._access_key
        if self._secret_key:
            kwargs["aws_secret_access_key"] = self._secret_key
        self._client = boto3.client("s3", **kwargs)
        self._verify_bucket()

    def _verify_bucket(self) -> None:
        """Pre-flight check: the bucket must already exist (S3 APIs don't
        auto-create it). Fail early with an actionable message."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchBucket", "404", "403"):
                raise RuntimeError(
                    f"[Storage] S3 bucket '{self.bucket}' not found or inaccessible. "
                    f"Create it first in Supabase Storage → Buckets "
                    f"(or set LOGICORE_STORAGE_MEDIA_ROOT=s3://<your-bucket>/prefix). "
                    f"Underlying error: {code}"
                )
            # Other errors (e.g. network) — let them surface on first use
            logger.debug("[Storage] bucket pre-flight check warning: %s", e)

    def _s3_key(self, path: str) -> str:
        if self.prefix:
            return f"{self.prefix}/{path}"
        return path

    def put(self, session_id: str, file_id: str, data: bytes,
            mime: str = "application/octet-stream") -> MediaInfo:
        ext = _guess_extension(mime)
        filename = f"{file_id}{ext}"
        key = self._s3_key(f"{session_id}/{filename}")

        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=mime,
        )

        rel_path = f"{session_id}/{filename}"
        return MediaInfo(
            file_id=file_id,
            path=rel_path,
            mime=mime,
            sha256=_sha256(data),
            size=len(data),
        )

    def get(self, path: str) -> Optional[bytes]:
        key = self._s3_key(path)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except ClientError:
            return None

    def delete(self, path: str) -> bool:
        key = self._s3_key(path)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def exists(self, path: str) -> bool:
        key = self._s3_key(path)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def get_path(self, session_id: str, file_id: str) -> Path:
        return Path(f"{session_id}/{file_id}.bin")
