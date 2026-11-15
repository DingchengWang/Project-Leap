"""Independent workbench sessions with real Qt state and isolated task files.

Synthetic workers exercise dispatch and callback routing. These tests establish
host state ownership, not scientific segmentation accuracy.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from unittest.mock import Mock, patch

from test_counting_controller import CountingControllerFixture
from PySide6.QtCore import QProcess
from leap_desktop import paths


class ControllerSessionTests(CountingControllerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        state = copy.deepcopy(self.controller._module_states["cell-counting"])
        state["id"] = "project-leap-2d"
        state["runtime_descriptor"].update(component_version="1.0.1", fiji_launcher="unused-fiji")
        self.controller._module_states["project-leap-2d"] = state

    def prepare_fluorescence(self):
        self.controller.selectModule("project-leap-2d")
        self.controller._image_info = {"path": str(self.root / "fluorescence-source.tif"),
                                       "name": "fluorescence-source.tif", "channel_count": 3}
        self.controller._channel_roles = ["DAPI", "eGFP", "KCNN2"]
        self.controller._series_index = self.controller._time_index = 0
        self.controller._status = "ready"
        return self.controller._active_session

    def test_two_preparations_survive_module_and_settings_navigation(self):
        self.inspect_fixture()
        self.controller.setCountSupportMarker("Biocytin")
        self.controller.setCountDisplayChannel(2, True)
        counting_inputs = copy.deepcopy(self.controller.imageInfo)
        fluorescence = self.prepare_fluorescence()
        self.controller.setAgeProfile("mature")
        self.controller.assignMarkerSource("DAPI", 2)
        expected_roles = list(self.controller.channelRoles)
        for page in ("settings", "jobs", "maintenance", "analysis"):
            self.controller.setPage(page)
            self.assertIs(self.controller._active_session, fluorescence)
        self.controller.selectModule("cell-counting")
        self.assertEqual(self.controller.imageInfo, counting_inputs)
        self.assertEqual(self.controller.countSupportChannel, 1)
        self.assertEqual(self.controller.countDisplayChannels, [0, 1, 2])
        self.controller.selectModule("project-leap-2d")
        self.assertEqual(self.controller.ageProfile, "mature")
        self.assertEqual(self.controller.channelRoles, expected_roles)

    def test_running_count_locks_only_its_inputs_and_blocks_second_scientific_task(self):
        self.prepare_fluorescence()
        self.controller.selectModule("cell-counting")
        run = self.start_fixture()
        before = copy.deepcopy(self.controller.imageInfo)
        roles = list(self.controller.channelRoles)
        request = (run / "request.json").read_bytes()
        for action, arguments in ((self.controller.setCountDapiChannel, (2,)),
                                  (self.controller.setCountSupportChannel, (2,)),
                                  (self.controller.setCountSupportMarker, ("custom",)),
                                  (self.controller.setCountDisplayChannel, (0, False)),
                                  (self.controller.setCountChannelColor, (0, "#abcdef")),
                                  (self.controller.setCountMappingMode, ("auto",)),
                                  (self.controller.selectSeries, (0,)),
                                  (self.controller.selectTime, (0,)),
                                  (self.controller.clearImportedImage, ())):
            action(*arguments)
        self.assertEqual(self.controller.imageInfo, before)
        self.assertEqual(self.controller.channelRoles, roles)
        self.assertTrue(self.controller.parametersLocked)
        self.controller.selectModule("project-leap-2d")
        self.assertTrue(self.controller.scientificTaskBusy)
        self.assertFalse(self.controller.activeModuleBusy)
        self.assertFalse(self.controller.parametersLocked)
        self.controller.setAgeProfile("neonatal")
        self.assertEqual(self.controller.ageProfile, "neonatal")
        with patch.object(self.controller, "_launch") as launch:
            self.controller.startAnalysis()
        launch.assert_not_called()
        self.assertEqual((run / "request.json").read_bytes(), request)
        self.assertEqual(list(self.paths.runs.iterdir()), [run])
        self.assertEqual(self.controller._worker_context.run_id, run.name)

    def test_count_completion_updates_own_session_while_other_module_is_visible(self):
        self.prepare_fluorescence()
        self.controller.selectModule("cell-counting")
        run = self.start_fixture()
        analysis = self.finish_fixture(run, dispatch=False)
        self.controller.selectModule("project-leap-2d")
        before = copy.deepcopy(self.controller.imageInfo)
        self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.wait_until(lambda: self.controller.counting.frameReady and not self.controller.counting.busy)
        self.assertEqual(self.controller.activeModuleId, "project-leap-2d")
        self.assertEqual(self.controller.imageInfo, before)
        self.assertEqual(self.controller.status, "ready")
        counting = self.controller._sessions["cell-counting"]
        self.assertEqual(counting.status, "waiting_review")
        self.assertEqual(counting.job["analysis_path"], str(analysis))
        self.assertEqual(counting.run_dir, run)
        self.assertFalse(self.controller.busy)
        self.controller.selectModule("cell-counting")
        self.assertTrue(self.controller.parametersLocked)
        self.assertEqual(self.controller.counting.runId, run.name)
        self.assertEqual(self.controller.countRunSettings["support_channel"], 1)

    def test_fluorescence_import_completion_keeps_original_source_and_module(self):
        self.inspect_fixture()
        count_before = copy.deepcopy(self.controller.imageInfo)
        self.prepare_fluorescence()
        self.controller.setAgeProfile("mature")
        with patch.object(self.controller, "_launch"):
            self.controller.startAnalysis()
        run = self.controller._run_dir
        self.controller._process_kind = "import"
        paths.write_json(run / "import-result.json", {"status": "completed"})
        self.controller.selectModule("cell-counting")
        routed = []

        def observe_launch(kind, arguments):
            descriptor = self.controller._runtime()
            environment = self.controller._environment(descriptor)
            routed.append((kind, arguments, environment.value("PROJECT_LEAP_MODULE_ID")))

        with patch.object(self.controller, "_launch", side_effect=observe_launch):
            self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        request = json.loads((run / "request.json").read_text())
        self.assertEqual(routed[0][2], "project-leap-2d")
        self.assertEqual(routed[0][1][:2], ["-m", "project_leap_2d.desktop_adapter"])
        self.assertEqual(request["provenance"]["source"], str(self.root / "fluorescence-source.tif"))
        self.assertEqual(request["engine_args"], [])
        self.assertNotIn("auto_measure", request)
        self.assertEqual(self.controller.imageInfo, count_before)
        self.assertEqual(self.controller.status, "ready")
        self.assertTrue(self.controller.busy)
        self.assertEqual(self.controller._worker_context.run_id, run.name)

    def test_review_edits_and_view_survive_navigation_without_reopening(self):
        run, _ = self.review_fixture()
        editor = self.controller.counting
        editor.addPoint(52, 44)
        editor.setView(1.8, 13, -17)
        before = copy.deepcopy(editor.points)
        with patch.object(editor, "close") as close:
            self.controller.selectModule("project-leap-2d")
            for page in ("settings", "jobs", "maintenance"):
                self.controller.setPage(page)
            self.controller.selectModule("cell-counting")
            self.controller.openCountingJob(run.name)
        close.assert_not_called()
        self.assertIs(self.controller.counting, editor)
        self.assertEqual(editor.points, before)
        self.assertEqual((editor.viewZoom, editor.viewPanX, editor.viewPanY), (1.8, 13, -17))
        self.assertTrue(editor.canUndo)
        self.assertEqual(self.controller.status, "waiting_review")
        self.assertTrue(self.controller.parametersLocked)
        with patch.object(self.controller, "_launch") as launch:
            self.controller.clearImportedImage()
            self.controller.inspectImage(str(self.source))
            self.controller.startAnalysis()
        launch.assert_not_called()
        self.assertEqual(editor.points, before)
        self.assertEqual(self.controller._run_dir, run)

    def test_count_confirmation_during_other_module_run_keeps_worker_owned_state(self):
        count_run, _ = self.review_fixture()
        self.prepare_fluorescence()
        with patch.object(self.controller, "_launch"):
            self.controller.startAnalysis()
        fluorescence_run = self.controller._run_dir
        self.controller.counting.confirm()
        self.wait_until(lambda: not self.controller.counting.busy and not self.controller.counting.available)
        self.assert_counting_workbench_reset()
        self.assertEqual(self.controller.activeModuleId, "project-leap-2d")
        self.assertEqual(self.controller.status, "importing")
        self.assertEqual(self.controller._run_dir, fluorescence_run)
        self.assertTrue(self.controller.busy)
        self.assertEqual(self.controller._worker_context.run_id, fluorescence_run.name)
        self.assertEqual(json.loads((count_run / "job.json").read_text())["status"], "completed")
        self.assertEqual(self.controller._sessions["cell-counting"].status, "idle")

    def test_clear_imported_image_removes_only_current_unstarted_preparation(self):
        self.inspect_fixture()
        self.prepare_fluorescence()
        self.controller.setAgeProfile("mature")
        self.controller.clearImportedImage()
        self.assertEqual(self.controller.imageInfo, {})
        self.assertEqual(self.controller.channelRoles, [])
        self.assertEqual((self.controller.status, self.controller.ageProfile), ("idle", "auto"))
        self.controller.selectModule("cell-counting")
        self.assertEqual(self.controller.imageInfo["path"], str(self.source))
        self.assertEqual(self.controller.countSupportChannel, 1)
        self.assertEqual(self.source_fingerprint(), self.source_before)

    def test_support_auto_manual_and_display_are_independent_and_request_is_frozen(self):
        self.inspect_fixture()
        self.assertEqual((self.controller.countDapiChannel, self.controller.countSupportChannel), (0, 1))
        self.assertEqual(self.controller.countDisplayChannels, [0, 1])
        self.controller.setCountDisplayChannel(0, False)
        self.controller.setCountSupportChannel(2)
        self.controller.setCountSupportMarker("custom astrocyte marker")
        self.assertEqual(self.controller.countMappingMode, "manual")
        self.assertEqual(self.controller.countDisplayChannels, [1])
        self.controller.setCountMappingMode("auto")
        self.assertEqual((self.controller.countSupportChannel, self.controller.countSupportMarker), (1, "Biocytin"))
        self.assertEqual(self.controller.countDisplayChannels, [1])
        self.controller.setCountChannelColor(0, "#123456")
        with patch.object(self.controller, "_launch"):
            self.controller.startAnalysis()
        request = json.loads((self.controller._run_dir / "request.json").read_text())
        self.assertEqual(request["schema_version"], 2)
        self.assertEqual(request["display_channels"], [1])
        self.assertEqual(request["channel_colors"][0], "#123456")
        self.assertEqual(request["support_channel"], 1)
        self.assertFalse(any("diameter" in key for key in request))
        self.assertFalse(hasattr(self.controller, "countNuclearDiameter"))
        self.assertFalse(hasattr(self.controller, "setCountNuclearDiameter"))
        self.controller._image_info["channel_colors"][0] = "#ffffff"
        self.assertEqual(self.controller.countRunSettings["channel_colors"][0], "#123456")

    def test_missing_support_or_empty_display_cannot_create_run(self):
        self.inspect_fixture()
        self.controller.setCountSupportChannel(-1)
        with patch.object(self.controller, "_launch") as launch:
            self.controller.startAnalysis()
            self.controller.setCountSupportChannel(1)
            self.controller.setCountDisplayChannel(0, False)
            self.controller.setCountDisplayChannel(1, False)
            self.controller.startAnalysis()
        launch.assert_not_called()
        self.assertEqual(list(self.paths.runs.iterdir()), [])

    def test_real_qprocess_inspection_callback_stays_with_launch_module(self):
        report = self.inspect_fixture()
        self.controller.clearImportedImage()
        descriptor = self.controller._component_info["runtime_descriptor"]
        descriptor["python"] = sys.executable
        original_launch = self.controller._launch

        def launch_synthetic(kind, arguments):
            program = "import time; time.sleep(0.1); print(" + repr(json.dumps(report)) + ")"
            original_launch(kind, ["-c", program])

        with patch.object(self.controller, "_launch", side_effect=launch_synthetic):
            self.controller.inspectImage(str(self.source))
        self.prepare_fluorescence()
        self.wait_until(lambda: not self.controller.busy)
        self.assertEqual(self.controller.activeModuleId, "project-leap-2d")
        self.assertEqual(self.controller.imageInfo["name"], "fluorescence-source.tif")
        self.assertEqual(self.controller._sessions["cell-counting"].status, "ready")
        self.assertEqual(self.controller._sessions["cell-counting"].image_info["path"], str(self.source))
        self.assertEqual(self.source_fingerprint(), self.source_before)

    def test_background_progress_and_logs_do_not_replace_visible_module_state(self):
        self.prepare_fluorescence()
        self.controller.selectModule("cell-counting")
        run = self.start_fixture()
        (run / "console.log").write_text("count worker output\n")
        (run / "events.jsonl").write_text(json.dumps({"run_id": run.name, "type": "progress",
                                                   "stage": "count_inference", "current": 1, "total": 4}) + "\n")
        self.controller.selectModule("project-leap-2d")
        self.controller._log_text = "fluorescence preparation\n"
        appended = []
        self.controller.logAppended.connect(appended.append)
        self.controller._poll()
        self.assertEqual(self.controller.logText, "fluorescence preparation\n")
        self.assertEqual(self.controller.status, "ready")
        self.assertEqual(appended, [])
        self.assertEqual(self.controller.runningTask["moduleId"], "cell-counting")
        self.assertEqual(self.controller.runningTask["runId"], run.name)
        self.assertEqual(self.controller.runningTask["status"], "running")
        self.assertEqual(self.controller.runningTask["progress"], 0.25)
        saved = json.loads((run / "job.json").read_text())
        self.assertEqual(saved["status"], "running")
        self.assertEqual(next(item for item in self.controller.jobs if item["id"] == run.name)["status"], "running")
        self.controller.selectModule("cell-counting")
        self.assertTrue(self.controller.logText.endswith("count worker output\n"))
        self.assertNotIn("fluorescence preparation", self.controller.logText)
        self.assertEqual(self.controller.progress, 0.25)

    def test_cancellation_while_browsing_other_module_targets_worker_run(self):
        self.prepare_fluorescence()
        self.controller.selectModule("cell-counting")
        run = self.start_fixture()
        self.controller._process = Mock()
        self.controller._process.processId.return_value = 424242
        self.controller.selectModule("project-leap-2d")
        with patch("leap_desktop.controller.os.kill") as kill:
            self.controller.cancelAnalysis()
        self.assertEqual(kill.call_args.args[0], 424242)
        self.assertTrue((run / "cancel.request").exists())
        self.assertEqual(self.controller.status, "ready")
        self.assertEqual(self.controller._sessions["cell-counting"].status, "cancelling")
        self.controller._process_finished(130, QProcess.ExitStatus.CrashExit)
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.runningTask, {})
        self.assertEqual(self.controller.status, "ready")
        self.assertEqual(json.loads((run / "job.json").read_text())["status"], "cancelled")
        self.assertEqual(self.controller._module_faults, {})

    def test_background_worker_failure_marks_only_its_module(self):
        self.prepare_fluorescence()
        self.controller.selectModule("cell-counting")
        run = self.start_fixture()
        paths.write_json(run / "outcome.json", {"status": "failed", "error_code": "technical_failure",
                                               "message": "Synthetic counting failure"})
        self.controller.selectModule("project-leap-2d")
        self.controller._process_finished(1, QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.controller._module_faults, {"cell-counting": "Synthetic counting failure"})
        self.assertEqual(self.controller.status, "ready")
        self.assertEqual(self.controller._sessions["cell-counting"].status, "failed")

    def test_count_calibration_rejects_missing_nonfinite_or_nonpositive_axes_before_launch(self):
        self.inspect_fixture()
        for axis in "xyz":
            for invalid in (None, 0, -1, float("nan"), float("inf"), True, "0.2"):
                with self.subTest(axis=axis, value=invalid):
                    self.controller._image_info["physical_size_um"] = {"x": 0.2, "y": 0.2, "z": 0.5, axis: invalid}
                    self.assertFalse(self.controller.countCalibrationReady)
                    self.assertIn(axis.upper(), self.controller.countInputError)
                    with patch.object(self.controller, "_launch") as launch:
                        self.controller.startAnalysis()
                    launch.assert_not_called()
        self.assertEqual(list(self.paths.runs.iterdir()), [])

    def test_count_calibration_single_plane_and_square_xy_match_engine_contract(self):
        self.inspect_fixture()
        self.controller._image_info["physical_size_um"] = {"x": 0.2, "y": 0.20019, "z": 0.5}
        self.assertTrue(self.controller.countCalibrationReady)
        self.controller._image_info["physical_size_um"]["y"] = 0.2003
        self.assertFalse(self.controller.countCalibrationReady)
        self.assertIn("XY", self.controller.countInputError)
        self.controller._image_info["z_count"] = 1
        del self.controller._image_info["physical_size_um"]["z"]
        self.assertTrue(self.controller.countCalibrationReady)
        self.assertEqual(self.controller.countInputError, "")


if __name__ == "__main__":
    unittest.main()
