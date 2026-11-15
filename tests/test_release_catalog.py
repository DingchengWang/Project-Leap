"""Real signed loopback transfers with disposable keys, payloads and app homes."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import base64
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]
from leap_desktop import maintenance as m
from leap_desktop.release_catalog import ReleaseConfiguration, fetch_catalog, CatalogError, validate_url
from leap_desktop.update_manager import check_component_updates, stage_component_updates, apply_staged_updates
from testing_signatures import signed_envelope


class SignedReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native_temp = tempfile.TemporaryDirectory(prefix="leap-signature-tests-", dir="/tmp")
        cls.native = Path(cls.native_temp.name)
        cls.signer = cls.native / "signer"
        cls.library = cls.native / "verify.dylib"
        subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(cls.native / "module-cache"), str(ROOT / "tools/component_catalog_signer.swift"), "-o", str(cls.signer)], check=True, capture_output=True)
        subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(cls.native / "module-cache"), "-emit-library", str(ROOT / "native/catalog_signature.swift"), "-o", str(cls.library)], check=True, capture_output=True)
        cls.key = cls.native / "private-key"
        cls.public = subprocess.check_output([str(cls.signer), "generate", str(cls.key)], text=True).strip()

    @classmethod
    def tearDownClass(cls):
        cls.native_temp.cleanup()

    def setUp(self):
        self.host_build_patch = mock.patch("leap_desktop.version.application_build", return_value="1")
        self.host_build_patch.start()
        self.addCleanup(self.host_build_patch.stop)
        self.temporary = tempfile.TemporaryDirectory(prefix="leap-release-tests-")
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.bundle = self.root / "packages"
        self.bundle.mkdir()
        self.packages = []
        self.latest = {}
        self.weights = b"test model bytes"
        self.model_patch = mock.patch.object(m, "MODEL_SHA256", hashlib.sha256(self.weights).hexdigest())
        self.model_patch.start()
        for identity in (m.COMPONENT_ID, m.COUNTING_MODULE_ID):
            self.module_seed(identity, "1.0.0")
        self.seed(m.MODEL_ID, m.MODEL_VERSION,
                  {"models": "Models", "model_file": m.MODEL_FILENAME, "model_sha256": m.MODEL_SHA256},
                  {"Models/cpsam_v2": self.weights})
        self.seed(m.SCIENCE_ID, "1.0.0", {"python": "python/bin/python", "site_packages": "site-packages"},
                  {"python/bin/python": b"#!/bin/sh\nexit 0\n", "site-packages/fixture.py": b"pass\n"})
        self.seed(m.FIJI_ID, "20260718.0417", {"fiji_launcher": "Fiji/launcher"},
                  {"Fiji/launcher": b"#!/bin/sh\nexit 0\n"})
        self.publish()

    def tearDown(self):
        self.model_patch.stop()
        self.temporary.cleanup()

    def seed(self, identity, version, fields, files):
        seed = self.bundle / (identity + "-" + version)
        seed.mkdir()
        for name, content in files.items():
            path = seed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            if name.endswith(("python", "launcher")):
                path.chmod(0o755)
        descriptor = dict(fields, schema_version=4, component_id=identity, component_version=version)
        (seed / "runtime.json").write_text(json.dumps(descriptor))
        m.build_manifest(seed, identity)
        archive = self.bundle / (seed.name + ".tar.gz")
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(seed, arcname=".")
        compatibility = {key: fields[key] for key in ("host_api", "minimum_host_build", "platform")} if identity in (m.COMPONENT_ID, m.COUNTING_MODULE_ID) else {}
        self.packages.append({"id": identity, "version": version, "path": archive.name, **compatibility,
            "dependencies": ({m.SCIENCE_ID: fields["science_runtime"]["version"], m.MODEL_ID: m.MODEL_VERSION, **({m.FIJI_ID: "20260718.0417"} if identity == m.COMPONENT_ID else {})} if identity in (m.COMPONENT_ID, m.COUNTING_MODULE_ID) else {}),
            "bytes": archive.stat().st_size, "sha256": m._digest(archive), "manifest_sha256": m._digest(seed / m.MANIFEST_NAME)})
        self.latest[identity] = version
        return seed

    def module_seed(self, identity, version, runtime_version="1.0.0", minimum_host_build=1):
        fields = {"engine": "engine", "host_api": 1, "minimum_host_build": minimum_host_build, "platform": "macos-arm64", "science_runtime": {"id": m.SCIENCE_ID, "version": runtime_version}, "model_component": m.model_dependency()}
        if identity == m.COMPONENT_ID:
            fields["fiji_component"] = m.FIJI_ID
        return self.seed(identity, version, fields, {"engine/worker.py": ("version = " + repr(version) + "\n").encode()})

    def publish(self):
        payload = {"schema_version": 2, "packages": self.packages, "latest": self.latest}
        (self.bundle / "signed.json").write_text(json.dumps(signed_envelope(payload, self.key, self.signer)))

    @contextmanager
    def server(self):
        requests = Counter()
        bundle = self.bundle
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(bundle), **kwargs)
            def do_GET(self):
                requests[self.path] += 1
                super().do_GET()
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        configuration = ReleaseConfiguration("https://release-fixture.invalid/signed.json", self.public, self.library)
        def transport(address):
            self.assertTrue(address.startswith("https://release-fixture.invalid/"))
            return urlopen(address.replace("https://release-fixture.invalid", "http://127.0.0.1:" + str(server.server_port)), timeout=10)
        try:
            with mock.patch("leap_desktop.release_catalog.open_url", side_effect=transport):
                yield configuration, requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def install(self, configuration, identity=m.COMPONENT_ID):
        result = m.install_component(self.home, module_id=identity, configuration=configuration)
        self.assertEqual(result["status"], "ready", result)
        return result

    def test_signed_download_shared_runtime_restart_and_module_only_update(self):
        with self.server() as (configuration, requests):
            first = self.install(configuration)
            second = self.install(configuration, m.COUNTING_MODULE_ID)
            self.assertEqual(first["runtime_descriptor"]["python"], second["runtime_descriptor"]["python"])
            self.assertEqual(requests["/science-runtime-1.0.0.tar.gz"], 1)
            self.assertEqual(requests["/cellpose-model-" + m.MODEL_VERSION + ".tar.gz"], 1)
            runtime_pointer = (self.home / "components/science-runtime/current.json").read_bytes()
            model_pointer = (self.home / "components/cellpose-model/current.json").read_bytes()
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0")
            self.publish()
            checked = check_component_updates(self.home, configuration)
            self.assertEqual(checked["status"], "updates_available", checked)
            target = checked["updates"][0]["target"]
            result = m.update_component(self.home, module_id=m.COUNTING_MODULE_ID, configuration=configuration, expected_target=target)
            self.assertEqual(result["status"], "ready", result)
            self.assertEqual(result["component_version"], "1.1.0")
            self.assertEqual(m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)["component_version"], "1.1.0")
            self.assertEqual(runtime_pointer, (self.home / "components/science-runtime/current.json").read_bytes())
            self.assertEqual(model_pointer, (self.home / "components/cellpose-model/current.json").read_bytes())
            self.assertEqual(requests["/science-runtime-1.0.0.tar.gz"], 1)
            self.assertEqual(requests["/cellpose-model-" + m.MODEL_VERSION + ".tar.gz"], 1)
            self.assertEqual(requests["/cell-counting-1.1.0.tar.gz"], 1)
            self.assertFalse(list(self.home.glob(".package-download-*")))

    def test_intact_repair_never_accesses_network_and_checks_every_dependency(self):
        with self.server() as (configuration, requests):
            self.install(configuration)
            before = requests.copy()
            real = m.verify_seed
            seen, active, maximum = [], 0, 0
            lock = threading.Lock()
            def observe(path, component_id=None):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                    seen.append(component_id)
                try:
                    time.sleep(0.03)
                    return real(path, component_id)
                finally:
                    with lock:
                        active -= 1
            with mock.patch.object(m, "verify_seed", side_effect=observe):
                result = m.repair_component(self.home, configuration=configuration)
            self.assertEqual(result["status"], "intact", result)
            self.assertEqual(set(seen), {m.COMPONENT_ID, m.SCIENCE_ID, m.MODEL_ID, m.FIJI_ID})
            self.assertEqual(maximum, 2)
            self.assertEqual(requests, before)

    def test_same_size_same_time_fiji_damage_requires_repair(self):
        with self.server() as (configuration, requests):
            installed = self.install(configuration)
            path = Path(installed["runtime_descriptor"]["fiji_launcher"])
            original = path.read_bytes()
            metadata = path.stat()
            path.write_bytes(original.replace(b"exit 0", b"exit 1"))
            os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            self.assertEqual(m.fiji_status(self.home)["status"], "ready")
            result = m.repair_component(self.home, configuration=configuration)
            self.assertEqual(result["status"], "ready", result)
            self.assertEqual(Path(result["runtime_descriptor"]["fiji_launcher"]).read_bytes(), original)
            self.assertEqual(requests["/fiji-20260718.0417.tar.gz"], 2)

    def test_signature_failure_prevents_all_package_downloads(self):
        envelope = json.loads((self.bundle / "signed.json").read_text())
        envelope["signature"] = base64.b64encode(bytes(64)).decode()
        (self.bundle / "signed.json").write_text(json.dumps(envelope))
        with self.server() as (configuration, requests):
            result = m.install_component(self.home, configuration=configuration)
            self.assertEqual(result["status"], "failed", result)
            self.assertEqual(dict(requests), {"/signed.json": 1})
            self.assertFalse((self.home / "components/project-leap-2d/current.json").exists())

    def test_bad_download_preserves_previous_generation(self):
        with self.server() as (configuration, requests):
            self.install(configuration, m.COUNTING_MODULE_ID)
            pointer = self.home / "components/cell-counting/current.json"
            before = pointer.read_bytes()
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0")
            self.publish()
            archive = self.bundle / "cell-counting-1.1.0.tar.gz"
            data = archive.read_bytes()
            for corrupted in (data[:100], data[:-1] + bytes([data[-1] ^ 1]), data + b"extra"):
                archive.write_bytes(corrupted)
                result = m.update_component(self.home, module_id=m.COUNTING_MODULE_ID, configuration=configuration)
                self.assertEqual(result["status"], "failed", result)
                self.assertEqual(pointer.read_bytes(), before)
                self.assertFalse(list(self.home.glob(".package-download-*")))
            self.assertEqual(m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)["status"], "ready")

    def test_changed_confirmed_target_is_rejected_before_download(self):
        with self.server() as (configuration, requests):
            self.install(configuration, m.COUNTING_MODULE_ID)
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0")
            self.publish()
            plan = check_component_updates(self.home, configuration)["updates"][0]
            self.module_seed(m.COUNTING_MODULE_ID, "1.2.0")
            self.publish()
            result = m.update_component(self.home, module_id=m.COUNTING_MODULE_ID, configuration=configuration, expected_target=plan["target"])
            self.assertEqual(result["status"], "failed", result)
            self.assertEqual(requests["/cell-counting-1.2.0.tar.gz"], 0)

    def test_frozen_plan_stages_then_applies_without_any_network(self):
        with self.server() as (configuration, requests):
            self.install(configuration, m.COUNTING_MODULE_ID)
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0")
            self.publish()
            plan = check_component_updates(self.home, configuration)["plan"]
            self.module_seed(m.COUNTING_MODULE_ID, "1.2.0")
            self.publish()
            staged = stage_component_updates(self.home, plan, configuration)
            self.assertEqual(staged["status"], "staged", staged)
            self.assertEqual(m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)["component_version"], "1.0.0")
            self.assertEqual(requests["/cell-counting-1.1.0.tar.gz"], 1)
            self.assertEqual(requests["/cell-counting-1.2.0.tar.gz"], 0)
            frozen = json.loads(json.dumps(staged["plan"]))
        # The origin server has now stopped; no hidden refetch is possible.
        result = apply_staged_updates(self.home, frozen, configuration)
        self.assertEqual(result["status"], "ready", result)
        self.assertEqual(m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)["component_version"], "1.1.0")
        self.assertFalse(Path(frozen["staging_dir"]).exists())

    def test_staged_archive_tampering_preserves_active_version(self):
        with self.server() as (configuration, requests):
            self.install(configuration, m.COUNTING_MODULE_ID)
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0")
            self.publish()
            plan = check_component_updates(self.home, configuration)["plan"]
            staged = stage_component_updates(self.home, plan, configuration)
        archive = Path(staged["plan"]["staging_dir"]) / "cell-counting-1.1.0.tar.gz"
        archive.write_bytes(b"tampered")
        result = apply_staged_updates(self.home, staged["plan"], configuration)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)["component_version"], "1.0.0")

    def test_two_runtime_versions_follow_consumers_and_retire_unused_version(self):
        with self.server() as (configuration, requests):
            first = self.install(configuration)
            self.install(configuration, m.COUNTING_MODULE_ID)
            original_runtime = Path(first["runtime_descriptor"]["science_root"])
            self.seed(m.SCIENCE_ID, "2.0.0", {"python": "python/bin/python", "site_packages": "site-packages"},
                      {"python/bin/python": b"#!/bin/sh\nexit 0\n", "site-packages/fixture.py": b"version = 2\n"})
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0", "2.0.0")
            self.publish()
            updated = m.update_component(self.home, module_id=m.COUNTING_MODULE_ID, configuration=configuration)
            self.assertEqual(updated["status"], "ready", updated)
            retained = m.component_status(self.home)
            self.assertEqual(retained["status"], "ready", retained)
            self.assertEqual(retained["runtime_descriptor"]["science_root"], str(original_runtime))
            self.assertNotEqual(updated["runtime_descriptor"]["science_root"], str(original_runtime))
            self.assertTrue(original_runtime.exists())
            # Repair selects its own R1, even when the shared current pointer is R2.
            self.assertEqual(m.repair_component(self.home, configuration=configuration)["status"], "intact")
            self.module_seed(m.COMPONENT_ID, "1.1.0", "2.0.0")
            self.publish()
            latest = m.update_component(self.home, configuration=configuration)
            self.assertEqual(latest["status"], "ready", latest)
            self.assertEqual(latest["runtime_descriptor"]["science_root"], updated["runtime_descriptor"]["science_root"])
            self.assertFalse(original_runtime.exists())
            self.assertEqual(requests["/science-runtime-2.0.0.tar.gz"], 1)
            self.assertEqual(m.remove_component(self.home)["status"], "removed")
            self.assertTrue(Path(updated["runtime_descriptor"]["science_root"]).exists())
            self.assertEqual(m.remove_component(self.home, m.COUNTING_MODULE_ID)["status"], "removed")
            self.assertFalse((self.home / "components/science-runtime").exists())

    def test_damaged_old_module_repairs_pinned_runtime_and_fiji_when_latest_has_changed(self):
        with self.server() as (configuration, requests):
            installed = self.install(configuration)
            self.install(configuration, m.COUNTING_MODULE_ID)
            old_descriptor = installed["runtime_descriptor"]
            old_files = {
                "engine": Path(old_descriptor["root"]) / "engine/worker.py",
                "python": Path(old_descriptor["python"]),
                "fiji_launcher": Path(old_descriptor["fiji_launcher"]),
            }
            expected = {name: path.read_bytes() for name, path in old_files.items()}
            self.seed(m.SCIENCE_ID, "2.0.0", {"python": "python/bin/python", "site_packages": "site-packages"},
                      {"python/bin/python": b"#!/bin/sh\nexit 0\n", "site-packages/fixture.py": b"version = 2\n"})
            self.seed(m.FIJI_ID, "20260719.0417", {"fiji_launcher": "Fiji/launcher"},
                      {"Fiji/launcher": b"#!/bin/sh\n# new Fiji\nexit 0\n"})
            self.module_seed(m.COMPONENT_ID, "1.1.0", "2.0.0")
            self.packages[-1]["dependencies"][m.FIJI_ID] = "20260719.0417"
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0", "2.0.0")
            self.publish()
            updated_counting = m.update_component(self.home, module_id=m.COUNTING_MODULE_ID, configuration=configuration)
            self.assertEqual(updated_counting["status"], "ready", updated_counting)
            new_runtime = updated_counting["runtime_descriptor"]["science_root"]
            for path in old_files.values():
                path.write_bytes(b"damaged old installed bytes")
            before = requests.copy()
            repaired = m.repair_component(self.home, configuration=configuration)
            self.assertEqual(repaired["status"], "ready", repaired)
            self.assertEqual(repaired["component_version"], "1.0.0")
            descriptor = repaired["runtime_descriptor"]
            self.assertEqual((Path(descriptor["root"]) / "engine/worker.py").read_bytes(), expected["engine"])
            self.assertEqual(Path(descriptor["python"]).read_bytes(), expected["python"])
            self.assertEqual(Path(descriptor["fiji_launcher"]).read_bytes(), expected["fiji_launcher"])
            self.assertEqual(m.science_status(self.home, version="1.0.0")["status"], "ready")
            self.assertEqual(m.fiji_status(self.home)["component_version"], "20260718.0417")
            self.assertEqual(m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)["runtime_descriptor"]["science_root"], new_runtime)
            self.assertEqual(dict(requests - before), {
                "/signed.json": 1,
                "/project-leap-2d-1.0.0.tar.gz": 1,
                "/science-runtime-1.0.0.tar.gz": 1,
                "/fiji-20260718.0417.tar.gz": 1,
            })

    def test_one_staged_plan_can_keep_distinct_runtime_versions(self):
        with self.server() as (configuration, requests):
            self.install(configuration)
            self.install(configuration, m.COUNTING_MODULE_ID)
            self.seed(m.SCIENCE_ID, "2.0.0", {"python": "python/bin/python", "site_packages": "site-packages"},
                      {"python/bin/python": b"#!/bin/sh\nexit 0\n", "site-packages/fixture.py": b"version = 2\n"})
            self.module_seed(m.COMPONENT_ID, "1.1.0", "1.0.0")
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0", "2.0.0")
            self.publish()
            checked = check_component_updates(self.home, configuration)
            self.assertEqual(len(checked["updates"]), 2)
            staged = stage_component_updates(self.home, checked["plan"], configuration)
            self.assertEqual(staged["status"], "staged", staged)
        applied = apply_staged_updates(self.home, staged["plan"], configuration)
        self.assertEqual(applied["status"], "ready", applied)
        first = m.component_status(self.home)
        second = m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)
        self.assertEqual(first["status"], "ready", first)
        self.assertEqual(second["status"], "ready", second)
        self.assertNotEqual(first["runtime_descriptor"]["science_root"], second["runtime_descriptor"]["science_root"])

    def test_failed_module_activation_discards_unreferenced_new_runtime(self):
        with self.server() as (configuration, requests):
            installed = self.install(configuration, m.COUNTING_MODULE_ID)
            old_runtime = Path(installed["runtime_descriptor"]["science_root"])
            self.seed(m.SCIENCE_ID, "2.0.0", {"python": "python/bin/python", "site_packages": "site-packages"},
                      {"python/bin/python": b"#!/bin/sh\nexit 0\n", "site-packages/fixture.py": b"version = 2\n"})
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0", "2.0.0")
            self.publish()
            replace = m.os.replace
            def fail_module_pointer(source, target):
                if Path(target).name == "current.json" and m.COUNTING_MODULE_ID in Path(target).parts:
                    raise OSError("injected module activation failure")
                return replace(source, target)
            with mock.patch.object(m.os, "replace", side_effect=fail_module_pointer):
                result = m.update_component(self.home, module_id=m.COUNTING_MODULE_ID, configuration=configuration)
            self.assertEqual(result["status"], "failed", result)
            remaining = m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)
            self.assertEqual(remaining["status"], "ready", remaining)
            self.assertEqual(remaining["runtime_descriptor"]["science_root"], str(old_runtime))
            self.assertEqual(m.science_status(self.home)["component_version"], "1.0.0")
            self.assertEqual([path.resolve() for path in (self.home / "components/science-runtime/generations").iterdir() if path.is_dir()], [old_runtime.resolve()])

    def test_future_host_target_can_stage_but_cannot_install_until_host_updates(self):
        with self.server() as (configuration, requests):
            self.install(configuration, m.COUNTING_MODULE_ID)
            pointer = self.home / "components/cell-counting/current.json"
            previous = pointer.read_bytes()
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0", minimum_host_build=2)
            self.publish()
            checked = check_component_updates(self.home, configuration)
            self.assertEqual(checked["status"], "updates_available", checked)
            self.assertEqual(checked["updates"][0]["minimum_host_build"], 2)
            denied = m.update_component(self.home, module_id=m.COUNTING_MODULE_ID, configuration=configuration)
            self.assertEqual(denied["status"], "failed", denied)
            self.assertIn("2", denied["message"])
            self.assertEqual(requests["/cell-counting-1.1.0.tar.gz"], 0)
            staged = stage_component_updates(self.home, checked["plan"], configuration)
            self.assertEqual(staged["status"], "staged", staged)
            self.assertEqual(requests["/cell-counting-1.1.0.tar.gz"], 1)
        denied = apply_staged_updates(self.home, staged["plan"], configuration)
        self.assertEqual(denied["status"], "failed", denied)
        self.assertEqual(pointer.read_bytes(), previous)
        self.assertEqual(m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)["status"], "ready")
        with mock.patch("leap_desktop.version.application_build", return_value="2"):
            applied = apply_staged_updates(self.home, staged["plan"], configuration)
            self.assertEqual(applied["status"], "ready", applied)
            self.assertEqual(m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)["component_version"], "1.1.0")

    def test_both_modules_repair_same_version_host_metadata_and_keep_history(self):
        identities = (m.COMPONENT_ID, m.COUNTING_MODULE_ID)
        for identity in identities:
            self.module_seed(identity, "1.1.0", minimum_host_build=2)
        self.publish()
        with self.server() as (configuration, requests):
            with mock.patch("leap_desktop.version.application_build", return_value="2"):
                installed = {identity: self.install(configuration, identity) for identity in identities}
            shared_pointers = {identity: (self.home / "components" / identity / "current.json").read_bytes()
                               for identity in (m.SCIENCE_ID, m.MODEL_ID, m.FIJI_ID)}
            history = self.home / "runs/retained-analysis/analysis.json"
            history.parent.mkdir(parents=True)
            history_bytes = b'{"schema_version": 2, "retained_result": true}\n'
            history.write_bytes(history_bytes)
            for identity in identities:
                seed = self.bundle / (identity + "-1.1.0")
                descriptor = json.loads((seed / "runtime.json").read_text())
                descriptor["minimum_host_build"] = 1
                (seed / "runtime.json").write_text(json.dumps(descriptor))
                m.build_manifest(seed, identity)
                archive = self.bundle / (seed.name + ".tar.gz")
                with tarfile.open(archive, "w:gz") as stream:
                    stream.add(seed, arcname=".")
                record = next(item for item in self.packages if item["id"] == identity and item["version"] == "1.1.0")
                record.update(minimum_host_build=1, bytes=archive.stat().st_size,
                              sha256=m._digest(archive), manifest_sha256=m._digest(seed / m.MANIFEST_NAME))
            self.publish()
            for identity in identities:
                with self.subTest(module=identity):
                    self.assertEqual(m.component_status(self.home, module_id=identity)["status"], "damaged")
                    result = m.repair_component(self.home, module_id=identity, configuration=configuration)
                    self.assertEqual(result["status"], "ready", result)
                    self.assertEqual(result["component_version"], installed[identity]["component_version"])
                    self.assertEqual(result["install_seq"], installed[identity]["install_seq"])
                    self.assertEqual(result["runtime_descriptor"]["minimum_host_build"], 1)
                    old = Path(installed[identity]["runtime_descriptor"]["root"])
                    repaired = Path(result["runtime_descriptor"]["root"])
                    self.assertNotEqual(repaired, old)
                    self.assertEqual((repaired / "engine/worker.py").read_bytes(), (old / "engine/worker.py").read_bytes())
                    self.assertEqual(requests["/" + identity + "-1.1.0.tar.gz"], 2)
            for identity, pointer in shared_pointers.items():
                self.assertEqual((self.home / "components" / identity / "current.json").read_bytes(), pointer)
            self.assertEqual(requests["/science-runtime-1.0.0.tar.gz"], 1)
            self.assertEqual(requests["/cellpose-model-" + m.MODEL_VERSION + ".tar.gz"], 1)
            self.assertEqual(requests["/fiji-20260718.0417.tar.gz"], 1)
            self.assertEqual(history.read_bytes(), history_bytes)

    def test_unsupported_host_api_or_platform_is_rejected_before_download(self):
        module = next(item for item in self.packages if item["id"] == m.COUNTING_MODULE_ID)
        for field, wrong in (("host_api", 2), ("platform", "windows-x64"), ("minimum_host_build", True)):
            with self.subTest(field=field):
                original = module[field]
                module[field] = wrong
                self.publish()
                with self.server() as (configuration, requests):
                    result = m.install_component(self.home, module_id=m.COUNTING_MODULE_ID, configuration=configuration)
                    self.assertEqual(result["status"], "failed", result)
                    self.assertEqual(dict(requests), {"/signed.json": 1})
                module[field] = original

    def test_offline_archive_enforces_descriptor_host_requirement(self):
        self.module_seed(m.COUNTING_MODULE_ID, "1.1.0", minimum_host_build=2)
        archive = self.bundle / "cell-counting-1.1.0.tar.gz"
        result = m.install_component(self.home, archive, module_id=m.COUNTING_MODULE_ID)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("2", result["message"])
        self.assertFalse((self.home / "components/cell-counting/current.json").exists())

    def test_staged_fiji_update_installs_the_exact_signed_dependency(self):
        required = "20261113.0001"
        with self.server() as (configuration, requests):
            self.install(configuration)
            self.seed(m.FIJI_ID, required, {"fiji_launcher": "Fiji/launcher"},
                      {"Fiji/launcher": b"#!/bin/sh\necho updated-fiji\n"})
            self.module_seed(m.COMPONENT_ID, "1.1.0")
            self.packages[-1]["dependencies"][m.FIJI_ID] = required
            self.publish()
            checked = check_component_updates(self.home, configuration)
            self.assertEqual(checked["plan"]["targets"][0]["dependencies"][m.FIJI_ID], required)
            staged = stage_component_updates(self.home, checked["plan"], configuration)
            self.assertEqual(staged["status"], "staged", staged)
            self.assertEqual(requests["/fiji-" + required + ".tar.gz"], 1)
        applied = apply_staged_updates(self.home, staged["plan"], configuration)
        self.assertEqual(applied["status"], "ready", applied)
        status = m.component_status(self.home)
        self.assertEqual(status["status"], "ready", status)
        self.assertEqual(status["component_version"], "1.1.0")
        self.assertEqual(status["fiji_version"], required)
        self.assertEqual(m.fiji_status(self.home)["component_version"], required)
        self.assertEqual(Path(status["runtime_descriptor"]["fiji_launcher"]).read_bytes(), b"#!/bin/sh\necho updated-fiji\n")

    def test_failed_module_activation_restores_the_previous_fiji_generation(self):
        required = "20261113.0001"
        with self.server() as (configuration, requests):
            installed = self.install(configuration)
            fiji_root = self.home / "components/fiji"
            original_records = {name: (fiji_root / name).read_bytes() for name in ("current.json", "installation.json")}
            self.seed(m.FIJI_ID, required, {"fiji_launcher": "Fiji/launcher"},
                      {"Fiji/launcher": b"#!/bin/sh\necho updated-fiji\n"})
            self.module_seed(m.COMPONENT_ID, "1.1.0")
            self.packages[-1]["dependencies"][m.FIJI_ID] = required
            self.publish()
            replace = m.os.replace
            def fail_module_pointer(source, target):
                if Path(target).name == "current.json" and m.COMPONENT_ID in Path(target).parts:
                    raise OSError("injected module activation failure")
                return replace(source, target)
            with mock.patch.object(m.os, "replace", side_effect=fail_module_pointer):
                result = m.update_component(self.home, configuration=configuration)
            self.assertEqual(result["status"], "failed", result)
            self.assertEqual({name: (fiji_root / name).read_bytes() for name in original_records}, original_records)
            self.assertEqual(m.fiji_status(self.home)["component_version"], "20260718.0417")
            self.assertEqual(m.component_status(self.home)["status"], "ready")
            self.assertEqual(m.component_status(self.home)["component_version"], "1.0.0")
            self.assertEqual(m.component_status(self.home)["runtime_descriptor"]["fiji_root"], installed["runtime_descriptor"]["fiji_root"])
            self.assertFalse(list((fiji_root / "generations").glob(required + "-*")))

    def test_staged_runtime_swap_keeps_both_dependencies_until_all_modules_activate(self):
        self.seed(m.SCIENCE_ID, "2.0.0", {"python": "python/bin/python", "site_packages": "site-packages"},
                  {"python/bin/python": b"#!/bin/sh\nexit 0\n", "site-packages/fixture.py": b"version = 2\n"})
        self.module_seed(m.COUNTING_MODULE_ID, "1.0.1", "2.0.0")
        self.publish()
        with self.server() as (configuration, requests):
            first = self.install(configuration)
            second = self.install(configuration, m.COUNTING_MODULE_ID)
            original_r1 = first["runtime_descriptor"]["science_root"]
            original_r2 = second["runtime_descriptor"]["science_root"]
            runtime_requests = {version: requests["/science-runtime-" + version + ".tar.gz"] for version in ("1.0.0", "2.0.0")}
            self.module_seed(m.COMPONENT_ID, "1.1.0", "2.0.0")
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0", "1.0.0")
            self.publish()
            checked = check_component_updates(self.home, configuration)
            staged = stage_component_updates(self.home, checked["plan"], configuration)
            self.assertEqual(staged["status"], "staged", staged)
            self.assertFalse(list(Path(staged["plan"]["staging_dir"]).glob("science-runtime-*.tar.gz")))
            self.assertEqual({version: requests["/science-runtime-" + version + ".tar.gz"] for version in runtime_requests}, runtime_requests)
        applied = apply_staged_updates(self.home, staged["plan"], configuration)
        self.assertEqual(applied["status"], "ready", applied)
        first = m.component_status(self.home)
        second = m.component_status(self.home, module_id=m.COUNTING_MODULE_ID)
        self.assertEqual(first["status"], "ready", first)
        self.assertEqual(second["status"], "ready", second)
        self.assertEqual(first["component_version"], "1.1.0")
        self.assertEqual(second["component_version"], "1.1.0")
        self.assertEqual(first["runtime_descriptor"]["science_root"], original_r2)
        self.assertEqual(second["runtime_descriptor"]["science_root"], original_r1)
        self.assertTrue(Path(original_r1).exists())
        self.assertTrue(Path(original_r2).exists())

    def test_staged_dependency_tampering_is_rejected_before_any_activation(self):
        required = "20261113.0001"
        with self.server() as (configuration, requests):
            self.install(configuration)
            self.seed(m.FIJI_ID, required, {"fiji_launcher": "Fiji/launcher"},
                      {"Fiji/launcher": b"#!/bin/sh\necho updated-fiji\n"})
            self.module_seed(m.COMPONENT_ID, "1.1.0")
            self.packages[-1]["dependencies"][m.FIJI_ID] = required
            self.publish()
            checked = check_component_updates(self.home, configuration)
            staged = stage_component_updates(self.home, checked["plan"], configuration)
            self.assertEqual(staged["status"], "staged", staged)
        plan = staged["plan"]
        destination = Path(plan["staging_dir"])
        signed_before = json.dumps(plan["signed_catalog"], sort_keys=True)
        archives_before = {path.name: m._digest(path) for path in destination.glob("*.tar.gz")}
        pointers_before = {path: path.read_bytes() for path in (self.home / "components").glob("*/current.json")}
        catalog_path = destination / "catalog.json"
        catalog = json.loads(catalog_path.read_text())
        self.assertEqual(catalog["packages"][m.COMPONENT_ID]["dependencies"][m.FIJI_ID], required)
        catalog["packages"][m.COMPONENT_ID]["dependencies"][m.FIJI_ID] = "20260718.0417"
        catalog_path.write_text(json.dumps(catalog))
        with mock.patch.object(m, "_activate", wraps=m._activate) as activation:
            result = apply_staged_updates(self.home, plan, configuration)
        self.assertEqual(result["status"], "failed", result)
        activation.assert_not_called()
        self.assertEqual({path: path.read_bytes() for path in pointers_before}, pointers_before)
        self.assertEqual(json.dumps(plan["signed_catalog"], sort_keys=True), signed_before)
        self.assertEqual({path.name: m._digest(path) for path in destination.glob("*.tar.gz")}, archives_before)
        self.assertEqual(m.component_status(self.home)["status"], "ready")

    def test_missing_staged_target_is_rejected_before_any_activation(self):
        with self.server() as (configuration, requests):
            self.install(configuration)
            self.install(configuration, m.COUNTING_MODULE_ID)
            self.module_seed(m.COMPONENT_ID, "1.1.0")
            self.module_seed(m.COUNTING_MODULE_ID, "1.1.0")
            self.publish()
            checked = check_component_updates(self.home, configuration)
            self.assertEqual([target["id"] for target in checked["plan"]["targets"]], [m.COMPONENT_ID, m.COUNTING_MODULE_ID])
            staged = stage_component_updates(self.home, checked["plan"], configuration)
            self.assertEqual(staged["status"], "staged", staged)
        plan = staged["plan"]
        destination = Path(plan["staging_dir"])
        signed_before = json.dumps(plan["signed_catalog"], sort_keys=True)
        archives_before = {path.name: m._digest(path) for path in destination.glob("*.tar.gz")}
        pointers_before = {path: path.read_bytes() for path in (self.home / "components").glob("*/current.json")}
        catalog_path = destination / "catalog.json"
        catalog = json.loads(catalog_path.read_text())
        missing = catalog["packages"].pop(m.COUNTING_MODULE_ID)
        self.assertTrue((destination / missing["path"]).is_file())
        catalog_path.write_text(json.dumps(catalog))
        with mock.patch.object(m, "_activate", wraps=m._activate) as activation:
            result = apply_staged_updates(self.home, plan, configuration)
        self.assertEqual(result["status"], "failed", result)
        activation.assert_not_called()
        self.assertEqual({path: path.read_bytes() for path in pointers_before}, pointers_before)
        self.assertEqual(json.dumps(plan["signed_catalog"], sort_keys=True), signed_before)
        self.assertEqual({path.name: m._digest(path) for path in destination.glob("*.tar.gz")}, archives_before)
        for identity in (m.COMPONENT_ID, m.COUNTING_MODULE_ID):
            status = m.component_status(self.home, module_id=identity)
            self.assertEqual(status["status"], "ready", status)
            self.assertEqual(status["component_version"], "1.0.0")

    def test_unconfigured_and_unsigned_url_are_explicit(self):
        self.assertEqual(check_component_updates(self.home, ReleaseConfiguration())["status"], "unconfigured")
        self.assertEqual(m.install_component(self.home, configuration=ReleaseConfiguration())["status"], "unconfigured")
        self.assertEqual(m.install_component(self.home, "http://127.0.0.1/catalog.json")["status"], "failed")
        with self.assertRaises(CatalogError):
            validate_url("http://127.0.0.1/catalog.json")
        with self.assertRaises(CatalogError):
            validate_url("http://example.com/catalog.json")


if __name__ == "__main__":
    unittest.main()
