"""Source lookup and pre-adoption checks use disposable delivery layouts."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from leap_desktop import paths


class PathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Leap paths 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bundle = self.root / "交付位置/Project Leap.app"
        self.resources = self.bundle / "Contents/Resources"
        self.resources.mkdir(parents=True)
        (self.bundle / "Contents/Info.plist").write_bytes(b"fixture")
        self.executable = self.bundle / "Contents/MacOS/Project Leap"
        self.executable.parent.mkdir()
        self.executable.touch()
        self.dev = self.root / "开发源码"
        self.dev.mkdir()

    def catalog(self, directory):
        directory.mkdir(parents=True)
        packages = {}
        for name in ("project-leap-2d", "fiji"):
            (directory / (name + ".tar.gz")).write_bytes(b"fixture archive, checked by maintenance separately")
            packages[name] = {"path": name + ".tar.gz"}
        (directory / "catalog.json").write_text(json.dumps({"schema_version": 1, "packages": packages}))
        return directory

    def app_paths(self, packaged):
        with mock.patch.object(sys, "frozen", packaged, create=True), mock.patch.object(sys, "executable", str(self.executable)), \
                mock.patch.object(paths, "resource_root", return_value=self.resources if packaged else self.dev):
            return paths.AppPaths(self.root / ("packaged data" if packaged else "development data"))

    def test_packaged_source_prefers_sibling_delivery_then_recorded_source(self):
        sibling = self.catalog(self.bundle.parent / "Project Leap Packages")
        recorded = self.catalog(self.root / "另存离线包")
        app = self.app_paths(True)
        self.assertEqual(app.package_source(recorded), sibling)
        (sibling / "catalog.json").write_text("broken json")
        self.assertEqual(app.package_source(recorded), recorded)
        (recorded / "fiji.tar.gz").unlink()
        self.assertIsNone(app.package_source(recorded))

    def test_packaged_app_never_falls_back_to_development_or_embedded_payload(self):
        self.catalog(self.resources / "runtime/prototype-d/packages")
        (self.resources / "runtime/seed").mkdir()
        app = self.app_paths(True)
        self.assertIsNone(app.package_source())
        self.assertFalse(hasattr(app, "seed"))
        self.assertFalse(hasattr(app, "engine"))

    def test_development_source_prefers_recorded_then_local_packages(self):
        development = self.catalog(self.dev / "runtime/build-packages")
        self.catalog(self.bundle.parent / "Project Leap Packages")
        recorded = self.catalog(self.root / "已保存安装源")
        app = self.app_paths(False)
        self.assertEqual(app.package_source(recorded), recorded)
        self.assertEqual(app.package_source(self.root / "missing"), development)
        (development / "catalog.json").unlink()
        self.assertIsNone(app.package_source())

    def test_recorded_managed_package_path_is_resolved_after_migration(self):
        app = self.app_paths(True)
        package = self.catalog(app.home / "packages")
        old = self.root / "旧应用数据"
        app.previous_homes = (str(old),)
        self.assertEqual(app.package_source(old / "packages"), package)

    def test_invalid_catalog_path_and_symlink_cannot_be_adopted(self):
        app = self.app_paths(True)
        package = self.catalog(self.root / "invalid packages")
        catalog = package / "catalog.json"
        value = json.loads(catalog.read_text())
        value["packages"]["fiji"]["path"] = "../outside"
        catalog.write_text(json.dumps(value))
        self.assertIsNone(app.package_source(package))
        self.assertIsNone(app.package_source({"not": "a path"}))
        self.assertIsNone(app.package_source("~project_leap_nonexistent_user_12345/packages"))
        self.assertIsNone(app.package_source("\0invalid"))
        link = self.root / "linked package"
        link.symlink_to(package, target_is_directory=True)
        self.assertIsNone(app.package_source(link))

    def test_protected_data_rejection_creates_no_config_or_data(self):
        user = self.root / "User"
        desktop = user / "Desktop"
        desktop.mkdir(parents=True)
        with mock.patch.object(Path, "home", return_value=user):
            with self.assertRaises(ValueError):
                paths.AppPaths(desktop / "Rejected Data")
        self.assertEqual(list(desktop.iterdir()), [])
        self.assertEqual(list(user.iterdir()), [desktop])

    def test_default_d_launch_does_not_adopt_prototype_c_state(self):
        user = self.root / "User"
        support = user / "Library/Application Support"
        old_data = support / "Project Leap Prototype"
        old_settings = support / "Project Leap Prototype Settings"
        old_data.mkdir(parents=True)
        old_settings.mkdir()
        sentinel = old_data / "historical-result.txt"
        sentinel.write_text("existing Prototype C result", encoding="utf-8")
        settings = old_settings / "storage.json"
        settings.write_text("existing settings must not be read or rewritten", encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=user), mock.patch.dict(os.environ, {}, clear=True):
            current = paths.AppPaths()
        self.assertEqual(current.home, support / "Project Leap Prototype D")
        self.assertEqual(current.config_home, support / "Project Leap Prototype D Settings")
        self.assertEqual(sentinel.read_text(), "existing Prototype C result")
        self.assertEqual(settings.read_text(), "existing settings must not be read or rewritten")


if __name__ == "__main__":
    unittest.main()
