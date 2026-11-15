"""Shared model and genuine URL download checks, entirely in disposable roots."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager, nullcontext
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import itertools
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import test_release_catalog as signed_tests
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from leap_desktop import maintenance as m, modules
from leap_desktop.release_catalog import ReleaseConfiguration
from sign_component_catalog import signed_envelope


class SharedModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        signed_tests.SignedReleaseTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        signed_tests.SignedReleaseTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.host_build_patch = mock.patch("leap_desktop.version.application_build", return_value="1")
        self.host_build_patch.start()
        self.addCleanup(self.host_build_patch.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="Leap shared-model tests ")
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.bundle = self.root / "packages"
        self.bundle.mkdir()
        self.weights = b"fixture cpsam weights\x00"
        self.hash_patch = mock.patch.object(m, "MODEL_SHA256", hashlib.sha256(self.weights).hexdigest())
        self.hash_patch.start()
        self.catalog = {"schema_version": 1, "packages": {}}
        for module in modules.list_modules():
            seed = self.bundle / module["id"]
            for name in ("python/bin", "site-packages", "engine"):
                (seed / name).mkdir(parents=True)
            executable = seed / "python/bin/python"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            (seed / "engine/worker.py").write_text("pass\n")
            descriptor = {"schema_version": 3, "component_id": module["id"],
                "component_version": module["version"], "python": "python/bin/python",
                "site_packages": "site-packages", "engine": "engine", "model_component": m.model_dependency()}
            if module["needs_fiji"]:
                descriptor["fiji_component"] = m.FIJI_ID
            self.finish_seed(seed, descriptor)
        seed = self.bundle / m.MODEL_ID
        (seed / "Models").mkdir(parents=True)
        (seed / "Models/cpsam_v2").write_bytes(self.weights)
        self.finish_seed(seed, {"schema_version": 2, "component_id": m.MODEL_ID,
            "component_version": m.MODEL_VERSION, "models": "Models", "model_file": m.MODEL_FILENAME,
            "model_sha256": m.MODEL_SHA256})
        seed = self.bundle / m.FIJI_ID
        (seed / "Fiji").mkdir(parents=True)
        (seed / "Fiji/launcher").write_text("#!/bin/sh\nexit 0\n")
        (seed / "Fiji/launcher").chmod(0o755)
        self.finish_seed(seed, {"schema_version": 2, "component_id": m.FIJI_ID,
            "component_version": "20260718.0417", "fiji_launcher": "Fiji/launcher"})
        self.save_catalog()

    def tearDown(self):
        self.hash_patch.stop()
        self.temp.cleanup()

    def finish_seed(self, seed, descriptor):
        (seed / "runtime.json").write_text(json.dumps(descriptor))
        m.build_manifest(seed, descriptor["component_id"])
        self.catalog["packages"][descriptor["component_id"]] = {"path": seed.name}

    def save_catalog(self):
        (self.bundle / "catalog.json").write_text(json.dumps(self.catalog))

    def install(self, module_id, source=None):
        result = (m.install_component(self.home, module_id=module_id, configuration=source) if isinstance(source, ReleaseConfiguration)
                  else m.install_component(self.home, source or self.bundle, module_id=module_id))
        self.assertEqual(result["status"], "ready", result)
        return result

    def archive_sources(self):
        for identity, record in self.catalog["packages"].items():
            archive = self.bundle / (identity + ".tar.gz")
            with tarfile.open(archive, "w:gz") as stream:
                stream.add(self.bundle / identity, arcname=".")
            record.update(path=archive.name, bytes=archive.stat().st_size, sha256=m._digest(archive))
        self.save_catalog()

    @contextmanager
    def source_server(self):
        packages = []
        for identity, item in self.catalog["packages"].items():
            seed = self.bundle / identity
            descriptor = json.loads((seed / "runtime.json").read_text())
            dependencies = {}
            if identity not in m.SHARED_IDS:
                if descriptor.get("model_component"):
                    dependencies[m.MODEL_ID] = m.MODEL_VERSION
                if descriptor.get("fiji_component"):
                    dependencies[m.FIJI_ID] = "20260718.0417"
            compatibility = {"host_api": 1, "minimum_host_build": 1, "platform": "macos-arm64"} if identity not in m.SHARED_IDS else {}
            packages.append(dict(item, id=identity, version=descriptor["component_version"], **compatibility,
                                 manifest_sha256=m._digest(seed / m.MANIFEST_NAME), dependencies=dependencies))
        payload = {"schema_version": 2, "packages": packages, "latest": {item["id"]: item["version"] for item in packages}}
        (self.bundle / "signed.json").write_text(json.dumps(signed_envelope(payload, self.key, self.signer)))
        with signed_tests.SignedReleaseTests.server(self) as result:
            yield result

    def test_catalog_counting_is_independent_of_fiji(self):
        module = modules.get_module(m.COUNTING_MODULE_ID)
        self.assertEqual((module["version"], module["entrypoint"], module["import_module"], module["input_page"]),
                         ("1.0.0", "leap_cell_counting", "leap_cell_counting", "counting"))
        self.assertFalse(module["needs_fiji"])
        installed = self.install(m.COUNTING_MODULE_ID)
        self.assertNotIn("fiji_launcher", installed["runtime_descriptor"])
        self.assertFalse((self.home / "components/fiji").exists())

    def test_both_offline_install_orders_share_one_unchanged_generation(self):
        for index, order in enumerate(itertools.permutations((m.COMPONENT_ID, m.COUNTING_MODULE_ID))):
            with self.subTest(order=order):
                self.home = self.root / ("home-" + str(index))
                first = self.install(order[0])
                pointer = (self.home / "components/cellpose-model/current.json").read_bytes()
                second = self.install(order[1])
                self.assertEqual(first["runtime_descriptor"]["models"], second["runtime_descriptor"]["models"])
                self.assertEqual(first["runtime_descriptor"]["model_root"], second["runtime_descriptor"]["model_root"])
                self.assertEqual(Path(first["runtime_descriptor"]["models"]).parent,
                                 Path(first["runtime_descriptor"]["model_root"]))
                refreshed = m.component_status(self.home, module_id=order[1])
                self.assertEqual(refreshed["runtime_descriptor"]["model_root"], second["runtime_descriptor"]["model_root"])
                self.assertEqual(pointer, (self.home / "components/cellpose-model/current.json").read_bytes())
                self.assertEqual(len(list((self.home / "components").rglob("cpsam_v2"))), 1)
                for result in (first, second):
                    self.assertFalse((Path(result["runtime_descriptor"]["root"]) / "Models").exists())

    def test_both_url_install_orders_fetch_model_once(self):
        self.archive_sources()
        for index, order in enumerate(itertools.permutations((m.COMPONENT_ID, m.COUNTING_MODULE_ID))):
            with self.subTest(order=order), self.source_server() as (url, requests):
                self.home = self.root / ("remote-" + str(index))
                first = self.install(order[0], url)
                second = self.install(order[1], url)
                self.assertEqual(requests["/cellpose-model.tar.gz"], 1)
                self.assertEqual(first["runtime_descriptor"]["models"], second["runtime_descriptor"]["models"])
                self.assertFalse(list(self.home.glob(".package-download-*")))

    def make_legacy_module(self):
        seed = self.bundle / m.COMPONENT_ID
        descriptor = json.loads((seed / "runtime.json").read_text())
        descriptor.update(schema_version=2, models="Models")
        descriptor.pop("model_component")
        (seed / "Models").mkdir()
        (seed / "Models/cpsam_v2").write_bytes(self.weights)
        self.finish_seed(seed, descriptor)
        self.save_catalog()
        return self.install(m.COMPONENT_ID)

    def test_verified_managed_b_weights_prevent_remote_model_download(self):
        legacy = self.make_legacy_module()
        self.archive_sources()
        with self.source_server() as (url, requests):
            counting = self.install(m.COUNTING_MODULE_ID, url)
            self.assertEqual(requests["/cellpose-model.tar.gz"], 0)
        self.assertEqual(Path(counting["runtime_descriptor"]["models"]).joinpath("cpsam_v2").read_bytes(), self.weights)
        self.assertNotEqual(legacy["runtime_descriptor"]["models"], counting["runtime_descriptor"]["models"])
        self.assertEqual(m.component_status(self.home)["status"], "ready")

    def test_corrupted_legacy_weight_is_not_reused(self):
        legacy = self.make_legacy_module()
        Path(legacy["runtime_descriptor"]["models"]).joinpath("cpsam_v2").write_bytes(b"damaged")
        self.archive_sources()
        with self.source_server() as (url, requests):
            self.install(m.COUNTING_MODULE_ID, url)
            self.assertEqual(requests["/cellpose-model.tar.gz"], 1)

    def test_damaged_remaining_consumer_prevents_shared_removal(self):
        for corrupt_pointer in (False, True):
            with self.subTest(corrupt_pointer=corrupt_pointer):
                self.home = self.root / ("damaged-" + str(corrupt_pointer))
                self.install(m.COMPONENT_ID)
                counting = self.install(m.COUNTING_MODULE_ID)
                damaged = (self.home / "components/cell-counting/current.json" if corrupt_pointer
                           else Path(counting["runtime_descriptor"]["root"]) / "runtime.json")
                damaged.write_text("damaged")
                result = m.remove_component(self.home, m.COMPONENT_ID)
                self.assertEqual(result["remaining_model_users"], [m.COUNTING_MODULE_ID])
                self.assertTrue(result["model_retained_for_users"])
                self.assertFalse(result["offer_remove_model"])
                self.assertEqual(m.remove_model(self.home)["status"], "blocked")
                self.assertEqual(m.model_status(self.home)["status"], "ready")

    def test_last_consumer_keeps_model_and_results_until_explicit_removal(self):
        self.install(m.COUNTING_MODULE_ID)
        result_file = self.home / "runs/fixture/counting/results/Cell_Counts.csv"
        result_file.parent.mkdir(parents=True)
        result_file.write_text("count\n7\n")
        result = m.remove_component(self.home, m.COUNTING_MODULE_ID)
        self.assertTrue(result["offer_remove_model"])
        self.assertTrue(result["model_retained"])
        self.assertEqual(m.model_status(self.home)["status"], "ready")
        self.assertEqual(m.remove_model(self.home)["status"], "removed")
        self.assertEqual(result_file.read_text(), "count\n7\n")

    def test_legacy_last_consumer_weights_are_retained_before_deletion(self):
        self.make_legacy_module()
        removed = m.remove_component(self.home)
        self.assertTrue(removed["offer_remove_model"])
        self.assertEqual(m.model_status(self.home)["status"], "ready")

    def test_legacy_model_survives_missing_or_malformed_current_pointer(self):
        self.make_legacy_module()
        for index, content in enumerate((None, "[]", "not json")):
            with self.subTest(content=content):
                self.home = self.root / ("legacy-pointer-" + str(index))
                legacy = self.install(m.COMPONENT_ID)
                pointer = self.home / "components/project-leap-2d/current.json"
                if content is None:
                    pointer.unlink()
                else:
                    pointer.write_text(content)
                self.assertTrue(m.component_status(self.home)["installed"])
                result = m.remove_component(self.home)
                self.assertEqual(result["status"], "removed", result)
                self.assertTrue(result["offer_remove_model"])
                shared = m.model_status(self.home)
                self.assertEqual(shared["status"], "ready", shared)
                self.assertEqual(Path(shared["runtime_descriptor"]["models"]).joinpath("cpsam_v2").read_bytes(), self.weights)
                self.assertFalse(Path(legacy["runtime_descriptor"]["root"]).exists())

    def test_unverifiable_legacy_model_blocks_deletion_and_preserves_module(self):
        self.make_legacy_module()
        for index, damage in enumerate(("missing-backup", "unsafe-backup", "manifest", "promotion")):
            with self.subTest(damage=damage):
                self.home = self.root / ("legacy-protect-" + str(index))
                legacy = self.install(m.COMPONENT_ID)
                root = self.home / "components/project-leap-2d"
                (root / "current.json").write_text("not json")
                if damage == "missing-backup":
                    (root / "installation.json").unlink()
                elif damage == "unsafe-backup":
                    state = json.loads((root / "installation.json").read_text())
                    state["generation"] = "../../outside"
                    (root / "installation.json").write_text(json.dumps(state))
                elif damage == "manifest":
                    Path(legacy["runtime_descriptor"]["root"]).joinpath(m.MANIFEST_NAME).write_text("invalid")
                before = m._inventory(root, hashes=True)
                injection = (mock.patch.object(m, "_activate", side_effect=OSError("injected promotion failure"))
                             if damage == "promotion" else nullcontext())
                with injection:
                    result = m.remove_component(self.home)
                self.assertEqual(result["status"], "failed", result)
                self.assertEqual(m._inventory(root, hashes=True), before)
                self.assertEqual(Path(legacy["runtime_descriptor"]["models"]).joinpath("cpsam_v2").read_bytes(), self.weights)
                self.assertFalse(m.model_status(self.home)["installed"])

    def test_legacy_weight_is_promoted_when_existing_shared_model_is_damaged(self):
        self.make_legacy_module()
        self.assertTrue(m._reuse_installed_model(self.home))
        shared = m.model_status(self.home)
        Path(shared["runtime_descriptor"]["models"]).joinpath("cpsam_v2").write_bytes(b"damaged shared weights")
        (self.home / "components/project-leap-2d/current.json").write_text("broken")
        result = m.remove_component(self.home)
        self.assertEqual(result["status"], "removed", result)
        self.assertTrue(result["offer_remove_model"])
        retained = m.model_status(self.home)
        self.assertEqual(retained["status"], "ready", retained)
        self.assertEqual(Path(retained["runtime_descriptor"]["models"]).joinpath("cpsam_v2").read_bytes(), self.weights)

    def test_shared_model_lock_prevents_removal(self):
        self.install(m.COUNTING_MODULE_ID)
        with m.component_use_lock(self.home):
            self.assertEqual(m.remove_model(self.home)["status"], "blocked")

    def test_cancelled_download_removes_temporary_bytes_and_never_activates(self):
        self.archive_sources()
        cancelled = []

        def progress(_fraction, message):
            if "下载" in message:
                cancelled.append(True)

        with self.source_server() as (url, _requests):
            result = m.install_component(self.home, progress=progress, module_id=m.COUNTING_MODULE_ID, cancel=lambda: bool(cancelled), configuration=url)
        self.assertEqual(result["status"], "cancelled", result)
        self.assertFalse(m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)["installed"])
        self.assertFalse(list(self.home.glob(".package-download-*")))

    def test_bad_download_hash_keeps_previous_model_and_cleans_download(self):
        self.install(m.COUNTING_MODULE_ID)
        pointer = (self.home / "components/cellpose-model/current.json").read_bytes()
        self.archive_sources()
        self.catalog["packages"][m.COMPONENT_ID]["sha256"] = "0" * 64
        self.save_catalog()
        with self.source_server() as (url, _requests):
            result = m.install_component(self.home, configuration=url)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(pointer, (self.home / "components/cellpose-model/current.json").read_bytes())
        self.assertFalse(list(self.home.glob(".package-download-*")))

    def test_cancellation_at_activation_keeps_previous_module_pointer(self):
        installed = self.install(m.COUNTING_MODULE_ID)
        Path(installed["runtime_descriptor"]["root"]).joinpath("engine/worker.py").write_text("damaged")
        pointer = (self.home / "components/cell-counting/current.json").read_bytes()
        cancelled = []

        def progress(fraction, _message):
            if fraction >= 0.96:
                cancelled.append(True)

        result = m.repair_component(self.home, self.bundle, progress, m.COUNTING_MODULE_ID,
                                    cancel=lambda: bool(cancelled))
        self.assertEqual(result["status"], "cancelled", result)
        self.assertEqual(pointer, (self.home / "components/cell-counting/current.json").read_bytes())
        self.assertEqual(m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)["status"], "damaged")

    def test_bad_model_bytes_or_dependency_never_activate(self):
        model_seed = self.bundle / m.MODEL_ID
        (model_seed / "Models/cpsam_v2").write_bytes(b"bad")
        m.build_manifest(model_seed)
        result = m.install_component(self.home, self.bundle, module_id=m.COUNTING_MODULE_ID)
        self.assertEqual(result["status"], "failed", result)
        self.assertFalse(m.model_status(self.home)["installed"])
        seed = self.bundle / m.COUNTING_MODULE_ID
        descriptor = json.loads((seed / "runtime.json").read_text())
        descriptor["model_component"]["sha256"] = "0" * 64
        (seed / "runtime.json").write_text(json.dumps(descriptor))
        with self.assertRaises(m.ComponentError):
            m.build_manifest(seed)

    def test_new_shared_components_rollback_on_module_activation_failure(self):
        real = m.os.replace

        def fail(source, target):
            if Path(target).name == "current.json" and m.COUNTING_MODULE_ID in Path(target).parts:
                raise OSError("injected module activation failure")
            return real(source, target)

        with mock.patch.object(m.os, "replace", side_effect=fail):
            result = m.install_component(self.home, self.bundle, module_id=m.COUNTING_MODULE_ID)
        self.assertEqual(result["status"], "failed", result)
        self.assertFalse((self.home / "components/cellpose-model").exists())


if __name__ == "__main__":
    unittest.main()
