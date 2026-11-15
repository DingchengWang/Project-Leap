#!/usr/bin/env python3
"""Verify five offline packages and sign an append-only online release catalog.

Production signing uses Sparkle's sign_update and the configured Keychain
account. Existing history must be authenticated with the configured public
key. This utility creates a new local file; it never uploads a release.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from leap_desktop import maintenance as maintenance
from leap_desktop.release_catalog import (HOST_REQUIREMENT_FIELDS, ReleaseCatalog,
                                         ReleaseConfiguration, validate_host_requirements)
from leap_desktop.update_manager import _version_tuple
from build.release_policy import MODULE_HOST_CONTRACT
from release_support import (PublicVerifier, canonical_bytes, load_settings,
                             require_https, sha256_file, sign_bytes)

PACKAGE_IDS = frozenset({"project-leap-2d", "cell-counting", "science-runtime", "cellpose-model", "fiji"})
MODULE_IDS = frozenset({"project-leap-2d", "cell-counting"})
MANIFEST_NAME = "content-manifest.json"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field: " + key)
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw, object_pairs_hook=_unique_object)


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise ValueError("Invalid component version")
    return value


def _resolve_member(name, members):
    """Resolve an archive path without extracting it or following host links."""
    pending, resolved, followed = name.split("/"), [], set()
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not resolved:
                raise ValueError("Archive symlink leaves the package: " + name)
            resolved.pop()
            continue
        candidate = "/".join(resolved + [part])
        member = members.get(candidate)
        if member is None:
            raise ValueError("Missing archive link or runtime target: " + candidate)
        if member.issym():
            if candidate in followed or member.linkname.startswith("/") or "\\" in member.linkname:
                raise ValueError("External or cyclic archive symlink: " + candidate)
            followed.add(candidate)
            pending = member.linkname.split("/") + pending
        else:
            if pending and not member.isdir():
                raise ValueError("Archive path crosses a non-directory: " + candidate)
            resolved.append(part)
    target = "/".join(resolved)
    if target not in members:
        raise ValueError("Missing archive target: " + name)
    return members[target]


def _runtime_metadata(descriptor, manifest, members, record):
    identity, version = manifest["component_id"], manifest["component_version"]
    if (not isinstance(descriptor, dict) or descriptor.get("component_id") != identity
            or descriptor.get("component_version") != version or record["version"] != version):
        raise ValueError("Catalog, runtime and manifest identities or versions differ: " + identity)
    dependencies = record.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError("Every package must declare its dependencies: " + identity)
    expected = {}
    if identity in MODULE_IDS:
        if descriptor.get("schema_version") != 4:
            raise ValueError("New engine modules must use runtime schema 4")
        validate_host_requirements(descriptor)
        if {key: descriptor[key] for key in HOST_REQUIREMENT_FIELDS} != MODULE_HOST_CONTRACT:
            raise ValueError("New module host requirements differ from the current release policy: " + identity)
        if any(record.get(key) != descriptor[key] for key in HOST_REQUIREMENT_FIELDS):
            raise ValueError("Catalog and runtime host requirements differ: " + identity)
        science = maintenance._science_dependency(descriptor.get("science_runtime"))
        model = maintenance._model_dependency(descriptor.get("model_component"))
        expected = {science["id"]: science["version"], model["id"]: model["version"]}
        if identity == "project-leap-2d":
            if descriptor.get("fiji_component") != "fiji":
                raise ValueError("Fluorescence module must declare shared Fiji")
            expected["fiji"] = _version(dependencies.get("fiji"))
        elif "fiji_component" in descriptor:
            raise ValueError("Counting module must not declare Fiji")
        if (any(key in descriptor for key in ("python", "site_packages", "models"))
                or any(name in members for name in ("python", "site-packages", "Models"))):
            raise ValueError("Engine modules must use shared runtime and model packages")
        paths = {"engine": "directory"}
    elif identity == "science-runtime":
        if descriptor.get("schema_version") != 4:
            raise ValueError("Shared scientific runtime must use schema 4")
        paths = {"python": "executable", "site_packages": "directory"}
    elif identity == "cellpose-model":
        if descriptor.get("schema_version") != 2:
            raise ValueError("Shared model must use runtime schema 2")
        maintenance._model_dependency({"id": identity, "version": version,
            "filename": descriptor.get("model_file"), "sha256": descriptor.get("model_sha256")})
        model_path = str(maintenance._relative_path(descriptor.get("models"))) + "/" + maintenance.MODEL_FILENAME
        if manifest["entries"].get(model_path, {}).get("sha256") != maintenance.MODEL_SHA256:
            raise ValueError("Shared model bytes differ from the pinned model")
        paths = {"models": "directory"}
    else:
        if descriptor.get("schema_version") != 2:
            raise ValueError("Fiji must use runtime schema 2")
        paths = {"fiji_launcher": "executable"}
    if dependencies != expected:
        raise ValueError("Catalog and runtime dependencies differ: " + identity)
    for field, kind in paths.items():
        relative = maintenance._relative_path(descriptor.get(field)).as_posix()
        target = _resolve_member(relative, members)
        if ((kind == "directory" and not target.isdir())
                or (kind == "executable" and (not target.isfile() or not target.mode & 0o111))):
            raise ValueError("Invalid runtime target: " + field)


def _verify_archive(path, identity, record):
    """Hash every archived file and compare its fixed manifest, without extraction."""
    if (type(record.get("bytes")) is not int or record["bytes"] <= 0
            or path.stat().st_size != record["bytes"] or sha256_file(path) != record.get("sha256")):
        raise ValueError("Archive bytes or SHA-256 differ from the offline catalog: " + identity)
    with tarfile.open(path, "r:gz") as archive:
        members = maintenance._archive_members(archive)
        entry = members.get(MANIFEST_NAME)
        if entry is None or not entry.isfile() or entry.size > 32 * 1024 * 1024:
            raise ValueError("Missing or oversized content manifest: " + identity)
        with archive.extractfile(entry) as stream:
            raw_manifest = stream.read()
        if hashlib.sha256(raw_manifest).hexdigest() != record.get("manifest_sha256"):
            raise ValueError("Manifest SHA-256 differs from the offline catalog: " + identity)
        manifest = maintenance._validate_manifest(_json(raw_manifest), identity)
        if set(members) != set(manifest["entries"]) | {MANIFEST_NAME}:
            raise ValueError("Archive membership differs from the manifest: " + identity)
        runtime = None
        for name, member in members.items():
            if name == MANIFEST_NAME:
                continue
            expected = manifest["entries"][name]
            kind = "file" if member.isfile() else "directory" if member.isdir() else "symlink"
            if expected["type"] != kind or (kind != "symlink" and expected["mode"] != member.mode):
                raise ValueError("Archive type or permissions differ from the manifest: " + name)
            if kind == "symlink":
                if expected["target"] != member.linkname:
                    raise ValueError("Archive symlink differs from the manifest: " + name)
                _resolve_member(name, members)
            elif kind == "file":
                if member.size != expected["bytes"]:
                    raise ValueError("Archived file length differs from the manifest: " + name)
                digest = hashlib.sha256()
                if name == "runtime.json" and member.size > 1024 * 1024:
                    raise ValueError("Runtime descriptor exceeds its size limit")
                with archive.extractfile(member) as stream:
                    chunks = [] if name == "runtime.json" else None
                    for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                        digest.update(block)
                        if chunks is not None:
                            chunks.append(block)
                if digest.hexdigest() != expected["sha256"]:
                    raise ValueError("Archived file SHA-256 differs from the manifest: " + name)
                if chunks is not None:
                    runtime = _json(b"".join(chunks))
        _runtime_metadata(runtime, manifest, members, record)
        file_count = sum(item["type"] == "file" for item in manifest["entries"].values())
        if "file_count" in record and (type(record["file_count"]) is not int or record["file_count"] != file_count):
            raise ValueError("Catalog file count differs from the content manifest: " + identity)


def verify_offline_catalog(catalog_path, packages_root, settings):
    """Return five verified online records, using explicit id/version asset URLs."""
    original = _json(Path(catalog_path).read_bytes())
    if (not isinstance(original, dict) or original.get("schema_version") != 1
            or not isinstance(original.get("packages"), dict) or set(original["packages"]) != PACKAGE_IDS):
        raise ValueError("Input must be an offline schema 1 catalog containing all five release packages")
    root = Path(packages_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Package root must be a directory")
    assets = settings["components"]["assets"]
    records = []
    for identity, item in sorted(original["packages"].items()):
        if not isinstance(item, dict) or "url" in item or "id" in item:
            raise ValueError("Offline package records must describe local archives")
        version = _version(item.get("version"))
        if identity in MODULE_IDS:
            _version_tuple(version)
        asset = assets.get(identity)
        if not isinstance(asset, dict) or asset.get("version") != version:
            raise ValueError("Release settings have no asset URL for " + identity + " " + version)
        url = require_https(asset.get("url"))
        path = root / maintenance._relative_path(item.get("path"))
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
            raise ValueError("Package archive must be a regular file inside the package root")
        _verify_archive(path, identity, item)
        records.append(dict({key: value for key, value in item.items() if key != "path"}, id=identity, url=url))
    return records


def _validate_payload(payload, catalog_url):
    catalog = ReleaseCatalog(payload, ReleaseConfiguration(catalog_url))
    locations = {}
    for identity, version in catalog.packages:
        item = catalog.packages[(identity, version)][1]
        address = catalog.packages[(identity, version)][0]
        content = (item["bytes"], item["sha256"], item["manifest_sha256"])
        if address in locations and locations[address] != content:
            raise ValueError("A published package URL cannot identify different archive contents: " + address)
        locations[address] = content
        dependencies = item.get("dependencies")
        if not isinstance(dependencies, dict):
            raise ValueError("Every retained package must declare its dependencies")
        for dependency, required_version in dependencies.items():
            if dependency not in PACKAGE_IDS or not isinstance(required_version, str):
                raise ValueError("Invalid package dependency")
            if (dependency, required_version) not in catalog.packages:
                raise ValueError("Missing retained dependency: " + dependency + " " + required_version)
            if dependency == identity:
                raise ValueError("A package cannot depend on itself")
    # Cycles cannot be installed, even when every referenced version exists.
    visited, active = set(), set()
    def visit(key):
        if key in active:
            raise ValueError("Package dependency cycle")
        if key in visited:
            return
        active.add(key)
        for dependency in catalog.packages[key][1]["dependencies"].items():
            visit(dependency)
        active.remove(key)
        visited.add(key)
    for key in catalog.packages:
        visit(key)


def load_previous_catalog(path, public_key, verifier, *, catalog_url):
    """Authenticate the exact previous payload bytes before parsing its history."""
    envelope = _json(Path(path).read_bytes())
    if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
        raise ValueError("Previous catalog must be a signed schema 1 envelope")
    try:
        raw = base64.b64decode(envelope.get("payload"), validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("Invalid previous catalog payload encoding") from exc
    verifier.verify(raw, envelope.get("signature"), public_key)
    payload = _json(raw)
    _validate_payload(payload, catalog_url)
    return payload


def merge_catalog(previous, records, *, catalog_url):
    """Append immutable versions; only explicitly supplied versions update latest."""
    if previous is None:
        previous = {"schema_version": 2, "packages": [], "latest": {}}
    _validate_payload(previous, catalog_url)
    merged = deepcopy(previous)
    indexed = {(item["id"], item["version"]): item for item in merged["packages"]}
    seen = set()
    for item in records:
        identity, version = item["id"], item["version"]
        if identity in MODULE_IDS:
            _version_tuple(version)
        key = (identity, version)
        if identity in seen:
            raise ValueError("Only one explicit latest version per component is allowed")
        seen.add(identity)
        if key in indexed and indexed[key] != item:
            raise ValueError("Cannot replace an existing component version: " + identity + " " + version)
        indexed[key] = deepcopy(item)
        merged["latest"][identity] = version
    merged["packages"] = [indexed[key] for key in sorted(indexed)]
    _validate_payload(merged, catalog_url)
    return merged


def publish_catalog(catalog_path, packages_root, output, *, sparkle_bin, settings_path=None, previous=None):
    """Verify, merge, sign via Keychain, then exclusively create a new output file."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Output already exists: " + str(output))
    settings = load_settings(settings_path)
    configuration = settings["components"]
    records = verify_offline_catalog(catalog_path, packages_root, settings)
    with PublicVerifier() as verifier:
        history = (load_previous_catalog(previous, configuration["public_key"], verifier,
                                         catalog_url=configuration["catalog_url"]) if previous is not None else None)
        payload = merge_catalog(history, records, catalog_url=configuration["catalog_url"])
        raw = canonical_bytes(payload)
        signature = sign_bytes(raw, configuration["public_key"], configuration["keychain_account"],
                               Path(sparkle_bin), verifier)
    envelope = {"schema_version": 1, "payload": base64.b64encode(raw).decode("ascii"), "signature": signature}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(envelope, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    return envelope


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path, help="Offline schema 1 catalog.json")
    parser.add_argument("--packages", type=Path, required=True, help="Root containing the catalog's local archives")
    parser.add_argument("--previous", type=Path, help="Authenticated previous signed catalog; retained without modification")
    parser.add_argument("--sparkle-bin", type=Path, required=True, help="Official Sparkle bin directory containing sign_update")
    parser.add_argument("--settings", type=Path, help="Explicit release settings; defaults to tools/build/release-settings.json")
    parser.add_argument("--output", type=Path, required=True, help="New signed catalog file; existing files are refused")
    args = parser.parse_args(argv)
    try:
        publish_catalog(args.catalog, args.packages, args.output, sparkle_bin=args.sparkle_bin,
                        settings_path=args.settings, previous=args.previous)
    except (ValueError, OSError, maintenance.ComponentError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
