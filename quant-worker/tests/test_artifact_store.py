"""Filesystem artifact object adapter tests."""

import tempfile
import unittest
from pathlib import Path

from worker.adapters.artifact_store import FilesystemArtifactObjectStore
from worker.domain import WorkerError


class ArtifactObjectStoreTests(unittest.TestCase):
    def test_publish_reuses_identical_immutable_object(self):
        store = FilesystemArtifactObjectStore()
        actor = "v1-" + "a" * 64
        artifact_id = "art_v1_" + "1" * 24
        digest = "sha256:e874922d2be47b7cba6d094ecc21c582adf5ae453a08f1fb36c48e2c7b8fabe3"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "predictions.parquet"
            source.write_bytes(b"immutable-predictions")
            key, size = store.publish(
                source,
                actor_key=actor,
                actor_root=root,
                artifact_id=artifact_id,
                filename=source.name,
                media_type="application/vnd.apache.parquet",
                content_hash=digest,
            )
            duplicate = root / source.name
            duplicate.write_bytes(b"immutable-predictions")
            repeated = store.publish(
                duplicate,
                actor_key=actor,
                actor_root=root,
                artifact_id=artifact_id,
                filename=duplicate.name,
                media_type="application/vnd.apache.parquet",
                content_hash=digest,
            )

            self.assertEqual((key, size), repeated)
            self.assertTrue(store.materialize(
                key,
                actor_key=actor,
                actor_root=root,
                artifact_id=artifact_id,
                filename="predictions.parquet",
                content_hash=digest,
            ).is_file())

    def test_filesystem_storage_rejects_path_traversal(self):
        store = FilesystemArtifactObjectStore()
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(WorkerError):
                store.materialize(
                    "../outside",
                    actor_key="development",
                    actor_root=Path(temporary),
                    artifact_id="art_v1_" + "2" * 24,
                    filename="outside",
                    content_hash="sha256:" + "0" * 64,
                )


if __name__ == "__main__":
    unittest.main()
