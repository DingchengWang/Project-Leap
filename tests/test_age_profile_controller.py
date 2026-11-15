"""Age-selector request/preflight tests; no scientific inference or Fiji launch."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
from PySide6.QtWidgets import QApplication
from leap_desktop import paths
from leap_desktop.controller import Controller


class AgeProfileControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="leap-age-controller-", dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = paths.AppPaths(self.root / "app-home")
        self.source = self.root / "source" / "original-neonatal.tif"
        self.source.parent.mkdir()
        self.source.write_bytes(b"Synthetic original sentinel; worker is not launched by these Qt tests.")
        self.source.chmod(0o444)
        self.source_before = self.fingerprint()
        initial = patch.object(Controller, "_initial_status", return_value=None)
        initial.start()
        self.addCleanup(initial.stop)
        self.controller = Controller(self.paths)
        self.controller._timer.stop()
        self.controller.locale.set_language("zh")
        self.addCleanup(self.cleanup_controller)
        self.controller._component_info = {"status": "ready", "runtime_descriptor": {
            "root": str(self.root / "absent-component"), "python": str(self.root / "unused-python"),
            "site_packages": str(self.root / "absent-site-packages"), "models": str(self.root / "absent-models"),
            "engine": str(self.root / "absent-engine"), "fiji_root": str(self.root / "absent-fiji"),
            "fiji_launcher": str(self.root / "unused-fiji"), "component_version": "1.0.1"}}
        self.select_channels(["DAPI", "eGFP", "KCNN2"])

    def cleanup_controller(self):
        controller = self.controller
        controller._timer.stop()
        controller._notice_timer.stop()
        controller.counting.close()
        controller._release_lock()
        deadline = time.monotonic() + 5
        while controller.counting.busy and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.005)
        self.assertFalse(controller.counting.busy)
        self.assertTrue(QThreadPool.globalInstance().waitForDone(5000))
        self.application.processEvents()
        controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def fingerprint(self):
        stat = self.source.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_mode, hashlib.sha256(self.source.read_bytes()).hexdigest()

    def select_channels(self, roles):
        self.controller._image_info = {"path": str(self.source), "name": self.source.name}
        self.controller._channel_roles = list(roles)
        self.controller._series_index = self.controller._time_index = 0

    def launch_import(self):
        with patch.object(self.controller, "_launch") as launch:
            self.controller.startAnalysis()
        run = self.controller._run_dir
        self.assertIsNotNone(run)
        launch.assert_called_once_with("import", ["-m", "leap_desktop.import_worker",
                                                  "--request", str(run / "import-request.json")])
        self.assertEqual(self.controller.status, "importing")
        request = json.loads((run / "import-request.json").read_text())
        self.assertEqual(request["source_path"], str(self.source))
        self.assertEqual(self.fingerprint(), self.source_before)
        # No child process exists; release the host lock before the next fixture launch.
        self.controller._release_lock()
        self.controller._busy = False
        return request, json.loads((run / "job.json").read_text())

    def test_default_auto_valid_settings_and_invalid_values_preserve_selection(self):
        self.assertEqual(self.controller.ageProfile, "auto")
        for value in ("mature", "neonatal", "auto"):
            self.controller.setAgeProfile(value)
            self.assertEqual(self.controller.ageProfile, value)
        self.controller.setAgeProfile("mature")
        for value in ("", "adult", "juvenile", "Mature", "auto\x00"):
            with self.subTest(invalid=value):
                self.controller.setAgeProfile(value)
                self.assertEqual(self.controller.ageProfile, "mature")

    def test_busy_maintenance_and_nonfluorescence_module_cannot_change_profile(self):
        self.controller.setAgeProfile("mature")
        for flag in ("_busy", "_maintenance_busy"):
            with self.subTest(flag=flag):
                setattr(self.controller, flag, True)
                self.controller.setAgeProfile("neonatal")
                self.assertEqual(self.controller.ageProfile, "mature")
                setattr(self.controller, flag, False)
        self.controller._active_module_id = "cell-counting"
        self.controller.setAgeProfile("neonatal")
        self.assertEqual(self.controller.ageProfile, "auto")
        self.controller._active_module_id = "project-leap-2d"
        self.assertEqual(self.controller.ageProfile, "mature")

    def test_new_source_inspection_resets_profile_to_auto(self):
        self.controller.setAgeProfile("neonatal")
        with patch.object(self.controller, "_launch") as launch:
            self.controller.inspectImage(str(self.source))
        self.assertEqual(self.controller.ageProfile, "auto")
        self.assertEqual(launch.call_args.args[0], "inspect")
        self.assertEqual(self.fingerprint(), self.source_before)

    def test_actual_import_request_profiles_and_saved_job_match_selection(self):
        for selected, expected in (("auto", None), ("mature", "mature"), ("neonatal", "neonatal")):
            with self.subTest(profile=selected):
                self.controller.setAgeProfile(selected)
                request, job = self.launch_import()
                self.assertEqual(request["profile"], expected)
                self.assertEqual(job["age_profile"], selected)
                self.assertEqual(request["channel_roles"], ["DAPI", "eGFP", "KCNN2"])

    def test_default_auto_retains_legacy_null_import_contract(self):
        # A source filename token does not silently become an explicit selector choice.
        request, job = self.launch_import()
        self.assertIsNone(request["profile"])
        self.assertEqual(job["age_profile"], "auto")
        self.assertEqual(request["series_index"], 0)
        self.assertEqual(request["time_index"], 0)

    def test_gfap_only_neonatal_blocks_before_replacing_previous_saved_job(self):
        run = self.paths.runs / "previous-completed"
        run.mkdir()
        saved = {"id": run.name, "module_id": "project-leap-2d", "status": "completed",
                 "source": str(self.source), "name": "previous", "created_at": "2026-10-19T00:00:00Z"}
        paths.write_json(run / "job.json", saved)
        original = (run / "job.json").read_bytes()
        self.controller._job, self.controller._run_dir = saved, run
        self.controller._status = "completed"
        self.select_channels(["DAPI", "GFAP", "KCNN2"])
        self.controller.setAgeProfile("neonatal")
        self.assertEqual(self.controller.ageProfile, "auto")
        self.assertIn("GFAP", self.controller.message)
        # An invalid value injected below the UI setter must still be rejected
        # before the historical record or scientific worker is touched.
        self.controller._age_profile = "neonatal"
        with patch.object(self.controller, "_launch") as launch, \
                patch.object(self.controller, "_runtime", side_effect=AssertionError("Unsupported input acquired runtime")):
            self.controller.startAnalysis()
        launch.assert_not_called()
        self.assertIn("GFAP", self.controller.message)
        self.assertIn("Mature", self.controller.message)
        self.assertEqual((run / "job.json").read_bytes(), original)
        self.assertEqual(list(self.paths.runs.iterdir()), [run])
        self.assertEqual(self.controller._job["id"], run.name)
        self.assertEqual(self.controller._run_dir, run)
        self.assertFalse(self.controller.busy)

    def test_gfap_only_auto_and_mature_remain_supported(self):
        self.select_channels(["DAPI", "GFAP", "KCNN2"])
        for selected, expected in (("auto", None), ("mature", "mature")):
            with self.subTest(profile=selected):
                self.controller.setAgeProfile(selected)
                request, _ = self.launch_import()
                self.assertEqual(request["profile"], expected)

    def test_neonatal_remains_supported_when_egfp_is_present(self):
        for roles in (["DAPI", "eGFP", "KCNN2"], ["DAPI", "eGFP", "GFAP", "KCNN2"]):
            with self.subTest(roles=roles):
                self.select_channels(roles)
                self.controller.setAgeProfile("neonatal")
                request, _ = self.launch_import()
                self.assertEqual(request["profile"], "neonatal")
                self.assertEqual(request["channel_roles"], roles)


if __name__ == "__main__":
    unittest.main()
