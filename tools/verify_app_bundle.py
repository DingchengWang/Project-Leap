#!/usr/bin/env python3
"""Verify the sealed UI and its independently removable offline component packages."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import sys
import tarfile
import tempfile

from prepare_module_packages import digest
from build.release_policy import (APP_FILES, QML_MODULES, MODULE_HOST_CONTRACT, MODULE_VERSIONS, SCIENTIFIC_VERSIONS, SCIENCE_RUNTIME_VERSION,
                                  SCIENCE_RUNTIME_DEPENDENCY, excluded_seed_entry)
from build_counting_stats import (PACKAGE_PATH as COUNTING_LIBRARY_PATH, SOURCE_PATH as COUNTING_SOURCE_PATH,
                                  LIBRARY_NAME as COUNTING_LIBRARY_NAME, ABI_VERSION as COUNTING_ABI_VERSION,
                                  library_contract as counting_library_contract)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMPONENTS = {"project-leap-2d", "cell-counting", "science-runtime", "cellpose-model", "fiji"}
MODEL_VERSION = "4.2.1.1"
MODEL_SHA256 = "0f1cc3f7ecdd8a037a57c6c48d9d8921391be4cbce3fa9f13c3e3a2e1253c667"
MODEL_DEPENDENCY = {"id": "cellpose-model", "version": MODEL_VERSION,
                    "filename": "cpsam_v2", "sha256": MODEL_SHA256}
DELIVERY_NAME = "Project Leap"
BUNDLE_IDENTIFIER = "org.projectleap.prototype.d"
SCIENCE_BASELINE_SHA256 = "606ff719a8c74e0be446b36691fa415ce02855e65c4dfbd2b589d7015407597d"
ENGINEERING_REVIEW_SHA256 = "bd35c62f4eaa686115abe45bb15e8703544996703a9e6dc1092af98a4a223367"
PRODUCTION_ENGINEERING_REVIEW_SHA256 = "4d0ca60ff9b0278191a2c26e9fcb98d64343e135a42bd2315eb4fd54ac420f99"
ENGINEERING_ADAPTER_ORIGINALS = {
    "engine/project_leap_2d/desktop_paths.py": "6a07a5de2d0db06b6b88b6efdd035ee84f5d1ea6c0aabd47fdada5482bc6a7f3",
    "engine/project_leap_2d/desktop_adapter.py": "be9d6c2fc8753f2cd2f397b8a71b7e0f0f92c4021b833a90975ee3724bfa1358",
}
REQUIRED_UI_SOURCES = {
    "app/leap_desktop/counting.py", "app/leap_desktop/qml/pages/CountingInput.qml",
    "app/leap_desktop/qml/components/CountCanvas.qml",
    "app/leap_desktop/qml/Main.qml", "app/leap_desktop/import_worker.py",
    "app/leap_desktop/run_details.py", "app/leap_desktop/resource_monitor.py",
    "app/leap_desktop/qml/components/RunDetails.qml",
}
EXPECTED_NATIVE_EXPORTS = {
    "leap_appearance_create", "leap_appearance_snapshot", "leap_appearance_destroy",
    "leap_glass_available", "leap_glass_create", "leap_glass_view", "leap_glass_update",
    "leap_glass_destroy",
    "leap_storage_publish_directory",
    "leap_glass_age_create", "leap_glass_age_view", "leap_glass_age_update",
    "leap_glass_age_destroy", "leap_application_icon_update", "leap_verify_ed25519",
}


def module_sources_for(component_id, sources):
    prefix = {"project-leap-2d": "engine/project_leap_2d/",
              "cell-counting": "engine/leap_cell_counting/"}.get(component_id)
    return {path: value for path, value in sources.items()
            if prefix and (path.startswith(prefix) or (component_id == "project-leap-2d" and path == "engine/VERSION"))}


def catalog_contract(catalog):
    packages = catalog.get("packages", {})
    versions = dict(MODULE_VERSIONS, **{"cellpose-model": MODEL_VERSION, "fiji": "20260718.0417", "science-runtime": SCIENCE_RUNTIME_VERSION})
    dependencies = {component: ({"science-runtime": SCIENCE_RUNTIME_VERSION, "cellpose-model": MODEL_VERSION,
                                 **({"fiji": "20260718.0417"} if component == "project-leap-2d" else {})}
                                if component in MODULE_VERSIONS else {}) for component in EXPECTED_COMPONENTS}
    return bool(catalog.get("schema_version") == 1 and set(packages) == EXPECTED_COMPONENTS
                and all(record.get("version") == versions[component]
                        and record.get("path") == f"{component}/{versions[component]}/package.tar.gz"
                        and record.get("dependencies") == dependencies[component]
                        and (component not in MODULE_VERSIONS or all(record.get(key) == value for key, value in MODULE_HOST_CONTRACT.items()))
                        for component, record in packages.items()))


def scientific_source_contract(sources, baseline_evidence, adapter_evidence, pruning_evidence=None):
    """Check the original baseline, then apply two pinned engineering reviews."""
    problems = []
    documents = []
    for label, evidence in (("baseline", baseline_evidence), ("engineering adapters", adapter_evidence)):
        raw = evidence.get("json", "")
        if not isinstance(raw, str) or hashlib.sha256(raw.encode("utf-8")).hexdigest() != evidence.get("sha256"):
            problems.append(label + " evidence hash does not match")
            documents.append({})
            continue
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            value = {}
            problems.append(label + " evidence is not valid JSON")
        documents.append(value if isinstance(value, dict) else {})
    original, approval = documents
    baseline = {path: value for path, value in original.items() if path.startswith("engine/project_leap_2d/")}
    if baseline_evidence.get("sha256") != SCIENCE_BASELINE_SHA256 or len(baseline) != 95:
        problems.append("The completed original 95-file scientific baseline does not match")
    if not ENGINEERING_REVIEW_SHA256 or adapter_evidence.get("sha256") != ENGINEERING_REVIEW_SHA256:
        problems.append("The engineering adapter review manifest differs from the approved record")
    changes = approval.get("changes", {})
    evidence_paths = approval.get("evidence", [])
    if (approval.get("schema_version") != 1 or approval.get("reviewed_by") != "root"
            or approval.get("baseline_sha256") != SCIENCE_BASELINE_SHA256
            or not isinstance(changes, dict) or set(changes) != set(ENGINEERING_ADAPTER_ORIGINALS)
            or not isinstance(evidence_paths, list) or not evidence_paths
            or not all(isinstance(path, str) and path for path in evidence_paths)):
        problems.append("The engineering adapter review record is incomplete or has an unexpected scope")
        changes = {}
    expected = dict(baseline)
    for path, change in changes.items():
        if not isinstance(change, dict):
            problems.append("Invalid reviewed adapter entry: " + path)
            continue
        before, after = change.get("before_sha256"), change.get("after_sha256")
        if (before != ENGINEERING_ADAPTER_ORIGINALS[path] or baseline.get(path) != before
                or not isinstance(after, str) or not re.fullmatch(r"[0-9a-f]{64}", after)
                or after == before or not isinstance(change.get("rationale"), str) or not change["rationale"].strip()):
            problems.append("Invalid reviewed adapter hash pair: " + path)
        else:
            expected[path] = after
    pruning_changes = {}
    if pruning_evidence is not None:
        raw = pruning_evidence.get("json", "")
        if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != pruning_evidence.get("sha256"):
            problems.append("Production engineering evidence hash does not match")
        else:
            try:
                pruning = json.loads(raw)
            except (TypeError, ValueError):
                pruning = {}
            if not isinstance(pruning, dict):
                pruning = {}
            if (pruning_evidence.get("sha256") != PRODUCTION_ENGINEERING_REVIEW_SHA256
                    or pruning.get("reviewed_by") != "root"
                    or pruning.get("schema_version") != 1 or pruning.get("baseline_sha256") != SCIENCE_BASELINE_SHA256
                    or not isinstance(pruning.get("changes"), dict)
                    or not set(pruning.get("changes", {})).issubset(baseline)
                    or not isinstance(pruning.get("evidence"), list) or not pruning.get("evidence")
                    or not all(isinstance(path, str) and path for path in pruning.get("evidence", []))):
                problems.append("Production engineering review record is incomplete")
            else:
                pruning_changes = pruning["changes"]
                for path, change in pruning_changes.items():
                    if not isinstance(change, dict):
                        problems.append("Invalid production engineering entry: " + path)
                        continue
                    before, after = change.get("before_sha256"), change.get("after_sha256")
                    if (expected.get(path) != before or after == before
                            or not isinstance(change.get("rationale"), str) or not change["rationale"].strip()
                            or (after is not None and not re.fullmatch(r"[0-9a-f]{64}", str(after)))):
                        problems.append("Invalid production engineering hash pair: " + path)
                    elif after is None:
                        expected.pop(path, None)
                    else:
                        expected[path] = after
    current = {path: value for path, value in sources.items() if path.startswith("engine/project_leap_2d/")}
    mismatches = sorted(path for path in set(current) | set(expected) if current.get(path) != expected.get(path))
    if mismatches:
        problems.append("Scientific source bytes differ from the reviewed snapshot")
    return {"passed": not problems, "problems": problems, "source_mismatches": mismatches,
            "original_source_files": len(baseline), "unchanged_source_files": sum(current.get(path) == value for path, value in baseline.items()),
            "reviewed_adapter_files": sorted(changes), "baseline_sha256": baseline_evidence.get("sha256"),
            "engineering_manifest_sha256": adapter_evidence.get("sha256"),
            "production_engineering_files": sorted(pruning_changes),
            "production_engineering_manifest_sha256": (pruning_evidence or {}).get("sha256")}


def verify_package(archive_path, component_id, record, module_sources, license_sources=None, native_record=None):
    result = {"archive_sha256": digest(archive_path), "archive_bytes": archive_path.stat().st_size}
    entries = {}
    descriptor = manifest = None
    engine_mismatches = []
    duplicates = []
    unsafe_members = []
    weight_files = []
    native_files = []
    native_content = None
    native_record = native_record or {}
    expected_sources = dict(module_sources)
    if component_id == "cell-counting" and native_record.get("path") == COUNTING_LIBRARY_PATH:
        expected_sources[COUNTING_LIBRARY_PATH] = native_record.get("sha256")
    with tarfile.open(archive_path, "r|gz") as archive:
        for member in archive:
            name = member.name.rstrip("/")
            if name.rsplit("/", 1)[-1] == COUNTING_LIBRARY_NAME:
                native_files.append(name)
            if name in entries or (name == "content-manifest.json" and manifest is not None):
                duplicates.append(name)
            if (not name or name.startswith("/") or "\\" in name
                    or any(part in ("", ".", "..") for part in name.split("/"))):
                unsafe_members.append(name)
            if not (member.isfile() or member.isdir() or member.issym()):
                unsafe_members.append(name)
                continue
            if member.isdir():
                entries[name] = {"type": "directory", "mode": member.mode}
            elif member.issym():
                # Normpath resolves archive-relative links without touching disk.
                target = os.path.normpath(os.path.join(os.path.dirname(name), member.linkname))
                if os.path.isabs(member.linkname) or target == ".." or target.startswith("../"):
                    unsafe_members.append(name)
                entries[name] = {"type": "symlink", "target": member.linkname}
            else:
                file_hash = hashlib.sha256()
                content = [] if name in ("runtime.json", "content-manifest.json", COUNTING_LIBRARY_PATH) else None
                with archive.extractfile(member) as stream:
                    for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                        file_hash.update(block)
                        if content is not None:
                            content.append(block)
                sha256 = file_hash.hexdigest()
                if name == COUNTING_LIBRARY_PATH:
                    native_content = b"".join(content)
                if sha256 == MODEL_SHA256 or name.rsplit("/", 1)[-1] == "cpsam_v2":
                    weight_files.append({"path": name, "sha256": sha256})
                if name == "content-manifest.json":
                    manifest = json.loads(b"".join(content))
                    result["manifest_sha256"] = sha256
                else:
                    entries[name] = {"type": "file", "mode": member.mode, "bytes": member.size, "sha256": sha256}
                    if name == "runtime.json":
                        descriptor = json.loads(b"".join(content))
                    if name.startswith("engine/") and sha256 != expected_sources.get(name):
                        engine_mismatches.append(name)
    if component_id in MODULE_VERSIONS:
        required = {"engine", "ThirdPartyNotices"}
    elif component_id == "science-runtime":
        required = {"python", "site-packages"}
    elif component_id == "cellpose-model":
        required = {"Models", "ThirdPartyNotices"}
    elif component_id == "fiji":
        required = {"Fiji"}
    else:
        raise ValueError("Unexpected delivery component: " + component_id)
    allowed = required | {"runtime.json"}
    roots = {name.split("/", 1)[0] for name in entries}
    descriptor_ok = bool(descriptor and descriptor.get("schema_version") == (4 if component_id in MODULE_VERSIONS or component_id == "science-runtime" else 2)
                         and descriptor.get("component_id") == component_id
                         and descriptor.get("component_version") == record["version"]
                         and (component_id == "cellpose-model" or descriptor.get("platform") == "macos-arm64"))
    engine_missing = sorted(set(expected_sources) - set(entries))
    native_verification = {"required": component_id == "cell-counting", "passed": component_id != "cell-counting"}
    native_ownership_ok = native_files == ([COUNTING_LIBRARY_PATH] if component_id == "cell-counting" else [])
    native_descriptor = None
    if component_id == "cell-counting":
        native_descriptor = {key: native_record.get(key) for key in ("path", "sha256", "source_sha256", "abi_version")}
        native_metadata_ok = bool(native_record.get("path") == COUNTING_LIBRARY_PATH
                                  and native_record.get("abi_version") == COUNTING_ABI_VERSION
                                  and native_record.get("source_path") == COUNTING_SOURCE_PATH
                                  and re.fullmatch(r"[0-9a-f]{64}", str(native_record.get("source_sha256", "")))
                                  and entries.get(COUNTING_LIBRARY_PATH, {}).get("sha256") == native_record.get("sha256")
                                  and entries.get(COUNTING_LIBRARY_PATH, {}).get("bytes") == native_record.get("bytes"))
        if native_content is not None and native_metadata_ok and native_ownership_ok:
            with tempfile.TemporaryDirectory(prefix="Leap-Counting-Stats-verify-", dir="/private/tmp") as directory:
                extracted = Path(directory) / COUNTING_LIBRARY_NAME
                extracted.write_bytes(native_content)
                native_verification = counting_library_contract(extracted)
        else:
            native_verification.update(metadata_matches=native_metadata_ok, passed=False)
    if component_id in MODULE_VERSIONS:
        descriptor_ok = bool(descriptor_ok and descriptor.get("engine") == "engine"
                             and descriptor.get("model_component") == MODEL_DEPENDENCY
                             and descriptor.get("component_version") == MODULE_VERSIONS[component_id]
                             and descriptor.get("science_runtime") == SCIENCE_RUNTIME_DEPENDENCY
                             and all(descriptor.get(key) == value for key, value in MODULE_HOST_CONTRACT.items())
                             and descriptor.get("scientific_version") == SCIENTIFIC_VERSIONS[component_id]
                             and "python" not in descriptor and "site_packages" not in descriptor
                             and "models" not in descriptor and "fiji_launcher" not in descriptor
                             and bool(module_sources))
        if component_id == "project-leap-2d":
            descriptor_ok = descriptor_ok and descriptor.get("fiji_component") == "fiji" and "native_statistics" not in descriptor
        else:
            descriptor_ok = (descriptor_ok and "fiji_component" not in descriptor
                             and descriptor.get("native_statistics") == native_descriptor)
        weight_ownership_ok = not weight_files
    elif component_id == "science-runtime":
        descriptor_ok = bool(descriptor_ok and descriptor.get("python") == "python/bin/python3.9"
                             and descriptor.get("python") in entries
                             and descriptor.get("site_packages") == "site-packages"
                             and descriptor.get("environment_contract") == "c076335b5cbac2c440f927cf92db02ac60f91136170c54b0a83c2db333f8da9a"
                             and not any(excluded_seed_entry(name) for name in entries))
        weight_ownership_ok = not weight_files
    elif component_id == "cellpose-model":
        descriptor_ok = bool(descriptor_ok and descriptor.get("component_version") == MODEL_VERSION
                             and descriptor.get("models") == "Models" and descriptor.get("model_file") == "cpsam_v2"
                             and descriptor.get("model_sha256") == MODEL_SHA256)
        weight_ownership_ok = (weight_files == [{"path": "Models/cpsam_v2", "sha256": MODEL_SHA256}]
                               and {path for path in entries if path.startswith("Models/")} == {"Models/cpsam_v2"})
    else:
        descriptor_ok = bool(descriptor_ok and descriptor.get("fiji_launcher", "").startswith("Fiji/")
                             and descriptor.get("fiji_launcher") in entries)
        weight_ownership_ok = not weight_files
    license_sources = license_sources or {}
    license_mismatches = [name for name, expected in license_sources.items()
                          if entries.get(name, {}).get("sha256") != expected]
    result.update(component_id=component_id, version=record["version"], entries_verified=len(entries),
                  descriptor=descriptor, unsafe_members=unsafe_members, duplicate_members=duplicates,
                  engine_source_mismatches=engine_mismatches, engine_source_missing=engine_missing,
                  component_roots=sorted(roots), descriptor_matches=descriptor_ok,
                  model_weight_files=weight_files, model_weight_ownership_matches=weight_ownership_ok,
                  counting_native_files=native_files, counting_native_ownership_matches=native_ownership_ok,
                  counting_native_verification=native_verification,
                  license_files_verified=len(license_sources), license_mismatches=license_mismatches,
                  content_manifest_matches=bool(manifest and manifest.get("entries") == entries))
    result["passed"] = bool(result["archive_sha256"] == record["sha256"]
                            and result["archive_bytes"] == record["bytes"]
                            and result.get("manifest_sha256") == record["manifest_sha256"]
                            and result["content_manifest_matches"] and descriptor_ok
                            and manifest.get("schema_version") == 1 and manifest.get("component_id") == component_id
                            and manifest.get("component_version") == record["version"]
                            and required.issubset(roots) and roots.issubset(allowed)
                            and weight_ownership_ok and not license_mismatches and native_ownership_ok and native_verification["passed"]
                            and not unsafe_members and not duplicates and not engine_mismatches and not engine_missing)
    return result


def architecture(path):
    result = subprocess.run(["/usr/bin/lipo", "-archs", str(path)], text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else result.stderr.strip()


def native_exports(library):
    """Read the public C symbol names without invoking any native operation."""
    result = subprocess.run(["/usr/bin/nm", "-gjU", str(library)], text=True, capture_output=True)
    symbols = sorted(line.strip()[1:] for line in result.stdout.splitlines()
                     if line.strip().startswith("_leap_"))
    missing = sorted(EXPECTED_NATIVE_EXPORTS - set(symbols))
    unexpected = sorted(set(symbols) - EXPECTED_NATIVE_EXPORTS)
    return {"symbols": symbols, "missing": missing, "unexpected": unexpected,
            "nm_exit_code": result.returncode, "nm_stderr": result.stderr,
            "passed": result.returncode == 0 and not missing and not unexpected}


def glass_runtime_contract(library):
    """Check the Swift bridge's deployment target and actual system-runtime load."""
    links = subprocess.run(["/usr/bin/otool", "-L", str(library)], text=True, capture_output=True)
    commands = subprocess.run(["/usr/bin/otool", "-l", str(library)], text=True, capture_output=True)
    linked = [line.strip().split(" (", 1)[0] for line in links.stdout.splitlines()[1:] if line.strip()]
    install_name = linked[0] if linked else None
    dependencies = linked[1:]
    rpaths = []
    minimum_macos = None
    command = None
    for line in commands.stdout.splitlines():
        text = line.strip()
        if text.startswith("cmd "):
            command = text.split(None, 1)[1]
        elif command == "LC_RPATH" and text.startswith("path "):
            rpaths.append(text[5:].split(" (offset", 1)[0])
        elif command == "LC_BUILD_VERSION" and text.startswith("minos "):
            minimum_macos = text.split(None, 1)[1]
    system_rpaths = {"/usr/lib/swift", "/usr/lib"}
    unexpected_rpaths = [path for path in rpaths if path not in system_rpaths]
    unexpected_dependencies = [path for path in dependencies
                               if not path.startswith(("/System/Library/Frameworks/", "/usr/lib/"))
                               and not (path.startswith("@rpath/libswift") and path.endswith(".dylib")
                                        and "/usr/lib/swift" in rpaths)]
    swiftui_linked = any("/SwiftUI.framework/" in path for path in dependencies)
    swift_core_linked = any(path.endswith("/libswiftCore.dylib") for path in dependencies)
    exports = native_exports(library)
    static_passed = bool(links.returncode == commands.returncode == 0
                         and install_name == "@rpath/libLeapGlass.dylib" and minimum_macos == "27.0"
                         and swiftui_linked and swift_core_linked
                         and not unexpected_dependencies and not unexpected_rpaths and exports["passed"])
    load_result = {"performed": False}
    if static_passed:
        # Import the library in an isolated process; never call its UI entry points.
        # Removing DYLD overrides proves the developer environment is not supplying Swift.
        environment = {key: value for key, value in os.environ.items() if not key.startswith("DYLD_")}
        try:
            loaded = subprocess.run([sys.executable, "-c", "import ctypes, sys; ctypes.CDLL(sys.argv[1])", str(library)],
                                    cwd="/private/tmp", env=environment, text=True, capture_output=True, timeout=20)
            load_result = {"performed": True, "exit_code": loaded.returncode,
                           "stderr": loaded.stderr, "tested_macos": platform.mac_ver()[0]}
        except subprocess.TimeoutExpired:
            load_result = {"performed": True, "timed_out": True, "tested_macos": platform.mac_ver()[0]}
    return {"install_name": install_name, "dependencies": dependencies, "rpaths": rpaths,
            "minimum_macos": minimum_macos, "swiftui_linked": swiftui_linked,
            "swift_core_linked": swift_core_linked, "unexpected_dependencies": unexpected_dependencies,
            "unexpected_rpaths": unexpected_rpaths, "exports": exports, "runtime_load": load_result,
            "passed": static_passed and load_result.get("exit_code") == 0}


def verify(app, build_report, output, packages=None, expected_build=None):
    app = Path(app).resolve()
    report = json.loads(Path(build_report).read_text())
    resources = app / "Contents/Resources"
    mismatches = [relative for relative, expected in report["source_manifest"].items()
                  if not (resources / relative).is_file() or digest(resources / relative) != expected]
    unexpected_sources = sorted({path.relative_to(resources).as_posix() for path in (resources / "app").rglob("*")
                                 if path.is_file()} - report["source_manifest"].keys())
    notice_mismatches = [relative for relative, expected in report.get("notice_manifest", {}).items()
                        if not (resources / relative).is_file() or digest(resources / relative) != expected]
    external_links = []
    forbidden = []
    forbidden_names = {"engine", "seed", "seed.tar.gz", "Fiji", "Models", "cpsam_v2",
                       "project_leap_2d", "leap_cell_counting", "torch", "scipy", "numpy", COUNTING_LIBRARY_NAME}
    for directory, dirs, files in os.walk(app, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink() and not path.resolve().is_relative_to(app):
                external_links.append({"path": str(path.relative_to(app)), "target": os.readlink(path)})
            # QtQml/Models is the UI list-model plugin, not scientific model weights.
            if name in forbidden_names and not (name == "Models" and path.parent.name == "QtQml"):
                forbidden.append(str(path.relative_to(app)))
    forbidden_ui_components = sorted({str(path.relative_to(app)) for path in app.rglob("*")
                                      if any(token in path.name for token in ("QtTest", "QtGraphs", "QtQuick3D", "QtWebEngine", "QtPdf", "QtVirtualKeyboard"))
                                      or path.name in {"qmltooling", "probe.py"}})
    qml_root = resources / "PySide6/Qt/qml"
    actual_qml_modules = {path.parent.relative_to(qml_root).as_posix() for path in qml_root.rglob("qmldir")}
    qml_modules_match = actual_qml_modules == QML_MODULES
    expected_app_sources = {"app/leap_desktop/" + name for name in APP_FILES}
    allowlist_match = set(report["source_manifest"]) == expected_app_sources
    release_configuration = json.loads((resources / "app/leap_desktop/release-config.json").read_text())
    production_configuration_valid = release_configuration == {"schema_version": 1, "catalog_url": "", "public_key": ""}
    main_source = (resources / "app/leap_desktop/__main__.py").read_text()
    production_entry_point = not any(flag in main_source for flag in
                                     ("--smoke-test", "--test-request", "--test-auto-measure", "--quit-after-ms", "--screenshot", "--native-library"))
    plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    sparkle_configuration_valid = ("SUFeedURL" not in plist and "SUPublicEDKey" not in plist
                                   and plist.get("SUEnableAutomaticChecks") is False
                                   and plist.get("SUAutomaticallyUpdate") is False
                                   and plist.get("SUVerifyUpdateBeforeExtraction") is True)
    executable = app / "Contents/MacOS" / plist["CFBundleExecutable"]
    signature = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], text=True, capture_output=True)
    architectures = {"app": architecture(executable),
                     "glass_bridge": architecture(app / "Contents/Frameworks/libLeapGlass.dylib"),
                     "sparkle_bridge": architecture(app / "Contents/Frameworks/libLeapSparkle.dylib")}
    glass_runtime = glass_runtime_contract(app / "Contents/Frameworks/libLeapGlass.dylib")
    package_root = Path(packages) if packages else app.parent / report["packages_directory"]
    catalog_path = package_root / "catalog.json"
    catalog = json.loads(catalog_path.read_text())
    if not catalog_contract(catalog) or not catalog_contract(report["packages"]):
        raise ValueError("Project Leap requires the exact five component identities, versions and archive paths")
    package_results = {component: verify_package(package_root / record["path"], component, record,
                                                module_sources_for(component, report["module_source_manifest"]),
                                                report.get("component_license_manifest", {}).get(component, {}),
                                                report.get("counting_native") if component == "cell-counting" else None)
                       for component, record in report["packages"]["packages"].items()}
    catalog_ok = digest(catalog_path) == report["packages_catalog_sha256"] and catalog == report["packages"]
    package_build_path = package_root / "package-build.json"
    package_build = json.loads(package_build_path.read_text())
    package_provenance_matches = bool(
        digest(package_build_path) == report.get("package_build_record_sha256")
        and package_build.get("release_policy_sha256") == report.get("package_original_release_policy_sha256")
        and package_build.get("catalog") == catalog
        and package_build.get("catalog_sha256") == report["packages_catalog_sha256"]
        and package_build.get("excluded_seed_entries") == report["excluded_seed_entries"])
    archive_paths = {path.relative_to(package_root).as_posix() for path in package_root.rglob("*.tar.gz")}
    archive_set_matches = archive_paths == {record["path"] for record in catalog["packages"].values()}
    baseline_evidence = report.get("scientific_baseline_source", {})
    adapter_evidence = report.get("engineering_adapter_changes", {})
    scientific_contract = scientific_source_contract(report["module_source_manifest"], baseline_evidence, adapter_evidence, report.get("engineering_pruning"))
    baseline = {path: value for path, value in json.loads(baseline_evidence.get("json", "{}")).items()
                if path.startswith("engine/project_leap_2d/")} if scientific_contract["passed"] else {}
    baseline_matches = bool(scientific_contract["passed"] and baseline == report.get("scientific_baseline_manifest")
                            and all(report.get("build_inputs", {}).get(evidence.get("path")) == evidence.get("sha256")
                                    for evidence in (baseline_evidence, adapter_evidence, report.get("engineering_pruning", {}))))
    required_ui_missing = sorted(REQUIRED_UI_SOURCES - report["source_manifest"].keys())
    notice_sources_match = (bool(report.get("notice_source_manifest"))
                            and report.get("notice_source_manifest") == report.get("notice_manifest"))
    component_licenses_present = (set(report.get("component_license_manifest", {})) == EXPECTED_COMPONENTS
                                  and all(report["component_license_manifest"].values()))
    counting_native_record = report.get("counting_native", {})
    counting_native_inputs_match = bool(
        report.get("native_build_inputs", {}).get(COUNTING_SOURCE_PATH) == counting_native_record.get("source_sha256")
        and report.get("build_inputs", {}).get("tools/build_counting_stats.py") == counting_native_record.get("builder_sha256")
        and counting_native_record.get("source_sha256") and counting_native_record.get("builder_sha256"))
    runtime_validation = report.get("scientific_runtime_validation", {})
    runtime_validation_passed = bool(runtime_validation.get("performed") is True
                                     and runtime_validation.get("exit_code") == 0
                                     and runtime_validation.get("cellpose_model_loaded") is True
                                     and runtime_validation.get("instanseg_model_loaded") is True
                                     and runtime_validation.get("numpy_scipy_computation") is True)
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "app": str(app),
              "app_version": plist.get("CFBundleShortVersionString"), "app_build": plist.get("CFBundleVersion"),
              "expected_build": expected_build,
              "app_display_name": plist.get("CFBundleDisplayName"), "delivery_name": app.parent.name,
              "bundle_identifier": plist.get("CFBundleIdentifier"),
              "minimum_macos": plist.get("LSMinimumSystemVersion"),
              "source_files_verified": len(report["source_manifest"]), "source_mismatches": mismatches,
              "unexpected_source_files": unexpected_sources,
              "notice_files_verified": len(report.get("notice_manifest", {})), "notice_mismatches": notice_mismatches,
              "notice_sources_match": notice_sources_match, "component_licenses_present": component_licenses_present,
              "scientific_runtime_validation_passed": runtime_validation_passed,
              "scientific_baseline_matches": baseline_matches, "scientific_source_contract": scientific_contract,
              "required_ui_sources_missing": required_ui_missing,
              "external_symlinks": external_links, "scientific_payload_inside_app": forbidden,
              "import_worker_present": (resources / "app/leap_desktop/import_worker.py").is_file(),
              "app_allowlist_matches": allowlist_match, "qml_modules_match": qml_modules_match,
              "qml_modules": sorted(actual_qml_modules), "forbidden_ui_components": forbidden_ui_components,
              "production_configuration_valid": production_configuration_valid, "production_entry_point": production_entry_point,
              "sparkle_configuration_valid": sparkle_configuration_valid,
              "architectures": architectures, "glass_runtime": glass_runtime,
              "codesign_exit_code": signature.returncode,
              "codesign_stderr": signature.stderr, "notarized": report["notarized"],
              "packages_directory": str(package_root), "catalog_matches": catalog_ok,
              "package_provenance_matches": package_provenance_matches,
              "archive_set_matches": archive_set_matches, "counting_native_inputs_match": counting_native_inputs_match,
              "packages": package_results}
    result["passed"] = bool(not mismatches and not unexpected_sources and not notice_mismatches and not external_links and not forbidden
                            and allowlist_match and qml_modules_match and not forbidden_ui_components
                            and production_configuration_valid and production_entry_point and sparkle_configuration_valid
                            and result["app_version"] == report["version"] == "1.0.0"
                            and result["app_build"] == report["build_number"] and result["minimum_macos"] == "27.0"
                            and isinstance(result["app_build"], str) and result["app_build"].isascii()
                            and result["app_build"].isdecimal() and int(result["app_build"]) >= 1
                            and (expected_build is None or result["app_build"] == expected_build)
                            and app.name == DELIVERY_NAME + ".app"
                            and result["app_display_name"] == "Project Leap"
                            and report.get("delivery_name") == DELIVERY_NAME
                            and result["delivery_name"] == DELIVERY_NAME
                            and result["bundle_identifier"] == BUNDLE_IDENTIFIER
                            and runtime_validation_passed and baseline_matches and not required_ui_missing and notice_sources_match and component_licenses_present
                            and result["import_worker_present"] and all(value == "arm64" for value in architectures.values())
                            and signature.returncode == 0 and glass_runtime["passed"]
                            and report["native_exports"]["symbols"] == glass_runtime["exports"]["symbols"]
                            and catalog_ok and package_provenance_matches and archive_set_matches and counting_native_inputs_match
                            and set(package_results) == EXPECTED_COMPONENTS
                            and all(item["passed"] for item in package_results.values())
                            and not report["source_changed_during_build"] and not report["native_inputs_changed_during_build"]
                            and bool(report.get("build_inputs")) and not report.get("build_inputs_changed_during_build", True))
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--build-report", type=Path, default=ROOT / "build/release-1.0.0/build-report.json")
    parser.add_argument("--expected-build", help="Require this exact positive build number; use 1 for the first public release")
    parser.add_argument("--packages", type=Path, help="Explicit offline package directory; defaults to app sibling")
    parser.add_argument("--output", type=Path, default=ROOT / "build/release-1.0.0/bundle-verification.json")
    args = parser.parse_args()
    if args.expected_build is not None and (not args.expected_build.isascii()
            or not args.expected_build.isdecimal() or int(args.expected_build) < 1):
        parser.error("Expected build must be a positive integer (1 or later)")
    app = args.app or Path(json.loads(args.build_report.read_text())["app"])
    verify(app, args.build_report, args.output, args.packages, args.expected_build)
