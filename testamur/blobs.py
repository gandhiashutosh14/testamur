"""Where uploaded bytes live: an object-store interface with a local and an S3-compatible implementation.

The engine stores each file once per tenant (content-addressed, see store.Blob) and asks the blob store for its bytes
when it reads it. The locator a store returns is recorded on the Blob row; keys are tenant-prefixed, so one tenant's
objects are never addressed through another's path. The local store is the default (and what the tests use); the S3
store runs against RustFS in the Compose stack, or AWS S3, or any S3-compatible service.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol


class BlobStore(Protocol):
    def put(self, tenant_id: int, sha256: str, data: bytes) -> str: ...

    def get(self, locator: str) -> bytes: ...


class LocalBlobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def put(self, tenant_id: int, sha256: str, data: bytes) -> str:
        path = self.root / str(tenant_id) / sha256[:2] / sha256
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)                     # a reader never sees half a file
        return str(path)

    def get(self, locator: str) -> bytes:
        return Path(locator).read_bytes()


class S3BlobStore:
    """An S3-compatible bucket. `client` is a boto3 S3 client; objects are written once and never overwritten
    (content-addressed)."""

    def __init__(self, bucket: str, client=None, prefix: str = "blobs"):
        if client is None:
            import boto3
            client = boto3.client("s3")
        self.bucket, self.client, self.prefix = bucket, client, prefix.strip("/")

    def put(self, tenant_id: int, sha256: str, data: bytes) -> str:
        key = f"{self.prefix}/{tenant_id}/{sha256[:2]}/{sha256}"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return f"s3://{self.bucket}/{key}"

    def get(self, locator: str) -> bytes:
        if not locator.startswith(f"s3://{self.bucket}/"):
            raise ValueError("not an object of this bucket")
        key = locator[len(f"s3://{self.bucket}/"):]
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def ensure_bucket(self, wait_seconds: float = 60.0) -> None:
        """Create the bucket if it is missing, waiting for a store that is still starting (a Compose stack)."""
        from botocore.exceptions import ClientError, EndpointConnectionError
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                self.client.head_bucket(Bucket=self.bucket)
                return
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
                    raise
                self.client.create_bucket(Bucket=self.bucket)
                return
            except EndpointConnectionError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(1.0)


def from_env(blob_root: str | Path) -> BlobStore:
    """S3 when TESTAMUR_S3_BUCKET is set (with the usual AWS_* or an S3 endpoint URL), else local files."""
    import os
    bucket = os.environ.get("TESTAMUR_S3_BUCKET")
    if not bucket:
        return LocalBlobStore(blob_root)
    import boto3
    from botocore.config import Config
    endpoint = os.environ.get("TESTAMUR_S3_ENDPOINT") or None
    # a self-hosted store is addressed by path (http://host:9000/bucket/key), not by a bucket subdomain
    client = boto3.client("s3", endpoint_url=endpoint,
                          config=Config(s3={"addressing_style": "path"} if endpoint else {},
                                        retries={"max_attempts": 5, "mode": "standard"}))
    store = S3BlobStore(bucket, client)
    store.ensure_bucket()
    return store
