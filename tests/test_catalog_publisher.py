"""Publisher integrity and append-only properties; all private keys are temporary."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "app")]
from leap_desktop import maintenance as maintenance
import sign_component_catalog as publisher
from release_support import PublicVerifier
from testing_signatures import signed_envelope

CATALOG_URL = "https://release-fixture.invalid/updates/components.json"


class CatalogPublisherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native_temp = tempfile.TemporaryDirectory(prefix="leap-publisher-signatures-", dir="/tmp")
        cls.native = Path(cls.native_temp.name)
        cls.signer = cls.native / "test-signer"
        subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(cls.native / "module-cache"),
                        str(ROOT / "tools/component_catalog_signer.swift"), "-o", str(cls.signer)],
                       check=True, capture_output=True)
        cls.key = cls.native / "test-private-key"
        cls.public = subprocess.check_output([str(cls.signer), "generate", str(cls.key)], text=True).strip()
        cls.verifier_context = PublicVerifier()
        cls.verifier = cls.verifier_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.verifier_context.__exit__(None, None, None)
        cls.native_temp.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="leap-publisher-tests-", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.packages = self.root / "packages"
        self.packages.mkdir()
        self.weights = b"fixture shared model weights"
        self.model_patch = mock.patch.object(maintenance, "MODEL_SHA256", hashlib.sha256(self.weights).hexdigest())
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.catalog = {"schema_version": 1, "packages": {}}
        self.settings = json.loads((ROOT / "tools/build/release-settings.json").read_text())
        self.settings["components"].update(catalog_url=CATALOG_URL, public_key=self.public,
                                           keychain_account="disposable-test-account", assets={})
        self.seed("science-runtime", "1.0.0", {"python": "python/bin/python", "site_packages": "site-packages"},
                  {"python/bin/python": b"#!/bin/sh\nexit 0\n", "site-packages/fixture.py": b"pass\n"})
        self.seed("cellpose-model", maintenance.MODEL_VERSION,
                  {"models": "Models", "model_file": maintenance.MODEL_FILENAME, "model_sha256": maintenance.MODEL_SHA256},
                  {"Models/cpsam_v2": self.weights})
        self.seed("fiji", "20260718.0417", {"fiji_launcher": "Fiji/launcher"}, {"Fiji/launcher": b"#!/bin/sh\nexit 0\n"})
        self.module("project-leap-2d", "1.0.0")
        self.module("cell-counting", "1.0.0")
        self.catalog_path = self.root / "catalog.json"
        self.settings_path = self.root / "settings.json"
        self.save_inputs()

    def seed(self, identity, version, fields, files, dependencies=None):
        seed = self.root / "seeds" / identity / version
        seed.mkdir(parents=True)
        for name, content in files.items():
            target = seed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            if name.endswith(("python", "launcher")):
                target.chmod(0o755)
        descriptor = dict(fields, schema_version=2 if identity in ("fiji", "cellpose-model") else 4,
                          component_id=identity, component_version=version)
        (seed / "runtime.json").write_text(json.dumps(descriptor))
        maintenance.build_manifest(seed, identity)
        archive = self.packages / (identity + "-" + version + ".tar.gz")
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(seed, arcname=".")
        record = dict(version=version, path=archive.name, bytes=archive.stat().st_size,
                      sha256=maintenance._digest(archive), manifest_sha256=maintenance._digest(seed / maintenance.MANIFEST_NAME),
                      dependencies=dependencies or {})
        if identity in publisher.MODULE_IDS:
            record.update({key: fields[key] for key in publisher.HOST_REQUIREMENT_FIELDS})
        self.catalog["packages"][identity] = record
        self.settings["components"]["assets"][identity] = {
            "version": version, "url": "https://downloads-fixture.invalid/" + archive.name}
        return seed

    def module(self, identity, version, science_version="1.0.0"):
        fields = {"engine": "engine", "host_api": 1, "minimum_host_build": 1, "platform": "macos-arm64",
                  "science_runtime": {"id": "science-runtime", "version": science_version},
                  "model_component": maintenance.model_dependency()}
        dependencies = {"science-runtime": science_version, "cellpose-model": maintenance.MODEL_VERSION}
        if identity == "project-leap-2d":
            fields["fiji_component"] = "fiji"
            dependencies["fiji"] = "20260718.0417"
        return self.seed(identity, version, fields, {"engine/worker.py": ("version = " + repr(version)).encode()}, dependencies)

    def save_inputs(self):
        self.catalog_path.write_text(json.dumps(self.catalog))
        self.settings_path.write_text(json.dumps(self.settings))

    def records(self):
        self.save_inputs()
        return publisher.verify_offline_catalog(self.catalog_path, self.packages, self.settings)

    def merge(self, previous=None, records=None):
        return publisher.merge_catalog(previous, self.records() if records is None else records, catalog_url=CATALOG_URL)

    def previous(self, payload):
        path = self.root / "previous.json"
        path.write_text(json.dumps(signed_envelope(payload, self.key, self.signer)))
        return path

    def repack(self, identity, *, refresh_manifest=False):
        record = self.catalog["packages"][identity]
        seed = self.root / "seeds" / identity / record["version"]
        if refresh_manifest:
            maintenance.build_manifest(seed, identity)
        archive = self.packages / record["path"]
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(seed, arcname=".")
        record.update(bytes=archive.stat().st_size, sha256=maintenance._digest(archive),
                      manifest_sha256=maintenance._digest(seed / maintenance.MANIFEST_NAME))
        self.save_inputs()

    def test_real_archives_become_absolute_https_records(self):
        records = self.records()
        self.assertEqual({item["id"] for item in records}, publisher.PACKAGE_IDS)
        for item in records:
            self.assertNotIn("path", item)
            self.assertEqual(item["url"], self.settings["components"]["assets"][item["id"]]["url"])
            self.assertEqual(item["sha256"], self.catalog["packages"][item["id"]]["sha256"])
        self.assertEqual(self.merge(records=records)["latest"],
                         {identity: item["version"] for identity, item in self.catalog["packages"].items()})

    def test_idempotence_and_all_five_package_permutations(self):
        records = self.records()
        expected = self.merge(records=records)
        for order in itertools.permutations(records):
            self.assertEqual(self.merge(records=order), expected)
            self.assertEqual(self.merge(expected, order), expected)

    def test_append_preserves_all_historical_records_and_dependency_versions(self):
        original = self.merge()
        original_snapshot = deepcopy(original)
        self.seed("science-runtime", "2.0.0", {"python": "python/bin/python", "site_packages": "site-packages"},
                  {"python/bin/python": b"#!/bin/sh\nexit 0\n", "site-packages/fixture.py": b"version = 2\n"})
        self.module("cell-counting", "2.0.0", science_version="2.0.0")
        with self.assertRaisesRegex(ValueError, "Missing retained dependency"):
            self.merge()
        result = self.merge(original)
        indexed = {(item["id"], item["version"]): item for item in result["packages"]}
        for item in original["packages"]:
            self.assertEqual(indexed[(item["id"], item["version"])], item)
        self.assertEqual(indexed[("project-leap-2d", "1.0.0")]["dependencies"]["science-runtime"], "1.0.0")
        self.assertEqual(result["latest"]["science-runtime"], "2.0.0")
        self.assertEqual(result["latest"]["cell-counting"], "2.0.0")
        self.assertEqual(original, original_snapshot)

    def test_partial_explicit_merge_retains_other_latest_values(self):
        previous = self.merge()
        self.module("cell-counting", "2.0.0")
        update = next(item for item in self.records() if item["id"] == "cell-counting")
        result = self.merge(previous, [update])
        self.assertEqual(result["latest"], dict(previous["latest"], **{"cell-counting": "2.0.0"}))

    def test_new_module_latest_requires_two_to_four_numeric_version_segments(self):
        previous = self.merge()
        module = next(item for item in previous["packages"] if item["id"] == "cell-counting")
        for version in ("1", "1.2.3.4.5", "1.0.0-rc1", "v1.0.0", "release"):
            item = dict(module, version=version, url="https://downloads-fixture.invalid/counting-" + version + ".tar.gz")
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.merge(previous, [item])
        for version in ("2.0", "2.0.0", "2.0.0.1"):
            item = dict(module, version=version, url="https://downloads-fixture.invalid/counting-" + version + ".tar.gz")
            with self.subTest(version=version):
                self.assertEqual(self.merge(previous, [item])["latest"]["cell-counting"], version)

    def test_valid_historical_non_numeric_module_version_is_retained(self):
        previous = self.merge()
        module = next(item for item in previous["packages"] if item["id"] == "cell-counting")
        module.update(version="0.9.0-rc1", url="https://downloads-fixture.invalid/counting-0.9.0-rc1.tar.gz")
        previous["latest"]["cell-counting"] = module["version"]
        history = publisher.load_previous_catalog(self.previous(previous), self.public, self.verifier,
                                                  catalog_url=CATALOG_URL)
        result = self.merge(history)
        self.assertIn(module, result["packages"])
        self.assertEqual(result["latest"]["cell-counting"], "1.0.0")

    def test_same_identity_conflict_is_rejected_for_every_content_or_metadata_change(self):
        previous = self.merge()
        for field, replacement in (("bytes", 1), ("sha256", "0" * 64), ("manifest_sha256", "0" * 64),
                                   ("url", "https://different.invalid/archive.tar.gz"), ("minimum_host_build", 2)):
            item = deepcopy(next(item for item in previous["packages"] if item["id"] == "cell-counting"))
            item[field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Cannot replace"):
                self.merge(previous, [item])

    def test_existing_url_cannot_be_reused_for_different_archive(self):
        previous = self.merge()
        item = deepcopy(next(item for item in previous["packages"] if item["id"] == "cell-counting"))
        item.update(version="2.0.0", sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "URL cannot"):
            self.merge(previous, [item])

    def test_missing_history_dependency_is_rejected(self):
        previous = self.merge()
        previous["packages"] = [item for item in previous["packages"] if item["id"] != "science-runtime"]
        previous["latest"].pop("science-runtime")
        with self.assertRaisesRegex(ValueError, "Missing retained dependency"):
            self.merge(previous, [])

    def test_previous_signature_is_verified_and_tampering_is_rejected(self):
        payload = self.merge()
        path = self.previous(payload)
        self.assertEqual(publisher.load_previous_catalog(path, self.public, self.verifier, catalog_url=CATALOG_URL), payload)
        envelope = json.loads(path.read_text())
        envelope["signature"] = base64.b64encode(bytes(64)).decode()
        path.write_text(json.dumps(envelope))
        with self.assertRaises(ValueError):
            publisher.load_previous_catalog(path, self.public, self.verifier, catalog_url=CATALOG_URL)

    def test_unsigned_previous_history_is_rejected(self):
        path = self.root / "unsigned.json"
        path.write_text(json.dumps(self.merge()))
        with self.assertRaisesRegex(ValueError, "signed schema 1"):
            publisher.load_previous_catalog(path, self.public, self.verifier, catalog_url=CATALOG_URL)

    def test_archive_sha_length_and_manifest_sha_mismatches_are_rejected(self):
        for field, replacement in (("bytes", 1), ("sha256", "0" * 64), ("manifest_sha256", "0" * 64)):
            original = deepcopy(self.catalog)
            self.catalog["packages"]["cell-counting"][field] = replacement
            self.save_inputs()
            with self.subTest(field=field), self.assertRaises(ValueError):
                publisher.verify_offline_catalog(self.catalog_path, self.packages, self.settings)
            self.catalog = original

    def test_rehashed_archive_with_manifest_content_mismatch_is_rejected(self):
        seed = self.root / "seeds/cell-counting/1.0.0"
        path = seed / "engine/worker.py"
        path.write_bytes(path.read_bytes().replace(b"1.0.0", b"9.0.0"))
        self.repack("cell-counting")
        with self.assertRaisesRegex(ValueError, "Archived file SHA-256"):
            self.records()

    def test_runtime_catalog_version_host_and_dependency_mismatches_are_rejected(self):
        for field, replacement in (("component_version", "2.0.0"), ("minimum_host_build", 2),
                                   ("science_runtime", {"id": "science-runtime", "version": "2.0.0"})):
            seed = self.root / "seeds/cell-counting/1.0.0"
            path = seed / "runtime.json"
            original = path.read_text()
            descriptor = json.loads(original)
            descriptor[field] = replacement
            path.write_text(json.dumps(descriptor))
            self.repack("cell-counting", refresh_manifest=True)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.records()
            path.write_text(original)
            self.repack("cell-counting", refresh_manifest=True)

    def test_new_module_rejects_development_host_contract_but_retains_signed_history(self):
        previous = self.merge()
        historical = next(item for item in previous["packages"] if item["id"] == "cell-counting")
        historical.update(version="0.9.0", minimum_host_build=17,
                          url="https://downloads-fixture.invalid/counting-0.9.0.tar.gz")
        previous["latest"]["cell-counting"] = "0.9.0"
        verified = publisher.load_previous_catalog(self.previous(previous), self.public, self.verifier,
                                                   catalog_url=CATALOG_URL)
        self.assertIn(historical, self.merge(verified)["packages"])
        path = self.root / "seeds/cell-counting/1.0.0/runtime.json"
        descriptor = json.loads(path.read_text())
        descriptor["minimum_host_build"] = 17
        path.write_text(json.dumps(descriptor))
        self.catalog["packages"]["cell-counting"]["minimum_host_build"] = 17
        self.repack("cell-counting", refresh_manifest=True)
        with self.assertRaisesRegex(ValueError, "current release policy"):
            self.records()

    def test_missing_package_stale_asset_mapping_and_non_https_url_are_rejected(self):
        original = deepcopy(self.catalog)
        self.catalog["packages"].pop("fiji")
        with self.assertRaisesRegex(ValueError, "all five"):
            self.records()
        self.catalog = original
        self.settings["components"]["assets"]["fiji"]["version"] = "stale"
        with self.assertRaisesRegex(ValueError, "no asset URL"):
            self.records()
        self.settings["components"]["assets"]["fiji"].update(version="20260718.0417", url="http://insecure.invalid/fiji.tar.gz")
        with self.assertRaises(ValueError):
            self.records()

    def test_parent_escape_and_external_symlink_are_rejected(self):
        record = self.catalog["packages"]["fiji"]
        local_path = record["path"]
        record["path"] = "../outside.tar.gz"
        with self.assertRaises(maintenance.ComponentError):
            self.records()
        record["path"] = local_path
        archive = self.packages / local_path
        outside = self.root / "outside.tar.gz"
        outside.write_bytes(archive.read_bytes())
        archive.unlink()
        archive.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "regular file inside"):
            self.records()

    def test_publisher_dispatches_configured_keychain_contract_and_preserves_previous(self):
        previous_path = self.previous(self.merge())
        original = previous_path.read_bytes()
        output = self.root / "candidate.json"
        def fixture_sign(raw, public_key, account, sparkle_bin, verifier):
            self.assertEqual(public_key, self.public)
            self.assertEqual(account, "disposable-test-account")
            self.assertEqual(sparkle_bin, Path("/tmp/official-sparkle-bin-fixture"))
            envelope = signed_envelope(json.loads(raw), self.key, self.signer)
            verifier.verify(raw, envelope["signature"], public_key)
            return envelope["signature"]
        with mock.patch.object(publisher, "PublicVerifier", return_value=mock.MagicMock(__enter__=mock.Mock(return_value=self.verifier))), \
                mock.patch.object(publisher, "sign_bytes", side_effect=fixture_sign) as sign:
            envelope = publisher.publish_catalog(self.catalog_path, self.packages, output,
                sparkle_bin=Path("/tmp/official-sparkle-bin-fixture"), settings_path=self.settings_path, previous=previous_path)
            self.assertEqual(sign.call_count, 1)
            self.assertEqual(json.loads(output.read_text()), envelope)
            with self.assertRaises(FileExistsError):
                publisher.publish_catalog(self.catalog_path, self.packages, output,
                    sparkle_bin=Path("/tmp/official-sparkle-bin-fixture"), settings_path=self.settings_path, previous=previous_path)
            self.assertEqual(sign.call_count, 1)
        self.assertEqual(previous_path.read_bytes(), original)
        self.assertEqual(publisher.load_previous_catalog(output, self.public, self.verifier, catalog_url=CATALOG_URL), self.merge())

    def test_invalid_history_never_reaches_signing_or_output(self):
        previous_path = self.previous(self.merge())
        envelope = json.loads(previous_path.read_text())
        envelope["signature"] = base64.b64encode(bytes(64)).decode()
        previous_path.write_text(json.dumps(envelope))
        output = self.root / "candidate.json"
        with mock.patch.object(publisher, "PublicVerifier", return_value=mock.MagicMock(__enter__=mock.Mock(return_value=self.verifier))), \
                mock.patch.object(publisher, "sign_bytes") as sign, self.assertRaises(ValueError):
            publisher.publish_catalog(self.catalog_path, self.packages, output,
                sparkle_bin=Path("/tmp/unused"), settings_path=self.settings_path, previous=previous_path)
        sign.assert_not_called()
        self.assertFalse(output.exists())

    def test_cli_rejects_removed_private_key_option(self):
        with mock.patch.object(sys, "stderr"), self.assertRaises(SystemExit) as raised:
            publisher.main([str(self.catalog_path), "--packages", str(self.packages), "--output", str(self.root / "candidate.json"),
                            "--sparkle-bin", "/tmp/unused", "--private-key", "/tmp/forbidden"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
