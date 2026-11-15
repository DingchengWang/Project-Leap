"""Final review classes across controller persistence and module navigation.

No QML window, scientific model or TIFF reader is launched. Real asynchronous
editor publication runs only against disposable synthetic channel images.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_counting_controller import CountingControllerFixture
from PySide6.QtCore import QProcess
from leap_desktop import paths


class FinalReviewControllerTests(CountingControllerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.count_run = self.start_fixture()
        self.analysis_path = self.finish_fixture(self.count_run, dispatch=False)
        analysis = json.loads(self.analysis_path.read_text())
        analysis.update(schema_version=2, auto_count=1, support_channel=1,
                        support_marker="Biocytin", display_channels=[0, 1])
        analysis["points"] = [
            {"id": "astrocyte", "label_id": 7, "x": 40, "y": 30, "z": 1,
             "source": "auto", "status": "accepted", "plane_anchors": {"1": [40, 30]}},
            {"id": "likely-unrecognized", "label_id": 42, "x": 100, "y": 85, "z": 1,
             "source": "auto", "status": "review", "review_hint": "likely_astrocyte",
             "plane_anchors": {"1": [100, 85]}},
            {"id": "ordinary-unrecognized", "label_id": 88, "x": 75, "y": 55, "z": 2,
             "source": "auto", "status": "review", "plane_anchors": {"2": [75, 55]}},
            {"id": "neuron", "label_id": 99, "x": 55, "y": 70, "z": 2,
             "source": "auto", "status": "excluded", "plane_anchors": {"2": [55, 70]}},
        ]
        paths.write_json(self.analysis_path, analysis)
        outcome = json.loads((self.count_run / "outcome.json").read_text())
        outcome["auto_count"] = 1
        paths.write_json(self.count_run / "outcome.json", outcome)
        self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.wait_until(lambda: not self.controller.counting.busy)

    def test_pending_confirmation_while_other_module_runs_preserves_its_worker_and_reopens_completed(self):
        editor = self.controller.counting
        self.assertEqual((editor.total, editor.pendingCount, editor.neuronCount), (1, 2, 1))
        self.assertEqual(len(editor.visiblePoints), 4)
        original_points = editor.points
        fluorescence = deepcopy(self.controller._module_states["cell-counting"])
        fluorescence["id"] = "project-leap-2d"
        fluorescence["runtime_descriptor"].update(component_version="1.0.1", fiji_launcher="unused-fiji")
        self.controller._module_states["project-leap-2d"] = fluorescence
        self.controller.selectModule("project-leap-2d")
        self.controller._image_info = {"path": str(self.source), "name": self.source.name}
        self.controller._channel_roles = ["DAPI", "eGFP", "KCNN2"]
        self.controller._series_index = self.controller._time_index = 0
        with patch.object(self.controller, "_launch"):
            self.controller.startAnalysis()
        fluorescence_run = self.controller._run_dir
        self.controller.setPage("settings")
        def session_state(session):
            value = dict(vars(session))
            value["run_details"] = vars(session.run_details) if session.run_details else None
            value["console_decoder"] = session.console_decoder.getstate()
            value["progress_reader"] = vars(session.progress_reader)
            return deepcopy(value)
        fluorescence_before = session_state(self.controller._active_session)
        editor.confirm()
        self.wait_until(lambda: not editor.available and not editor.busy)
        self.assert_counting_workbench_reset()
        self.assertEqual(session_state(self.controller._active_session), fluorescence_before)
        self.assertEqual((self.controller.activeModuleId, self.controller.page, self.controller.status),
                         ("project-leap-2d", "settings", "importing"))
        self.assertTrue(self.controller.busy)
        self.assertEqual(self.controller._run_dir, fluorescence_run)
        self.assertEqual(self.controller._worker_context.run_id, fluorescence_run.name)
        job = json.loads((self.count_run / "job.json").read_text())
        self.assertEqual((job["status"], job["count"], job["revision"]), ("completed", 1, 1))
        saved = json.loads((Path(job["results_dir"]) / "Cell_Count_Session.json").read_text())
        self.assertEqual((saved["count"], saved["pending_count"], saved["excluded_count"]), (1, 2, 1))
        self.assertEqual(saved["points"], original_points)
        self.reopen_counting(self.count_run.name)
        self.assertEqual((self.controller.page, self.controller.status), ("analysis", "completed"))
        self.assertEqual(self.controller.resultInfo["count"], 1)
        self.assertFalse(self.controller.parametersLocked)
        self.assertEqual(editor.points, original_points)
        # The scientific launch above was deliberately replaced at its boundary.
        self.controller._busy = False
        self.controller._worker_context = None
        self.controller._release_lock()
        editor.close()
        restarted = self.make_controller()
        restarted.openCountingJob(self.count_run.name)
        self.wait_until(lambda: not restarted.counting.busy)
        self.assertEqual(restarted.status, "completed")
        self.assertTrue(restarted.counting.confirmedState)
        self.assertFalse(restarted.parametersLocked)
        self.assertEqual((restarted.counting.total, restarted.counting.pendingCount,
                          restarted.counting.neuronCount), (1, 2, 1))
        self.assertEqual(restarted.counting.points, original_points)
        self.assertEqual(self.source_fingerprint(), self.source_before)

    def test_hidden_deletion_and_undo_change_count_without_reclassifying_to_neuron(self):
        editor = self.controller.counting
        editor.selectPoint("astrocyte")
        editor.deleteSelected()
        deleted = next(point for point in editor.points if point["id"] == "astrocyte")
        self.assertEqual(deleted["status"], "accepted")
        self.assertTrue(deleted["deleted"])
        self.assertEqual((editor.total, editor.pendingCount, editor.neuronCount), (0, 2, 1))
        self.assertNotIn("astrocyte", {point["id"] for point in editor.visiblePoints})
        editor.undo()
        self.assertEqual((editor.total, editor.pendingCount, editor.neuronCount), (1, 2, 1))
        editor.redo()
        first = self.confirm_and_wait()
        self.assertEqual((first["status"], first["count"]), ("completed", 0))
        first_session = Path(first["results_dir"]) / "Cell_Count_Session.json"
        first_bytes = first_session.read_bytes()
        saved = json.loads(first_bytes)
        self.assertEqual((saved["count"], saved["pending_count"], saved["excluded_count"], saved["deleted_count"]),
                         (0, 2, 1, 1))
        self.assertEqual(len(saved["points"]), 4)
        self.reopen_counting(self.count_run.name)
        self.assertTrue(next(point for point in editor.points if point["id"] == "astrocyte")["deleted"])
        editor.addPoint(40, 30)
        self.assertEqual((editor.total, editor.pendingCount, editor.neuronCount), (1, 2, 1))
        self.assertTrue(editor.dirty)
        self.assertEqual(json.loads((self.count_run / "job.json").read_text())["count"], 0)
        second = self.confirm_and_wait()
        self.assertEqual((second["count"], second["revision"]), (1, 2))
        self.assertEqual(first_session.read_bytes(), first_bytes)
        self.assertFalse(self.controller.parametersLocked)


if __name__ == "__main__":
    unittest.main()
