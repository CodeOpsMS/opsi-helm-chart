"""Safety boundaries for optional storage and explicit data preparation."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_runtime
from test_runtime import CHART, load

storage = load("file_storage", "file-storage.py")
spec = importlib.util.spec_from_file_location("prepare", CHART.parents[1] / "scripts/prepare-file-storage.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class FileStorageTests(unittest.TestCase):
    def test_foreign_identity_and_symlink_directory_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in storage.DIRECTORIES:
                (root / name).mkdir()
            marker = root / storage.MARKER
            marker.write_text(json.dumps({"version": 1, "hostId": "other.example.org"}))
            with self.assertRaisesRegex(RuntimeError, "another server"):
                storage.validate_marker(root, "test.example.org")
            marker.write_text(json.dumps({"version": 1, "hostId": "test.example.org"}))
            storage.validate_marker(root, "test.example.org")
            (root / "depot").rmdir()
            (root / "depot").symlink_to(root / "repository", target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "real directory"):
                storage.validate_marker(root, "test.example.org")

    def test_root_partial_or_conflicting_ids_rejected_before_commands(self):
        for uid, gid in [(0, 988), (977, None), (None, 988), (-1, 988)]:
            with self.subTest(uid=uid, gid=gid), patch.object(storage.subprocess, 'run') as command:
                with self.assertRaises(RuntimeError):
                    storage.align_ids(uid, gid)
                command.assert_not_called()
        with patch.object(storage.pwd, "getpwuid", return_value=type("User", (), {"pw_name": "unrelated"})()), patch.object(storage.subprocess, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, "another local identity"):
                storage.align_ids(977, 988)
            command.assert_not_called()

    def test_foreign_existing_owner_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "foreign owner"):
                storage.validate_tree(Path(directory), os.getuid() + 1, os.getgid())

    def test_traversal_errors_are_not_silently_skipped(self):
        def unreadable(*args, **kwargs):
            kwargs['onerror'](PermissionError('unreadable subtree'))
            return iter(())
        with patch.object(storage.os, 'walk', side_effect=unreadable):
            with self.assertRaises(PermissionError):
                storage.validate_tree(Path('/unused'), 977, 988)
            with self.assertRaises(PermissionError):
                prepare.inventory(Path('/unused'))

    def test_preparation_preserves_content_links_and_modes_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            for name in storage.DIRECTORIES:
                (source / name).mkdir(parents=True)
            executable = source / "depot" / "setup.sh"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o750)
            (source / "depot" / "setup").symlink_to("setup.sh")
            target = root / "target"
            result = prepare.prepare(source, target, "test.example.org", os.getuid(), os.getgid())
            self.assertTrue(all(value['verified'] for value in result.values()))
            storage.validate_marker(target, "test.example.org")
            self.assertEqual((target / "depot/setup.sh").read_bytes(), executable.read_bytes())
            self.assertEqual((target / "depot/setup.sh").stat().st_mode & 0o777, 0o750)
            self.assertEqual(os.readlink(target / "depot/setup"), "setup.sh")
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                prepare.prepare(source, target, "test.example.org", os.getuid(), os.getgid())


class FileStorageHelmTests(unittest.TestCase):
    helm = test_runtime.ChartTests.helm

    def test_claim_is_separate_and_ids_are_paired(self):
        for extra in [[], ['--set', 'fileStorage.mappedUid=977'], ['--set', 'fileStorage.mappedUid=0,fileStorage.mappedGid=988']]:
            result = self.helm('--set', 'fileStorage.enabled=true', *extra)
            self.assertNotEqual(result.returncode, 0)
        result = self.helm('--set', 'fileStorage.enabled=true,fileStorage.existingClaim=opsi-data')
        self.assertNotEqual(result.returncode, 0)
        result = self.helm('--set', 'fileStorage.enabled=true,fileStorage.existingClaim=files,fileStorage.mappedUid=977,fileStorage.mappedGid=988')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mountPath: /data/lib/depot', result.stdout)
        self.assertNotIn('mountPath: /var/lib/opsi/depot', result.stdout)
        self.assertIn('claimName: opsi-data', result.stdout)
        self.assertIn('claimName: files', result.stdout)
