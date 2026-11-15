"""Lifecycle deletion is confined to disposable application fixtures."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from leap_desktop import lifecycle, storage
from leap_desktop.paths import APPLICATION_ID, AppPaths, maintenance_lock_path, write_json


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Leap 生命周期 中文 ")
        self.root = Path(self.temp.name).resolve()
        self.user_home = self.root / "isolated user"
        self.user_home.mkdir()
        self.environment = mock.patch.dict(os.environ, {"HOME": str(self.user_home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.home = self.root / "原应用数据 空格"
        self.config = self.root / "固定配置 中文"
        self.paths = AppPaths(self.home, config_dir=self.config)
        run = self.paths.runs / "完成任务"
        (run / "workspace/Result").mkdir(parents=True)
        (run / "cache").mkdir()
        (run / "cache/temporary").write_text("rebuildable")
        self.result = run / "workspace/Result/数据.csv"
        self.result.write_bytes(b"name,value\nGFAP,123\n")
        self.job = {"id": run.name, "status": "completed", "results_dir": str(self.result.parent), "source": str(self.root / "原图.tif")}
        write_json(run / "job.json", self.job)
        write_json(run / "request.json", {"workspace": str(run / "workspace"), "provenance": {"source": self.job["source"], "old": str(run)}})
        (self.home / "components/fiji").mkdir(parents=True)
        (self.home / "components/fiji/launcher").write_bytes(b"fiji")
        (self.home / "components/fiji/current").symlink_to("launcher")
        (self.home / "packages").mkdir()
        (self.home / "packages/cached.tar.gz").write_bytes(b"archive")
        pref = self.user_home / "Library/Preferences" / (lifecycle.BUNDLE_ID + ".plist")
        pref.parent.mkdir(parents=True)
        pref.write_bytes(b"application preferences")
        (self.home / "settings.json").write_text('{"language":"zh"}')
        self.sentinels = [self.root / "原图.tif", self.root / "用户另存安装包.dmg", self.root / "外部导出.csv",
                          self.root / "Project Leap 2D Support/keep", self.root / "Other Fiji.app/keep"]
        for path in self.sentinels:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"outside must survive")
        self.bundle = self.root / "测试 Project Leap.app"
        self.executable = self.bundle / "Contents/MacOS/Project Leap"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_text("#!/bin/sh\nexit 0\n")
        with (self.bundle / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleIdentifier": lifecycle.BUNDLE_ID, "CFBundleExecutable": self.executable.name}, stream)

    def tearDown(self):
        self.temp.cleanup()

    def run_helper(self, plan):
        helper = self.root / ("helper-" + uuid.uuid4().hex)
        helper.mkdir()
        script = helper / "cleanup.sh"
        script.write_text(lifecycle._helper_script(plan, 2147483647, helper))
        subprocess.run(["/bin/sh", str(script)], check=True, capture_output=True)
        self.assertFalse(helper.exists())

    def assert_sentinels(self):
        for path in self.sentinels:
            self.assertEqual(path.read_bytes(), b"outside must survive")

    def test_explicit_data_uses_isolated_config_and_relaunch_follows_pointer(self):
        isolated = AppPaths(self.root / "另一数据")
        self.assertEqual(isolated.config_home.parent, self.root)
        target = self.root / "新位置 中文"
        old_lock = maintenance_lock_path(self.home)
        request = (self.paths.runs / "完成任务/request.json").read_bytes()
        result = storage.migrate_app_data(self.paths, target)
        self.assertEqual(result["status"], "migrated", result)
        reopened = AppPaths(config_dir=self.config)
        self.assertEqual(reopened.home, target)
        self.assertEqual(maintenance_lock_path(target), old_lock)
        self.assertFalse(self.home.exists())
        with self.assertRaises(ValueError):
            maintenance_lock_path(self.home)
        self.assertFalse(self.home.exists())
        self.assertEqual((reopened.runs / "完成任务/request.json").read_bytes(), request)
        self.assertEqual(reopened.resolve_managed_path(self.job["results_dir"]), target / "runs/完成任务/workspace/Result")
        self.assertEqual(reopened.resolve_managed_path(self.job["source"]), Path(self.job["source"]))
        self.assertTrue((target / "components/fiji/current").is_symlink())
        self.assert_sentinels()
        with self.assertRaises(ValueError):
            AppPaths(self.home, config_dir=self.config)
        self.assertFalse(self.home.exists())

    def test_data_root_symlink_is_rejected_before_adoption(self):
        external = self.root / "unowned empty folder"
        external.mkdir()
        alias = self.root / "数据快捷链接"
        alias.symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            AppPaths(alias)
        with self.assertRaises(ValueError):
            maintenance_lock_path(alias)
        self.assertEqual(list(external.iterdir()), [])

    def test_multiple_migrations_keep_original_record_paths_resolvable(self):
        first = self.root / "迁移一"
        second = self.root / "迁移二"
        self.assertEqual(storage.migrate_app_data(self.paths, first)["status"], "migrated")
        paths = AppPaths(config_dir=self.config)
        self.assertEqual(storage.migrate_app_data(paths, second)["status"], "migrated")
        paths = AppPaths(config_dir=self.config)
        self.assertEqual(paths.resolve_managed_path(self.result), second / self.result.relative_to(self.home))
        self.assertEqual(paths.resolve_managed_path(first / "runs/完成任务"), second / "runs/完成任务")

    def test_copy_failure_preserves_active_original_and_removes_partial_copy(self):
        target = self.root / "失败目标"
        def broken_copy(source, destination, **kwargs):
            destination.mkdir()
            (destination / "partial").write_bytes(b"partial")
            raise OSError("simulated full disk")
        with mock.patch.object(storage.shutil, "copytree", side_effect=broken_copy):
            result = storage.migrate_app_data(self.paths, target)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(self.result.exists())
        self.assertFalse(target.exists())
        self.assertEqual(AppPaths(config_dir=self.config).home, self.home)
        self.assertEqual(list(self.root.glob("*.migrating-*")), [])
        self.assert_sentinels()

    def test_corrupt_copy_and_activation_failure_leave_original_active(self):
        target = self.root / "校验失败"
        original_copy = storage.shutil.copytree
        def corrupt(source, destination, **kwargs):
            original_copy(source, destination, **kwargs)
            (destination / "runs/完成任务/workspace/Result/数据.csv").write_text("corrupt")
        with mock.patch.object(storage.shutil, "copytree", side_effect=corrupt):
            self.assertEqual(storage.migrate_app_data(self.paths, target)["status"], "failed")
        original_write = storage.write_json
        def fail_activation(path, value):
            if Path(path) == self.paths.settings_file:
                raise OSError("cannot activate")
            return original_write(path, value)
        with mock.patch.object(storage, "write_json", side_effect=fail_activation):
            result = storage.migrate_app_data(self.paths, target)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(target.exists())
        self.assertEqual(AppPaths(config_dir=self.config).home, self.home)
        self.assertEqual(self.result.read_bytes(), b"name,value\nGFAP,123\n")

    def test_uninstall_keep_results_and_remove_everything_modes(self):
        plan = lifecycle.prepare_uninstall(self.paths, True, executable=self.executable)
        self.run_helper(plan)
        self.assertTrue(self.result.exists())
        self.assertTrue((self.home / "application.json").exists())
        for path in (self.bundle, self.config, self.home / "components", self.home / "cache", self.home / "settings.json", self.home / "packages", self.home / "runs/完成任务/cache"):
            self.assertFalse(path.exists(), path)
        self.assertTrue((self.user_home / "Library/Preferences" / (lifecycle.BUNDLE_ID + ".plist")).exists())
        self.assert_sentinels()

    def test_keep_results_deletes_only_completed_cache_and_preserves_recovery_masks(self):
        recovery_masks = []
        records = {"cancelled": {"status": "cancelled"}, "failed": {"status": "failed"},
                   "running": {"status": "running"}, "interrupted": {"status": "interrupted"},
                   "unknown": {}, "malformed": "not json", "missing": None, "invalid-type": []}
        for name, record in records.items():
            run = self.paths.runs / name
            (run / "cache").mkdir(parents=True)
            mask = run / "cache/manual-edited-mask.tif"
            mask.write_bytes(b"manually edited recovery mask: " + name.encode())
            recovery_masks.append((mask, mask.read_bytes()))
            if record is not None:
                if isinstance(record, str):
                    (run / "job.json").write_text(record)
                else:
                    write_json(run / "job.json", record)
        plan = lifecycle.prepare_uninstall(self.paths, True, executable=self.executable)
        self.run_helper(plan)
        self.assertFalse((self.paths.runs / "完成任务/cache").exists())
        self.assertEqual(self.result.read_bytes(), b"name,value\nGFAP,123\n")
        for mask, expected in recovery_masks:
            self.assertEqual(mask.read_bytes(), expected)
        self.assert_sentinels()

    def test_uninstall_delete_all_preserves_external_sources_exports_and_other_apps(self):
        recovery = self.paths.runs / "failed/cache/manual-edited-mask.tif"
        recovery.parent.mkdir(parents=True)
        recovery.write_bytes(b"manual recovery")
        write_json(recovery.parent.parent / "job.json", {"status": "failed"})
        plan = lifecycle.prepare_uninstall(self.paths, False, executable=self.executable)
        self.run_helper(plan)
        for path in (self.bundle, self.home, self.config):
            self.assertFalse(path.exists())
        self.assert_sentinels()

    def test_only_default_config_owns_shared_bundle_preferences(self):
        default_config = self.user_home / "Library/Application Support/Project Leap Prototype D Settings"
        official = AppPaths(self.root / "正式应用自选数据", config_dir=default_config)
        self.assertFalse(official.is_isolated)
        self.assertTrue(self.paths.is_isolated)
        preference = self.user_home / "Library/Preferences" / (lifecycle.BUNDLE_ID + ".plist")
        isolated_plan = lifecycle.prepare_uninstall(self.paths, False, executable=self.executable)
        self.assertNotIn(str(preference), [item["path"] for item in isolated_plan["targets"]])
        official_plan = lifecycle.prepare_uninstall(official, False, executable=self.executable)
        self.assertIn(str(preference), [item["path"] for item in official_plan["targets"]])
        self.run_helper(official_plan)
        self.assertFalse(preference.exists())
        self.assertTrue(self.result.exists())
        self.assert_sentinels()

    def test_uninstall_refuses_other_bundle_and_changed_target_before_any_deletion(self):
        with self.assertRaises(ValueError):
            lifecycle.prepare_uninstall(self.paths, False, executable=sys.executable)
        plan = lifecycle.prepare_uninstall(self.paths, False, executable=self.executable)
        moved = self.home.with_name("已移开")
        self.home.rename(moved)
        self.home.mkdir()
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_helper(plan)
        self.assertTrue(self.bundle.exists())
        self.assertTrue(moved.exists())
        self.assert_sentinels()

    def test_external_symlink_blocks_migration_and_cannot_delete_external_target(self):
        (self.home / "outside").symlink_to(self.sentinels[0])
        result = storage.migrate_app_data(self.paths, self.root / "拒绝外链")
        self.assertEqual(result["status"], "failed")
        plan = lifecycle.prepare_uninstall(self.paths, False, executable=self.executable)
        self.run_helper(plan)
        self.assert_sentinels()

    def test_cross_process_lock_blocks_migration_and_uninstall(self):
        lock = maintenance_lock_path(self.home)
        child = subprocess.Popen([sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_SH); print('ready',flush=True); sys.stdin.read()", str(lock)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            self.assertEqual(storage.migrate_app_data(self.paths, self.root / "locked")["status"], "blocked")
            self.assertEqual(lifecycle.schedule_uninstall(self.paths, False, executable=self.executable)["status"], "blocked")
        finally:
            child.communicate("")
        self.assertTrue(self.bundle.exists())
        self.assert_sentinels()

    def test_legacy_worker_shared_lock_blocks_uninstall(self):
        legacy = self.home / "components/project-leap-2d/component.lock"
        legacy.parent.mkdir(parents=True)
        child = subprocess.Popen([sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_SH); print('ready',flush=True); sys.stdin.read()", str(legacy)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            result = lifecycle.schedule_uninstall(self.paths, False, executable=self.executable)
            self.assertEqual(result["status"], "blocked", result)
            self.assertTrue(self.bundle.exists())
            self.assertTrue(self.result.exists())
        finally:
            child.communicate("")
        self.assert_sentinels()

    def test_deferred_helper_waits_holds_lock_cleans_itself_and_keeps_results(self):
        legacy = self.home / "components/project-leap-2d/component.lock"
        legacy.parent.mkdir(parents=True)
        legacy.touch()
        waiter = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        workers = []
        real_popen = subprocess.Popen
        def launch(*args, **kwargs):
            worker = real_popen(*args, **kwargs)
            workers.append(worker)
            return worker
        try:
            with mock.patch.object(lifecycle.subprocess, "Popen", side_effect=launch):
                result = lifecycle.schedule_uninstall(self.paths, True, executable=self.executable, parent_pid=waiter.pid)
            self.assertEqual(result["status"], "scheduled", result)
            self.assertTrue(self.bundle.exists())
            with maintenance_lock_path(self.home).open("a") as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with legacy.open("a") as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            waiter.communicate(b"")
            helper = Path(result["failure_log"]).parent
            for _ in range(100):
                if not helper.exists():
                    break
                time.sleep(0.05)
            self.assertFalse(helper.exists(), result)
            self.assertEqual(workers[0].wait(timeout=5), 0)
            self.assertFalse(self.bundle.exists())
            self.assertTrue(self.result.exists())
            self.assertFalse(self.config.exists())
            self.assert_sentinels()
        finally:
            if waiter.poll() is None:
                waiter.communicate(b"")


if __name__ == "__main__":
    unittest.main()
