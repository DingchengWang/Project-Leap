#!/usr/bin/env python3
"""Restore verified scientific and Sparkle build inputs from a retained delivery."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from prepare_module_packages import digest, entry, json_bytes, MODEL_SHA256
from verify_app_bundle import catalog_contract, module_sources_for, verify_package


def restore_seed(packages: Path, destination: Path):
    """Rebuild a seed from verified payload bytes; retain its distinct provenance."""
    provenance_path = destination.with_name(destination.name + ".provenance.json")
    if destination.exists() or provenance_path.exists():
        raise FileExistsError("Choose an absent seed destination: " + str(destination))
    catalog = json.loads((packages / "catalog.json").read_text())
    build = json.loads((packages / "package-build.json").read_text())
    if (not catalog_contract(catalog) or catalog != build["catalog"]
            or digest(packages / "catalog.json") != build["catalog_sha256"]):
        raise ValueError("The retained delivery catalog does not match its build record")
    engine_sources = {name: item["sha256"] for name, item in build["engine_entries"].items()
                      if item["type"] == "file"}
    components = {"science-runtime": {"python", "site-packages"},
                  "fiji": {"Fiji"}, "cellpose-model": {"Models"}}
    verified = {}
    for component in components:
        record = catalog["packages"][component]
        print("Verifying retained " + component, flush=True)
        result = verify_package(packages / record["path"], component, record,
                                module_sources_for(component, engine_sources))
        if not result["passed"]:
            raise ValueError("Retained package verification failed: " + json.dumps(result))
        verified[component] = result
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".restored-seed-", dir=destination.parent))
    try:
        entries = {}
        links = []
        for component, selected_roots in components.items():
            archive_path = packages / catalog["packages"][component]["path"]
            print("Restoring " + component, flush=True)
            with tarfile.open(archive_path, "r:gz") as archive:
                manifest_bytes = archive.extractfile("content-manifest.json").read()
                if hashlib.sha256(manifest_bytes).hexdigest() != catalog["packages"][component]["manifest_sha256"]:
                    raise ValueError("Retained package manifest changed during restoration: " + component)
                manifest = json.loads(manifest_bytes)
                for name, expected in manifest["entries"].items():
                    if name.split("/", 1)[0] not in selected_roots:
                        continue
                    if name in entries:
                        raise ValueError("Duplicate restored seed entry: " + name)
                    target = temporary / name
                    # The independent verifier already rejected noncanonical and external paths.
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if expected["type"] == "directory":
                        target.mkdir(exist_ok=True)
                        target.chmod(expected["mode"])
                    elif expected["type"] == "symlink":
                        links.append((target, expected["target"]))
                    else:
                        with archive.extractfile(name) as source, target.open("xb") as output:
                            shutil.copyfileobj(source, output, 4 * 1024 * 1024)
                        target.chmod(expected["mode"])
                        if entry(target, temporary) != expected:
                            raise ValueError("Restored bytes differ from retained manifest: " + name)
                    entries[name] = expected
        for target, link in links:
            target.symlink_to(link)
        for target, _ in links:
            if entry(target, temporary) != entries[target.relative_to(temporary).as_posix()]:
                raise ValueError("Restored symlink differs from retained manifest: " + str(target))
        descriptor = dict(verified["science-runtime"]["descriptor"])
        for key in ("engine", "fiji_component", "model_component"):
            descriptor.pop(key, None)
        descriptor.update(schema_version=1, component_id="project-leap-2d", component_version="1.0.1", models="Models",
                          fiji_launcher=verified["fiji"]["descriptor"]["fiji_launcher"])
        (temporary / "runtime.json").write_bytes(json_bytes(descriptor))
        entries["runtime.json"] = entry(temporary / "runtime.json", temporary)
        if entries["Models/cpsam_v2"]["sha256"] != MODEL_SHA256:
            raise ValueError("The restored model differs from the pinned scientific model")
        manifest = {"schema_version": 1, "component_id": "project-leap-2d",
                    "component_version": "1.0.1", "entries": entries}
        (temporary / "content-manifest.json").write_bytes(json_bytes(manifest))
        provenance = {"schema_version": 1, "restored_at_utc": datetime.now(timezone.utc).isoformat(),
                      "method": "reconstructed-from-verified-delivery-packages",
                      "source_packages": str(packages.resolve()),
                      "source_catalog_sha256": digest(packages / "catalog.json"),
                      "source_package_build_sha256": digest(packages / "package-build.json"),
                      "source_archives": {key: catalog["packages"][key] for key in components},
                      "source_seed_manifest_sha256": build["seed_manifest_sha256"],
                      "restored_seed_manifest_sha256": digest(temporary / "content-manifest.json"),
                      "original_seed_manifest_recovered": False,
                      "payload_entries_verified": len(entries) - 1,
                      "runtime_descriptor_reconstructed": True}
        os.replace(temporary, destination)
        provenance_path.write_bytes(json_bytes(provenance))
        return provenance
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def restore_sparkle(app: Path, vendor: Path):
    framework = app / "Contents/Frameworks/Sparkle.framework"
    license_path = app / "Contents/Resources/ThirdPartyNotices/Sparkle-LICENSE.txt"
    destination = vendor / "Sparkle-2.10.0"
    provenance_path = vendor / "Sparkle-2.10.0.provenance.json"
    if destination.exists() or provenance_path.exists():
        raise FileExistsError("Sparkle build inputs already exist: " + str(destination))
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    if plistlib.loads((framework / "Resources/Info.plist").read_bytes())["CFBundleShortVersionString"] != "2.10.0":
        raise ValueError("The retained delivery does not contain Sparkle 2.10.0")
    vendor.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    shutil.copytree(framework, destination / framework.name, symlinks=True)
    shutil.copy2(license_path, destination / "LICENSE")
    entries = {path.relative_to(destination).as_posix(): entry(path, destination)
               for path in sorted(destination.rglob("*"))}
    provenance = {"schema_version": 1, "version": "2.10.0",
                  "restored_at_utc": datetime.now(timezone.utc).isoformat(),
                  "method": "recovered-from-verified-delivery",
                  "source_app": str(app.resolve()),
                  "source_bundle_build": plistlib.loads((app / "Contents/Info.plist").read_bytes())["CFBundleVersion"],
                  "source_codesign_strict_verified": True, "entries": entries}
    provenance_path.write_bytes(json_bytes(provenance))
    return {"version": "2.10.0", "entries_verified": len(entries), "provenance": str(provenance_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delivery", type=Path, help="Retained folder containing the app and Project Leap Packages")
    parser.add_argument("--seed", type=Path, default=ROOT / "runtime/seed")
    parser.add_argument("--vendor", type=Path, default=ROOT / "native/vendor")
    args = parser.parse_args()
    print(json.dumps({"seed": restore_seed(args.delivery / "Project Leap Packages", args.seed),
                      "sparkle": restore_sparkle(args.delivery / "Project Leap.app", args.vendor)}, indent=2))
