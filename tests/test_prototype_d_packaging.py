"""Reject source, dependency and weight-ownership mistakes in tiny offline packages."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_app as build
import prepare_module_packages as packages
import verify_app_bundle as verifier
import build_counting_stats as native_stats


class ProductionPackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native_temp = tempfile.TemporaryDirectory(prefix="Leap-native-statistics-test-", dir="/private/tmp")
        cls.native_library = Path(cls.native_temp.name) / native_stats.LIBRARY_NAME
        cls.native_record = native_stats.build_library(native_stats.ROOT / native_stats.SOURCE_PATH, cls.native_library)

    @classmethod
    def tearDownClass(cls):
        cls.native_temp.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ProjectLeap-production-package-test-", dir="/private/tmp")
        self.root = Path(self.temporary.name)
        self.model_bytes = b"small independently owned model fixture"
        self.model_hash = hashlib.sha256(self.model_bytes).hexdigest()
        self.dependency = dict(verifier.MODEL_DEPENDENCY, sha256=self.model_hash)
        self.patches = [mock.patch.object(verifier, "MODEL_SHA256", self.model_hash),
                        mock.patch.object(verifier, "MODEL_DEPENDENCY", self.dependency)]
        for patch in self.patches:
            patch.start()
        self.counter = 0

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temporary.cleanup()

    def make_package(self, component, *, payload=None, descriptor_changes=None, expected_sources=None, licenses=None, include_native=True):
        self.counter += 1
        tree = self.root / str(self.counter)
        tree.mkdir()
        descriptor = {"schema_version": 4, "component_id": component,
                      "component_version": verifier.MODULE_VERSIONS.get(component, verifier.MODEL_VERSION),
                      "platform": "macos-arm64"}
        if component in verifier.MODULE_VERSIONS:
            source = ("engine/project_leap_2d/__init__.py" if component == "project-leap-2d"
                      else "engine/leap_cell_counting/__init__.py")
            files = {"ThirdPartyNotices/ProjectLeap/LICENSE": b"license",
                     source: b"# fixed source\n"}
            descriptor.update(engine="engine", science_runtime=verifier.SCIENCE_RUNTIME_DEPENDENCY,
                              scientific_version=verifier.SCIENTIFIC_VERSIONS[component], model_component=self.dependency,
                              **{key: value for key, value in verifier.MODULE_HOST_CONTRACT.items() if key != "platform"})
            if component == "project-leap-2d":
                descriptor["fiji_component"] = "fiji"
                files["engine/VERSION"] = b"1.0.1\n"
        elif component == "cellpose-model":
            files = {"Models/cpsam_v2": self.model_bytes, "ThirdPartyNotices/Cellpose/LICENSE": b"license"}
            descriptor.update(schema_version=2, models="Models", model_file="cpsam_v2", model_sha256=self.model_hash)
        else:
            raise AssertionError(component)
        if component == "cell-counting":
            descriptor["native_statistics"] = {key: self.native_record[key] for key in
                                               ("path", "sha256", "source_sha256", "abi_version")}
            if include_native:
                files[native_stats.PACKAGE_PATH] = self.native_library.read_bytes()
        files.update(payload or {})
        descriptor.update(descriptor_changes or {})
        for name, content in files.items():
            destination = tree / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        entries = {path.relative_to(tree).as_posix(): packages.entry(path, tree) for path in tree.rglob("*")}
        sources = {name: tree / name for name in entries}
        archive = self.root / f"{self.counter}.tar.gz"
        record = packages.write_package(archive, descriptor, entries, sources)
        if expected_sources is None:
            expected_sources = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()
                                if name.startswith("engine/") and name != native_stats.PACKAGE_PATH}
        return verifier.verify_package(archive, component, record, expected_sources, licenses,
                                       self.native_record if component == "cell-counting" else None)

    def test_independent_modules_accept_without_embedded_model(self):
        for component in verifier.MODULE_VERSIONS:
            with self.subTest(component=component):
                result = self.make_package(component)
                self.assertTrue(result["passed"], result)
                self.assertEqual(result["model_weight_files"], [])

    def test_module_rejects_duplicate_model_even_with_valid_archive_manifest(self):
        for path in ("Models/cpsam_v2", "site-packages/renamed-weight"):
            with self.subTest(path=path):
                result = self.make_package("cell-counting", payload={path: self.model_bytes})
                self.assertFalse(result["passed"])
                self.assertFalse(result["model_weight_ownership_matches"])

    def test_module_rejects_embedded_scientific_runtime(self):
        for path in ("python/bin/python3.9", "site-packages/numpy/__init__.py"):
            with self.subTest(path=path):
                self.assertFalse(self.make_package("cell-counting", payload={path: b"duplicated runtime"})["passed"])

    def test_seed_excludes_suites_but_preserves_imported_testing_helpers(self):
        from build.release_policy import excluded_seed_entry
        for name in ("site-packages/numpy/tests/test_core.py", "site-packages/scipy/io/tests/data/fixture.mat",
                     "site-packages/torch/testing/_internal/common_utils.py", "python/lib/python3.9/test/test_os.py"):
            self.assertTrue(excluded_seed_entry(name), name)
        for name in ("site-packages/numpy/testing/__init__.py", "site-packages/scipy/_lib/_testutils.py",
                     "site-packages/torch/testing/__init__.py", "site-packages/torch/testing/_utils.py",
                     "site-packages/torch/testing/_internal/logging_tensor.py",
                     "site-packages/torch/testing/_internal/distributed/fake_pg.py"):
            self.assertFalse(excluded_seed_entry(name), name)

    def test_app_source_allowlist_excludes_test_controls(self):
        from build.release_policy import APP_FILES
        self.assertNotIn("probe.py", APP_FILES)
        self.assertIn("release-config.json", APP_FILES)
        self.assertIn("release_catalog.py", APP_FILES)
        for name in ("run_details.py", "resource_monitor.py", "qml/components/RunDetails.qml"):
            self.assertIn(name, APP_FILES)
            self.assertIn("app/leap_desktop/" + name, verifier.REQUIRED_UI_SOURCES)
        self.assertNotIn("docs/cell-counting.html", APP_FILES)
        self.assertNotIn("docs/project-leap-2d.html", APP_FILES)
        self.assertIn("docs/licenses.html", APP_FILES)
        self.assertIn("docs/references.html", APP_FILES)

    def test_counting_rejects_fluorescence_dependency(self):
        result = self.make_package("cell-counting", descriptor_changes={"fiji_component": "fiji"})
        self.assertFalse(result["passed"])
        self.assertFalse(result["descriptor_matches"])

    def test_shared_model_requires_exact_hash_and_one_weight(self):
        self.assertTrue(self.make_package("cellpose-model")["passed"])
        for payload in ({"Models/cpsam_v2": b"altered"}, {"Models/extra-model": b"extra"}):
            with self.subTest(payload=list(payload)):
                result = self.make_package("cellpose-model", payload=payload)
                self.assertFalse(result["passed"])
                self.assertFalse(result["model_weight_ownership_matches"])

    def test_module_rejects_wrong_shared_model_hash(self):
        result = self.make_package("cell-counting", descriptor_changes={
            "model_component": dict(self.dependency, sha256="0" * 64)})
        self.assertFalse(result["passed"])

    def test_changed_or_missing_source_fails_independent_verification(self):
        for sources in ({"engine/leap_cell_counting/__init__.py": "0" * 64},
                        {"engine/leap_cell_counting/missing.py": "0" * 64}):
            with self.subTest(sources=sources):
                result = self.make_package("cell-counting", expected_sources=sources)
                self.assertFalse(result["passed"])

    def test_redistributed_license_must_match_frozen_source(self):
        path = "ThirdPartyNotices/Cellpose/LICENSE"
        expected = {path: hashlib.sha256(b"license").hexdigest()}
        self.assertTrue(self.make_package("cellpose-model", licenses=expected)["passed"])
        result = self.make_package("cellpose-model", licenses=expected, payload={path: b"changed"})
        self.assertFalse(result["passed"])
        self.assertEqual(result["license_mismatches"], [path])

    def test_engine_sources_have_exactly_one_module_owner(self):
        sources = {"engine/VERSION": "version", "engine/project_leap_2d/run.py": "fluor",
                   "engine/leap_cell_counting/run.py": "count"}
        fluor = verifier.module_sources_for("project-leap-2d", sources)
        count = verifier.module_sources_for("cell-counting", sources)
        self.assertEqual(set(fluor), {"engine/VERSION", "engine/project_leap_2d/run.py"})
        self.assertEqual(set(count), {"engine/leap_cell_counting/run.py"})
        self.assertEqual(set(fluor) | set(count), set(sources))
        self.assertFalse(set(fluor) & set(count))

    def test_counting_native_library_is_required_and_bound_to_signed_bytes(self):
        missing = self.make_package("cell-counting", include_native=False)
        self.assertFalse(missing["passed"])
        self.assertFalse(missing["counting_native_ownership_matches"])
        damaged = self.make_package("cell-counting", payload={native_stats.PACKAGE_PATH: b"damaged library"})
        self.assertFalse(damaged["passed"])
        self.assertFalse(damaged["counting_native_verification"]["passed"])
        altered = self.make_package("cell-counting", descriptor_changes={"native_statistics": {
            **{key: self.native_record[key] for key in ("path", "sha256", "source_sha256", "abi_version")},
            "source_sha256": "0" * 64}})
        self.assertFalse(altered["passed"])
        self.assertFalse(altered["descriptor_matches"])

    def test_counting_native_library_cannot_enter_fluorescence_package(self):
        result = self.make_package("project-leap-2d", payload={native_stats.PACKAGE_PATH: self.native_library.read_bytes()})
        self.assertFalse(result["passed"])
        self.assertFalse(result["counting_native_ownership_matches"])

    def test_native_library_contract_checks_current_binary_and_abi(self):
        result = native_stats.library_contract(self.native_library)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["architecture"], "arm64")
        self.assertEqual(result["minimum_macos"], "27.0")
        self.assertEqual(set(result["exports"]), native_stats.EXPECTED_EXPORTS)
        self.assertEqual(result["runtime_load"]["abi"], "1")
        incompatible = self.root / "incompatible.c"
        incompatible.write_text("""#include <stdint.h>
__attribute__((visibility("default"))) int leap_counting_stats_abi_version(void) { return 2; }
__attribute__((visibility("default"))) int leap_counting_stats_u8(void) { return 0; }
__attribute__((visibility("default"))) int leap_counting_stats_u16(void) { return 0; }
""")
        with self.assertRaisesRegex(RuntimeError, "contract failed"):
            native_stats.build_library(incompatible, self.root / "incompatible.dylib")

    def test_package_cache_invalidates_changed_native_source_builder_or_identity(self):
        engine = self.root / "engine"
        engine.mkdir()
        inputs = {"runtime/seed/content-manifest.json": packages.json_bytes({"entries": {
                      "site-packages/science/__init__.py": {"type": "file", "sha256": "fixed"}}}),
                  "tools/prepare_module_packages.py": b"package builder",
                  "tools/build_counting_stats.py": b"native builder",
                  "tools/build/release_policy.py": b"policy",
                  "tools/build/verify_seed_runtime.py": b"validator",
                  "tools/build/licenses/models/InstanSeg-APACHE-2.0.txt": b"model license",
                  "tools/build/licenses/models/InstanSeg-NOTICE.md": b"model attribution", "LICENSE": b"license", "NOTICE": b"notice",
                  native_stats.SOURCE_PATH: b"native source"}
        for name, content in inputs.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        destination = self.root / "packages"
        destination.mkdir()
        catalog = {"schema_version": 1, "packages": {}}
        versions = {**verifier.MODULE_VERSIONS, "science-runtime": verifier.SCIENCE_RUNTIME_VERSION,
                    "cellpose-model": verifier.MODEL_VERSION, "fiji": "20260718.0417"}
        for component in sorted(verifier.EXPECTED_COMPONENTS):
            relative = f"{component}/{versions[component]}/package.tar.gz"
            archive = destination / relative
            archive.parent.mkdir(parents=True)
            archive.write_bytes(component.encode())
            dependencies = ({"science-runtime": verifier.SCIENCE_RUNTIME_VERSION, "cellpose-model": verifier.MODEL_VERSION,
                             **({"fiji": "20260718.0417"} if component == "project-leap-2d" else {})}
                            if component in verifier.MODULE_VERSIONS else {})
            catalog["packages"][component] = {"path": relative, "version": versions[component],
                                              "sha256": packages.digest(archive), "dependencies": dependencies,
                                              **(verifier.MODULE_HOST_CONTRACT if component in verifier.MODULE_VERSIONS else {})}
        (destination / "catalog.json").write_bytes(packages.json_bytes(catalog))
        record = {"engine_entries": {"fixed": True},
                  "seed_manifest_sha256": packages.digest(self.root / "runtime/seed/content-manifest.json"),
                  "package_builder_sha256": packages.digest(self.root / "tools/prepare_module_packages.py"),
                  "release_policy_sha256": packages.digest(self.root / "tools/build/release_policy.py"),
                  "excluded_seed_entries": {},
                  "license_inputs": packages.license_input_hashes(self.root),
                  "seed_validator_sha256": packages.digest(self.root / "tools/build/verify_seed_runtime.py"),
                  "scientific_runtime_validation": {"performed": True},
                  "counting_native": {"source_sha256": packages.digest(self.root / native_stats.SOURCE_PATH),
                                      "builder_sha256": packages.digest(self.root / "tools/build_counting_stats.py"),
                                      "signing_identity": "-"},
                  "catalog": catalog, "catalog_sha256": packages.digest(destination / "catalog.json")}
        (destination / "package-build.json").write_bytes(packages.json_bytes(record))
        with mock.patch.object(build, "ROOT", self.root), \
                mock.patch.object(build, "engine_inventory", return_value={"fixed": True}), \
                mock.patch.object(build, "prepare", return_value={"rebuilt": True}) as prepare:
            self.assertEqual(build.package_record(destination, engine), record)
            prepare.assert_not_called()
            policy_path = self.root / "tools/build/release_policy.py"
            policy_path.write_bytes(b"UI-only allowlist change")
            self.assertEqual(build.package_record(destination, engine), record)
            prepare.assert_not_called()
            policy_path.write_bytes(inputs["tools/build/release_policy.py"])
            with mock.patch.object(build, "excluded_seed_entry", return_value=True):
                self.assertEqual(build.package_record(destination, engine), {"rebuilt": True})
                prepare.assert_called_once()
                prepare.reset_mock()
            with mock.patch.object(verifier, "SCIENCE_RUNTIME_VERSION", "changed science dependency"):
                self.assertEqual(build.package_record(destination, engine), {"rebuilt": True})
                prepare.assert_called_once()
                prepare.reset_mock()
            for changed in (native_stats.SOURCE_PATH, "tools/build_counting_stats.py", "tools/prepare_module_packages.py", "tools/build/verify_seed_runtime.py", "LICENSE"):
                path = self.root / changed
                path.write_bytes(b"changed bytes")
                self.assertEqual(build.package_record(destination, engine), {"rebuilt": True})
                prepare.assert_called_once()
                prepare.reset_mock()
                path.write_bytes(inputs[changed])
            self.assertEqual(build.package_record(destination, engine, "changed identity"), {"rebuilt": True})
            prepare.assert_called_once()
            self.assertEqual(prepare.call_args.kwargs["identity"], "changed identity")

    def test_catalog_rejects_extra_components_or_unbound_paths(self):
        versions = {**verifier.MODULE_VERSIONS, "cellpose-model": verifier.MODEL_VERSION, "fiji": "20260718.0417", "science-runtime": verifier.SCIENCE_RUNTIME_VERSION}
        catalog = {"schema_version": 1, "packages": {
            component: {"version": version, "path": f"{component}/{version}/package.tar.gz",
                        "dependencies": ({"science-runtime": verifier.SCIENCE_RUNTIME_VERSION, "cellpose-model": verifier.MODEL_VERSION,
                                          **({"fiji": "20260718.0417"} if component == "project-leap-2d" else {})}
                                         if component in verifier.MODULE_VERSIONS else {}),
                        **(verifier.MODULE_HOST_CONTRACT if component in verifier.MODULE_VERSIONS else {})}
            for component, version in versions.items()}}
        self.assertTrue(verifier.catalog_contract(catalog))
        for field in verifier.MODULE_HOST_CONTRACT:
            incompatible = json.loads(json.dumps(catalog))
            del incompatible["packages"]["cell-counting"][field]
            self.assertFalse(verifier.catalog_contract(incompatible))
        altered = json.loads(json.dumps(catalog))
        altered["packages"]["cell-counting"]["path"] = "../package.tar.gz"
        self.assertFalse(verifier.catalog_contract(altered))
        del catalog["packages"]["cell-counting"]
        self.assertFalse(verifier.catalog_contract(catalog))

    def test_output_guard_preserves_previous_release_trees(self):
        retained = ("runtime/packages", "runtime/prototype-c", "artifacts/prototype-c")
        with mock.patch.object(packages, "ROOT", self.root):
            for relative in retained:
                directory = self.root / relative
                directory.mkdir(parents=True, exist_ok=True)
                sentinel = directory / "unchanged"
                sentinel.write_bytes(b"prior release")
                for candidate in (directory, directory / "replacement", directory.parent):
                    with self.subTest(candidate=str(candidate)), self.assertRaises(ValueError):
                        packages.assert_isolated_output(candidate)
                self.assertEqual(sentinel.read_bytes(), b"prior release")
            packages.assert_isolated_output(self.root / "runtime/prototype-d/packages")
            packages.assert_isolated_output(self.root / "artifacts/prototype-d/final")

    def test_catalog_rejects_a_different_counting_package_version(self):
        versions = {**verifier.MODULE_VERSIONS, "cellpose-model": verifier.MODEL_VERSION, "fiji": "20260718.0417", "science-runtime": verifier.SCIENCE_RUNTIME_VERSION}
        versions["cell-counting"] = "2.1.0"
        catalog = {"schema_version": 1, "packages": {
            component: {"version": version, "path": f"{component}/{version}/package.tar.gz",
                        "dependencies": ({"science-runtime": verifier.SCIENCE_RUNTIME_VERSION, "cellpose-model": verifier.MODEL_VERSION,
                                          **({"fiji": "20260718.0417"} if component == "project-leap-2d" else {})}
                                         if component in verifier.MODULE_VERSIONS else {}),
                        **(verifier.MODULE_HOST_CONTRACT if component in verifier.MODULE_VERSIONS else {})}
            for component, version in versions.items()}}
        self.assertFalse(verifier.catalog_contract(catalog))

    def test_delivery_root_change_does_not_rebind_scientific_baseline(self):
        with mock.patch.object(build, "PROTOTYPE_ROOT", self.root / "unrelated-delivery"):
            evidence = build.scientific_source_evidence()
        self.assertEqual(evidence["scientific_baseline_source"]["sha256"], verifier.SCIENCE_BASELINE_SHA256)
        self.assertEqual(evidence["engineering_adapter_changes"]["sha256"], verifier.ENGINEERING_REVIEW_SHA256)
        self.assertTrue(evidence["scientific_baseline_source"]["path"].startswith("artifacts/prototype-c/baseline/"))

    def science_review_fixture(self):
        baseline = self.root / "baseline"
        baseline.mkdir(exist_ok=True)
        raw = (build.SCIENCE_BASELINE_ROOT / "source-sha256.json").read_text()
        (baseline / "source-sha256.json").write_text(raw)
        originals = json.loads(raw)
        changes = {path: {"before_sha256": before,
                          "after_sha256": hashlib.sha256((path + " reviewed replacement").encode()).hexdigest(),
                          "rationale": "Fixture for an explicit reviewed engineering replacement"}
                   for path, before in verifier.ENGINEERING_ADAPTER_ORIGINALS.items()}
        approval = {"schema_version": 1, "reviewed_by": "root", "baseline_sha256": verifier.SCIENCE_BASELINE_SHA256,
                    "changes": changes, "evidence": ["tests/test_prototype_d_packaging.py"]}
        approval_path = baseline / "engineering-adapter-changes.json"
        approval_path.write_text(json.dumps(approval))
        review_patch = mock.patch.object(verifier, "ENGINEERING_REVIEW_SHA256", packages.digest(approval_path))
        review_patch.start()
        self.patches.append(review_patch)
        sources = {path: value for path, value in originals.items() if path.startswith("engine/")}
        sources.update({path: change["after_sha256"] for path, change in changes.items()})
        sources["engine/leap_cell_counting/run.py"] = "new counting source"
        pruning_path = baseline / "engineering-pruning.json"
        pruning_path.write_text(json.dumps({"schema_version": 1, "reviewed_by": "root",
                                           "baseline_sha256": verifier.SCIENCE_BASELINE_SHA256,
                                           "changes": {}, "evidence": ["explicit empty fixture"]}))
        for patch in (mock.patch.object(build, "PRUNING_MANIFEST", pruning_path),
                      mock.patch.object(verifier, "PRODUCTION_ENGINEERING_REVIEW_SHA256", packages.digest(pruning_path))):
            patch.start()
            self.patches.append(patch)
        return sources, originals, approval

    def test_science_baseline_accepts_only_reviewed_two_file_replacements(self):
        sources, original, approval = self.science_review_fixture()
        with mock.patch.object(build, "SCIENCE_BASELINE_ROOT", self.root / "baseline"):
            baseline = build.scientific_baseline(sources)
            self.assertEqual(len(baseline), 95)
            self.assertEqual(len([path for path, value in baseline.items() if sources[path] == value]), 93)
            adapter = "engine/project_leap_2d/desktop_adapter.py"
            for current in ({}, dict(sources, **{adapter: "unreviewed new bytes"}),
                            dict(sources, **{adapter: original[adapter]}),
                            dict(sources, **{"engine/project_leap_2d/analysis_workflow.py": "changed"}),
                            dict(sources, **{"engine/project_leap_2d/new.py": "extra"})):
                with self.subTest(current=current), self.assertRaises(RuntimeError):
                    build.scientific_baseline(current)

    def test_production_pruning_requires_pinned_exact_before_after_and_deletion(self):
        sources, original, approval = self.science_review_fixture()
        path = "engine/project_leap_2d/analysis_workflow.py"
        pruning_path = self.root / "baseline/engineering-pruning.json"
        pruning = json.loads(pruning_path.read_text())
        pruning["changes"][path] = {"before_sha256": sources[path], "after_sha256": None,
                                     "rationale": "Explicit deletion fixture"}
        pruning_path.write_text(json.dumps(pruning))
        del sources[path]
        with mock.patch.object(build, "SCIENCE_BASELINE_ROOT", self.root / "baseline"):
            with self.assertRaises(RuntimeError):
                build.scientific_baseline(sources)
            with mock.patch.object(verifier, "PRODUCTION_ENGINEERING_REVIEW_SHA256", packages.digest(pruning_path)):
                self.assertEqual(len(build.scientific_baseline(sources)), 95)
            pruning["changes"][path]["before_sha256"] = "0" * 64
            pruning_path.write_text(json.dumps(pruning))
            with mock.patch.object(verifier, "PRODUCTION_ENGINEERING_REVIEW_SHA256", packages.digest(pruning_path)):
                with self.assertRaises(RuntimeError):
                    build.scientific_baseline(sources)

    def test_science_review_manifest_edit_or_scope_expansion_is_rejected(self):
        sources, original, approval = self.science_review_fixture()
        review_path = self.root / "baseline/engineering-adapter-changes.json"
        adapter = "engine/project_leap_2d/desktop_adapter.py"
        with mock.patch.object(build, "SCIENCE_BASELINE_ROOT", self.root / "baseline"):
            approval["changes"][adapter]["after_sha256"] = "f" * 64
            review_path.write_text(json.dumps(approval))
            sources[adapter] = "f" * 64
            with self.assertRaises(RuntimeError):
                build.scientific_baseline(sources)
            approval["changes"]["engine/project_leap_2d/analysis_workflow.py"] = dict(approval["changes"][adapter])
            review_path.write_text(json.dumps(approval))
            # Even an independently pinned record cannot widen the two-file scope.
            with mock.patch.object(verifier, "ENGINEERING_REVIEW_SHA256", packages.digest(review_path)):
                with self.assertRaises(RuntimeError):
                    build.scientific_baseline(sources)

    def test_science_review_rejects_wrong_original_hash_and_missing_approval(self):
        sources, original, approval = self.science_review_fixture()
        review_path = self.root / "baseline/engineering-adapter-changes.json"
        adapter = "engine/project_leap_2d/desktop_adapter.py"
        with mock.patch.object(build, "SCIENCE_BASELINE_ROOT", self.root / "baseline"):
            for change in ("before_sha256", "reviewed_by"):
                broken = json.loads(json.dumps(approval))
                if change == "before_sha256":
                    broken["changes"][adapter][change] = "0" * 64
                else:
                    del broken[change]
                review_path.write_text(json.dumps(broken))
                with mock.patch.object(verifier, "ENGINEERING_REVIEW_SHA256", packages.digest(review_path)):
                    with self.assertRaises(RuntimeError):
                        build.scientific_baseline(sources)

    def test_prepare_actual_five_package_output_passes_independent_verifier(self):
        seed, engine, output = (self.root / name for name in ("seed", "engine", "packages"))
        launcher = "Fiji/Fiji.app/Contents/MacOS/fiji-macos-arm64"
        cellpose_license = "site-packages/cellpose-4.2.1.1.dist-info/licenses/LICENSE"
        seed_files = {
            "python/bin/python3.9": b"interpreter", "python/LICENSE.txt": b"python license",
            "Models/cpsam_v2": self.model_bytes, cellpose_license: b"cellpose license",
            launcher: b"fiji launcher", "Fiji/LICENSE.txt": b"fiji license",
            "Fiji/Fiji.app/Contents/Info.plist": plistlib.dumps({"CFBundleVersion": "fixture"}),
        }
        descriptor = {"schema_version": 1, "component_id": "project-leap-2d", "component_version": "1.0.1",
                      "platform": "macos-arm64", "python": "python/bin/python3.9", "site_packages": "site-packages",
                      "models": "Models", "fiji_launcher": launcher,
                      "environment_contract": "c076335b5cbac2c440f927cf92db02ac60f91136170c54b0a83c2db333f8da9a"}
        seed_files["runtime.json"] = packages.json_bytes(descriptor)
        for name, content in seed_files.items():
            path = seed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        seed_entries = {path.relative_to(seed).as_posix(): packages.entry(path, seed) for path in seed.rglob("*")}
        manifest = {"schema_version": 1, "component_id": "project-leap-2d", "component_version": "1.0.1",
                    "entries": seed_entries}
        (seed / packages.MANIFEST).write_bytes(packages.json_bytes(manifest))
        for name in ("VERSION", "project_leap_2d/__main__.py", "leap_cell_counting/__main__.py"):
            path = engine / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("1.0.1\n" if name == "VERSION" else "# fixture\n")
        engine_sources = {"engine/" + path.relative_to(engine).as_posix(): packages.digest(path)
                          for path in engine.rglob("*") if path.is_file()}
        for name in ("LICENSE", "NOTICE", "tools/build/licenses/models/InstanSeg-APACHE-2.0.txt", "tools/build/licenses/models/InstanSeg-NOTICE.md"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture license")
        with mock.patch.object(packages, "MODEL_SHA256", self.model_hash), \
                mock.patch.object(packages, "verify_pruned_seed", return_value={"performed": False, "fixture": True}):
            result = packages.prepare(seed, engine, output)
        self.assertTrue(verifier.catalog_contract(result["catalog"]))
        self.assertEqual(result["package_builder_sha256"], packages.digest(Path(packages.__file__)))
        for component in verifier.MODULE_VERSIONS:
            self.assertEqual(result["catalog"]["packages"][component]["minimum_host_build"], 1)
        with mock.patch.object(build, "ROOT", self.root):
            # Point the build helper at this fixture's seed through its documented layout.
            runtime = self.root / "runtime"
            runtime.mkdir()
            (runtime / "seed").symlink_to(seed, target_is_directory=True)
            licenses = build.component_license_sources()
        for component, record in result["catalog"]["packages"].items():
            with self.subTest(component=component):
                verified = verifier.verify_package(output / record["path"], component, record,
                                                    verifier.module_sources_for(component, engine_sources), licenses[component],
                                                    result["counting_native"] if component == "cell-counting" else None)
                self.assertTrue(verified["passed"], verified)
        self.assertEqual((seed / packages.MANIFEST).read_bytes(), packages.json_bytes(manifest))
        previous_archives = {name: record["sha256"] for name, record in result["catalog"]["packages"].items()}
        with mock.patch.object(packages, "MODEL_SHA256", self.model_hash), \
                mock.patch.object(packages, "MODULE_HOST_CONTRACT", dict(packages.MODULE_HOST_CONTRACT, minimum_host_build=2)), \
                mock.patch.object(packages, "verify_pruned_seed", return_value={"performed": False, "fixture": True}):
            updated = packages.prepare(seed, engine, output, replace=True)
        self.assertEqual(set(updated["reused_components"]), {"science-runtime", "cellpose-model", "fiji"})
        for component in verifier.EXPECTED_COMPONENTS:
            if component in verifier.MODULE_VERSIONS:
                self.assertEqual(updated["catalog"]["packages"][component]["minimum_host_build"], 2)
                self.assertNotEqual(updated["catalog"]["packages"][component]["sha256"], previous_archives[component])
            else:
                self.assertEqual(updated["catalog"]["packages"][component]["sha256"], previous_archives[component])


if __name__ == "__main__":
    unittest.main()
