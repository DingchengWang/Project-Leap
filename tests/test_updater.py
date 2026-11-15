from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from leap_desktop.updater import SparkleUpdater


class UpdaterTests(unittest.TestCase):
    def test_unbundled_environment_has_explicit_state(self):
        with mock.patch.object(SparkleUpdater, "_application_bundle", return_value=None):
            updater = SparkleUpdater()
        self.assertEqual(updater.check_for_updates()["status"], "unavailable")

    def test_missing_framework_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            updater = SparkleUpdater(directory)
            with mock.patch("sys.platform", "darwin"):
                self.assertEqual(updater.start()["status"], "unavailable")

    def test_information_check_does_not_request_installation(self):
        updater = SparkleUpdater()
        library = mock.Mock()
        library.leap_sparkle_state_json.return_value = b'{"status":"checking"}'
        updater._library = library
        self.assertEqual(updater.check_for_updates()["status"], "checking")
        library.leap_sparkle_check.assert_called_once_with()
        library.leap_sparkle_install.assert_not_called()

    def test_installation_uses_frozen_build_feed_and_host_while_busy(self):
        updater = SparkleUpdater()
        library = mock.Mock()
        library.leap_sparkle_state_json.return_value = b'{"status":"downloading"}'
        updater._library = library
        result = updater.install_update("42", "https://example.org/appcast.xml", "41", installation_busy=True)
        self.assertEqual(result["status"], "downloading")
        library.leap_sparkle_set_installation_busy.assert_called_once_with(1)
        library.leap_sparkle_install.assert_called_once_with(b"42", b"https://example.org/appcast.xml", b"41")
        updater.set_installation_busy(False)
        self.assertEqual(library.leap_sparkle_set_installation_busy.call_args.args, (0,))

    def test_incomplete_or_nul_update_plan_cannot_reach_native_installation(self):
        updater = SparkleUpdater()
        library = mock.Mock()
        updater._library = library
        for plan in (("", "https://example.org/feed", "1"), ("2", "https://example.org/feed\x00", "1"), (2, "https://example.org/feed", "1")):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                updater.install_update(*plan)
        library.leap_sparkle_install.assert_not_called()

    def test_loading_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Path(directory) / "Contents/Frameworks/libLeapSparkle.dylib"
            bridge.parent.mkdir(parents=True)
            bridge.write_bytes(b"not a dynamic library")
            with mock.patch("sys.platform", "darwin"):
                self.assertEqual(SparkleUpdater(directory).start()["status"], "failed")

    def test_background_thread_cannot_call_cocoa(self):
        errors = []
        def worker():
            try:
                SparkleUpdater().start()
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
