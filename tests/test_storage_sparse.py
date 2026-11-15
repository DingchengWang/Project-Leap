"""Sparse NPY labels keep bytes, modes and APFS allocation during migration."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from leap_desktop import storage
from leap_desktop.paths import AppPaths


@unittest.skipUnless(sys.platform == "darwin", "macOS sparse copyfile contract")
class SparseStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="Leap sparse migration ", dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.paths = AppPaths(self.root / "old", config_dir=self.root / "settings")
        self.labels = self.paths.runs / "synthetic/counting/labels.npy"
        self.labels.parent.mkdir(parents=True)
        labels = np.lib.format.open_memmap(self.labels, mode="w+", dtype=np.uint32, shape=(8, 1024, 2048))
        labels[0, 10:20, 40:60] = 3
        labels[7, 1000:1010, 2000:2010] = 9
        labels.flush()
        del labels
        self.labels.chmod(0o640)
        (self.labels.parent / "labels-link.npy").symlink_to(self.labels.name)
        self.before = self.labels.stat()
        self.digest = self.sha256(self.labels)
        self.assertLess(self.before.st_blocks * 512, self.before.st_size // 16)

    @staticmethod
    def sha256(path):
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def test_actual_migration_preserves_sparse_npy_bytes_mode_and_links(self):
        target = self.root / "new"
        result = storage.migrate_app_data(self.paths, target)
        self.assertEqual(result["status"], "migrated", result)
        copied = target / self.labels.relative_to(self.paths.home)
        after = copied.stat()
        self.assertEqual(after.st_size, self.before.st_size)
        self.assertEqual(after.st_mode, self.before.st_mode)
        self.assertEqual(self.sha256(copied), self.digest)
        self.assertLessEqual(after.st_blocks * 512, self.before.st_blocks * 512)
        self.assertEqual(os.readlink(copied.parent / "labels-link.npy"), self.labels.name)
        data = np.load(copied, mmap_mode="r")
        self.assertEqual(int(data[0, 10, 40]), 3)
        self.assertEqual(int(data[7, 1000, 2000]), 9)
        self.assertEqual(int(data[3, 500, 1000]), 0)
        self.assertFalse(self.paths.home.exists())

    def test_sparse_unsupported_target_fails_without_dense_fallback(self):
        calls = []
        def unsupported(source, destination, state, flags):
            calls.append(flags)
            ctypes.set_errno(errno.ENOTSUP)
            return -1
        library = types.SimpleNamespace(copyfile=unsupported)
        target = self.root / "unsupported"
        settings = self.paths.settings_file.read_bytes()
        with mock.patch.object(storage.ctypes, "CDLL", return_value=library):
            result = storage.migrate_app_data(self.paths, target)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("稀疏", result["message"])
        self.assertTrue(calls)
        self.assertTrue(all(flags & (1 << 27) for flags in calls))
        self.assertTrue(all(not flags & (1 << 3) for flags in calls))
        self.assertEqual(self.paths.settings_file.read_bytes(), settings)
        self.assertEqual(self.sha256(self.labels), self.digest)
        self.assertEqual(self.labels.stat().st_blocks, self.before.st_blocks)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob(".*.migrating-*")), [])


if __name__ == "__main__":
    unittest.main()
