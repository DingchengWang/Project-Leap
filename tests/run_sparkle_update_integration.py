#!/usr/bin/env python3
"""Exercise the production Sparkle bridge using disposable signed dummy apps."""
from __future__ import annotations
import argparse
import base64
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import plistlib
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "native/vendor/Sparkle-2.10.0"

def run(*args, **kwargs):
    return subprocess.run([str(value) for value in args], check=True, **kwargs)


def prepare_signer(directory):
    source = directory / "sign.swift"
    source.write_text('''import Foundation
import CryptoKit
let input = try JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as! [String: String]
let key = try Curve25519.Signing.PrivateKey(rawRepresentation: Data(base64Encoded: input["seed"]!)!)
let signature = try key.signature(for: Data(contentsOf: URL(fileURLWithPath: input["path"]!)))
let result = ["public": key.publicKey.rawRepresentation.base64EncodedString(), "signature": signature.base64EncodedString()]
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: result))
''')
    executable = directory / "sign"
    run("xcrun", "swiftc", "-module-cache-path", directory / "swift-cache", source, "-o", executable)
    return executable


def compile_fixture(directory):
    executable = directory / "Fixture"
    run("xcrun", "clang", "-fobjc-arc", "-arch", "arm64", "-mmacosx-version-min=27.0",
        "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-I", ROOT / "native",
        "-F", VENDOR, "-framework", "Cocoa", "-framework", "Sparkle",
        "-Wl,-rpath,@executable_path/../Frameworks", ROOT / "tests/native/SparkleUpdateFixture.m",
        ROOT / "native/sparkle_bridge.m", "-o", executable)
    return executable


def scenario(directory, name, signer, executable):
    work = directory / name
    feed = work / "feed"
    feed.mkdir(parents=True)
    requests = []
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            requests.append(self.path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(feed)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/appcast.xml"
    seed = base64.b64encode(secrets.token_bytes(32)).decode()
    report = work / "events.jsonl"
    report.write_text("")
    bundle_id = "org.projectleap.fixture." + uuid.uuid4().hex
    # Derive the public key with a disposable public file; the seed stays in memory.
    def sign(path):
        data = json.dumps({"seed": seed, "path": str(path)}).encode()
        return json.loads(run(signer, input=data, capture_output=True).stdout)
    public = sign(report)["public"]
    apps = {}
    for build, parent in (("1", "installed"), ("2", "candidate")):
        app = work / parent / "Leap Update Fixture.app"
        macos = app / "Contents/MacOS"
        frameworks = app / "Contents/Frameworks"
        macos.mkdir(parents=True)
        frameworks.mkdir()
        shutil.copy2(executable, macos / "Fixture")
        shutil.copytree(VENDOR / "Sparkle.framework", frameworks / "Sparkle.framework", symlinks=True)
        info = {"CFBundleIdentifier": bundle_id, "CFBundleName": "Leap Update Fixture", "CFBundleExecutable": "Fixture",
                "CFBundlePackageType": "APPL", "CFBundleVersion": build, "CFBundleShortVersionString": "1.0." + build,
                "LSMinimumSystemVersion": "27.0", "SUFeedURL": url, "SUPublicEDKey": public,
                "SUEnableAutomaticChecks": False, "SUAutomaticallyUpdate": False, "SUVerifyUpdateBeforeExtraction": True,
                "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True}, "FixtureReportPath": str(report), "FixtureScenario": name}
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        run("/usr/bin/codesign", "--force", "--deep", "--sign", "-", app, capture_output=True)
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", app, capture_output=True)
        apps[build] = app
    archive = feed / "update.zip"
    run("/usr/bin/ditto", "-c", "-k", "--keepParent", apps["2"], archive)
    signature = sign(archive)["signature"]
    if name == "bad_signature":
        signature = base64.b64encode(bytes(64)).decode()
    (feed / "appcast.xml").write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><title>Disposable fixture</title>
<item><title>Build 2</title><sparkle:version>2</sparkle:version><sparkle:shortVersionString>1.0.2</sparkle:shortVersionString><sparkle:minimumSystemVersion>27.0</sparkle:minimumSystemVersion>
<enclosure url="http://127.0.0.1:{server.server_port}/update.zip" length="{archive.stat().st_size}" type="application/octet-stream" sparkle:edSignature="{signature}"/>
</item></channel></rss>''')
    process = subprocess.Popen([str(apps["1"] / "Contents/MacOS/Fixture")], stdout=subprocess.DEVNULL, stderr=(work / "stderr.log").open("wb"))
    deadline = time.monotonic() + 70
    try:
        while time.monotonic() < deadline:
            events = [json.loads(line) for line in report.read_text().splitlines()]
            if any(item.get("event") == "relaunched" for item in events):
                break
            if process.poll() is not None and name != "success":
                break
            if any(item.get("event") == "timeout" for item in events):
                break
            time.sleep(.1)
        events = [json.loads(line) for line in report.read_text().splitlines()]
        statuses = [item["status"] for item in events if "status" in item]
        if name == "success":
            assert any(item.get("event") == "relaunched" and item["build"] == "2" for item in events), events
            assert any(item.get("event") == "idle_released" and item["waited_seconds"] >= 1 for item in events), events
            assert "ready_to_install" in statuses, events
            assert plistlib.loads((apps["1"] / "Contents/Info.plist").read_bytes())["CFBundleVersion"] == "2"
        elif name == "bad_signature":
            assert "failed" in statuses and "ready_to_install" not in statuses, events
            assert plistlib.loads((apps["1"] / "Contents/Info.plist").read_bytes())["CFBundleVersion"] == "1"
        else:
            assert "failed" in statuses and "/update.zip" not in requests, events
        return {"scenario": name, "passed": True, "statuses": statuses, "requests": requests,
                "events": str(report), "installed_build": plistlib.loads((apps["1"] / "Contents/Info.plist").read_bytes())["CFBundleVersion"]}
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scenario", action="append", choices=("plan_mismatch", "feed_mismatch", "host_mismatch", "bad_signature", "success"))
    arguments = parser.parse_args()
    directory = arguments.output or Path(tempfile.mkdtemp(prefix="leap-sparkle-integration-", dir="/private/tmp"))
    directory.mkdir(exist_ok=True)
    signer = prepare_signer(directory)
    executable = compile_fixture(directory)
    results = []
    for name in arguments.scenario or ("plan_mismatch", "feed_mismatch", "host_mismatch", "bad_signature", "success"):
        result = scenario(directory, name, signer, executable)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    (directory / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
