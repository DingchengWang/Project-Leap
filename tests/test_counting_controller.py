"""Counting controller/editor integration with synthetic data and offscreen Qt.

Only the scientific subprocess boundary is replaced. Native Qt rendering,
asynchronous confirmation, job persistence, and restart recovery execute for real.
No installed component, original research image, GPU, or Fiji process is used.
"""
from __future__ import annotations

import csv
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

from PySide6.QtCore import QCoreApplication, QEvent, QProcess, QThreadPool, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from leap_desktop import counting, maintenance, modules, paths
from leap_desktop.controller import Controller
from leap_desktop.counting import CountingEditor


class CountingControllerFixture:
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="leap-count-controller-", dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = paths.AppPaths(self.root / "app-home")
        self.source = self.root / "original-source.tif"
        self.source.write_bytes(b"Synthetic source sentinel; TIFF decoding belongs to the worker tests.")
        self.source.chmod(0o444)
        self.source_before = self.source_fingerprint()
        # Suppress the asynchronous installed-component scan, not controller/editor behavior.
        initial = patch.object(Controller, "_initial_status", return_value=None)
        initial.start()
        self.addCleanup(initial.stop)
        self.controllers = []
        self.addCleanup(self.cleanup_controllers)
        self.controller = self.make_controller()

    def make_controller(self, *, installed=True):
        controller = Controller(paths.AppPaths(self.paths.home))
        controller._timer.stop()
        self.controllers.append(controller)
        descriptor = {"root": str(self.root / "absent-component"),
                      "python": str(self.root / "unused-python"),
                      "site_packages": str(self.root / "absent-site-packages"),
                      "models": str(self.root / "absent-models"),
                      "engine": str(self.root / "absent-engine"), "component_version": "1.0.0"}
        state = {"id": "cell-counting", "installed": installed,
                 "status": "ready" if installed else "not_installed"}
        if installed:
            state["runtime_descriptor"] = descriptor
        controller._module_states = {"cell-counting": state}
        controller._active_module_id = "cell-counting"
        controller._component_info = state
        self.assertIsInstance(controller.counting, CountingEditor)
        return controller

    def cleanup_controllers(self):
        for controller in self.controllers:
            controller._timer.stop()
            controller._notice_timer.stop()
            controller._closing = True
            controller.counting.close()
            controller._release_lock()
        self.wait_until(lambda: all(c.counting.drained and not c.maintenanceBusy
                                   and c._maintenance_thread is None for c in self.controllers))
        self.assertTrue(QThreadPool.globalInstance().waitForDone(5000))
        self.application.processEvents()
        for controller in self.controllers:
            controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def wait_until(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.application.processEvents()
            # Headless tests decode the same saved pixels before acknowledging
            # the frame handoff normally delivered by QML Image.Ready.
            for controller in self.controllers:
                editor = controller.counting
                if editor.imageUrl and editor.pendingFrameToken:
                    image = QImage(QUrl(editor.imageUrl).toLocalFile())
                    if not image.isNull():
                        editor.commitFrame(editor.imageUrl, editor.pendingFrameToken)
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Asynchronous counting controller/editor work did not settle")

    def source_fingerprint(self):
        stat = self.source.stat()
        return (stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_mode,
                hashlib.sha256(self.source.read_bytes()).hexdigest())

    def inspect_fixture(self, *, dapi_channel=0, controller=None):
        controller = controller or self.controller
        series = {"series_index": 0, "name": "synthetic counting source", "axes": "ZCYX",
                  "shape": [4, 3, 120, 160], "channel_count": 3, "time_count": 1,
                  "z_count": 4, "physical_size_um": {"X": 0.2, "Y": 0.2, "Z": 0.5},
                  "channel_hints": ["DAPI", "Biocytin (Cy2)", "SR101"],
                  "channel_roles": ["DAPI", "Biocytin", "SR101"] if dapi_channel == 0 else ["Other"] * 3,
                  "dapi_channel": dapi_channel, "supported_layout": True}
        report = {"path": str(self.source), "series_count": 1, "series": [series]}
        with patch.object(controller, "_launch") as launch:
            controller.inspectImage(str(self.source))
        launch.assert_called_once()
        kind, args = launch.call_args.args
        self.assertEqual(kind, "inspect")
        self.assertEqual(args[:2], ["-m", "leap_cell_counting"])
        self.assertEqual(args[2:4], ["--inspect", str(self.source)])
        controller._process_kind = "inspect"
        controller._inspect_buffer = json.dumps(report)
        controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertEqual(controller.status, "ready")
        self.assertFalse(controller.busy)
        return report

    def start_fixture(self):
        self.inspect_fixture()
        with patch.object(self.controller, "_launch") as launch:
            self.controller.startAnalysis()
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0], "analysis")
        self.controller._process_kind = "analysis"
        self.assertTrue(self.controller.busy)
        return self.controller._run_dir

    def finish_fixture(self, run, *, dispatch=True):
        base = run / "counting"
        (base / "channels").mkdir(parents=True)
        channels = []
        for i, (name, color, value) in enumerate((("DAPI", "#0000FF", 80),
                                                 ("Biocytin", "#FFFFFF", 30),
                                                 ("SR101", "#FF0000", 20))):
            image = QImage(160, 120, QImage.Format.Format_Grayscale8)
            image.fill(QColor(value, value, value))
            relative = f"channels/C{i + 1}.png"
            self.assertTrue(image.save(str(base / relative)))
            channels.append({"index": i, "name": name, "role": name, "color": color, "path": relative})
        analysis = {"schema_version": 1, "run_id": run.name, "source_path": str(self.source),
                    "series_index": 0, "time_index": 0, "dapi_channel": 0,
                    "width": 160, "height": 120, "z_count": 4,
                    "physical_size_um": {"X": 0.2, "Y": 0.2, "Z": 0.5},
                    "auto_count": 2, "default_label_size": 16, "channels": channels,
                    "channel_roles": ["DAPI", "Biocytin", "SR101"], "preview_path": "composite.png",
                    "points": [{"id": "auto-7", "label_id": 7, "x": 40, "y": 30, "z": None, "source": "auto"},
                               {"id": "auto-42", "label_id": 42, "x": 100, "y": 85, "z": None, "source": "auto"}]}
        analysis_path = base / "analysis.json"
        paths.write_json(analysis_path, analysis)
        image = counting.composite_image(base, channels, 160, 120)
        self.assertTrue(image.save(str(base / "composite.png")))
        paths.write_json(run / "outcome.json", {"schema_version": 1, "status": "review_ready",
                         "run_id": run.name, "analysis_path": str(analysis_path), "auto_count": 2,
                         "message": "Synthetic worker outcome; awaiting user confirmation"})
        if dispatch:
            self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
            self.wait_until(lambda: bool(self.controller.counting.imageUrl) and not self.controller.counting.busy)
        return analysis_path

    def review_fixture(self):
        run = self.start_fixture()
        return run, self.finish_fixture(run)

    def confirm_and_wait(self, controller=None):
        controller = controller or self.controller
        run = controller._session_for("cell-counting").run_dir
        controller.counting.confirm()
        self.wait_until(lambda: not controller.counting.busy and not controller.counting.available
                        and not controller.maintenanceBusy and not controller._pending_cleanup_ids)
        self.assert_counting_workbench_reset(controller)
        return json.loads((run / "job.json").read_text())

    def assert_counting_workbench_reset(self, controller=None):
        controller = controller or self.controller
        session = controller._session_for("cell-counting")
        self.assertEqual((session.status, session.image_info, session.channel_roles,
                          session.run_settings, session.result_info, session.selected_job_id),
                         ("idle", {}, [], {}, {}, ""))
        self.assertIsNone(session.job)
        self.assertIsNone(session.run_dir)
        self.assertIsNone(session.series_index)
        self.assertIsNone(session.time_index)
        editor = controller.counting
        self.assertFalse(editor.available)
        self.assertEqual((editor.points, editor.selectedIds, editor.imageUrl, editor.appliedImageUrl),
                         ([], [], "", ""))
        self.assertEqual((editor.viewZoom, editor.viewPanX, editor.viewPanY), (1.0, 0.0, 0.0))
        self.assertFalse(editor.canUndo)
        self.assertFalse(editor.canRedo)

    def reopen_counting(self, identifier, controller=None):
        controller = controller or self.controller
        controller.openCountingJob(identifier)
        self.wait_until(lambda: bool(controller.counting.imageUrl) and not controller.counting.busy)

class CountingControllerTests(CountingControllerFixture, unittest.TestCase):
    def test_catalog_order_controls_both_lists_independent_of_status_arrival(self):
        future = {**modules.CATALOG[-1], "id": "future-analysis", "name": "Future module"}
        catalog = (*modules.CATALOG, future)
        expected = [item["id"] for item in catalog]
        self.assertEqual(expected[:2], ["project-leap-2d", "cell-counting"])
        with patch.object(modules, "CATALOG", catalog):
            for installed in (True, False):
                with self.subTest(installed=installed):
                    states = [{"id": identifier, "installed": installed,
                               "status": "damaged" if installed else "not_installed",
                               "install_seq": index}
                              for index, identifier in enumerate(reversed(expected))]
                    with patch.object(maintenance, "list_statuses", return_value=states):
                        self.controller._refresh_modules()
                    visible = self.controller.installedModules if installed else self.controller.availableModules
                    self.assertEqual([item["id"] for item in visible], expected)

    def test_inspection_auto_dapi_and_manual_source_override(self):
        self.inspect_fixture()
        self.assertEqual(self.controller.countDapiChannel, 0)
        self.assertEqual(self.controller.channelRoles, ["DAPI", "Biocytin", "SR101"])
        self.controller.setCountDapiChannel(2)
        self.assertEqual(self.controller.countDapiChannel, 2)
        self.assertEqual(self.controller.channelRoles, ["Other", "Biocytin", "DAPI"])
        self.controller.setCountDapiChannel(0)
        self.assertEqual(self.controller.channelRoles.count("DAPI"), 1)
        self.assertEqual(self.controller.countDapiChannel, 0)
        self.controller.setCountDapiChannel(99)
        self.assertEqual(self.controller.countDapiChannel, 0)
        self.assertEqual(self.source_fingerprint(), self.source_before)

    def test_unresolved_dapi_requires_manual_selection_before_launch(self):
        self.inspect_fixture(dapi_channel=-1)
        self.assertEqual(self.controller.countDapiChannel, -1)
        with patch.object(self.controller, "_launch") as launch:
            self.controller.startAnalysis()
            launch.assert_not_called()
        self.assertEqual(list(self.paths.runs.iterdir()), [])
        self.controller.setCountDapiChannel(1)
        self.assertEqual(self.controller.countDapiChannel, 1)
        self.controller.setCountDapiChannel(-1)
        self.assertEqual(self.controller.countDapiChannel, -1)

    def test_count_start_dispatches_original_source_without_split_or_fiji(self):
        self.inspect_fixture()
        self.controller.setCountDapiChannel(2)
        self.controller.setCountChannelColor(1, "#123456")
        with patch.object(self.controller, "_launch") as launch, \
                patch.object(self.controller, "_start_engine", side_effect=AssertionError("Fluorescence engine invoked")):
            self.controller.startAnalysis()
        run = self.controller._run_dir
        launch.assert_called_once_with("analysis", ["-m", "leap_cell_counting", "--request", str(run / "request.json")])
        request = json.loads((run / "request.json").read_text())
        self.assertEqual(request["source_path"], str(self.source))
        self.assertEqual((request["series_index"], request["time_index"], request["dapi_channel"]), (0, 0, 2))
        self.assertEqual(request["channel_roles"], ["Other", "Biocytin", "DAPI"])
        self.assertEqual(request["channel_colors"][1], "#123456")
        self.assertNotIn("fiji_launcher", request)
        self.assertFalse((run / "import-request.json").exists())
        self.assertFalse((run / "workspace").exists())
        self.assertFalse(list(run.rglob("*.tif")))
        self.assertIsNone(self.controller._fiji_pid)
        self.assertEqual(self.source_fingerprint(), self.source_before)

    def test_review_ready_survives_job_reload_and_controller_restart(self):
        run, analysis = self.review_fixture()
        saved = json.loads((run / "job.json").read_text())
        self.assertEqual(saved["status"], "waiting_review")
        self.assertEqual(saved["analysis_path"], str(analysis))
        self.assertEqual((saved["count"], saved["auto_count"]), (2, 2))
        self.assertFalse(self.controller.busy)
        self.assertFalse(self.controller.counting.confirmedState)
        self.assertFalse(self.controller.resultInfo["confirmed"])
        with maintenance.component_maintenance_lock(self.paths.home):
            pass  # No scientific worker owns the component lock during native review.
        self.controller._load_jobs()
        self.assertEqual(self.controller.jobs[0]["status"], "waiting_review")
        self.controller.counting.close()
        self.wait_until(lambda: not self.controller.counting.busy)
        restarted = self.make_controller()
        self.assertEqual(restarted.jobs[0]["status"], "waiting_review")
        with patch.object(restarted, "_matches_process", side_effect=AssertionError("Review tried to reconnect a worker")):
            restarted._recover_live_job()
        restarted.openCountingJob(run.name)
        self.wait_until(lambda: bool(restarted.counting.imageUrl) and not restarted.counting.busy)
        self.assertEqual((restarted.status, restarted.counting.total), ("waiting_review", 2))
        self.assertFalse(restarted.busy)

    def test_real_confirmation_signal_updates_count_results_and_revision_history(self):
        run, _ = self.review_fixture()
        emitted = []
        self.controller.counting.confirmed.connect(lambda identifier, summary: emitted.append((identifier, dict(summary))))
        self.controller.counting.addPoint(40, 30)  # Same XY remains a distinct manual point.
        first = self.confirm_and_wait()
        first_dir = Path(first["results_dir"])
        first_bytes = {p.name: p.read_bytes() for p in first_dir.iterdir() if p.is_file()}
        self.assertEqual((first["count"], first["auto_count"], first["revision"]), (3, 2, 1))
        self.assertEqual(emitted[0][0], run.name)
        self.assertEqual(emitted[0][1]["count"], 3)
        self.assertEqual(self.controller.jobs[0]["count"], 3)
        self.controller.setPage("jobs")
        self.controller.selectJob(run.name)
        self.assertEqual(self.controller.resultInfo["count"], 3)
        self.assertTrue(self.controller.resultInfo["confirmed"])
        self.assertEqual({p["name"] for p in self.controller.resultInfo["artifacts"]},
                         {"Cell_Count_Overlay.png", "Cell_Counts.csv", "Cell_Count_Session.json"})
        with (first_dir / "Cell_Counts.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 3)
        self.assertEqual([int(row["display_no"]) for row in rows], [1, 2, 3])
        self.reopen_counting(run.name)
        self.assertTrue(self.controller.counting.confirmedState)
        self.controller.counting.addPoint(75, 60)
        second = self.confirm_and_wait()
        self.assertEqual((second["count"], second["revision"]), (4, 2))
        self.assertNotEqual(second["results_dir"], first["results_dir"])
        self.assertEqual(first_bytes, {p.name: p.read_bytes() for p in first_dir.iterdir() if p.is_file()})
        self.assertEqual(self.source_fingerprint(), self.source_before)

    def test_restart_recovers_outcome_after_review_event_before_process_finished(self):
        run = self.start_fixture()
        self.controller._job["worker"] = {"pid": 424242, "kind": "analysis"}
        event = {"schema_version": 1, "run_id": run.name, "sequence": 1,
                 "type": "review_ready", "stage": "count_prepare_review",
                 "message": "Synthetic worker completed; host callback has not run"}
        (run / "events.jsonl").write_text(json.dumps(event) + "\n")
        self.assertTrue(self.controller._read_events())
        analysis = self.finish_fixture(run, dispatch=False)
        incomplete = json.loads((run / "job.json").read_text())
        self.assertEqual(incomplete["status"], "waiting_review")
        self.assertNotIn("analysis_path", incomplete)
        self.assertTrue((run / "outcome.json").is_file())
        self.assertTrue(analysis.is_file())
        self.assertFalse(self.controller.counting.available)
        # Emulate the crashed host releasing its OS lock without its finish callback.
        self.controller._release_lock()
        restarted = self.make_controller()
        with patch.object(restarted, "_matches_process", return_value=False) as matches, \
                patch.object(restarted, "_launch", side_effect=AssertionError("Recovery relaunched inference")):
            restarted._recover_live_job()
            self.wait_until(lambda: bool(restarted.counting.imageUrl) and not restarted.counting.busy)
        matches.assert_called_with(424242, run / "request.json")
        recovered = json.loads((run / "job.json").read_text())
        self.assertEqual(recovered["analysis_path"], str(analysis))
        self.assertEqual(recovered["outcome"]["status"], "review_ready")
        self.assertEqual((recovered["status"], recovered["count"]), ("waiting_review", 2))
        self.assertEqual((restarted.status, restarted.counting.runId, restarted.counting.total),
                         ("waiting_review", run.name, 2))
        self.assertFalse(restarted.busy)
        self.assertIsNone(restarted._detached_pid)
        self.assertFalse(restarted.counting.confirmedState)
        self.assertEqual(restarted.resultInfo["analysis_path"], str(analysis))
        self.assertEqual(self.source_fingerprint(), self.source_before)

    def test_saved_confirmation_reopens_without_module_model_or_original_source(self):
        run, _ = self.review_fixture()
        self.controller.counting.addPoint(60, 60)
        saved = self.confirm_and_wait()
        self.controller.counting.close()
        self.wait_until(lambda: not self.controller.counting.busy)
        self.source.unlink()
        restarted = self.make_controller(installed=False)
        with patch.object(restarted, "_launch", side_effect=AssertionError("Saved MIP review launched a worker")), \
                patch.object(restarted, "_runtime", side_effect=AssertionError("Saved review required a module")):
            restarted.openCountingJob(run.name)
            self.wait_until(lambda: bool(restarted.counting.imageUrl) and not restarted.counting.busy)
        self.assertEqual(restarted.counting.total, saved["count"])
        self.assertTrue(restarted.counting.confirmedState)
        self.assertEqual(restarted.status, "completed")
        self.assertIsNone(restarted.counting._runtime)
        self.assertEqual(restarted.counting.zIndex, -1)
        self.assertFalse(QImage(QUrl(restarted.counting.imageUrl).toLocalFile()).isNull())
        group = next(g for g in restarted.jobGroups if g["moduleId"] == "cell-counting")
        self.assertFalse(group["installed"])
        self.assertEqual(group["jobs"][0]["count"], 3)

    def test_failed_new_confirmation_preserves_previous_published_job_and_files(self):
        run, analysis = self.review_fixture()
        saved = self.confirm_and_wait()
        job_bytes = (run / "job.json").read_bytes()
        latest = analysis.parent / "results/latest.json"
        pointer_bytes = latest.read_bytes()
        previous = Path(saved["results_dir"])
        previous_bytes = {p.name: p.read_bytes() for p in previous.iterdir() if p.is_file()}
        self.reopen_counting(run.name)
        self.controller.counting.addPoint(65, 50)
        emitted = []
        self.controller.counting.confirmed.connect(lambda *args: emitted.append(args))
        original_atomic = counting._atomic_json

        def fail_latest(path, value):
            if Path(path) == latest:
                raise OSError("Injected publication-pointer failure")
            return original_atomic(path, value)

        with patch("leap_desktop.counting._atomic_json", side_effect=fail_latest):
            self.controller.counting.confirm()
            self.wait_until(lambda: not self.controller.counting.busy)
        self.assertIn("Injected publication-pointer failure", self.controller.counting.message)
        self.assertEqual(emitted, [])
        self.assertEqual((run / "job.json").read_bytes(), job_bytes)
        self.assertEqual(latest.read_bytes(), pointer_bytes)
        self.assertEqual(previous_bytes, {p.name: p.read_bytes() for p in previous.iterdir() if p.is_file()})
        self.assertEqual(self.controller.resultInfo["count"], 2)
        self.assertEqual(self.controller.counting.total, 3)
        self.assertTrue(self.controller.counting.dirty)
        self.assertFalse(self.controller.counting.confirmedState)
        self.assertTrue(self.controller.counting.canUndo)
        self.assertTrue(self.controller.counting.available)
        self.assertEqual(self.source_fingerprint(), self.source_before)

    def test_job_record_failure_keeps_review_and_undo_until_successful_retry(self):
        run, _ = self.review_fixture()
        editor = self.controller.counting
        editor.addPoint(60, 50)
        points = editor.points
        session = self.controller._session_for("cell-counting")
        job_before = (run / "job.json").read_bytes()
        original_write = paths.write_json

        def fail_job(path, value):
            if Path(path) == run / "job.json":
                raise OSError("Injected job-record save failure")
            return original_write(path, value)

        with patch("leap_desktop.controller.write_json", side_effect=fail_job):
            editor.confirm()
            self.wait_until(lambda: "Injected job-record save failure" in session.message)
        self.assertEqual((run / "job.json").read_bytes(), job_before)
        self.assertIs(self.controller._session_for("cell-counting"), session)
        self.assertEqual(session.status, "waiting_review")
        self.assertEqual(editor.points, points)
        self.assertTrue(editor.available)
        self.assertTrue(editor.canUndo)
        self.assertTrue(editor.imageUrl)
        saved = self.confirm_and_wait()
        self.assertEqual((saved["status"], saved["count"]), ("completed", 3))

    def test_open_other_saved_job_keeps_current_session_when_draft_flush_fails(self):
        run, analysis = self.review_fixture()
        saved = self.confirm_and_wait()
        self.reopen_counting(run.name)
        editor = self.controller.counting
        editor.setView(1.75, 13, -8)
        session = self.controller._session_for("cell-counting")
        image_info = dict(session.image_info)
        points = editor.points
        other = {**saved, "id": "another-saved-run", "name": "Another saved image"}
        self.controller._jobs.append(other)
        draft = analysis.parent / "draft.json"
        original_atomic = counting._atomic_json

        def fail_draft(path, value):
            if Path(path) == draft:
                raise OSError("Injected draft flush failure")
            return original_atomic(path, value)

        with patch("leap_desktop.counting._atomic_json", side_effect=fail_draft):
            self.controller.openCountingJob(other["id"])
        self.assertIs(self.controller._session_for("cell-counting"), session)
        self.assertEqual(session.image_info, image_info)
        self.assertEqual(session.job["id"], run.name)
        self.assertEqual(editor.runId, run.name)
        self.assertEqual(editor.points, points)
        self.assertTrue(editor.available)
        self.assertEqual((editor.viewZoom, editor.viewPanX, editor.viewPanY), (1.75, 13, -8))
        self.assertIn("Injected draft flush failure", session.message)


if __name__ == "__main__":
    unittest.main()
