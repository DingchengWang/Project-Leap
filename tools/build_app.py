#!/usr/bin/env python3
"""Build the standalone UI and deliver optional scientific packages alongside it."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_module_packages import DEFAULT_PACKAGE_ROOT, PACKAGE_DIRECTORY, assert_isolated_output, digest, engine_inventory, prepare, module_notices, license_input_hashes
from build_counting_stats import SOURCE_PATH as COUNTING_SOURCE_PATH
from build.release_policy import app_payload, excluded_seed_entry
from verify_app_bundle import BUNDLE_IDENTIFIER, EXPECTED_COMPONENTS, native_exports, scientific_source_contract, catalog_contract
from release_support import load_settings, component_configuration, sparkle_configuration

ROOT = Path(__file__).resolve().parents[1]
DELIVERY_NAME = "Project Leap"
DMG_FILENAME = "Project Leap V1.0.0.dmg"
PROTOTYPE_ROOT = ROOT / "artifacts/prototype-d"
REFINEMENT_BASELINE_ROOT = ROOT / "artifacts/project-leap-final-refinement/baseline"
RELEASE_ROOT = ROOT / "artifacts/release-1.0.0"
RELEASE_OUTPUT_ROOT = ROOT / "build/release-1.0.0"
PRUNING_MANIFEST = RELEASE_ROOT / "engineering-pruning.json"
SCIENCE_BASELINE_ROOT = ROOT / "artifacts/prototype-c/baseline"


def run(command, env=None):
    print("Running:", " ".join(map(str, command)), flush=True)
    subprocess.run([str(part) for part in command], cwd=ROOT, env=env, check=True)


def source_inventory():
    result = {path.relative_to(ROOT).as_posix(): digest(path) for path in app_payload(ROOT)}
    for path in sorted((ROOT / "engine").rglob("*")):
        if (path.is_file() and "__pycache__" not in path.parts
                and path.suffix != ".pyc" and path.name != ".DS_Store"):
            result[path.relative_to(ROOT).as_posix()] = digest(path)
    return result


def native_inventory():
    paths = ["native/sparkle_bridge.m", "native/sparkle_bridge.h", "native/embed_sparkle.py", "native/catalog_signature.swift",
             "native/vendor/Sparkle-2.10.0.provenance.json", "native/glass_bridge.swift",
             "native/glass_bridge.h", "native/storage_bridge.swift", "native/build_glass.py",
             "native/selection_interaction.swift", "native/glass_selection.swift", "native/PublicGlassAPI.h",
             COUNTING_SOURCE_PATH]
    return {relative: digest(ROOT / relative) for relative in paths}


def build_inventory():
    """Track all build code and license input bytes alongside app sources."""
    files = [ROOT / "tools" / name for name in
             ("build_app.py", "prepare_module_packages.py", "verify_app_bundle.py", "build_counting_stats.py", "release_support.py")]
    files += [SCIENCE_BASELINE_ROOT / name for name in
              ("source-sha256.json", "engineering-adapter-changes.json", "completion-from-b.json")]
    files.append(PROTOTYPE_ROOT / "baseline/source-sha256.json")
    files.append(REFINEMENT_BASELINE_ROOT / "source-sha256.json")
    files.extend([PRUNING_MANIFEST, ROOT / "LICENSE", ROOT / "NOTICE"])
    files += [ROOT / "runtime/seed" / name for name in ("runtime.json", "content-manifest.json")]
    seed_provenance = ROOT / "runtime/seed.provenance.json"
    if seed_provenance.is_file():
        files.append(seed_provenance)
    files += [path for path in (ROOT / "tools/build").rglob("*")
              if path.is_file() and "__pycache__" not in path.parts
              and path.suffix not in {".pyc", ".icns"} and path.name != ".DS_Store"]
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in sorted(files)}


def validate_release_configuration(root=ROOT):
    """Reject missing or stale public settings before any build side effect."""
    path = Path(root) / "tools/build/release-settings.json"
    settings = load_settings(path)
    configuration = json.loads((Path(root) / "app/leap_desktop/release-config.json").read_text(encoding="utf-8"))
    if (not isinstance(configuration, dict) or type(configuration.get("schema_version")) is not int
            or configuration != component_configuration(settings)):
        raise ValueError("The app component configuration differs from the public release settings")
    return settings


def release_configuration_record(root, settings):
    return {"path": "tools/build/release-settings.json",
            "sha256": digest(Path(root) / "tools/build/release-settings.json"),
            "components": component_configuration(settings), "sparkle": sparkle_configuration(settings)}


def scientific_source_evidence(snapshot=None):
    folder = snapshot / "artifacts/prototype-c/baseline" if snapshot else SCIENCE_BASELINE_ROOT
    result = {}
    for key, filename in (("scientific_baseline_source", "source-sha256.json"),
                          ("engineering_adapter_changes", "engineering-adapter-changes.json")):
        path = folder / filename
        result[key] = {"path": "artifacts/prototype-c/baseline/" + filename,
                       "sha256": digest(path), "json": path.read_text(encoding="utf-8")}
    pruning = snapshot / "artifacts/release-1.0.0/engineering-pruning.json" if snapshot else PRUNING_MANIFEST
    result["engineering_pruning"] = {"path": "artifacts/release-1.0.0/engineering-pruning.json",
                                     "sha256": digest(pruning), "json": pruning.read_text(encoding="utf-8")}
    return result


def scientific_baseline(sources, snapshot=None):
    evidence = scientific_source_evidence(snapshot)
    check = scientific_source_contract(sources, evidence["scientific_baseline_source"], evidence["engineering_adapter_changes"], evidence["engineering_pruning"])
    if not check["passed"]:
        raise RuntimeError("Scientific source review failed: " + json.dumps(check))
    return {path: value for path, value in json.loads(evidence["scientific_baseline_source"]["json"]).items()
            if path.startswith("engine/project_leap_2d/")}


def first_party_module_notices(component_id):
    return {name: digest(path) for name, path in module_notices(component_id, ROOT).items()}


def component_license_sources():
    """Retain the fixed seed's license bytes in each redistributed runtime."""
    seed_entries = json.loads((ROOT / "runtime/seed/content-manifest.json").read_text())["entries"]
    notices = {path: item["sha256"] for path, item in seed_entries.items()
               if item.get("type") == "file" and not excluded_seed_entry(path)
               and any(token in path.lower() for token in ("license", "licence", "copying", "copyright", "notice"))}
    science = {path: value for path, value in notices.items() if path.startswith(("python/", "site-packages/"))}
    model_license = "site-packages/cellpose-4.2.1.1.dist-info/licenses/LICENSE"
    return {"science-runtime": science,
            "project-leap-2d": first_party_module_notices("project-leap-2d"),
            "cell-counting": first_party_module_notices("cell-counting"),
            "fiji": {path: value for path, value in notices.items() if path.startswith("Fiji/")},
            "cellpose-model": {"ThirdPartyNotices/Cellpose/LICENSE": seed_entries[model_license]["sha256"]}}


def source_snapshot(staging):
    """Freeze source once; only the UI subset enters the signed app."""
    before = source_inventory()
    native_before = native_inventory()
    build_before = build_inventory()
    scientific_baseline(before)
    snapshot = staging / "source"
    snapshot.mkdir()
    for relative, expected in {**before, **native_before, **build_before}.items():
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
        if digest(target) != expected:
            raise RuntimeError("Source changed during snapshot: " + relative)
    if (source_inventory() != before or native_inventory() != native_before
            or build_inventory() != build_before):
        raise RuntimeError("Source changed while snapshotting; retry after the edit finishes")
    vendor = ROOT / "native/vendor/Sparkle-2.10.0"
    if not vendor.is_dir():
        raise RuntimeError("Verified Sparkle vendor directory is missing")
    run(["/bin/cp", "-cR", vendor, snapshot / "native/vendor/Sparkle-2.10.0"])
    (staging / "source-manifest.json").write_text(json.dumps(before, indent=2) + "\n")
    return snapshot, before, native_before, build_before


def package_record(packages, engine, identity="-"):
    """Reuse only a complete package set built from this exact engine generation."""
    native_source = Path(engine).parent / COUNTING_SOURCE_PATH
    record_path = packages / "package-build.json"
    if record_path.is_file():
        record = json.loads(record_path.read_text())
        seed_entries = json.loads((ROOT / "runtime/seed/content-manifest.json").read_text())["entries"]
        # Match the effective scientific policy; UI allowlist edits do not change these packages.
        excluded_entries = {name: value for name, value in seed_entries.items() if excluded_seed_entry(name)}
        catalog_path = packages / "catalog.json"
        if (record["engine_entries"] == engine_inventory(engine)
                and record["seed_manifest_sha256"] == digest(ROOT / "runtime/seed/content-manifest.json")
                and record.get("package_builder_sha256") == digest(ROOT / "tools/prepare_module_packages.py")
                and record.get("excluded_seed_entries") == excluded_entries
                and record.get("license_inputs") == license_input_hashes(ROOT)
                and record.get("seed_validator_sha256") == digest(ROOT / "tools/build/verify_seed_runtime.py")
                and record.get("scientific_runtime_validation", {}).get("performed") is True
                and record.get("counting_native", {}).get("source_sha256") == digest(native_source)
                and record.get("counting_native", {}).get("builder_sha256") == digest(ROOT / "tools/build_counting_stats.py")
                and record.get("counting_native", {}).get("signing_identity") == identity
                and catalog_contract(record["catalog"])
                and catalog_path.is_file() and digest(catalog_path) == record["catalog_sha256"]):
            if all((packages / item["path"]).is_file() and digest(packages / item["path"]) == item["sha256"]
                   for item in record["catalog"]["packages"].values()):
                return record
    return prepare(ROOT / "runtime/seed", engine, packages, replace=record_path.is_file(),
                   native_source=native_source, identity=identity)


def write_install_instructions(path):
    path.write_text("""Project Leap — Installation / 安装说明

中文
系统要求：Apple Silicon Mac，macOS 27 或更新版本。
本安装镜像包含 Project Leap 应用和全部分析组件。
1. 将整个“Project Leap”文件夹复制到“应用程序”，保持
   Project Leap.app 与 Project Leap Packages 位于同一文件夹中。
2. 打开 Project Leap.app，在“功能与维护”中安装需要的模块。
3. 保留 Project Leap Packages 文件夹，供重新安装和修复使用。

English
Requires an Apple Silicon Mac with macOS 27 or later.
This disk image includes the Project Leap app and all analysis components.
1. Copy the entire “Project Leap” folder to Applications.
   Keep Project Leap.app and Project Leap Packages in the same folder.
2. Open Project Leap.app and install the modules you need in Modules & Maintenance.
3. Retain the Project Leap Packages folder for reinstallation and repair.
""", encoding="utf-8")


def main():
    settings = validate_release_configuration()
    application = settings["application"]
    main_only_dmg_filename = application["archive_filename"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=DELIVERY_NAME, choices=[DELIVERY_NAME])
    parser.add_argument("--version", default=application["version"], choices=[application["version"]])
    parser.add_argument("--build-number", default=str(application["first_public_build"]))
    parser.add_argument("--identity", default="-", help="codesign identity; '-' means local ad hoc signing")
    parser.add_argument("--output", type=Path, default=RELEASE_OUTPUT_ROOT)
    parser.add_argument("--packages", type=Path, default=DEFAULT_PACKAGE_ROOT)
    parser.add_argument("--packages-only", action="store_true")
    parser.add_argument("--zip", action="store_true")
    parser.add_argument("--dmg", action="store_true", help="Create the complete offline delivery DMG")
    parser.add_argument("--main-only-dmg", action="store_true", help="Create a separate app-only DMG for online updates")
    args = parser.parse_args()
    if not args.build_number.isascii() or not args.build_number.isdecimal() or int(args.build_number) < 1:
        parser.error("Build number must be a positive integer (1 or later)")
    try:
        assert_isolated_output(args.packages)
        assert_isolated_output(args.output)
    except ValueError as error:
        parser.error(str(error))
    if args.packages_only:
        scientific_baseline(source_inventory())
        print(json.dumps(package_record(args.packages.resolve(), ROOT / "engine", args.identity)["catalog"], indent=2))
        return
    ui_python = ROOT / "runtime/ui-env/bin/python"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for filename in ["build-report.json"] + ([DELIVERY_NAME + ".zip"] if args.zip else []) + ([DMG_FILENAME] if args.dmg else []) + ([main_only_dmg_filename] if args.main_only_dmg else []):
        if (output / filename).exists():
            raise FileExistsError("Choose a new output directory to preserve the prior build: " + filename)
    staging = Path(tempfile.mkdtemp(prefix="ProjectLeap-build-", dir="/private/tmp"))
    snapshot, complete_sources, native_manifest, build_manifest = source_snapshot(staging)
    if validate_release_configuration(snapshot) != settings:
        raise RuntimeError("Public release settings changed while preparing the build")
    packages = args.packages.resolve()
    package_info = package_record(packages, snapshot / "engine", args.identity)
    run([ui_python, snapshot / "tools/build/make_icon.py", "--svg", snapshot / "app/leap_desktop/assets/icon.svg",
         "--output", snapshot / "tools/build/ProjectLeap.icns"])
    env = os.environ.copy()
    env.update({"PYINSTALLER_CONFIG_DIR": str(staging / "pyinstaller-cache"),
                "PYTHONDONTWRITEBYTECODE": "1", "LEAP_BUILD_ROOT": str(snapshot),
                "LEAP_BUILD_NAME": args.name, "LEAP_BUILD_VERSION": args.version,
                "LEAP_BUILD_DISPLAY_NAME": "Project Leap",
                "LEAP_BUILD_NUMBER": args.build_number, "LEAP_SIGN_IDENTITY": args.identity})
    run([ui_python, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", staging,
         "--workpath", staging / "pyinstaller-work", snapshot / "tools/build/project_leap.spec"], env)
    app = staging / (args.name + ".app")
    run([ui_python, snapshot / "native/embed_sparkle.py", app, "--identity", args.identity], env)
    run([ui_python, snapshot / "native/build_glass.py", app, "--identity", args.identity], env)
    exports = native_exports(app / "Contents/Frameworks/libLeapGlass.dylib")
    if not exports["passed"]:
        raise RuntimeError("Native C exports do not match the UI and storage contracts: " + json.dumps(exports))
    notice_manifest = {path.relative_to(app / "Contents/Resources").as_posix(): digest(path)
                       for path in (app / "Contents/Resources/ThirdPartyNotices").rglob("*") if path.is_file()}
    notice_sources = {"ThirdPartyNotices/UI/" + path.removeprefix("tools/build/licenses/"): value
                      for path, value in build_manifest.items() if path.startswith("tools/build/licenses/")}
    notice_sources.update({"ThirdPartyNotices/ProjectLeap/" + name: digest(snapshot / name) for name in ("LICENSE", "NOTICE")})
    notice_sources["ThirdPartyNotices/Sparkle-LICENSE.txt"] = digest(snapshot / "native/vendor/Sparkle-2.10.0/LICENSE")
    if notice_manifest != notice_sources:
        raise RuntimeError("The bundled notices differ from the frozen license inputs")
    run(["/usr/bin/codesign", "--force", "--sign", args.identity, app])
    verification = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], text=True, capture_output=True)
    if verification.returncode:
        raise RuntimeError(verification.stderr)
    signed = subprocess.run(["/usr/bin/codesign", "-d", "--verbose=4", str(app)], text=True, capture_output=True)
    # Keep the bundle outside FileProvider; ZIP/DMG preserve its sealed resources.
    delivery = staging / "delivery" / DELIVERY_NAME
    delivery.mkdir(parents=True)
    run(["/bin/cp", "-cR", app, delivery / app.name])
    run(["/bin/cp", "-cR", packages, delivery / PACKAGE_DIRECTORY])
    write_install_instructions(delivery / "Install — 安装说明.txt")
    report = {"built_at_utc": datetime.now(timezone.utc).isoformat(), "app": str(delivery / app.name),
              "delivery_directory": str(delivery), "version": args.version, "build_number": args.build_number,
              "delivery_name": DELIVERY_NAME, "bundle_identifier": BUNDLE_IDENTIFIER,
              "signing": "ad-hoc" if args.identity == "-" else "Developer ID requested",
              "source_manifest": {key: value for key, value in complete_sources.items() if key.startswith("app/")},
              "module_source_manifest": {key: value for key, value in complete_sources.items() if key.startswith("engine/")},
              "source_changed_during_build": source_inventory() != complete_sources,
              "native_build_inputs": native_manifest, "native_inputs_changed_during_build": native_inventory() != native_manifest,
              "build_inputs": build_manifest, "build_inputs_changed_during_build": build_inventory() != build_manifest,
              "release_configuration": release_configuration_record(snapshot, settings),
              "source_snapshot": str(snapshot), "scientific_baseline_manifest": scientific_baseline(complete_sources, snapshot),
              **scientific_source_evidence(snapshot),
              "native_exports": exports,
              "notice_manifest": notice_manifest, "notice_source_manifest": notice_sources,
              "component_license_manifest": component_license_sources(),
              "counting_native": package_info["counting_native"],
              "scientific_runtime_validation": package_info["scientific_runtime_validation"],
              "excluded_seed_entries": package_info["excluded_seed_entries"],
              "notarized": False, "codesign_verification": verification.stderr,
              "codesign_details": signed.stderr, "packages": package_info["catalog"],
              "package_build_record_sha256": digest(packages / "package-build.json"),
              "package_original_release_policy_sha256": package_info["release_policy_sha256"],
              "packages_catalog_sha256": package_info["catalog_sha256"], "packages_directory": PACKAGE_DIRECTORY,
              "scientific_payload_inside_app": False}
    if (report["source_changed_during_build"] or report["native_inputs_changed_during_build"]
            or report["build_inputs_changed_during_build"]):
        raise RuntimeError("Source changed during the build; candidate cannot be delivered")
    if args.zip:
        zip_path = output / (DELIVERY_NAME + ".zip")
        run(["/usr/bin/ditto", "-c", "-k", "--zlibCompressionLevel", "1", "--keepParent", delivery, zip_path])
        report["zip"] = {"path": str(zip_path), "sha256": digest(zip_path), "bytes": zip_path.stat().st_size}
    if args.dmg:
        stage = staging / "dmg-stage"
        stage.mkdir()
        run(["/bin/cp", "-cR", delivery, stage / delivery.name])
        (stage / "Applications").symlink_to("/Applications")
        dmg_path = output / DMG_FILENAME
        run(["/usr/bin/hdiutil", "create", "-volname", DELIVERY_NAME, "-srcfolder", stage,
             "-format", "UDZO", "-imagekey", "zlib-level=1", dmg_path])
        report["dmg"] = {"path": str(dmg_path), "sha256": digest(dmg_path), "bytes": dmg_path.stat().st_size}
    if args.main_only_dmg:
        app_stage = staging / "main-only-dmg-stage"
        app_stage.mkdir()
        run(["/bin/cp", "-cR", app, app_stage / app.name])
        (app_stage / "Applications").symlink_to("/Applications")
        app_dmg = output / main_only_dmg_filename
        run(["/usr/bin/hdiutil", "create", "-volname", DELIVERY_NAME, "-srcfolder", app_stage,
             "-format", "UDZO", "-imagekey", "zlib-level=1", app_dmg])
        report["main_only_dmg"] = {"path": str(app_dmg), "sha256": digest(app_dmg), "bytes": app_dmg.stat().st_size}
    report["source_changed_during_build"] |= source_inventory() != complete_sources
    report["native_inputs_changed_during_build"] |= native_inventory() != native_manifest
    report["build_inputs_changed_during_build"] |= build_inventory() != build_manifest
    if (report["source_changed_during_build"] or report["native_inputs_changed_during_build"]
            or report["build_inputs_changed_during_build"]):
        raise RuntimeError("Source changed while creating delivery containers; candidate cannot be delivered")
    (output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"source_manifest", "module_source_manifest", "native_build_inputs", "build_inputs",
                                     "scientific_baseline_manifest", "scientific_baseline_source", "engineering_adapter_changes", "engineering_pruning",
                                     "notice_manifest", "notice_source_manifest", "component_license_manifest", "excluded_seed_entries",
                                     "codesign_verification", "codesign_details"}}, indent=2))
    for path in (staging / args.name, staging / "dmg-stage", staging / "pyinstaller-work", staging / "pyinstaller-cache", staging / "main-only-dmg-stage"):
        if path.is_dir():
            shutil.rmtree(path)


if __name__ == "__main__":
    main()
