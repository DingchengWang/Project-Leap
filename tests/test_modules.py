"""Catalog, dependency and deletion invariants using tiny offline packages."""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from leap_desktop import maintenance as m, modules


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Leap module invariants ")
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.catalog = [dict(modules.CATALOG[0]), {
            "id": "fixture-second", "name": "Fixture module", "version": "1.0.1",
            "entrypoint": "fixture.adapter", "input_page": "fixture", "github_url": "",
            "import_module": "fixture.importer", "needs_fiji": True}]
        self.patch = mock.patch.object(modules, "CATALOG", tuple(self.catalog))
        self.patch.start()
        self.bundle = self.root / "packages"
        self.bundle.mkdir()
        packages = {}
        for module in self.catalog:
            seed = self.bundle / module["id"]
            for relative in ("python/bin", "site-packages", "Models", "engine"):
                (seed / relative).mkdir(parents=True, exist_ok=True)
            (seed / "python/bin/python").write_text("#!/bin/sh\nexit 0\n")
            (seed / "python/bin/python").chmod(0o755)
            (seed / "Models/model").write_bytes(b"model")
            (seed / "engine/adapter.py").write_text("pass\n")
            (seed / "runtime.json").write_text(json.dumps({"schema_version": 2,
                "component_id": module["id"], "component_version": module["version"],
                "python": "python/bin/python", "site_packages": "site-packages", "models": "Models",
                "engine": "engine", "fiji_component": "fiji"}))
            m.build_manifest(seed)
            packages[module["id"]] = {"path": module["id"], "version": module["version"]}
        fiji = self.bundle / "fiji"
        (fiji / "Fiji").mkdir(parents=True)
        (fiji / "Fiji/launcher").write_text("#!/bin/sh\nexit 0\n")
        (fiji / "Fiji/launcher").chmod(0o755)
        (fiji / "runtime.json").write_text(json.dumps({"schema_version": 2, "component_id": "fiji",
            "component_version": "20260718.0417", "fiji_launcher": "Fiji/launcher"}))
        m.build_manifest(fiji)
        packages["fiji"] = {"path": "fiji"}
        (self.bundle / "catalog.json").write_text(json.dumps({"schema_version": 1, "packages": packages}))

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def install(self, module_id=m.COMPONENT_ID):
        result = m.install_component(self.home, self.bundle, module_id=module_id)
        self.assertEqual(result["status"], "ready", result)
        return result

    def test_empty_catalog_view_is_read_only_and_does_not_invent_installs(self):
        statuses = m.list_statuses(self.home)
        self.assertEqual(len(statuses), 2)
        self.assertFalse(any(item["installed"] for item in statuses))
        self.assertFalse(self.home.exists())
        result = modules.get_module()
        result["name"] = "changed"
        self.assertEqual(modules.get_module()["name"], "星形胶质细胞荧光强度分析")

    def test_every_install_permutation_and_repair_preserve_catalog_display_order(self):
        # Exhaust both orderings of the bounded two-module domain; no random
        # generator is needed to check this entire small state space.
        original_home = self.home
        ids = [module["id"] for module in self.catalog]
        for index, order in enumerate(itertools.permutations(ids)):
            with self.subTest(order=order):
                self.home = original_home.with_name("permutation-" + str(index))
                for module_id in order:
                    self.install(module_id)
                statuses = m.list_statuses(self.home)
                self.assertEqual([item["id"] for item in statuses], ids)
                sequences = [item["install_seq"] for item in statuses]
                result = m.repair_component(self.home, self.bundle, module_id=order[0])
                self.assertEqual(result["status"], "intact", result)
                self.assertEqual([item["install_seq"] for item in m.list_statuses(self.home)], sequences)
        self.home = original_home

    def test_damaged_module_remains_installed_and_keeps_fiji_dependency(self):
        self.install()
        second = self.install("fixture-second")
        Path(second["runtime_descriptor"]["models"]).joinpath("model").write_bytes(b"bad")
        status = m.component_status(self.home, module_id="fixture-second")
        self.assertTrue(status["installed"])
        self.assertEqual(status["status"], "damaged")
        removed = m.remove_component(self.home)
        self.assertFalse(removed["offer_remove_fiji"])
        self.assertEqual(removed["remaining_fiji_users"], ["fixture-second"])
        self.assertEqual(m.remove_fiji(self.home)["status"], "blocked")

    def test_installed_module_without_fiji_does_not_prevent_shared_removal(self):
        self.catalog[1]["needs_fiji"] = False
        with mock.patch.object(modules, "CATALOG", tuple(self.catalog)):
            self.install()
            second = self.install("fixture-second")
            self.assertNotIn("fiji_launcher", second["runtime_descriptor"])
            result = m.remove_component(self.home)
            self.assertTrue(result["offer_remove_fiji"])
            self.assertEqual(result["remaining_fiji_users"], [])
            self.assertEqual(m.remove_fiji(self.home)["status"], "removed")
            self.assertEqual(m.component_status(self.home, module_id="fixture-second")["status"], "ready")

    def test_complete_module_deletion_preserves_runs_and_shared_fiji(self):
        installed = self.install()
        root = self.home / "components" / m.COMPONENT_ID
        (root / "generations/staging-abandoned").mkdir()
        cache = self.home / "cache/modules" / m.COMPONENT_ID
        cache.mkdir(parents=True)
        (cache / "thumbnail.png").write_bytes(b"cache")
        result = self.home / "runs/run-one/result.csv"
        result.parent.mkdir(parents=True)
        result.write_bytes(b"unchanged historical result")
        lock = m.component_lock_path(self.home)
        inode = lock.stat().st_ino
        response = m.remove_component(self.home)
        self.assertEqual(response["status"], "removed", response)
        self.assertTrue(response["offer_remove_fiji"])
        self.assertFalse(root.exists())
        self.assertFalse(cache.exists())
        self.assertEqual(result.read_bytes(), b"unchanged historical result")
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertTrue(Path(installed["runtime_descriptor"]["fiji_launcher"]).exists())
        self.assertEqual(m.remove_fiji(self.home)["status"], "removed")
        self.assertFalse((self.home / "components/fiji").exists())
        self.assertEqual(lock.stat().st_ino, inode)

    def test_removal_while_worker_holds_lock_is_blocked(self):
        self.install()
        with m.component_use_lock(self.home):
            self.assertEqual(m.remove_component(self.home)["status"], "blocked")
            self.assertEqual(m.remove_fiji(self.home)["status"], "blocked")
        self.assertTrue(m.component_status(self.home)["installed"])

    def test_reinstall_records_later_sequence_but_preserves_catalog_order(self):
        first = self.install()
        self.install("fixture-second")
        m.remove_component(self.home)
        self.assertEqual(m.install_component(self.home)["status"], "unconfigured")
        self.assertFalse(m.component_status(self.home)["installed"])
        again = self.install()
        self.assertGreater(again["install_seq"], first["install_seq"])
        self.assertEqual([item["id"] for item in m.list_statuses(self.home)], [m.COMPONENT_ID, "fixture-second"])

    def test_missing_or_malformed_pointer_keeps_installed_record_and_is_repairable(self):
        self.install()
        pointer = self.home / "components" / m.COMPONENT_ID / "current.json"
        for content in (None, "[]", "not json"):
            with self.subTest(content=content):
                if content is None:
                    pointer.unlink()
                else:
                    pointer.write_text(content)
                status = m.component_status(self.home)
                self.assertTrue(status["installed"])
                self.assertEqual(status["status"], "damaged")
                self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "ready")

    def test_symlinked_cache_parent_rejects_before_module_deletion(self):
        self.install()
        outside = self.root / "outside"
        outside.mkdir()
        (self.home / "cache").mkdir()
        (self.home / "cache/modules").symlink_to(outside, target_is_directory=True)
        self.assertEqual(m.remove_component(self.home)["status"], "failed")
        self.assertTrue(m.component_status(self.home)["installed"])

    def test_invalid_split_runtime_json_does_not_prevent_deletion(self):
        installed = self.install()
        Path(installed["runtime_descriptor"]["root"]).joinpath("runtime.json").write_text("invalid")
        self.assertEqual(m.component_status(self.home)["status"], "damaged")
        result = m.remove_component(self.home)
        self.assertEqual(result["status"], "removed", result)
        self.assertTrue(result["offer_remove_fiji"])

    def test_catalog_path_escape_and_wrong_package_identity_are_rejected(self):
        catalog = self.bundle / "catalog.json"
        value = json.loads(catalog.read_text())
        value["packages"][m.COMPONENT_ID]["path"] = "../escape"
        catalog.write_text(json.dumps(value))
        self.assertEqual(m.install_component(self.home, self.bundle)["status"], "failed")
        value["packages"][m.COMPONENT_ID]["path"] = "fixture-second"
        catalog.write_text(json.dumps(value))
        self.assertEqual(m.install_component(self.home, self.bundle)["status"], "failed")

    def make_legacy_installation(self):
        self.install()
        root = self.home / "components" / m.COMPONENT_ID
        state, generation = m._current(root)
        import shutil
        shutil.copytree(self.bundle / "fiji/Fiji", generation / "Fiji")
        descriptor = json.loads((generation / "runtime.json").read_text())
        descriptor.update(schema_version=1, fiji_launcher="Fiji/launcher")
        descriptor.pop("engine")
        descriptor.pop("fiji_component")
        (generation / "runtime.json").write_text(json.dumps(descriptor))
        m.build_manifest(generation)
        state["manifest_sha256"] = m._digest(generation / m.MANIFEST_NAME)
        m._atomic_json(root / "current.json", state)
        m._atomic_json(root / "generations" / (state["generation"] + ".receipt.json"), m._inventory(generation, hashes=False))
        shutil.rmtree(self.home / "components/fiji")
        return generation

    def test_legacy_generation_requires_repair_and_preserves_fiji_when_removed(self):
        self.make_legacy_installation()
        status = m.component_status(self.home)
        self.assertTrue(status["installed"])
        self.assertEqual(status["status"], "damaged")
        self.assertIn("独立分析程序", status["message"])
        response = m.remove_component(self.home)
        self.assertEqual(response["status"], "removed", response)
        self.assertTrue(response["offer_remove_fiji"])
        self.assertEqual(m.fiji_status(self.home)["status"], "ready")

    def test_broken_legacy_pointer_preserves_module_and_fiji_until_repaired(self):
        original_home = self.home
        for index, content in enumerate((None, "[]", "not json", "missing-generation")):
            with self.subTest(content=content):
                self.home = original_home.with_name("legacy-pointer-" + str(index))
                generation = self.make_legacy_installation()
                root = self.home / "components" / m.COMPONENT_ID
                pointer = root / "current.json"
                if content == "missing-generation":
                    state = json.loads(pointer.read_text())
                    state["generation"] = "missing-generation"
                    pointer.write_text(json.dumps(state))
                elif content is None:
                    pointer.unlink()
                else:
                    pointer.write_text(content)
                before = m._inventory(root, hashes=True)
                response = m.remove_component(self.home)
                self.assertEqual(response["status"], "failed", response)
                self.assertIn("请先修复当前模块", response["message"])
                self.assertEqual(m._inventory(root, hashes=True), before)
                self.assertTrue((generation / "Fiji/launcher").is_file())
                self.assertFalse(m.fiji_status(self.home)["installed"])
                self.assertEqual(m.repair_component(self.home, self.bundle)["status"], "ready")
                removed = m.remove_component(self.home)
                self.assertEqual(removed["status"], "removed", removed)
                self.assertTrue(removed["offer_remove_fiji"])
        self.home = original_home

    def test_broken_split_pointer_without_legacy_fiji_does_not_prevent_deletion(self):
        original_home = self.home
        for index, content in enumerate((None, "[]", "not json", "missing-generation")):
            with self.subTest(content=content):
                self.home = original_home.with_name("split-pointer-" + str(index))
                installed = self.install()
                root = self.home / "components" / m.COMPONENT_ID
                pointer = root / "current.json"
                if content == "missing-generation":
                    state = json.loads(pointer.read_text())
                    state["generation"] = "missing-generation"
                    pointer.write_text(json.dumps(state))
                elif content is None:
                    pointer.unlink()
                else:
                    pointer.write_text(content)
                # Exercise the guard even when the separate Fiji is absent.
                m._remove_owned_tree(self.home / "components/fiji")
                self.assertFalse((Path(installed["runtime_descriptor"]["root"]) / "Fiji").exists())
                response = m.remove_component(self.home)
                self.assertEqual(response["status"], "removed", response)
                self.assertFalse(root.exists())
                self.assertFalse(response["offer_remove_fiji"])
        self.home = original_home

    def test_bad_legacy_fiji_is_retained_as_damaged_then_can_be_removed(self):
        generation = self.make_legacy_installation()
        (generation / "Fiji/launcher").write_text("damaged")
        result = m.remove_component(self.home)
        self.assertEqual(result["status"], "removed", result)
        self.assertTrue(result["offer_remove_fiji"])
        fiji = m.fiji_status(self.home)
        self.assertTrue(fiji["installed"])
        self.assertEqual(fiji["status"], "damaged")
        self.assertIsNone(fiji["runtime_descriptor"])
        retained = list((self.home / "components/fiji/generations").glob("retained-*/Fiji/launcher"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_text(), "damaged")
        self.assertEqual(m.remove_fiji(self.home)["status"], "removed")

    def test_failed_legacy_fiji_copy_keeps_module_installed(self):
        generation = self.make_legacy_installation()
        (generation / "Fiji/launcher").write_text("damaged")
        with mock.patch.object(m.shutil, "copytree", side_effect=OSError("injected unreadable Fiji")):
            result = m.remove_component(self.home)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("injected unreadable Fiji", result["message"])
        self.assertTrue(m.component_status(self.home)["installed"])


if __name__ == "__main__":
    unittest.main()
