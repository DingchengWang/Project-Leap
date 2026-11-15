"""All maintenance fault injections use disposable roots, never installed apps."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from leap_desktop import maintenance as m


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.host_build_patch = mock.patch("leap_desktop.version.application_build", return_value="1")
        self.host_build_patch.start()
        self.addCleanup(self.host_build_patch.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="Leap 维护测试 ")
        self.root = Path(self.temp.name).resolve()
        self.seed = self.root / "seed"
        self.home = self.root / "app home 中文"
        for path in ("python/bin", "site-packages", "engine", "Models"):
            (self.seed / path).mkdir(parents=True, exist_ok=True)
        for name in ("python/bin/python3",):
            (self.seed / name).write_text("#!/bin/sh\nexit 0\n")
            (self.seed / name).chmod(0o755)
        (self.seed / "python/bin/python").symlink_to("python3")
        (self.seed / "site-packages/module.py").write_text("VALUE = 1\n")
        (self.seed / "Models/model").write_bytes(b"unaltered model\x00")
        self.descriptor = {"schema_version": 2, "component_id": m.COMPONENT_ID,
                           "component_version": "1.0.1", "python": "python/bin/python",
                           "site_packages": "site-packages", "fiji_component": "fiji", "engine": "engine", "models": "Models"}
        (self.seed / "runtime.json").write_text(json.dumps(self.descriptor))
        m.build_manifest(self.seed)
        self.fiji = self.root / "fiji"
        (self.fiji / "Fiji").mkdir(parents=True)
        (self.fiji / "Fiji/launcher").write_text("#!/bin/sh\nexit 0\n")
        (self.fiji / "Fiji/launcher").chmod(0o755)
        (self.fiji / "runtime.json").write_text(json.dumps({"schema_version": 2, "component_id": "fiji",
            "component_version": "20260718.0417", "fiji_launcher": "Fiji/launcher"}))
        m.build_manifest(self.fiji)
        self.bundle = self.root / "packages"
        self.bundle.mkdir()
        self.seed.rename(self.bundle / "module")
        self.seed = self.bundle / "module"
        self.fiji.rename(self.bundle / "fiji")
        self.fiji = self.bundle / "fiji"
        self.package_catalog = {"schema_version": 1, "packages": {
            m.COMPONENT_ID: {"path": "module"}, "fiji": {"path": "fiji"}}}
        (self.bundle / "catalog.json").write_text(json.dumps(self.package_catalog))

    def tearDown(self):
        self.temp.cleanup()

    def install(self):
        result = m.install_component(self.home, self.bundle)
        self.assertEqual(result["status"], "ready", result)
        return Path(result["runtime_descriptor"]["root"])

    def damage_active(self):
        current = m._current(self.home / "components" / m.COMPONENT_ID)
        (current[1] / "Models/model").write_bytes(b"damaged active payload")

    def pointer(self):
        return (self.home / "components" / m.COMPONENT_ID / "current.json").read_bytes()

    def archive(self):
        archive = self.bundle / "seed.tar.gz"
        with tarfile.open(archive, "w:gz") as target:
            target.add(self.seed, arcname=".")
        return archive

    def test_install_resolves_paths_and_preserves_user_data(self):
        from leap_desktop.paths import ensure_app_home
        ensure_app_home(self.home)
        (self.home / "jobs").mkdir(parents=True)
        sentinel = self.home / "jobs/completed.json"
        sentinel.write_text("keep")
        progress = []
        result = m.install_component(self.home, self.bundle, lambda f, message: progress.append(f))
        self.assertEqual(result["status"], "ready", result)
        self.assertEqual(progress[-1], 1.0)
        for key in m.DESCRIPTOR_PATHS:
            self.assertTrue(Path(result["runtime_descriptor"][key]).is_relative_to(self.home))
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual(m.component_status(self.home)["status"], "ready")
        self.assertEqual(m.verify_seed(self.seed)["component_version"], "1.0.1")

    def test_status_without_install_does_not_create_files(self):
        self.assertEqual(m.component_status(self.home)["status"], "not_installed")
        self.assertFalse(self.home.exists())

    def test_archive_install_and_internal_symlink(self):
        archive = self.archive()
        self.package_catalog["packages"][m.COMPONENT_ID]["path"] = archive.name
        (self.bundle / "catalog.json").write_text(json.dumps(self.package_catalog))
        result = m.install_component(self.home, self.bundle)
        self.assertEqual(result["status"], "ready", result)
        self.assertTrue(Path(result["runtime_descriptor"]["python"]).is_symlink())

    def test_same_version_repair_activates_complete_new_generation(self):
        old = self.install()
        (old / "Models/model").write_bytes(b"broken")
        self.assertEqual(m.component_status(self.home)["status"], "damaged")
        result = m.repair_component(self.home, self.bundle)
        self.assertEqual(result["status"], "ready", result)
        repaired = Path(result["runtime_descriptor"]["root"])
        self.assertNotEqual(repaired, old)
        self.assertEqual((repaired / "Models/model").read_bytes(), b"unaltered model\x00")
        self.assertTrue(old.exists())

    def test_same_version_repair_replaces_incompatible_host_metadata_and_preserves_history(self):
        self.descriptor.update(host_api=1, minimum_host_build=2, platform="macos-arm64")
        (self.seed / "engine/worker.py").write_bytes(b"# retained scientific engine\n")
        (self.seed / "runtime.json").write_text(json.dumps(self.descriptor))
        m.build_manifest(self.seed)
        with mock.patch("leap_desktop.version.application_build", return_value="2"):
            old = self.install()
            previous_status = m.component_status(self.home)
        fiji_pointer = (self.home / "components/fiji/current.json").read_bytes()
        history = self.home / "runs/retained-analysis/analysis.json"
        history.parent.mkdir(parents=True)
        history_bytes = b'{"schema_version": 2, "retained_result": true}\n'
        history.write_bytes(history_bytes)
        self.assertEqual(m.component_status(self.home)["status"], "damaged")
        self.descriptor["minimum_host_build"] = 1
        (self.seed / "runtime.json").write_text(json.dumps(self.descriptor))
        m.build_manifest(self.seed)
        result = m.repair_component(self.home, self.bundle)
        self.assertEqual(result["status"], "ready", result)
        repaired = Path(result["runtime_descriptor"]["root"])
        self.assertNotEqual(repaired, old)
        self.assertEqual(result["component_version"], self.descriptor["component_version"])
        self.assertEqual(result["install_seq"], previous_status["install_seq"])
        self.assertEqual(result["runtime_descriptor"]["minimum_host_build"], 1)
        self.assertEqual((repaired / "engine/worker.py").read_bytes(), (old / "engine/worker.py").read_bytes())
        self.assertEqual(history.read_bytes(), history_bytes)
        self.assertEqual((self.home / "components/fiji/current.json").read_bytes(), fiji_pointer)
        self.assertTrue(old.exists())

    def test_incompatible_repair_source_preserves_previous_generation(self):
        self.descriptor.update(host_api=1, minimum_host_build=2, platform="macos-arm64")
        (self.seed / "runtime.json").write_text(json.dumps(self.descriptor))
        m.build_manifest(self.seed)
        with mock.patch("leap_desktop.version.application_build", return_value="2"):
            old = self.install()
        pointer = self.pointer()
        result = m.repair_component(self.home, self.bundle)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("2", result["message"])
        self.assertEqual(self.pointer(), pointer)
        self.assertTrue(old.exists())

    def test_bad_seed_keeps_previous_pointer_and_environment(self):
        old = self.install()
        self.damage_active()
        pointer = self.pointer()
        (self.seed / "Models/model").write_bytes(b"corrupt")
        result = m.repair_component(self.home, self.bundle)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.pointer(), pointer)
        self.assertTrue(old.exists())
        self.assertEqual(m.component_status(self.home)["status"], "damaged")

    def test_wrong_version_is_not_repair(self):
        self.install()
        self.damage_active()
        pointer = self.pointer()
        self.descriptor["component_version"] = "1.0.2"
        (self.seed / "runtime.json").write_text(json.dumps(self.descriptor))
        m.build_manifest(self.seed)
        self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "failed")
        self.assertEqual(self.pointer(), pointer)

    def test_shared_analysis_locks_reject_maintenance(self):
        self.install()
        pointer = self.pointer()
        with m.component_use_lock(self.home), m.component_use_lock(self.home):
            self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "blocked")
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "intact")

    def test_worker_lock_survives_release_of_gui_lock(self):
        self.install()
        command = ("import fcntl,os,sys; "
                   "fd=os.open(sys.argv[1],os.O_RDWR); fcntl.flock(fd,fcntl.LOCK_SH); "
                   "print('locked',flush=True); sys.stdin.read()")
        with m.component_use_lock(self.home):
            worker = subprocess.Popen([sys.executable, "-c", command, str(m.component_lock_path(self.home))],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            self.assertEqual(worker.stdout.readline().strip(), "locked")
        try:
            self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "blocked")
            with self.assertRaises(m.ComponentBusy):
                with m.component_maintenance_lock(self.home):
                    self.fail("Orphan worker's shared lock must block application updates")
        finally:
            worker.communicate("", timeout=10)
        self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "intact")

    def test_update_session_exclusive_lock_blocks_analysis_and_repair(self):
        self.install()
        with m.component_maintenance_lock(self.home):
            with self.assertRaises(m.ComponentBusy):
                with m.component_use_lock(self.home):
                    self.fail("Analysis cannot start during an application update")
            self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "blocked")
        with m.component_use_lock(self.home):
            pass

    def test_maintenance_fd_context_does_not_create_module_directories(self):
        with m.maintenance_lock_fds(self.home) as descriptors:
            self.assertEqual(len(descriptors), 1)
            self.assertFalse((self.home / "components").exists())

    def test_inherited_maintenance_fds_keep_fixed_and_legacy_locks_after_parent_release(self):
        self.install()
        legacy = self.home / "components" / m.COMPONENT_ID / "component.lock"
        legacy.touch()
        with m.maintenance_lock_fds(self.home) as descriptors:
            self.assertEqual(len(descriptors), 2)
            child = subprocess.Popen([sys.executable, "-c", "import sys; print('inherited',flush=True); sys.stdin.read()"],
                pass_fds=descriptors, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            self.assertEqual(child.stdout.readline().strip(), "inherited")
        try:
            self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "blocked")
            with legacy.open("r+") as stream:
                with self.assertRaises(BlockingIOError):
                    m.fcntl.flock(stream.fileno(), m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)
        finally:
            child.communicate("", timeout=10)
        self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "intact")

    def test_disk_copy_failure_keeps_previous_environment(self):
        self.install()
        self.damage_active()
        pointer = self.pointer()
        with mock.patch.object(m.shutil, "copytree", side_effect=OSError(28, "No space left")):
            result = m.repair_component(self.home, self.bundle)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual(m.component_status(self.home)["status"], "damaged")

    def test_activation_failure_keeps_previous_environment(self):
        self.install()
        self.damage_active()
        pointer = self.pointer()
        real = m.os.replace
        def fail_pointer(source, destination):
            if Path(destination).name == "current.json":
                raise PermissionError("injected activation failure")
            return real(source, destination)
        with mock.patch.object(m.os, "replace", side_effect=fail_pointer):
            result = m.repair_component(self.home, self.bundle)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual(m.component_status(self.home)["status"], "damaged")

    def test_current_pointer_wins_over_stale_backup_version_source_and_sequence(self):
        self.install()
        root = self.home / "components" / m.COMPONENT_ID
        current = json.loads((root / "current.json").read_text())
        backup = {**current, "component_version": "9.9", "source": "wrong-source", "install_seq": 999}
        (root / "installation.json").write_text(json.dumps(backup))
        status = m.component_status(self.home)
        self.assertEqual(status["component_version"], "1.0.1")
        self.assertEqual(status["source"], current["source"])
        result = m.repair_component(self.home, self.bundle)
        self.assertEqual(result["status"], "intact", result)
        self.assertEqual(result["install_seq"], current["install_seq"])

    def test_initial_receipt_write_failure_removes_generation_and_new_fiji(self):
        real = m._atomic_json
        def fail_module_receipt(path, value, **kwargs):
            if path.name.endswith(".receipt.json") and m.COMPONENT_ID in path.parts:
                raise OSError("injected module receipt failure")
            return real(path, value, **kwargs)
        with mock.patch.object(m, "_atomic_json", side_effect=fail_module_receipt):
            result = m.install_component(self.home, self.bundle)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("injected module receipt failure", result["message"])
        generations = self.home / "components" / m.COMPONENT_ID / "generations"
        self.assertEqual(list(generations.iterdir()), [])
        self.assertFalse(m.component_status(self.home)["installed"])
        self.assertFalse((self.home / "components/fiji").exists())

    def test_module_activation_failure_rolls_back_only_newly_installed_fiji(self):
        real = m.os.replace
        def fail_module_pointer(source, destination):
            if Path(destination).name == "current.json" and m.COMPONENT_ID in Path(destination).parts:
                raise OSError("injected module pointer failure")
            return real(source, destination)
        with mock.patch.object(m.os, "replace", side_effect=fail_module_pointer):
            result = m.install_component(self.home, self.bundle)
        self.assertEqual(result["status"], "failed", result)
        generations = self.home / "components" / m.COMPONENT_ID / "generations"
        self.assertEqual(list(generations.iterdir()), [])
        self.assertFalse((self.home / "components/fiji").exists())
        self.install()
        fiji_pointer = (self.home / "components/fiji/current.json").read_bytes()
        m.remove_component(self.home)
        with mock.patch.object(m.os, "replace", side_effect=fail_module_pointer):
            result = m.install_component(self.home, self.bundle)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual((self.home / "components/fiji/current.json").read_bytes(), fiji_pointer)
        self.assertEqual(m.fiji_status(self.home)["status"], "ready")

    def test_repair_receipt_failure_removes_only_uncommitted_generation(self):
        active = self.install()
        self.damage_active()
        root = self.home / "components" / m.COMPONENT_ID
        before = set((root / "generations").iterdir())
        pointer = self.pointer()
        real = m._atomic_json
        def fail_module_receipt(path, value, **kwargs):
            if path.name.endswith(".receipt.json") and m.COMPONENT_ID in path.parts:
                raise OSError("injected repair receipt failure")
            return real(path, value, **kwargs)
        with mock.patch.object(m, "_atomic_json", side_effect=fail_module_receipt):
            result = m.repair_component(self.home, self.bundle)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(set((root / "generations").iterdir()), before)
        self.assertEqual(self.pointer(), pointer)
        self.assertTrue(active.exists())
        self.assertEqual(m.component_status(self.home)["status"], "damaged")

    def test_post_commit_sync_error_reports_actual_activated_state(self):
        self.install()
        self.damage_active()
        before = self.pointer()
        real = m.os.fsync
        injected = []
        def fail_committed_directory(fd):
            if not injected and self.pointer() != before:
                injected.append(True)
                raise OSError("injected directory synchronization failure")
            return real(fd)
        with mock.patch.object(m.os, "fsync", side_effect=fail_committed_directory):
            result = m.repair_component(self.home, self.bundle)
        self.assertEqual(result["status"], "ready")
        self.assertIn("durability_warning", result)
        self.assertNotEqual(self.pointer(), before)
        self.assertEqual(m.component_status(self.home)["status"], "ready")

    def test_interruption_before_commit_does_not_change_pointer(self):
        self.install()
        self.damage_active()
        pointer = self.pointer()
        def interrupt(fraction, message):
            if fraction >= 0.70:
                raise OSError("interrupted")
        result = m.repair_component(self.home, self.bundle, interrupt)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.pointer(), pointer)
        self.assertFalse(list((self.home / "components" / m.COMPONENT_ID / "generations").glob("staging-*")))

    def test_unlisted_file_invalidates_seed(self):
        (self.seed / "extra.py").write_text("x")
        self.assertEqual(m.install_component(self.home, self.bundle)["status"], "failed")

    def test_external_symlink_and_descriptor_traversal_rejected(self):
        (self.seed / "outside").symlink_to(self.root / "external")
        self.assertEqual(m.install_component(self.home, self.bundle)["status"], "failed")
        (self.seed / "outside").unlink()
        self.descriptor["python"] = "../python"
        (self.seed / "runtime.json").write_text(json.dumps(self.descriptor))
        with self.assertRaises(m.ComponentError):
            m.build_manifest(self.seed)

    def test_managed_directory_symlink_rejected(self):
        self.home.mkdir()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.home / "components").symlink_to(elsewhere, target_is_directory=True)
        self.assertEqual(m.install_component(self.home, self.bundle)["status"], "failed")
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_unowned_nonempty_root_is_never_adopted(self):
        self.home.mkdir()
        sentinel = self.home / "existing-user-file"
        sentinel.write_bytes(b"unchanged")
        self.assertEqual(m.install_component(self.home, self.bundle)["status"], "failed")
        self.assertEqual(sentinel.read_bytes(), b"unchanged")
        self.assertFalse((self.home / "components").exists())

    def test_malicious_archive_path_rejected_without_writes_outside(self):
        archive = self.archive()
        with tarfile.open(archive, "w:gz") as target:
            member = tarfile.TarInfo("../../escape")
            member.size = 3
            target.addfile(member, io.BytesIO(b"bad"))
        self.assertEqual(m.install_component(self.home, archive)["status"], "failed")
        self.assertFalse((self.root / "escape").exists())

    def test_archive_byte_corruption_is_never_activated(self):
        self.install()
        self.damage_active()
        pointer = self.pointer()
        (self.seed / "Models/model").write_bytes(b"same-size-bad!!\x00")
        result = m.repair_component(self.home, self.archive())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.pointer(), pointer)

    def test_ready_install_is_idempotent(self):
        self.install()
        pointer = self.pointer()
        self.assertEqual(m.install_component(self.home, self.bundle)["status"], "ready")
        self.assertEqual(self.pointer(), pointer)

    def test_missing_install_cannot_be_repaired(self):
        self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "failed")

    def test_explicit_legacy_host_requirements_are_enforced(self):
        self.descriptor.update(host_api=2, minimum_host_build=1, platform="macos-arm64")
        (self.seed / "runtime.json").write_text(json.dumps(self.descriptor))
        with self.assertRaises(ValueError):
            m.build_manifest(self.seed)
        self.descriptor.update(host_api=1, minimum_host_build=2)
        (self.seed / "runtime.json").write_text(json.dumps(self.descriptor))
        m.build_manifest(self.seed)
        result = m.install_component(self.home, self.bundle)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("2", result["message"])
        self.assertFalse((self.home / "components" / m.COMPONENT_ID / "current.json").exists())

    def test_invalid_manifest_structure_is_an_explicit_failure(self):
        (self.seed / m.MANIFEST_NAME).write_text("[]")
        self.assertEqual(m.install_component(self.home, self.bundle)["status"], "failed")

    def test_truncated_archive_never_activates(self):
        archive = self.archive()
        archive.write_bytes(archive.read_bytes()[:100])
        self.assertEqual(m.install_component(self.home, archive)["status"], "failed")
        self.assertFalse((self.home / "components" / m.COMPONENT_ID / "current.json").exists())


if __name__ == "__main__":
    unittest.main()
