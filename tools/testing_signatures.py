"""Disposable Ed25519 signatures for tests and the isolated release probe only.

Production publishing uses release_support.sign_bytes with Sparkle Keychain
signing. This fixture must never receive a production private key.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import tempfile


def signed_envelope(payload, key_path, signer):
    """Sign a fixture payload using a temporary test key and test Swift helper."""
    key = Path(key_path).resolve(strict=True)
    if not key.is_relative_to(Path("/tmp").resolve()):
        raise ValueError("Test signing keys must live in a disposable /tmp directory")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    with tempfile.TemporaryDirectory(prefix="leap-catalog-test-sign-", dir="/tmp") as temporary:
        path = Path(temporary) / "payload.json"
        path.write_bytes(raw)
        signature = subprocess.check_output([str(signer), "sign", str(key), str(path)], text=True).strip()
    return {"schema_version": 1, "payload": base64.b64encode(raw).decode(), "signature": signature}
