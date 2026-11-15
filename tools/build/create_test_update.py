#!/usr/bin/env python3
"""Prepare isolated real A/B apps and signed loopback-only Sparkle test feeds.

The ephemeral Ed25519 private seed is held in memory and sent to Sparkle's
official signer over stdin. It is never written into an app, feed, or keychain.
The original app bundle is read-only. The scientific runtime archive is copied
unchanged into both test builds.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import plistlib
import secrets
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SIGNER = ROOT / "native/vendor/Sparkle-2.10.0/bin/sign_update"
SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"


def run(arguments, **kwargs):
    return subprocess.run([str(item) for item in arguments], check=True, **kwargs)


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def public_key(seed):
    source = ("import Foundation; import CryptoKit; "
              "let seed = FileHandle.standardInput.readDataToEndOfFile(); "
              "let key = try Curve25519.Signing.PrivateKey(rawRepresentation: seed); "
              "print(key.publicKey.rawRepresentation.base64EncodedString())")
    cache = ROOT / "runtime/cache/swift"
    cache.mkdir(parents=True, exist_ok=True)
    return run(["/usr/bin/swift", "-module-cache-path", cache, "-e", source], input=seed, capture_output=True).stdout.decode().strip()


def sign(seed, path):
    return run([SIGNER, "--ed-key-file", "-", "-p", path], input=base64.b64encode(seed), capture_output=True).stdout.decode().strip()


def verify(seed, path, signature=None):
    arguments = [SIGNER, "--verify", "--ed-key-file", "-", path]
    if signature is not None:
        arguments.append(signature)
    result = subprocess.run([str(item) for item in arguments], input=base64.b64encode(seed), capture_output=True)
    return {"returncode": result.returncode, "stdout": result.stdout.decode(), "stderr": result.stderr.decode()}


def appcast(path, url, signature, length):
    ET.register_namespace("sparkle", SPARKLE_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Project Leap isolated update test"
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = "Internal build B"
    ET.SubElement(item, "description").text = "本地更新测试；分析模块保持 1.0.1。"
    ET.SubElement(item, "{" + SPARKLE_NS + "}version").text = "1002"
    ET.SubElement(item, "{" + SPARKLE_NS + "}shortVersionString").text = "0.1.0-testB"
    ET.SubElement(item, "{" + SPARKLE_NS + "}minimumSystemVersion").text = "12.0"
    ET.SubElement(item, "enclosure", {"url": url, "length": str(length), "type": "application/octet-stream",
                                      "{" + SPARKLE_NS + "}edSignature": signature})
    ET.ElementTree(rss).write(path, encoding="utf-8", xml_declaration=True)


def prepare(source_app, output, port):
    source_app = Path(source_app).resolve()
    output = Path(output).resolve()
    if not (source_app / "Contents/Info.plist").is_file():
        raise ValueError("Source must be a built macOS .app")
    output.mkdir(parents=True, exist_ok=False)
    seed = secrets.token_bytes(32)
    public = public_key(seed)
    bundle_id = "org.projectleap.prototype.updatetest." + uuid.uuid4().hex
    base_url = "http://127.0.0.1:" + str(port)
    feed_dir = output / "feed"
    feed_dir.mkdir()
    apps = {}
    for label, version, number, parent in (("A", "0.1.0-testA", "1001", "applications"),
                                            ("B", "0.1.0-testB", "1002", "build-b")):
        destination = output / parent / source_app.name
        destination.parent.mkdir()
        with tempfile.TemporaryDirectory(prefix="Leap-test-update-sign-", dir="/private/tmp") as temporary:
            staged = Path(temporary) / source_app.name
            run(["/bin/cp", "-cR", source_app, staged])
            plist = staged / "Contents/Info.plist"
            info = plistlib.loads(plist.read_bytes())
            info.update({"CFBundleIdentifier": bundle_id, "CFBundleVersion": number,
                         "CFBundleShortVersionString": version, "SUFeedURL": base_url + "/appcast.xml",
                         "SUPublicEDKey": public, "SUEnableAutomaticChecks": False,
                         "SUAutomaticallyUpdate": False, "SUVerifyUpdateBeforeExtraction": True,
                         "SURequireSignedFeed": True, "SUSignedFeedFailureExpirationInterval": 0,
                         "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True, "NSAllowsArbitraryLoads": True},
                         "LSEnvironment": {"PROJECT_LEAP_HOME": str(output / "app-home")},
                         "LeapInternalUpdateTest": True})
            plist.write_bytes(plistlib.dumps(info))
            (staged / "Contents/Resources/update-test-build.json").write_text(json.dumps({"build": label, "analysis_module": "1.0.1"}))
            run(["/usr/bin/xattr", "-cr", staged])
            run(["/usr/bin/codesign", "--force", "--sign", "-", staged])
            run(["/usr/bin/codesign", "--verify", "--deep", "--strict", staged])
            run(["/bin/cp", "-cR", staged, destination])
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", destination])
        apps[label] = str(destination)
    archive = feed_dir / "Project-Leap-test-B.zip"
    run(["/usr/bin/ditto", "-c", "-k", "--zlibCompressionLevel", "1", "--keepParent", apps["B"], archive])
    signature = sign(seed, archive)
    good = verify(seed, archive, signature)
    if good["returncode"]:
        raise RuntimeError("Sparkle rejected the generated update signature")
    corrupt = feed_dir / "Project-Leap-test-B-corrupt.zip"
    run(["/bin/cp", "-c", archive, corrupt])
    with corrupt.open("r+b") as stream:
        stream.seek(archive.stat().st_size // 2)
        byte = stream.read(1)
        stream.seek(-1, 1)
        stream.write(bytes([byte[0] ^ 1]))
    bad_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    for name, filename, sig in (("appcast.xml", archive.name, signature),
                               ("bad-signature.xml", archive.name, bad_signature),
                               ("corrupt-download.xml", corrupt.name, signature)):
        feed = feed_dir / name
        appcast(feed, base_url + "/" + filename, sig, archive.stat().st_size)
        sign(seed, feed)
        if verify(seed, feed)["returncode"]:
            raise RuntimeError("Sparkle rejected generated feed: " + name)
    invalid = verify(seed, archive, bad_signature)
    corrupt_check = verify(seed, corrupt, signature)
    if invalid["returncode"] == 0 or corrupt_check["returncode"] == 0:
        raise RuntimeError("Negative signature validation unexpectedly succeeded")
    report = {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
              "source_app": str(source_app), "test_apps": apps, "feed_directory": str(feed_dir),
              "feed_url": base_url + "/appcast.xml", "port": port, "bundle_id": bundle_id,
              "public_ed_key": public, "archive_sha256": digest(archive), "archive_bytes": archive.stat().st_size,
              "signature_verification": good, "wrong_signature_verification": invalid,
              "corrupt_archive_verification": corrupt_check,
              "private_key_persisted": False, "codesigning": "ad-hoc", "notarized": False,
              "sparkle_ui_download_replace_relaunch_verified": False}
    (output / "test-update.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def serve(output, port):
    import functools
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(Path(output) / "feed"))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print("Serving only on loopback: http://127.0.0.1:" + str(port) + "/appcast.xml", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve(args.output, args.port)
    elif args.app:
        print(json.dumps(prepare(args.app, args.output, args.port), ensure_ascii=False, indent=2))
    else:
        parser.error("--app is required unless --serve is selected")
