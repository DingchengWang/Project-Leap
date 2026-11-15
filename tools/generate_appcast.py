#!/usr/bin/env python3
"""Publish a verified main-app DMG as a Sparkle RSS feed and detached signature.

The publisher authenticates a previous XML file with its adjacent ``.sig`` file
before retaining any history. This publisher-only signature binds every old
item even when its archive is no longer local; it does not enable Sparkle's
SURequireSignedFeed setting. Keep both files together between releases.

Sparkle format: https://sparkle-project.org/documentation/publishing/
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
import json
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from release_support import PublicVerifier, load_settings, require_https, sha256_file, sign_bytes

SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
RELEASE_NS = "urn:projectleap:release-metadata"
ET.register_namespace("sparkle", SPARKLE_NS)
ET.register_namespace("leap", RELEASE_NS)


def sparkle(name):
    return "{" + SPARKLE_NS + "}" + name


def release(name):
    return "{" + RELEASE_NS + "}" + name


def positive_integer(value, label):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError(label + " must be a positive integer without leading zeros")
    return int(value)


def version_string(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}", value):
        raise ValueError(label + " must be a three-part numeric release version")
    return value


def system_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?", value):
        raise ValueError("minimum macOS version must contain major.minor or major.minor.patch")
    return value if value.count(".") == 2 else value + ".0"


def check_signature_text(value):
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid Ed25519 signature encoding") from error
    if len(raw) != 64 or base64.b64encode(raw).decode("ascii") != value:
        raise ValueError("An Ed25519 signature must encode exactly 64 bytes")
    return value


def read_archive_info(archive):
    """Inspect the actual DMG, mounted read-only, without launching its app."""
    with tempfile.TemporaryDirectory(prefix="leap-appcast-mount-") as temporary:
        mountpoint = Path(temporary) / "volume"
        mountpoint.mkdir()
        subprocess.run(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen",
                        "-mountpoint", str(mountpoint), str(archive)], check=True, capture_output=True)
        try:
            applications = [path for path in mountpoint.glob("*.app") if path.is_dir() and not path.is_symlink()]
            if len(applications) != 1:
                raise ValueError("The main-only DMG must contain exactly one top-level application")
            info_path = applications[0] / "Contents/Info.plist"
            if (not info_path.is_file() or info_path.is_symlink()
                    or not info_path.resolve().is_relative_to(mountpoint.resolve())):
                raise ValueError("The archived app must contain a regular Contents/Info.plist")
            info = plistlib.loads(info_path.read_bytes())
            if not isinstance(info, dict):
                raise ValueError("The archived Info.plist must be a dictionary")
            return info
        finally:
            subprocess.run(["/usr/bin/hdiutil", "detach", str(mountpoint)], check=True, capture_output=True)


def validate_metadata(info, report, application):
    """Bind settings and build-report metadata to the Info.plist inside the DMG."""
    version = version_string(report.get("version"), "build-report version")
    if version != version_string(application["version"], "configured version"):
        raise ValueError("The build-report version differs from the configured release")
    build = positive_integer(report.get("build_number"), "build-report build number")
    first = positive_integer(application["first_public_build"], "first public build")
    if build < first:
        raise ValueError("The build number precedes the first public build")
    identity = application["bundle_identifier"]
    if not identity or report.get("bundle_identifier") != identity:
        raise ValueError("The build-report bundle identifier differs from the configured application")
    expected = {"CFBundleIdentifier": identity, "CFBundleShortVersionString": version,
                "SUFeedURL": application["feed_url"], "SUPublicEDKey": application["public_key"]}
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError("Archived Info.plist does not match release settings: " + key)
    if positive_integer(info.get("CFBundleVersion"), "archived CFBundleVersion") != build:
        raise ValueError("Archived CFBundleVersion differs from the build report")
    minimum = system_version(application["minimum_macos"])
    if system_version(info.get("LSMinimumSystemVersion")) != minimum:
        raise ValueError("Archived minimum macOS version differs from release settings")
    return {"version": version, "build": build, "minimum_macos": minimum, "bundle_identifier": identity}


def validate_archive(archive, report_path, application):
    archive = Path(archive).resolve(strict=True)
    if not archive.is_file() or archive.suffix.lower() != ".dmg":
        raise ValueError("The update archive must be the final main-only DMG")
    if archive.name != application["archive_filename"]:
        raise ValueError("The archive filename differs from release settings")
    require_https(application["archive_url"])
    require_https(application["feed_url"])
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    record = report.get("main_only_dmg")
    if not isinstance(record, dict):
        raise ValueError("build-report.json must contain main_only_dmg metadata")
    if Path(record.get("path", "")).name != archive.name:
        raise ValueError("The build report names a different main-only DMG")
    length = positive_integer(record.get("bytes"), "main-only DMG byte count")
    sha256 = record.get("sha256")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("The build report must contain a lowercase SHA-256 digest")
    if archive.stat().st_size != length or sha256_file(archive) != sha256:
        raise ValueError("The final DMG does not match its build-report size and SHA-256")
    metadata = validate_metadata(read_archive_info(archive), report, application)
    return {**metadata, "bytes": length, "sha256": sha256, "url": application["archive_url"]}


def only_child(parent, name):
    children = parent.findall(name)
    if len(children) != 1:
        raise ValueError("Expected exactly one appcast field: " + name)
    return children[0]


def item_identity(item, application):
    enclosure = only_child(item, "enclosure")
    url = enclosure.get("url")
    require_https(url)
    if enclosure.get("type") != "application/octet-stream":
        raise ValueError("Unexpected enclosure media type")
    check_signature_text(enclosure.get(sparkle("edSignature")))
    identity = only_child(item, release("bundleIdentifier")).text
    if identity != application["bundle_identifier"]:
        raise ValueError("Previous appcast belongs to a different application")
    sha256 = only_child(item, release("sha256")).text
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Previous appcast contains an invalid SHA-256 digest")
    minimum = only_child(item, sparkle("minimumSystemVersion")).text
    if minimum != system_version(minimum):
        raise ValueError("Appcast minimumSystemVersion must use major.minor.patch")
    build = positive_integer(only_child(item, sparkle("version")).text, "appcast build number")
    if build < positive_integer(application["first_public_build"], "first public build"):
        raise ValueError("Previous appcast contains a pre-public build")
    published = parsedate_to_datetime(only_child(item, "pubDate").text)
    if published.tzinfo is None:
        raise ValueError("Appcast pubDate must include a timezone")
    return {"version": version_string(only_child(item, sparkle("shortVersionString")).text, "appcast version"),
            "build": build, "minimum_macos": minimum, "bundle_identifier": identity,
            "bytes": positive_integer(enclosure.get("length"), "enclosure length"), "sha256": sha256, "url": url}


def authenticated_previous(path, application, verifier):
    path = Path(path)
    payload = path.read_bytes()
    signature = check_signature_text(Path(str(path) + ".sig").read_text(encoding="ascii").strip())
    verifier.verify(payload, signature, application["public_key"])
    root = ET.fromstring(payload)
    if root.tag != "rss" or root.attrib != {"version": "2.0"}:
        raise ValueError("Previous appcast must be an RSS 2.0 document")
    channel = only_child(root, "channel")
    if only_child(channel, "link").text != application["feed_url"]:
        raise ValueError("Previous appcast uses a different feed URL")
    items = channel.findall("item")
    identities = [item_identity(item, application) for item in items]
    builds = [item["build"] for item in identities]
    if not builds or builds != sorted(set(builds), reverse=True):
        raise ValueError("Previous builds must be unique and ordered newest first")
    return root, channel, identities, payload, signature


def sign_archive(archive, application, sparkle_bin, verifier):
    result = subprocess.run([str(Path(sparkle_bin) / "sign_update"), "--account",
                             application["keychain_account"], "-p", str(archive)],
                            check=True, text=True, capture_output=True)
    signature = check_signature_text(result.stdout.strip())
    verifier.verify(Path(archive).read_bytes(), signature, application["public_key"])
    return signature


def write_pair(output, payload, signature):
    output = Path(output)
    signature_path = Path(str(output) + ".sig")
    signature_bytes = (signature + "\n").encode("ascii")
    for path, content in ((output, payload), (signature_path, signature_bytes)):
        if path.exists() and path.read_bytes() != content:
            raise FileExistsError("Choose a new candidate output path; refusing to overwrite " + str(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    for path, content in ((output, payload), (signature_path, signature_bytes)):
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(content)


def publish(archive, build_report, output, *, settings, sparkle_bin, previous=None):
    """Validate and sign one real release, returning an auditable result dictionary."""
    application = settings["application"]
    archive = Path(archive).resolve(strict=True)
    metadata = validate_archive(archive, build_report, application)
    with PublicVerifier() as verifier:
        if previous is None:
            if metadata["build"] != application["first_public_build"]:
                raise ValueError("The first appcast must start at first_public_build; supply --previous for later builds")
            root = ET.Element("rss", {"version": "2.0"})
            channel = ET.SubElement(root, "channel")
            ET.SubElement(channel, "title").text = application["name"] + " updates"
            ET.SubElement(channel, "link").text = application["feed_url"]
            ET.SubElement(channel, "description").text = "Updates for " + application["name"] + " on macOS"
            identities = []
        else:
            root, channel, identities, old_payload, old_signature = authenticated_previous(previous, application, verifier)
            existing = next((item for item in identities if item["build"] == metadata["build"]), None)
            if existing is not None:
                if existing != metadata:
                    raise ValueError("A published build number cannot be reused for different release content")
                if metadata["build"] != identities[0]["build"]:
                    raise ValueError("Cannot republish an older build as the current release")
                old_item = channel.findall("item")[0]
                verifier.verify(archive.read_bytes(), only_child(old_item, "enclosure").get(sparkle("edSignature")),
                                application["public_key"])
                write_pair(output, old_payload, old_signature)
                return {**metadata, "appcast": str(output), "signature": str(output) + ".sig",
                        "items": len(identities), "unchanged": True}
            if metadata["build"] <= identities[0]["build"]:
                raise ValueError("A new release build must increase beyond the current appcast build")
            if any(item["url"] == metadata["url"] for item in identities):
                raise ValueError("Each new release must use a new immutable archive URL")
        archive_signature = sign_archive(archive, application, sparkle_bin, verifier)
        if archive.stat().st_size != metadata["bytes"] or sha256_file(archive) != metadata["sha256"]:
            raise ValueError("The archive changed during publication")
        item = ET.Element("item")
        ET.SubElement(item, "title").text = application["name"] + " " + metadata["version"]
        ET.SubElement(item, "pubDate").text = format_datetime(datetime.now(timezone.utc).replace(microsecond=0))
        ET.SubElement(item, sparkle("version")).text = str(metadata["build"])
        ET.SubElement(item, sparkle("shortVersionString")).text = metadata["version"]
        ET.SubElement(item, sparkle("minimumSystemVersion")).text = metadata["minimum_macos"]
        ET.SubElement(item, release("bundleIdentifier")).text = metadata["bundle_identifier"]
        ET.SubElement(item, release("sha256")).text = metadata["sha256"]
        ET.SubElement(item, "enclosure", {"url": metadata["url"], "length": str(metadata["bytes"]),
                                         "type": "application/octet-stream", sparkle("edSignature"): archive_signature})
        previous_items = list(channel)
        channel.insert(next((index for index, child in enumerate(previous_items) if child.tag == "item"), len(previous_items)), item)
        ET.indent(root, space="  ")
        payload = ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"
        signature = sign_bytes(payload, application["public_key"], application["keychain_account"], Path(sparkle_bin), verifier)
        verifier.verify(payload, signature, application["public_key"])
        write_pair(output, payload, signature)
    return {**metadata, "appcast": str(output), "signature": str(output) + ".sig",
            "items": len(identities) + 1, "unchanged": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Final main-only DMG")
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New candidate appcast.xml path")
    parser.add_argument("--previous", type=Path, help="Previously published XML; its adjacent .sig is required")
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--sparkle-bin", type=Path, required=True, help="Verified Sparkle 2.10.0 bin directory")
    args = parser.parse_args()
    result = publish(args.archive, args.build_report, args.output, settings=load_settings(args.settings),
                     sparkle_bin=args.sparkle_bin, previous=args.previous)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
