"""Where the bytes live: the engine stores and reads every file through the blob store, keys are content-addressed
under the tenant, and an object is fetched only through a locator of its own bucket. The S3 store runs here against
an in-memory stand-in for the S3 calls it makes; the Compose stack's job runs it against a real S3-compatible
server."""
import io
import random

import pytest

from testamur.blobs import LocalBlobStore, S3BlobStore
from testamur.engine import Engine
from testamur.harness import load_registry
from testamur.pack import load_pack
from testamur.store import Blob, Store
from testamur.synth.render import DocContent, render_pdf
from testamur.synth.world import build_world


class FakeS3:
    """The four S3 calls the store makes, over a dict."""

    def __init__(self):
        self.buckets: dict[str, dict[str, bytes]] = {}

    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    def create_bucket(self, Bucket):
        self.buckets.setdefault(Bucket, {})

    def put_object(self, Bucket, Key, Body):
        self.buckets[Bucket][Key] = bytes(Body)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.buckets[Bucket][Key])}


def test_the_s3_store_keys_objects_by_tenant_and_content_and_refuses_other_buckets():
    s3 = FakeS3()
    store = S3BlobStore("docs", s3)
    store.ensure_bucket()
    where = store.put(7, "ab" + "0" * 62, b"%PDF-1.7 hello")
    assert where == f"s3://docs/blobs/7/ab/ab{'0' * 62}"
    assert store.get(where) == b"%PDF-1.7 hello"
    with pytest.raises(ValueError):
        store.get(where.replace("s3://docs/", "s3://elsewhere/"))
    store.ensure_bucket()                                    # idempotent
    assert list(s3.buckets) == ["docs"]


def test_the_local_store_never_shows_half_a_file(tmp_path):
    store = LocalBlobStore(tmp_path)
    where = store.put(1, "cd" + "1" * 62, b"bytes")
    assert store.get(where) == b"bytes" and not list(tmp_path.rglob("*.part"))


def test_the_engine_stores_and_reads_every_file_through_its_blob_store(tmp_path):
    world = build_world(seed=5, size="tiny", out=tmp_path / "w", digital_only=True)
    store = Store(f"sqlite:///{tmp_path / 'b.db'}")
    load_registry(store, world)
    s3 = FakeS3()
    blobs = S3BlobStore("docs", s3)
    blobs.ensure_bucket()
    engine = Engine(store, load_pack(), world.as_of, tmp_path / "never-used", blobs=blobs)
    u = world.uploads[0]
    page = [("title", "LETTER OF AUTHORITY"), ("kv", "Date of issue", world.as_of.isoformat())]
    data = render_pdf(DocContent([page]), random.Random(1))
    uid = engine.ingest(u.tenant_id, u.matter_id, "a.pdf", data, "portal", u.uploaded_at)
    engine.process(u.tenant_id, uid)
    assert len(s3.buckets["docs"]) == 1 and not (tmp_path / "never-used").exists()
    with store.tenant(u.tenant_id) as s:
        (blob,) = s.query(Blob).all()
        assert blob.path.startswith(f"s3://docs/blobs/{u.tenant_id}/")
    store.engine.dispose()
