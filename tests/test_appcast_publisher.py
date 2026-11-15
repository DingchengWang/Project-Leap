"""Real DMG and Ed25519 publisher tests; all private keys stay in temporary files.

Run with SPARKLE_BIN pointing to an official Sparkle 2.10.0 bin directory.
The fixture adapter substitutes a disposable key file for --account before
calling the real sign_update executable. It never accesses the Keychain.
"""
from __future__ import annotations

import base64
import copy
from contextlib import nullcontext
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import generate_appcast as publisher
from release_support import PublicVerifier, sha256_file


class AppcastPublisherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin":
            raise unittest.SkipTest("Real publisher tests require macOS hdiutil and CryptoKit")
        official_bin = Path(os.environ.get("SPARKLE_BIN", ROOT / "native/vendor/Sparkle-2.10.0/bin"))
        cls.official_signer = official_bin / "sign_update"
        if not cls.official_signer.is_file():
            raise unittest.SkipTest("Set SPARKLE_BIN to the official Sparkle 2.10.0 bin directory")
        cls.temporary = tempfile.TemporaryDirectory(prefix="leap-appcast-fixtures-", dir="/private/tmp")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.fixture_root = Path(cls.temporary.name)
        key_generator = cls.fixture_root / "generate-key"
        subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(cls.fixture_root / "swift-cache"),
                        str(ROOT / "tools/component_catalog_signer.swift"), "-o", str(key_generator)],
                       check=True, capture_output=True)
        cls.key = cls.fixture_root / "key.raw"
        cls.public_key = subprocess.check_output([str(key_generator), "generate", str(cls.key)], text=True).strip()
        encoded_key = cls.fixture_root / "key.base64"
        encoded_key.write_bytes(base64.b64encode(cls.key.read_bytes()))
        encoded_key.chmod(0o600)
        cls.fixture_bin = cls.fixture_root / "bin"
        cls.fixture_bin.mkdir()
        wrapper = cls.fixture_bin / "sign_update"
        wrapper.write_text("#!" + sys.executable + "\n" +
                           "import pathlib, subprocess, sys\n" +
                           "args = sys.argv[1:]\n" +
                           "assert args[:2] == ['--account', 'temporary-appcast-test']\n" +
                           "args = ['--ed-key-file', '-'] + args[2:]\n" +
                           "result = subprocess.run([" + repr(str(cls.official_signer)) + "] + args, input=pathlib.Path(" +
                           repr(str(encoded_key)) + ").read_bytes(), capture_output=True)\n" +
                           "sys.stdout.buffer.write(result.stdout)\n" +
                           "sys.stderr.buffer.write(result.stderr)\n" +
                           "sys.exit(result.returncode)\n", encoding="utf-8")
        wrapper.chmod(0o700)
        cls.settings = json.loads((ROOT / "tools/build/release-settings.json").read_text(encoding="utf-8"))
        cls.settings["application"].update({"name": "Leap Publisher Fixture", "public_key": cls.public_key,
                "keychain_account": "temporary-appcast-test",
                "feed_url": "https://publisher-fixture.invalid/updates/appcast.xml",
                "archive_filename": "Leap-1.0.0.dmg", "archive_url": "https://publisher-fixture.invalid/Leap-1.0.0.dmg",
                "minimum_macos": "27.0", "version": "1.0.0", "first_public_build": 1})
        cls.fixtures = {}
        for build in (1, 2):
            settings = cls.settings_for(build)
            application = settings["application"]
            work = cls.fixture_root / ("build-" + str(build))
            stage = work / "stage"
            contents = stage / "Leap Publisher Fixture.app/Contents"
            contents.mkdir(parents=True)
            info = {"CFBundleIdentifier": application["bundle_identifier"], "CFBundleName": application["name"],
                    "CFBundlePackageType": "APPL", "CFBundleVersion": str(build),
                    "CFBundleShortVersionString": application["version"],
                    "LSMinimumSystemVersion": application["minimum_macos"], "SUFeedURL": application["feed_url"],
                    "SUPublicEDKey": application["public_key"]}
            (contents / "Info.plist").write_bytes(plistlib.dumps(info))
            archive = work / application["archive_filename"]
            subprocess.run(["/usr/bin/hdiutil", "create", "-quiet", "-srcfolder", str(stage),
                            "-format", "UDZO", str(archive)], check=True, capture_output=True)
            report = {"version": application["version"], "build_number": str(build),
                      "bundle_identifier": application["bundle_identifier"],
                      "main_only_dmg": {"path": str(archive), "bytes": archive.stat().st_size, "sha256": sha256_file(archive)}}
            report_path = work / "build-report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            cls.fixtures[build] = (archive, report_path, info)
        cls.verifier_context = PublicVerifier()
        cls.verifier = cls.verifier_context.__enter__()
        cls.addClassCleanup(cls.verifier_context.__exit__, None, None, None)
        # Reuse the real native public-key verifier across cases; the CLI case
        # separately exercises normal per-invocation construction and cleanup.
        patcher = mock.patch.object(publisher, "PublicVerifier", side_effect=lambda: nullcontext(cls.verifier))
        patcher.start()
        cls.addClassCleanup(patcher.stop)

    @classmethod
    def settings_for(cls, build):
        settings = copy.deepcopy(cls.settings)
        version = "1.0." + str(build - 1)
        settings["application"].update({"version": version, "archive_filename": "Leap-" + version + ".dmg",
                                       "archive_url": "https://publisher-fixture.invalid/Leap-" + version + ".dmg"})
        return settings

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="leap-appcast-test-", dir="/private/tmp")
        self.addCleanup(self.work.cleanup)
        self.directory = Path(self.work.name)

    def publish(self, build=1, *, previous=None, output=None, settings=None, archive=None, report=None):
        fixture_archive, fixture_report, _ = self.fixtures[build]
        return publisher.publish(archive or fixture_archive, report or fixture_report,
                                 output or self.directory / "appcast.xml", settings=settings or self.settings_for(build),
                                 sparkle_bin=self.fixture_bin, previous=previous)

    def tamper_report(self, mutate, build=1):
        report = json.loads(self.fixtures[build][1].read_text())
        mutate(report)
        path = self.directory / "build-report.json"
        path.write_text(json.dumps(report))
        return path

    def sign_fixture(self, payload):
        path = self.directory / "payload.json"
        path.write_bytes(payload)
        return subprocess.check_output([str(self.official_signer), "--ed-key-file", "-", "-p", str(path)],
                                       input=base64.b64encode(self.key.read_bytes())).decode().strip()

    def test_real_archive_rss_and_both_signatures(self):
        result = self.publish()
        output = Path(result["appcast"])
        root = ET.fromstring(output.read_bytes())
        self.assertEqual(root.tag, "rss")
        self.assertEqual(root.get("version"), "2.0")
        item = root.find("channel/item")
        self.assertEqual(item.findtext(publisher.sparkle("version")), "1")
        self.assertEqual(item.findtext(publisher.sparkle("shortVersionString")), "1.0.0")
        self.assertEqual(item.findtext(publisher.sparkle("minimumSystemVersion")), "27.0.0")
        self.assertEqual(item.findtext(publisher.release("sha256")), sha256_file(self.fixtures[1][0]))
        enclosure = item.find("enclosure")
        self.assertEqual(enclosure.get("length"), str(self.fixtures[1][0].stat().st_size))
        self.assertEqual(enclosure.get("url"), self.settings_for(1)["application"]["archive_url"])
        with nullcontext(self.verifier) as verifier:
            verifier.verify(self.fixtures[1][0].read_bytes(), enclosure.get(publisher.sparkle("edSignature")), self.public_key)
            verifier.verify(output.read_bytes(), Path(str(output) + ".sig").read_text().strip(), self.public_key)
        self.assertFalse(result["unchanged"])

    def test_command_line_generates_candidate_and_json_summary(self):
        settings_path = self.directory / "settings.json"
        settings_path.write_text(json.dumps(self.settings_for(1)))
        output = self.directory / "cli/appcast.xml"
        result = subprocess.run([sys.executable, str(ROOT / "tools/generate_appcast.py"),
                                 "--archive", str(self.fixtures[1][0]), "--build-report", str(self.fixtures[1][1]),
                                 "--output", str(output), "--settings", str(settings_path),
                                 "--sparkle-bin", str(self.fixture_bin)], check=True, text=True, capture_output=True)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["build"], 1)
        self.assertEqual(summary["appcast"], str(output))
        self.assertEqual(summary["signature"], str(output) + ".sig")
        self.assertTrue(output.is_file())
        self.assertTrue(Path(summary["signature"]).is_file())

    def test_history_appends_without_local_previous_archive(self):
        first = Path(self.publish()["appcast"])
        old = ET.fromstring(first.read_bytes()).find("channel/item")
        # The second release receives only the signed old feed, not an archive path.
        second = self.directory / "next/appcast.xml"
        result = self.publish(2, previous=first, output=second)
        items = ET.fromstring(second.read_bytes()).findall("channel/item")
        self.assertEqual([item.findtext(publisher.sparkle("version")) for item in items], ["2", "1"])
        self.assertEqual(publisher.item_identity(items[1], self.settings["application"]),
                         publisher.item_identity(old, self.settings["application"]))
        self.assertEqual(result["items"], 2)

    def test_same_release_is_byte_identical_and_needs_no_signer(self):
        first = Path(self.publish()["appcast"])
        with mock.patch.object(publisher, "sign_archive", side_effect=AssertionError("must not sign again")):
            result = self.publish(previous=first, output=self.directory / "repeated.xml")
        self.assertTrue(result["unchanged"])
        self.assertEqual(first.read_bytes(), Path(result["appcast"]).read_bytes())
        self.assertEqual(Path(str(first) + ".sig").read_bytes(), Path(result["signature"]).read_bytes())

    def test_modified_history_is_rejected_before_it_is_used(self):
        first = Path(self.publish()["appcast"])
        first.write_bytes(first.read_bytes().replace(b"Leap Publisher Fixture 1.0.0", b"Tampered release"))
        with self.assertRaises((ValueError, RuntimeError)):
            self.publish(2, previous=first, output=self.directory / "next.xml")
        self.assertFalse((self.directory / "next.xml").exists())

    def test_missing_history_signature_is_rejected(self):
        first = Path(self.publish()["appcast"])
        Path(str(first) + ".sig").unlink()
        with self.assertRaises(FileNotFoundError):
            self.publish(2, previous=first, output=self.directory / "next.xml")

    def test_bad_detached_signature_is_rejected(self):
        first = Path(self.publish()["appcast"])
        Path(str(first) + ".sig").write_text(base64.b64encode(bytes(64)).decode())
        with self.assertRaises((ValueError, RuntimeError)):
            self.publish(2, previous=first, output=self.directory / "next.xml")

    def test_build_number_cannot_be_reused_for_changed_content(self):
        first = Path(self.publish()["appcast"])
        archive = self.directory / self.fixtures[1][0].name
        shutil.copyfile(self.fixtures[1][0], archive)
        # This case isolates the history identity check. Real DMG inspection
        # runs in the integration cases; changed bytes must not reuse a build.
        with archive.open("ab") as stream:
            stream.write(b"changed-release")
        report = self.tamper_report(lambda value: value["main_only_dmg"].update(
            {"bytes": archive.stat().st_size, "sha256": sha256_file(archive)}))
        with mock.patch.object(publisher, "read_archive_info", return_value=self.fixtures[1][2]):
            with self.assertRaisesRegex(ValueError, "cannot be reused"):
                self.publish(previous=first, output=self.directory / "changed.xml", archive=archive, report=report)

    def test_new_build_cannot_replace_an_old_archive_url(self):
        first = Path(self.publish()["appcast"])
        settings = self.settings_for(2)
        settings["application"]["archive_url"] = self.settings_for(1)["application"]["archive_url"]
        with self.assertRaisesRegex(ValueError, "immutable archive URL"):
            self.publish(2, previous=first, output=self.directory / "next.xml", settings=settings)

    def test_first_feed_requires_first_public_build(self):
        with self.assertRaisesRegex(ValueError, "first_public_build"):
            self.publish(2)

    def test_corrupt_archive_size_or_digest_fails_before_mounting(self):
        for field, value in (("bytes", self.fixtures[1][0].stat().st_size + 1), ("sha256", "0" * 64)):
            with self.subTest(field=field):
                report = self.tamper_report(lambda report: report["main_only_dmg"].update({field: value}))
                with mock.patch.object(publisher, "read_archive_info", side_effect=AssertionError("must not mount")):
                    with self.assertRaisesRegex(ValueError, "size and SHA-256"):
                        self.publish(report=report)

    def test_metadata_mismatches_are_rejected(self):
        report = json.loads(self.fixtures[1][1].read_text())
        for key, value in (("CFBundleIdentifier", "org.wrong.application"), ("CFBundleVersion", "2"),
                           ("CFBundleShortVersionString", "2.0.0"), ("LSMinimumSystemVersion", "26.0"),
                           ("SUFeedURL", "https://wrong.invalid/appcast.xml"), ("SUPublicEDKey", "wrong")):
            with self.subTest(key=key):
                info = dict(self.fixtures[1][2], **{key: value})
                with self.assertRaises(ValueError):
                    publisher.validate_metadata(info, report, self.settings["application"])

    def test_zero_negative_boolean_and_noncanonical_builds_are_rejected(self):
        for value in (0, -1, True, "01", "１", "1.0", None):
            with self.subTest(value=value):
                report = json.loads(self.fixtures[1][1].read_text())
                report["build_number"] = value
                with self.assertRaises(ValueError):
                    publisher.validate_metadata(self.fixtures[1][2], report, self.settings["application"])

    def test_archive_signature_is_independently_verified(self):
        with nullcontext(self.verifier) as verifier:
            with mock.patch.object(publisher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0,
                                   stdout=base64.b64encode(bytes(64)).decode(), stderr="")):
                with self.assertRaises((ValueError, RuntimeError)):
                    publisher.sign_archive(self.fixtures[1][0], self.settings["application"], self.fixture_bin, verifier)

    def test_authenticated_but_invalid_history_is_rejected(self):
        first = Path(self.publish()["appcast"])
        tree = ET.fromstring(first.read_bytes())
        channel = tree.find("channel")
        channel.append(copy.deepcopy(channel.find("item")))
        payload = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
        first.write_bytes(payload)
        Path(str(first) + ".sig").write_text(self.sign_fixture(payload))
        with self.assertRaisesRegex(ValueError, "unique and ordered"):
            self.publish(2, previous=first, output=self.directory / "next.xml")

    def test_different_existing_output_is_preserved(self):
        output = self.directory / "appcast.xml"
        output.write_bytes(b"existing published data")
        with self.assertRaises(FileExistsError):
            self.publish(output=output)
        self.assertEqual(output.read_bytes(), b"existing published data")
        self.assertFalse(Path(str(output) + ".sig").exists())


if __name__ == "__main__":
    unittest.main()
