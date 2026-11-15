#!/usr/bin/env python3
"""Sign an offline package catalog for publication using an existing Ed25519 key.

The output is a signed online catalog. Key creation is separate and never part
of an application build. No file is uploaded or published by this utility.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def signed_envelope(payload, key_path, signer):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    with tempfile.TemporaryDirectory(prefix="leap-catalog-sign-") as temporary:
        path = Path(temporary) / "payload.json"
        path.write_bytes(raw)
        signature = subprocess.check_output([str(signer), "sign", str(key_path), str(path)], text=True).strip()
    return {"schema_version": 1, "payload": base64.b64encode(raw).decode(), "signature": signature}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original = json.loads(args.catalog.read_text())
    if original.get("schema_version") != 1 or not isinstance(original.get("packages"), dict):
        parser.error("Input must be an offline schema 1 component catalog")
    packages = [dict(item, id=identity) for identity, item in original["packages"].items()]
    for item in packages:
        if not all(item.get(key) for key in ("version", "path", "bytes", "sha256", "manifest_sha256")):
            parser.error("Each package must include version, path, bytes, SHA-256 and manifest SHA-256")
    payload = {"schema_version": 2, "packages": packages, "latest": {item["id"]: item["version"] for item in packages}}
    with tempfile.TemporaryDirectory(prefix="leap-sign-tool-") as temporary:
        signer = Path(temporary) / "signer"
        subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(Path(temporary) / "module-cache"), str(ROOT / "tools/component_catalog_signer.swift"), "-o", str(signer)], check=True)
        envelope = signed_envelope(payload, args.private_key, signer)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(envelope, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
