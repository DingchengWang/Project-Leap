#!/usr/bin/env python3
"""Verify the vendored framework and compile/sign the minimal bridge."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

NATIVE = Path(__file__).resolve().parent
SPARKLE = NATIVE / "vendor/Sparkle-2.10.0/Sparkle.framework"


def verify_vendor():
    root = SPARKLE.parent
    provenance = json.loads((root.parent / "Sparkle-2.10.0.provenance.json").read_text())
    actual = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        info = path.lstat()
        if path.is_symlink():
            if not path.resolve().is_relative_to(root) or not path.exists():
                raise ValueError("External or broken Sparkle vendor link: " + name)
            actual[name] = {"type": "symlink", "target": os.readlink(path)}
        elif path.is_dir():
            actual[name] = {"type": "directory", "mode": info.st_mode & 0o7777}
        elif path.is_file():
            actual[name] = {"type": "file", "mode": info.st_mode & 0o7777,
                            "bytes": info.st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        else:
            raise ValueError("Unsupported Sparkle vendor entry: " + name)
    if provenance.get("version") != "2.10.0" or provenance.get("entries") != actual or not actual:
        raise ValueError("Sparkle vendor bytes differ from their provenance record")
    return {"version": "2.10.0", "entries_verified": len(actual)}


def run(*arguments):
    subprocess.run([str(item) for item in arguments], check=True)


def embed(app, identity="-"):
    verify_vendor()
    app = Path(app).resolve()
    if not (app / "Contents/Info.plist").is_file():
        raise ValueError("Expected an existing macOS application bundle")
    frameworks = app / "Contents/Frameworks"
    frameworks.mkdir(exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="Leap-Sparkle-sign-", dir="/private/tmp")
    staging = Path(temporary.name)
    destination = staging / "Sparkle.framework"
    shutil.copytree(SPARKLE, destination, symlinks=True)
    # FileProvider can immediately reattach FinderInfo in an iCloud directory.
    # Sign outside that directory, then copy the verified result into the app.
    run("/usr/bin/xattr", "-cr", destination)
    bridge = staging / "libLeapSparkle.dylib"
    run("xcrun", "clang", "-Werror", "-dynamiclib", "-fobjc-arc", "-arch", "arm64", "-mmacosx-version-min=12.0",
        "-F", SPARKLE.parent, "-framework", "Cocoa", "-framework", "Sparkle",
        "-Wl,-rpath,@loader_path", "-Wl,-install_name,@rpath/libLeapSparkle.dylib",
        NATIVE / "sparkle_bridge.m", "-o", bridge)
    version = destination / "Versions/B"
    options = ["--options", "runtime", "--timestamp"] if identity != "-" else []
    # Sign from nested executable to containing bundle. The application itself
    # is signed by build_app.py after all resources have been assembled.
    for item in [version / "Autoupdate", version / "Updater.app",
                 version / "XPCServices/Installer.xpc", version / "XPCServices/Downloader.xpc",
                 destination, bridge]:
        run("/usr/bin/codesign", "--force", "--sign", identity, *options, item)
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", destination)
    installed = frameworks / "Sparkle.framework"
    if installed.exists():
        shutil.rmtree(installed)
    shutil.copytree(destination, installed, symlinks=True)
    shutil.copy2(bridge, frameworks / bridge.name)
    notices = app / "Contents/Resources/ThirdPartyNotices"
    notices.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SPARKLE.parent / "LICENSE", notices / "Sparkle-LICENSE.txt")
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", installed)
    temporary.cleanup()
    return frameworks / bridge.name


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path, nargs="?")
    parser.add_argument("--identity", default="-")
    parser.add_argument("--verify-vendor", action="store_true")
    args = parser.parse_args()
    if args.verify_vendor:
        print(json.dumps(verify_vendor()))
    elif args.app:
        print(embed(args.app, args.identity))
    else:
        parser.error("Provide an application bundle or --verify-vendor")
