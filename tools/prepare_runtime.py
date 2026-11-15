#!/usr/bin/env python3
"""Copy scientific payload bytes; relocate only installation metadata and links."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path.home() / "Applications/Project Leap 2D Support/Releases/environment-c076335b5cbac2c4"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def clone(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"Refusing to replace {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/bin/cp", "-cR", str(source), str(target)], check=True)


def relocate_python_metadata(source: Path, seed: Path) -> dict:
    """Resolve CPython's build-path metadata relative to the copied interpreter."""
    relative = "python/lib/python3.9/_sysconfigdata__darwin_darwin.py"
    path = seed / relative
    original_prefix = str(source / "Managed Python/cpython-3.9.25-macos-aarch64-none")
    content = path.read_text()
    replacements = content.count(original_prefix)
    if not replacements:
        raise ValueError("Expected source-prefix metadata is absent; inspect the CPython layout")
    before = digest(path)
    content = content.replace(original_prefix, "__PROJECT_LEAP_PYTHON_PREFIX__")
    content += '''
# Resolve only relocated installation paths; compiler flags and values are unchanged.
import os as _project_leap_os
_project_leap_prefix = _project_leap_os.path.realpath(
    _project_leap_os.path.join(_project_leap_os.path.dirname(__file__), "..", "..")
)
build_time_vars = {
    key: value.replace("__PROJECT_LEAP_PYTHON_PREFIX__", _project_leap_prefix)
    if isinstance(value, str) else value
    for key, value in build_time_vars.items()
}
del _project_leap_os, _project_leap_prefix
'''
    path.write_text(content)
    return {"path":relative, "purpose":"Relocate installation paths dynamically; preserve all non-path values",
            "source_sha256":before, "relocated_sha256":digest(path), "prefix_replacements":replacements}


def inventory(root: Path) -> dict:
    entries = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                resolved = path.resolve()
                if not resolved.is_relative_to(root.resolve()):
                    raise ValueError(f"External symlink: {relative} -> {os.readlink(path)}")
                entries[relative] = {"type": "symlink", "target": os.readlink(path)}
            elif path.is_file():
                entries[relative] = {"type": "file", "bytes": path.stat().st_size, "sha256": digest(path)}
    return entries


def prepare(source: Path, seed: Path) -> None:
    if seed.exists():
        raise FileExistsError(f"Runtime seed already exists: {seed}")
    seed.mkdir(parents=True)
    clone(source / "Managed Python/cpython-3.9.25-macos-aarch64-none", seed / "python")
    clone(source / "Environment/lib/python3.9/site-packages", seed / "site-packages")
    clone(source / "Fiji", seed / "Fiji")
    clone(source / "Models", seed / "Models")
    path_adaptation = relocate_python_metadata(source, seed)
    # Standalone CPython uses relative internal links. Any absolute internal link is
    # rewritten in the copy only; a link escaping the supplied source is rejected.
    mapping = [
        (source / "Managed Python/cpython-3.9.25-macos-aarch64-none", seed / "python"),
        (source / "Environment/lib/python3.9/site-packages", seed / "site-packages"),
        (source / "Fiji", seed / "Fiji"), (source / "Models", seed / "Models"),
    ]
    rewritten = []
    for directory, dirs, files in os.walk(seed, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if not path.is_symlink():
                continue
            link = os.readlink(path)
            if not os.path.isabs(link):
                continue
            for original, copied in mapping:
                try:
                    relative = Path(link).relative_to(original)
                except ValueError:
                    continue
                replacement = os.path.relpath(copied / relative, path.parent)
                path.unlink()
                path.symlink_to(replacement)
                rewritten.append({"path": path.relative_to(seed).as_posix(), "target": replacement})
                break
            else:
                raise ValueError(f"Unsupported external link in copy: {path} -> {link}")
    descriptor = {
        "schema_version": 1,
        "component_id": "project-leap-2d",
        "component_version": "1.0.1",
        "platform": "macos-arm64",
        "python_version": "3.9.25",
        "python": "python/bin/python3.9",
        "site_packages": "site-packages",
        "fiji_launcher": "Fiji/Fiji.app/Contents/MacOS/fiji-macos-arm64",
        "models": "Models",
        "environment_contract": "c076335b5cbac2c440f927cf92db02ac60f91136170c54b0a83c2db333f8da9a",
    }
    (seed / "runtime.json").write_text(json.dumps(descriptor, indent=2) + "\n")
    entries = inventory(seed)
    sys.path.insert(0, str(ROOT / "app"))
    from leap_desktop.maintenance import build_manifest
    build_manifest(seed)
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "seed": str(seed),
              "source_read_only": str(source), "file_count": sum(e["type"] == "file" for e in entries.values()),
              "symlink_count": sum(e["type"] == "symlink" for e in entries.values()), "rewritten_symlinks": rewritten,
              "descriptor": descriptor, "path_adaptations":[path_adaptation],
              "manifest_sha256": digest(seed / "content-manifest.json")}
    (ROOT / "runtime/logs/seed-preparation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--seed", type=Path, default=ROOT / "runtime/seed")
    args = parser.parse_args()
    prepare(args.source.resolve(), args.seed.resolve())
