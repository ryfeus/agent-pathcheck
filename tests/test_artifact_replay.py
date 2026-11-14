"""Artifact verification happens before any saved code is executed."""

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.aws_finops.replay import replay_artifacts, verify_artifact


class ArtifactTests(unittest.TestCase):
    def test_downloaded_path_matches_manifest_and_modified_bytes_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.json"
            path.write_text('{"trials": []}')
            manifest = {"assets": {path.name: {"sha256": sha256(path.read_bytes()).hexdigest()}}}
            self.assertEqual(verify_artifact(path, manifest), manifest["assets"][path.name]["sha256"])
            path.write_text('{"trials": ["changed"]}')
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                verify_artifact(path, manifest)

    def test_all_inputs_are_verified_before_starting_an_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "unrecognized.json"
            artifact.write_text("invalid JSON must not even be parsed")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"assets": {}}))
            with patch("examples.aws_finops.replay.Engine") as engine:
                with self.assertRaisesRegex(ValueError, "Unknown artifact"):
                    replay_artifacts([artifact], manifest_path=manifest)
                engine.assert_not_called()
