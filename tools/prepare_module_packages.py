#!/usr/bin/env python3
"""Create engine modules and shared runtime, Fiji and model packages."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import tarfile
import tempfile

from build.release_policy import (MODULE_HOST_CONTRACT, MODULE_VERSIONS, SCIENTIFIC_VERSIONS, SCIENCE_RUNTIME_VERSION,
                                  SCIENCE_RUNTIME_DEPENDENCY, excluded_seed_entry)

from build.verify_seed_runtime import verify_pruned_seed
from build_counting_stats import PACKAGE_PATH as COUNTING_LIBRARY_PATH, SOURCE_PATH as COUNTING_SOURCE_PATH, build_library

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIRECTORY = "Project Leap Packages"
DEFAULT_PACKAGE_ROOT = ROOT / "build/release-1.0.0/packages"
FIJI_VERSION = "20260718.0417"
MODEL_VERSION = "4.2.1.1"
MODEL_SHA256 = "0f1cc3f7ecdd8a037a57c6c48d9d8921391be4cbce3fa9f13c3e3a2e1253c667"
MANIFEST = "content-manifest.json"


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def engine_inventory(engine):
    result = {}
    for path in sorted(Path(engine).rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc" or path.name == ".DS_Store":
            continue
        relative = "engine/" + path.relative_to(engine).as_posix()
        result[relative] = entry(path, Path(engine))
    result["engine"] = {"type": "directory", "mode": stat.S_IMODE(Path(engine).stat().st_mode)}
    return result


def entry(path, root):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(path)
        if os.path.isabs(target) or not path.resolve().is_relative_to(root.resolve()) or not path.exists():
            raise ValueError("Invalid package symlink: " + str(path))
        return {"type": "symlink", "target": target}
    if stat.S_ISDIR(info.st_mode):
        return {"type": "directory", "mode": stat.S_IMODE(info.st_mode)}
    if stat.S_ISREG(info.st_mode):
        return {"type": "file", "mode": stat.S_IMODE(info.st_mode), "bytes": info.st_size, "sha256": digest(path)}
    raise ValueError("Unsupported package entry: " + str(path))


class CheckedReader:
    """Hash the exact bytes tarfile consumes, rather than a prior file read."""
    def __init__(self, stream):
        self.stream = stream
        self.hash = hashlib.sha256()

    def read(self, size=-1):
        data = self.stream.read(size)
        self.hash.update(data)
        return data


def package_manifest(descriptor, entries):
    runtime = json_bytes(descriptor)
    entries = dict(entries)
    entries["runtime.json"] = {"type": "file", "mode": 0o644, "bytes": len(runtime),
                               "sha256": hashlib.sha256(runtime).hexdigest()}
    manifest = {"schema_version": 1, "component_id": descriptor["component_id"],
                "component_version": descriptor["component_version"], "entries": entries}
    return runtime, entries, json_bytes(manifest)


def write_package(destination, descriptor, entries, sources):
    runtime, entries, manifest_bytes = package_manifest(descriptor, entries)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w:gz", compresslevel=1, dereference=False) as archive:
        for name, expected in sorted(entries.items()):
            member = tarfile.TarInfo(name)
            member.mtime = 0
            member.mode = expected.get("mode", 0o777)
            if expected["type"] == "directory":
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
                continue
            if expected["type"] == "symlink":
                path = sources[name]
                if not path.is_symlink() or os.readlink(path) != expected["target"]:
                    raise RuntimeError("Package source symlink changed: " + name)
                member.type = tarfile.SYMTYPE
                member.linkname = expected["target"]
                archive.addfile(member)
                continue
            member.size = expected["bytes"]
            if name == "runtime.json":
                archive.addfile(member, io.BytesIO(runtime))
                continue
            path = sources[name]
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size != member.size or stat.S_IMODE(info.st_mode) != member.mode:
                raise RuntimeError("Package source metadata changed: " + name)
            with path.open("rb") as stream:
                reader = CheckedReader(stream)
                archive.addfile(member, reader)
                if reader.hash.hexdigest() != expected["sha256"] or stream.read(1):
                    raise RuntimeError("Package source bytes changed: " + name)
        member = tarfile.TarInfo(MANIFEST)
        member.mode = 0o644
        member.size = len(manifest_bytes)
        archive.addfile(member, io.BytesIO(manifest_bytes))
    return {"version": descriptor["component_version"], "sha256": digest(destination),
            "bytes": destination.stat().st_size, "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "file_count": sum(item["type"] == "file" for item in entries.values())}


def assert_isolated_output(output):
    """Reject writes into retained releases or their ancestors before staging."""
    output = Path(output).resolve()
    for relative in ("runtime/packages", "runtime/prototype-c", "artifacts/prototype-c"):
        retained = (ROOT / relative).resolve()
        if output == retained or retained in output.parents or output in retained.parents:
            raise ValueError("Build output must not replace retained release paths: " + relative)


def module_notices(component_id, repository):
    mapping = {"ThirdPartyNotices/ProjectLeap/" + name: Path(repository) / name for name in ("LICENSE", "NOTICE")}
    if component_id == "project-leap-2d":
        mapping["ThirdPartyNotices/InstanSeg/LICENSE"] = Path(repository) / "tools/build/licenses/models/InstanSeg-APACHE-2.0.txt"
        mapping["ThirdPartyNotices/InstanSeg/NOTICE.md"] = Path(repository) / "tools/build/licenses/models/InstanSeg-NOTICE.md"
    return mapping


def license_input_hashes(repository):
    repository = Path(repository)
    paths = {source for component in MODULE_VERSIONS for source in module_notices(component, repository).values()}
    return {path.relative_to(repository).as_posix(): digest(path) for path in sorted(paths)}


def add_notices(entries, sources, mapping):
    for target, source in mapping.items():
        for parent in Path(target).parents:
            if parent.as_posix() != ".":
                entries[parent.as_posix()] = {"type": "directory", "mode": 0o755}
        entries[target] = entry(source, source.parent)
        sources[target] = source


def prepare(seed, engine, output, *, replace=False, native_source=None, identity="-"):
    seed, engine, output = Path(seed).resolve(), Path(engine).resolve(), Path(output).absolute()
    native_source = Path(native_source) if native_source is not None else ROOT / COUNTING_SOURCE_PATH
    assert_isolated_output(output)
    if (output.is_symlink() or seed == output or seed in output.parents or engine == output or engine in output.parents
            or output in seed.parents or output in engine.parents):
        raise ValueError("Package output must be separate from its input trees")
    if output.exists() and (not replace or not (output / "package-build.json").is_file()):
        raise FileExistsError("Refusing to replace an existing package directory without --replace and its build record")
    version = (engine / "VERSION").read_text().strip()
    if version != "1.0.1":
        raise ValueError("This delivery supports scientific module 1.0.1 only")
    build_inputs = {path: digest(path) for path in
                    (Path(__file__), Path(__file__).parent / "build_counting_stats.py",
                     Path(__file__).parent / "build/release_policy.py",
                     Path(__file__).parent / "build/verify_seed_runtime.py")}
    previous = {}
    if (output / "package-build.json").is_file():
        previous = json.loads((output / "package-build.json").read_text())
        catalog_path = output / "catalog.json"
        if (not catalog_path.is_file() or digest(catalog_path) != previous.get("catalog_sha256")
                or json.loads(catalog_path.read_text()) != previous.get("catalog")):
            raise ValueError("Existing package build record does not match its catalog")
    original_manifest_bytes = (seed / MANIFEST).read_bytes()
    original = json.loads(original_manifest_bytes)
    original_descriptor_bytes = (seed / "runtime.json").read_bytes()
    if hashlib.sha256(original_descriptor_bytes).hexdigest() != original["entries"]["runtime.json"]["sha256"]:
        raise ValueError("The original runtime descriptor differs from its fixed manifest")
    original_descriptor = json.loads(original_descriptor_bytes)
    if original.get("component_id") != "project-leap-2d" or original.get("component_version") != version:
        raise ValueError("Unexpected scientific seed identity")
    if original_descriptor.get("environment_contract") != "c076335b5cbac2c440f927cf92db02ac60f91136170c54b0a83c2db333f8da9a":
        raise ValueError("Unexpected scientific runtime environment contract")
    # Each shared dependency has one owner; module archives contain engine code only.
    science_entries, fiji_entries, model_entries = {}, {}, {}
    science_sources, fiji_sources, model_sources = {}, {}, {}
    excluded_seed_entries = {}
    for name, expected in original["entries"].items():
        if (not name or name.startswith("/") or "\\" in name
                or any(part in ("", ".", "..") for part in name.split("/"))):
            raise ValueError("Noncanonical seed path: " + name)
        if expected.get("type") == "symlink":
            target = os.path.normpath(os.path.join(os.path.dirname(name), expected["target"]))
            if os.path.isabs(expected["target"]) or target == ".." or target.startswith("../"):
                raise ValueError("External seed symlink: " + name)
        if name == "runtime.json":
            continue
        if excluded_seed_entry(name):
            excluded_seed_entries[name] = expected
            continue
        prefix = name.split("/", 1)[0]
        if prefix not in {"python", "site-packages", "Models", "Fiji"}:
            raise ValueError("Unexpected seed payload entry: " + name)
        selected_entries = fiji_entries if prefix == "Fiji" else model_entries if prefix == "Models" else science_entries
        selected_sources = fiji_sources if prefix == "Fiji" else model_sources if prefix == "Models" else science_sources
        selected_entries[name] = expected
        selected_sources[name] = seed / name
    engine_entries = engine_inventory(engine)
    if COUNTING_LIBRARY_PATH in engine_entries:
        raise ValueError("Build counting statistics from frozen C source, not an engine-tree binary")
    module_entries, module_sources, counting_entries, counting_sources = {}, {}, {}, {}
    for name, expected in engine_entries.items():
        source = engine if name == "engine" else engine / name.removeprefix("engine/")
        if name in ("engine", "engine/VERSION", "engine/project_leap_2d") or name.startswith("engine/project_leap_2d/"):
            module_entries[name], module_sources[name] = expected, source
        if name in ("engine", "engine/leap_cell_counting") or name.startswith("engine/leap_cell_counting/"):
            counting_entries[name], counting_sources[name] = expected, source
    repository = engine.parent
    license_inputs = license_input_hashes(repository)
    add_notices(module_entries, module_sources, module_notices("project-leap-2d", repository))
    add_notices(counting_entries, counting_sources, module_notices("cell-counting", repository))
    if "engine/leap_cell_counting/__main__.py" not in counting_entries:
        raise ValueError("The counting engine entry point is missing")
    if set(model_entries) != {"Models", "Models/cpsam_v2"} or model_entries["Models/cpsam_v2"]["sha256"] != MODEL_SHA256:
        raise ValueError("The model seed differs from the fixed cpsam_v2 content")
    license_source = "site-packages/cellpose-4.2.1.1.dist-info/licenses/LICENSE"
    for name in ("ThirdPartyNotices", "ThirdPartyNotices/Cellpose"):
        model_entries[name] = {"type": "directory", "mode": 0o755}
    model_entries["ThirdPartyNotices/Cellpose/LICENSE"] = original["entries"][license_source]
    model_sources["ThirdPartyNotices/Cellpose/LICENSE"] = seed / license_source
    dependency = {"id": "cellpose-model", "version": MODEL_VERSION, "filename": "cpsam_v2", "sha256": MODEL_SHA256}
    science_descriptor = {key: value for key, value in original_descriptor.items()
                          if key not in {"component_id", "component_version", "models", "fiji_launcher"}}
    science_descriptor.update(schema_version=4, component_id="science-runtime", component_version=SCIENCE_RUNTIME_VERSION)
    module_descriptor = {"schema_version": 4, "component_id": "project-leap-2d",
                         "component_version": MODULE_VERSIONS["project-leap-2d"],
                         "scientific_version": SCIENTIFIC_VERSIONS["project-leap-2d"],
                         "platform": "macos-arm64", "engine": "engine", "fiji_component": "fiji",
                         "science_runtime": SCIENCE_RUNTIME_DEPENDENCY, "model_component": dependency, **MODULE_HOST_CONTRACT}
    counting_descriptor = dict(module_descriptor, component_id="cell-counting",
                               component_version=MODULE_VERSIONS["cell-counting"],
                               scientific_version=SCIENTIFIC_VERSIONS["cell-counting"])
    counting_descriptor.pop("fiji_component")
    model_descriptor = {"schema_version": 2, "component_id": "cellpose-model", "component_version": MODEL_VERSION,
                        "models": "Models", "model_file": "cpsam_v2", "model_sha256": MODEL_SHA256,
                        "source": "fixed-project-leap-scientific-seed", "upstream_package": "cellpose==4.2.1.1",
                        "license": "ThirdPartyNotices/Cellpose/LICENSE"}
    bundle = plistlib.loads((seed / "Fiji/Fiji.app/Contents/Info.plist").read_bytes())
    fiji_descriptor = {"schema_version": 2, "component_id": "fiji", "component_version": FIJI_VERSION,
                       "platform": "macos-arm64", "fiji_launcher": original_descriptor["fiji_launcher"],
                       "distribution_snapshot": "20260718-0417", "upstream_bundle_version": bundle["CFBundleVersion"],
                       "source_url": "https://downloads.imagej.net/fiji/archive/latest/20260718-0417/fiji-latest-macos-arm64-jdk.zip"}
    previous_validation = previous.get("scientific_runtime_validation", {})
    if (previous.get("seed_manifest_sha256") == hashlib.sha256(original_manifest_bytes).hexdigest()
            and previous.get("engine_entries") == engine_entries
            and previous.get("release_policy_sha256") == build_inputs[Path(__file__).parent / "build/release_policy.py"]
            and previous.get("seed_validator_sha256") == build_inputs[Path(__file__).parent / "build/verify_seed_runtime.py"]
            and previous_validation.get("performed") is True and previous_validation.get("exit_code") == 0
            and previous_validation.get("cellpose_model_loaded") is True
            and previous_validation.get("instanseg_model_loaded") is True):
        seed_validation = previous_validation
    else:
        seed_validation = verify_pruned_seed(seed, engine, excluded_seed_entries)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".leap-packages-", dir=output.parent))
    try:
        native_directory = temporary / "native-build"
        native_library = native_directory / Path(COUNTING_LIBRARY_PATH).name
        native_record = build_library(native_source, native_library, identity)
        counting_entries[COUNTING_LIBRARY_PATH] = entry(native_library, native_directory)
        counting_sources[COUNTING_LIBRARY_PATH] = native_library
        counting_descriptor["native_statistics"] = {key: native_record[key] for key in
                                                    ("path", "sha256", "source_sha256", "abi_version")}
        catalog = {"schema_version": 1, "packages": {}}
        reused_components = []
        for component_id, descriptor, entries, sources in (
                ("project-leap-2d", module_descriptor, module_entries, module_sources),
                ("cell-counting", counting_descriptor, counting_entries, counting_sources),
                ("science-runtime", science_descriptor, science_entries, science_sources),
                ("cellpose-model", model_descriptor, model_entries, model_sources),
                ("fiji", fiji_descriptor, fiji_entries, fiji_sources)):
            relative = f"{component_id}/{descriptor['component_version']}/package.tar.gz"
            previous_archive = previous.get("catalog", {}).get("packages", {}).get(component_id, {})
            manifest_sha256 = hashlib.sha256(package_manifest(descriptor, entries)[2]).hexdigest()
            retained = output / relative
            destination = temporary / relative
            if (previous_archive.get("path") == relative and previous_archive.get("version") == descriptor["component_version"]
                    and previous_archive.get("manifest_sha256") == manifest_sha256
                    and retained.is_file() and retained.stat().st_size == previous_archive.get("bytes")
                    and digest(retained) == previous_archive.get("sha256")):
                print("Reusing verified unchanged " + component_id, flush=True)
                destination.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["/bin/cp", "-c", str(retained), str(destination)], check=True)
                if digest(destination) != previous_archive["sha256"]:
                    raise RuntimeError("Retained component changed during reuse: " + component_id)
                record = {key: previous_archive[key] for key in ("version", "sha256", "bytes", "manifest_sha256", "file_count")}
                reused_components.append(component_id)
            else:
                print("Packaging " + component_id + " from the fixed local source", flush=True)
                record = write_package(destination, descriptor, entries, sources)
            dependencies = {}
            if component_id in MODULE_VERSIONS:
                dependencies.update({"science-runtime": SCIENCE_RUNTIME_VERSION, "cellpose-model": MODEL_VERSION})
                if component_id == "project-leap-2d":
                    dependencies["fiji"] = FIJI_VERSION
            catalog["packages"][component_id] = dict(record, path=relative, dependencies=dependencies,
                                                       **(MODULE_HOST_CONTRACT if component_id in MODULE_VERSIONS else {}))
        if (any(digest(path) != expected for path, expected in build_inputs.items())
                or engine_inventory(engine) != engine_entries or (seed / MANIFEST).read_bytes() != original_manifest_bytes
                or (seed / "runtime.json").read_bytes() != original_descriptor_bytes
                or license_input_hashes(repository) != license_inputs
                or digest(native_source) != native_record["source_sha256"]):
            raise RuntimeError("Package inputs changed during archive creation")
        shutil.rmtree(native_directory)
        (temporary / "catalog.json").write_bytes(json_bytes(catalog))
        record = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                  "package_builder_sha256": digest(Path(__file__)),
                  "seed_manifest_sha256": hashlib.sha256(original_manifest_bytes).hexdigest(),
                  "engine_entries": engine_entries, "counting_native": native_record,
                  "catalog_sha256": digest(temporary / "catalog.json"),
                  "catalog": catalog, "source_bytes_verified_while_archiving": True, "reused_components": reused_components,
                  "original_seed_modified": False,
                  "excluded_seed_entries": excluded_seed_entries,
                  "scientific_runtime_validation": seed_validation,
                  "release_policy_sha256": digest(Path(__file__).parent / "build/release_policy.py"),
                  "license_inputs": license_inputs,
                  "seed_validator_sha256": digest(Path(__file__).parent / "build/verify_seed_runtime.py")}
        (temporary / "package-build.json").write_bytes(json_bytes(record))
        if output.exists():
            prior = output.with_name(output.name + ".replaced-" + temporary.name.removeprefix(".leap-packages-"))
            os.replace(output, prior)
            try:
                os.replace(temporary, output)
            except Exception:
                os.replace(prior, output)
                raise
            shutil.rmtree(prior)
        else:
            os.replace(temporary, output)
        return record
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=ROOT / "runtime/seed")
    parser.add_argument("--engine", type=Path, default=ROOT / "engine")
    parser.add_argument("--output", type=Path, default=DEFAULT_PACKAGE_ROOT)
    parser.add_argument("--native-source", type=Path, default=ROOT / COUNTING_SOURCE_PATH)
    parser.add_argument("--identity", default="-", help="Signing identity for the counting statistics library")
    parser.add_argument("--replace", action="store_true", help="Replace only a previously generated package directory")
    arguments = parser.parse_args()
    result = prepare(arguments.seed, arguments.engine, arguments.output, replace=arguments.replace,
                     native_source=arguments.native_source, identity=arguments.identity)
    print(json.dumps({key: value for key, value in result.items() if key != "engine_entries"}, indent=2))
