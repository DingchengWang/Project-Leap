"""Task cleanup operates only on disposable, application-owned fixtures."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from leap_desktop import task_storage
from leap_desktop.maintenance import component_use_lock
from leap_desktop.paths import AppPaths, write_json


class TaskStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="Leap task storage ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.paths = AppPaths(self.root / "data", config_dir=self.root / "settings")
        self.source = self.root / "original.tif"
        self.source.write_bytes(b"original image")
        self.export = self.root / "export.csv"
        self.export.write_bytes(b"external export")

    def run_fixture(self, identifier, status="completed"):
        root = self.paths.runs / identifier
        root.mkdir()
        write_json(root / "job.json", {"id": identifier, "status": status, "source": str(self.source)})
        for relative in ("cache", "counting/.editor", "workspace/Sample Image", "counting/revision-001"):
            target = root / relative
            target.mkdir(parents=True)
            (target / "data.bin").write_bytes((identifier + relative).encode())
        (root / "counting/labels.npy").write_bytes(b"final labels")
        return root

    def assert_external_unchanged(self):
        self.assertEqual(self.source.read_bytes(), b"original image")
        self.assertEqual(self.export.read_bytes(), b"external export")

    def test_batch_deletes_only_selected_runs_with_external_links_preserved(self):
        first = self.run_fixture("first", "failed")
        second = self.run_fixture("second", "interrupted")
        retained = self.run_fixture("retained")
        (first / "external").symlink_to(self.source)
        os.link(self.export, first / "export-hard-link")
        # A broken selected record must not make its owned data undeletable.
        (second / "request.json").write_text("interrupted json")
        result = task_storage.delete_tasks(self.paths, [first.name, second.name])
        self.assertEqual(result["status"], "deleted", result)
        self.assertEqual(result["deleted_ids"], [first.name, second.name])
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue((retained / "counting/labels.npy").exists())
        self.assert_external_unchanged()

    def test_every_batch_id_is_checked_before_any_deletion(self):
        run = self.run_fixture("safe")
        for invalid in ("../original.tif", "safe/../safe", ".", "..", "", str(self.root), "a\\b", "bad\x00id", None):
            with self.subTest(identifier=invalid):
                result = task_storage.delete_tasks(self.paths, [run.name, invalid])
                self.assertEqual(result["status"], "failed", result)
                self.assertEqual(result["deleted_ids"], [])
                self.assertTrue(run.exists())
        self.assert_external_unchanged()

    def test_run_symlink_and_runs_root_symlink_are_rejected(self):
        external = self.root / "external"
        external.mkdir()
        alias = self.paths.runs / "alias"
        alias.symlink_to(external, target_is_directory=True)
        self.assertEqual(task_storage.delete_tasks(self.paths, ["alias"])["status"], "failed")
        alias.unlink()
        self.paths.runs.rmdir()
        self.paths.runs.symlink_to(external, target_is_directory=True)
        self.assertEqual(task_storage.delete_tasks(self.paths, ["alias"])["status"], "failed")
        self.assertTrue(external.exists())

    def test_retained_source_references_are_resolved_before_deletion(self):
        removed = self.run_fixture("removed")
        retained = self.run_fixture("retained")
        source = removed / "counting/labels.npy"
        alias = self.root / "input-alias.npy"
        alias.symlink_to(source)
        records = [("job.json", {"source": str(alias)}),
                   ("request.json", {"source_path": "../removed/counting/labels.npy"}),
                   ("counting/analysis.json", {"source_path": "../../removed/counting/labels.npy"})]
        for relative, value in records:
            with self.subTest(record=relative):
                record = retained / relative
                original = record.read_bytes() if record.exists() else None
                write_json(record, value)
                result = task_storage.delete_tasks(self.paths, [removed.name])
                self.assertEqual(result["status"], "failed", result)
                self.assertIn("仍被引用", result["message"])
                self.assertTrue(source.exists())
                if original is None:
                    record.unlink()
                else:
                    record.write_bytes(original)

    def test_migrated_source_and_current_source_both_protect_run(self):
        removed = self.run_fixture("removed")
        retained = self.run_fixture("retained")
        source = removed / "counting/labels.npy"
        old = self.root / "old-home"
        self.paths.previous_homes = (str(old),)
        write_json(retained / "request.json", {"source_path": str(old / source.relative_to(self.paths.home))})
        self.assertEqual(task_storage.delete_tasks(self.paths, [removed.name])["status"], "failed")
        (retained / "request.json").unlink()
        self.assertEqual(task_storage.delete_tasks(self.paths, [removed.name], current_sources=[str(source)])["status"], "failed")
        self.assertTrue(source.exists())

    def test_historical_non_source_paths_do_not_block_deletion(self):
        removed = self.run_fixture("removed")
        retained = self.run_fixture("retained")
        write_json(retained / "request.json", {"provenance": {"old_result_path": str(removed / "counting/labels.npy")}})
        self.assertEqual(task_storage.delete_tasks(self.paths, [removed.name])["status"], "deleted")

    def test_shared_use_lock_blocks_mutation(self):
        run = self.run_fixture("busy")
        with component_use_lock(self.paths.home):
            self.assertEqual(task_storage.delete_tasks(self.paths, [run.name])["status"], "blocked")
            self.assertEqual(task_storage.clear_disposable_cache(self.paths)["status"], "blocked")
        self.assertTrue(run.exists())

    def test_partial_failure_reports_exact_deleted_ids(self):
        first, second = self.run_fixture("first"), self.run_fixture("second")
        remove = task_storage.shutil.rmtree
        def fail_second(path, *args, **kwargs):
            if path == second:
                raise OSError("simulated permission failure")
            return remove(path, *args, **kwargs)
        with mock.patch.object(task_storage.shutil, "rmtree", side_effect=fail_second):
            result = task_storage.delete_tasks(self.paths, [first.name, second.name])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["deleted_ids"], [first.name])
        self.assertTrue(second.exists())

    def test_cache_cleanup_preserves_live_preview_sources_and_all_final_results(self):
        complete = self.run_fixture("complete")
        failed = self.run_fixture("failed", "failed")
        review = self.run_fixture("review", "waiting_review")
        current = self.paths.cache / "preview-current.png"
        current.write_bytes(b"displayed pixels")
        stale = self.paths.cache / "preview-old.png"
        stale.write_bytes(b"old pixels")
        qt = self.paths.cache / "qmlcache"
        qt.mkdir()
        (qt / "compiled.qmlc").write_bytes(b"compiled")
        (self.paths.cache / "external").symlink_to(self.source)
        result = task_storage.clear_disposable_cache(self.paths, current_sources=[str(current)])
        self.assertEqual(result["status"], "cleared", result)
        self.assertEqual(result["cleared_ids"], [complete.name])
        self.assertEqual(current.read_bytes(), b"displayed pixels")
        self.assertFalse(stale.exists())
        self.assertFalse(qt.exists())
        for relative in task_storage._CACHE_PATHS:
            self.assertFalse((complete / relative).exists())
            self.assertTrue((failed / relative / "data.bin").exists())
            self.assertTrue((review / relative / "data.bin").exists())
        self.assertEqual((complete / "counting/labels.npy").read_bytes(), b"final labels")
        self.assertTrue((complete / "counting/revision-001/data.bin").exists())
        self.assert_external_unchanged()

    def test_automatic_cleanup_only_touches_requested_completed_task(self):
        first, second = self.run_fixture("first"), self.run_fixture("second")
        global_cache = self.paths.cache / "preview.png"
        global_cache.write_bytes(b"preview")
        result = task_storage.cleanup_completed_task(self.paths, first.name)
        self.assertEqual(result["status"], "cleared", result)
        self.assertFalse((first / "cache").exists())
        self.assertTrue((second / "cache/data.bin").exists())
        self.assertTrue(global_cache.exists())

    def test_cache_symlink_and_source_dependency_fail_before_cleanup(self):
        run = self.run_fixture("complete")
        retained = self.run_fixture("retained", "failed")
        write_json(retained / "request.json", {"source_path": str(run / "cache/data.bin")})
        self.assertEqual(task_storage.clear_disposable_cache(self.paths)["status"], "failed")
        self.assertTrue((run / "counting/.editor/data.bin").exists())
        (retained / "request.json").unlink()
        task_storage.shutil.rmtree(run / "cache")
        (run / "cache").symlink_to(self.root)
        self.assertEqual(task_storage.clear_disposable_cache(self.paths)["status"], "failed")
        self.assert_external_unchanged()

    def test_usage_counts_allocated_bytes_once_per_inode_and_never_follows_links(self):
        run = self.run_fixture("usage")
        sparse = run / "sparse-label.npy"
        with sparse.open("wb") as stream:
            stream.write(b"header")
            stream.seek(64 * 1024 * 1024 - 1)
            stream.write(b"\0")
        os.link(sparse, run / "hard-link.npy")
        self.source.write_bytes(b"x" * (2 * 1024 * 1024))
        (run / "external-source").symlink_to(self.source)
        result = task_storage.task_storage_usage(self.paths)
        self.assertEqual(result["status"], "inspected", result)
        item = result["tasks"][0]
        expected = {}
        for path, info in task_storage._walk(run):
            expected[info.st_dev, info.st_ino] = info
        self.assertEqual(item["allocated_bytes"], sum(info.st_blocks * 512 for info in expected.values()))
        self.assertGreater(item["logical_bytes"], 64 * 1024 * 1024)
        self.assertLess(item["allocated_bytes"], 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
