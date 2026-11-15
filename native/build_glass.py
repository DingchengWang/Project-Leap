#!/usr/bin/env python3
"""Build the AppKit glass controls and Foundation storage services for macOS 27+."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

NATIVE = Path(__file__).resolve().parent
LIBRARY_NAME = "libLeapGlass.dylib"


def build(output, identity="-"):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="Leap-Glass-build-", dir="/private/tmp") as directory:
        library = Path(directory) / LIBRARY_NAME
        subprocess.run(["xcrun", "swiftc", "-swift-version", "6", "-emit-library",
                        "-target", "arm64-apple-macosx27.0", "-warnings-as-errors",
                        "-module-cache-path", str(Path(directory) / "module-cache"),
                        "-Xlinker", "-install_name", "-Xlinker", "@rpath/" + LIBRARY_NAME,
                        "-import-objc-header", str(NATIVE / "PublicGlassAPI.h"),
                        str(NATIVE / "selection_interaction.swift"), str(NATIVE / "glass_selection.swift"),
                        str(NATIVE / "glass_bridge.swift"), str(NATIVE / "storage_bridge.swift"),
                        str(NATIVE / "catalog_signature.swift"),
                        "-o", str(library)], check=True)
        options = ["--options", "runtime", "--timestamp"] if identity != "-" else []
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", identity, *options, str(library)], check=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(library)], check=True)
        # Never rewrite a Mach-O inode already mapped by a running test/app:
        # macOS retains its signature pages and rejects a changed image.
        with tempfile.NamedTemporaryFile(prefix=".LeapGlass-", dir=output.parent, delete=False) as target:
            replacement = Path(target.name)
        try:
            shutil.copy2(library, replacement)
            os.replace(replacement, output)
        finally:
            replacement.unlink(missing_ok=True)
    return output


def embed(app, identity="-"):
    app = Path(app).resolve()
    if not (app / "Contents/Info.plist").is_file():
        raise ValueError("Expected an existing macOS application bundle")
    return build(app / "Contents/Frameworks" / LIBRARY_NAME, identity)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path, nargs="?")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--identity", default="-")
    arguments = parser.parse_args()
    if bool(arguments.app) == bool(arguments.output):
        parser.error("Choose either an application bundle or --output")
    print(build(arguments.output, arguments.identity) if arguments.output else embed(arguments.app, arguments.identity))
