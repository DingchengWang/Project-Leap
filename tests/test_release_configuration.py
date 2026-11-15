"""Exercise production release configuration without building or starting an app."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "app"))

import build_app
from build.release_policy import APP_FILES
from leap_desktop import modules
from release_support import load_settings, component_configuration, sparkle_configuration
from verify_app_bundle import release_configuration_contract


class ReleaseConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings(ROOT / "tools/build/release-settings.json")
        self.components = component_configuration(self.settings)
        self.sparkle = sparkle_configuration(self.settings)
        temporary = tempfile.TemporaryDirectory(prefix="leap-release-configuration-", dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings_path = self.root / "tools/build/release-settings.json"
        self.settings_path.parent.mkdir(parents=True)
        self.settings_path.write_text(json.dumps(self.settings))
        self.component_path = self.root / "app/leap_desktop/release-config.json"
        self.component_path.parent.mkdir(parents=True)
        self.component_path.write_text(json.dumps(self.components))

    def contract(self, components=None, sparkle=None):
        # Round-trip the formats the bundle actually contains, including bools.
        configuration = json.loads(json.dumps(self.components if components is None else components))
        plist = plistlib.loads(plistlib.dumps(self.sparkle if sparkle is None else sparkle))
        return release_configuration_contract(configuration, plist, self.settings)

    def test_source_configuration_and_module_urls_match_public_settings(self):
        self.assertEqual(build_app.validate_release_configuration(ROOT), self.settings)
        configuration = json.loads((ROOT / "app/leap_desktop/release-config.json").read_text())
        self.assertEqual(configuration, self.components)
        self.assertTrue(self.contract()["production_configuration_valid"])
        self.assertTrue(self.contract()["sparkle_configuration_valid"])
        self.assertEqual(len(APP_FILES), 42)
        self.assertEqual(self.settings["application"]["first_public_build"], 1)
        self.assertEqual(self.settings["application"]["bundle_identifier"], "org.projectleap.prototype.d")
        self.assertEqual({module["input_page"]: module["github_url"] for module in modules.list_modules()},
                         self.settings["module_repositories"])

    def test_empty_insecure_and_wrong_component_urls_fail(self):
        for url in ("", "http://example.org/components.json", "https://example.org/components.json"):
            with self.subTest(url=url):
                configuration = dict(self.components, catalog_url=url)
                self.assertFalse(self.contract(components=configuration)["production_configuration_valid"])
                self.component_path.write_text(json.dumps(configuration))
                with self.assertRaises(ValueError):
                    build_app.validate_release_configuration(self.root)

    def test_missing_empty_wrong_and_insecure_app_feed_fail(self):
        for url in (None, "", "http://example.org/appcast.xml", "https://example.org/appcast.xml"):
            with self.subTest(url=url):
                plist = dict(self.sparkle)
                if url is None:
                    plist.pop("SUFeedURL")
                else:
                    plist["SUFeedURL"] = url
                self.assertFalse(self.contract(sparkle=plist)["sparkle_configuration_valid"])

    def test_empty_and_exchanged_public_keys_fail(self):
        for key in ("", self.settings["application"]["public_key"]):
            with self.subTest(component_key=key):
                self.assertFalse(self.contract(components=dict(self.components, public_key=key))["production_configuration_valid"])
        for key in ("", self.settings["components"]["public_key"]):
            with self.subTest(application_key=key):
                self.assertFalse(self.contract(sparkle=dict(self.sparkle, SUPublicEDKey=key))["sparkle_configuration_valid"])
        exchanged = self.contract(components=dict(self.components, public_key=self.sparkle["SUPublicEDKey"]),
                                  sparkle=dict(self.sparkle, SUPublicEDKey=self.components["public_key"]))
        self.assertFalse(exchanged["production_configuration_valid"])
        self.assertFalse(exchanged["sparkle_configuration_valid"])

    def test_explicit_update_policy_requires_actual_boolean_values(self):
        expected = {"SUEnableAutomaticChecks": False, "SUAutomaticallyUpdate": False,
                    "SUVerifyUpdateBeforeExtraction": True}
        for key, value in expected.items():
            self.assertIs(self.sparkle[key], value)
            for wrong in (not value, int(value), str(value)):
                with self.subTest(field=key, wrong=wrong):
                    self.assertFalse(self.contract(sparkle=dict(self.sparkle, **{key: wrong}))["sparkle_configuration_valid"])
            missing = dict(self.sparkle)
            missing.pop(key)
            self.assertFalse(self.contract(sparkle=missing)["sparkle_configuration_valid"])

    def test_malformed_component_object_and_boolean_schema_fail(self):
        for configuration in ([], {**self.components, "schema_version": True}, {**self.components, "extra": "field"}):
            with self.subTest(configuration=configuration):
                self.assertFalse(self.contract(components=configuration)["production_configuration_valid"])
                self.component_path.write_text(json.dumps(configuration))
                with self.assertRaises(ValueError):
                    build_app.validate_release_configuration(self.root)

    def test_invalid_authoritative_settings_fail_preflight(self):
        for section, field, wrong in (("components", "catalog_url", ""),
                                      ("application", "feed_url", "http://example.org/appcast.xml"),
                                      ("components", "public_key", "invalid"),
                                      ("application", "public_key", self.components["public_key"])):
            with self.subTest(section=section, field=field):
                settings = copy.deepcopy(self.settings)
                settings[section][field] = wrong
                self.settings_path.write_text(json.dumps(settings))
                with self.assertRaises(ValueError):
                    build_app.validate_release_configuration(self.root)

    def execute_spec(self, root):
        analysis = mock.Mock(return_value=SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[]))
        bundle = mock.Mock()
        environment = {"LEAP_BUILD_ROOT": str(root), "LEAP_BUILD_NAME": "Project Leap",
                       "LEAP_BUILD_DISPLAY_NAME": "Project Leap", "LEAP_BUILD_VERSION": "1.0.0",
                       "LEAP_BUILD_NUMBER": "1", "LEAP_SIGN_IDENTITY": "-"}
        with mock.patch.dict(os.environ, environment), mock.patch.object(sys, "path", list(sys.path)):
            runpy.run_path(str(ROOT / "tools/build/project_leap.spec"), init_globals={
                "Analysis": analysis, "PYZ": mock.Mock(), "EXE": mock.Mock(),
                "COLLECT": mock.Mock(), "BUNDLE": bundle})
        return analysis, bundle

    def test_executed_spec_injects_real_feed_keys_and_preserves_payload(self):
        analysis, bundle = self.execute_spec(ROOT)
        arguments = bundle.call_args.kwargs
        plist = plistlib.loads(plistlib.dumps(arguments["info_plist"]))
        self.assertTrue(self.contract(sparkle=plist)["sparkle_configuration_valid"])
        self.assertEqual(arguments["bundle_identifier"], self.settings["application"]["bundle_identifier"])
        self.assertEqual(plist["CFBundleVersion"], "1")
        payload = [Path(source).relative_to(ROOT / "app/leap_desktop").as_posix()
                   for source, destination in analysis.call_args.kwargs["datas"]
                   if destination.startswith("app/leap_desktop")]
        self.assertEqual(set(payload), APP_FILES)
        self.assertEqual(len(payload), 42)

    def test_executed_spec_rejects_stale_component_configuration(self):
        self.component_path.write_text(json.dumps(dict(self.components, public_key="")))
        with self.assertRaisesRegex(ValueError, "component configuration differs"):
            self.execute_spec(self.root)

    def test_executed_spec_reads_configured_feed_instead_of_hard_coded_value(self):
        settings = copy.deepcopy(self.settings)
        settings["application"]["feed_url"] = "https://dingchengwang.github.io/Project-Leap/updates/next-appcast.xml"
        self.settings_path.write_text(json.dumps(settings))
        for relative in APP_FILES:
            destination = self.root / "app/leap_desktop" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "app/leap_desktop" / relative, destination)
        _, bundle = self.execute_spec(self.root)
        self.assertEqual(bundle.call_args.kwargs["info_plist"]["SUFeedURL"], settings["application"]["feed_url"])

    def test_preflight_failure_precedes_build_actions(self):
        with mock.patch.object(build_app, "validate_release_configuration", side_effect=ValueError("invalid release configuration")), \
             mock.patch.object(build_app, "run") as run, \
             mock.patch.object(build_app, "source_snapshot") as snapshot, \
             mock.patch.object(build_app, "package_record") as packages:
            with self.assertRaisesRegex(ValueError, "invalid release configuration"):
                build_app.main()
        run.assert_not_called()
        snapshot.assert_not_called()
        packages.assert_not_called()

    def test_build_inventory_and_report_bind_configuration_and_helper(self):
        digest = build_app.digest
        relevant = {ROOT / name for name in ("tools/release_support.py", "tools/build/release-settings.json")}
        # Public source checkouts omit the separately retained scientific seed and baselines.
        # Hash the actual release inputs while stubbing unrelated private build inputs.
        with mock.patch.object(build_app, "digest", side_effect=lambda path: digest(path) if path in relevant else "fixture-input"):
            inventory = build_app.build_inventory()
        for name in ("tools/release_support.py", "tools/build/release-settings.json"):
            self.assertEqual(inventory[name], build_app.digest(ROOT / name))
        evidence = build_app.release_configuration_record(ROOT, self.settings)
        self.assertEqual(evidence["sha256"], inventory[evidence["path"]])
        self.assertEqual(evidence["components"], self.components)
        self.assertEqual(evidence["sparkle"], self.sparkle)


if __name__ == "__main__":
    unittest.main()
