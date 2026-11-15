"""Exercise storage publication with the built Foundation bridge and tiny data."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from leap_desktop import storage
from leap_desktop.paths import APPLICATION_ID, AppPaths, maintenance_lock_path, write_json


class StorageCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Leap storage coordination 中文 ", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PROJECT_LEAP_HOME", None)
        os.environ.pop("PROJECT_LEAP_CONFIG_HOME", None)
        self.home = self.root / "原数据 中文"
        self.config = self.root / "固定配置"
        self.paths = AppPaths(self.home, config_dir=self.config)
        (self.home / "components/project-leap-2d").mkdir(parents=True)
        self.result = self.paths.runs / "完成任务/结果.bin"
        self.result.parent.mkdir()
        self.result.write_bytes(bytes(range(256)) * 16)
        self.result.chmod(0o640)
        (self.result.parent / "结果链接").symlink_to(self.result.name)
        self.before = storage.tree_manifest(self.home)
        self.settings = self.paths.settings_file.read_bytes()

    def migrate(self, destination):
        # This is the same background-thread boundary used by maintenance.
        with ThreadPoolExecutor(max_workers=1) as worker:
            return worker.submit(storage.migrate_app_data, self.paths, destination).result(timeout=20)

    def assert_original_active(self):
        self.assertEqual(self.paths.settings_file.read_bytes(), self.settings)
        self.assertEqual(storage.tree_manifest(self.home), self.before)
        self.assertEqual(AppPaths(config_dir=self.config).home, self.home)
        self.assertEqual(list(self.root.glob(".*.migrating-*")), [])

    def check_success(self, existing_empty):
        destination = self.root / "新数据 中文"
        if existing_empty:
            destination.mkdir()
        result = self.migrate(destination)
        self.assertEqual(result["status"], "migrated", result)
        self.assertFalse(self.home.exists())
        after = storage.tree_manifest(destination)
        self.assertEqual({key: value for key, value in after.items() if key != "application.json"},
                         {key: value for key, value in self.before.items() if key != "application.json"})
        self.assertEqual(json.loads(self.paths.settings_file.read_text())["data_dir"], str(destination))
        self.assertEqual(AppPaths(config_dir=self.config).home, destination)
        self.assertEqual(list(self.root.glob(".*.migrating-*")), [])

    def test_publish_new_directory_preserves_content_modes_and_links(self):
        self.check_success(existing_empty=False)

    def test_publish_over_existing_empty_directory(self):
        self.check_success(existing_empty=True)

    def test_publication_error_preserves_pointer_and_original(self):
        destination = self.root / "无法发布"
        for library in (self.root / "unavailable.dylib", Path("/usr/lib/libSystem.B.dylib")):
            with self.subTest(library=library), mock.patch.object(
                    AppPaths, "native_library", new_callable=mock.PropertyMock, return_value=library):
                result = self.migrate(destination)
            self.assertEqual(result["status"], "failed", result)
            self.assertFalse(destination.exists())
            self.assert_original_active()

    def test_target_becomes_nonempty_before_coordinated_access(self):
        destination = self.root / "并发写入目标"
        publish = storage._publish_directory
        def populate_then_publish(paths, source, target):
            target.mkdir()
            (target / "外部写入.txt").write_bytes(b"must survive")
            return publish(paths, source, target)
        with mock.patch.object(storage, "_publish_directory", side_effect=populate_then_publish):
            result = self.migrate(destination)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("empty directory", result["message"])
        self.assertEqual((destination / "外部写入.txt").read_bytes(), b"must survive")
        self.assert_original_active()

    def test_missing_configured_root_is_not_recreated(self):
        saved = self.home.with_name("已移动原数据")
        self.home.rename(saved)
        with self.assertRaisesRegex(ValueError, "已配置的应用数据目录或身份标记不存在"):
            AppPaths(config_dir=self.config)
        with self.assertRaisesRegex(ValueError, "已配置的应用数据目录或身份标记不存在"):
            maintenance_lock_path(self.home)
        self.assertFalse(self.home.exists())
        self.assertEqual(storage.tree_manifest(saved), self.before)
        self.assertEqual(self.paths.settings_file.read_bytes(), self.settings)

    def test_configured_marker_must_exist_and_match_application(self):
        marker = self.home / "application.json"
        marker.unlink()
        with self.assertRaisesRegex(ValueError, "已配置的应用数据目录或身份标记不存在"):
            AppPaths(config_dir=self.config)
        self.assertFalse(marker.exists())
        self.assertEqual(self.paths.settings_file.read_bytes(), self.settings)
        write_json(marker, {"schema_version": 1, "application": "another.application"})
        invalid = marker.read_bytes()
        with self.assertRaisesRegex(ValueError, "该目录不属于 Project Leap"):
            AppPaths(config_dir=self.config)
        self.assertEqual(marker.read_bytes(), invalid)
        self.assertEqual(self.paths.settings_file.read_bytes(), self.settings)

    def test_first_launch_creates_data_and_fixed_pointer(self):
        home, config = self.root / "首次数据", self.root / "首次配置"
        self.assertFalse(home.exists())
        paths = AppPaths(home, config_dir=config)
        self.assertEqual(json.loads((home / "application.json").read_text())["application"], APPLICATION_ID)
        self.assertEqual(json.loads(paths.settings_file.read_text())["data_dir"], str(home))
        self.assertTrue(paths.runs.is_dir())


if __name__ == "__main__":
    unittest.main()
