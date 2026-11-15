"""Update coordination with real controller signals, persistence and file locks.

Only Sparkle and the maintenance worker boundary are replaced. No network,
installer, scientific process or existing application data is accessed.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from leap_desktop import maintenance, update_manager
from leap_desktop.controller import Controller
from leap_desktop.paths import AppPaths


class FakeUpdater:
    def __init__(self, events):
        self.events = events
        self.state = {"status": "up_to_date", "session_in_progress": False, "can_check": True}
        self.install_calls = []
        self.installation_busy = []

    def check_for_updates(self):
        self.events.append("app_check")
        self.state = {"status": "checking", "session_in_progress": True, "can_check": False}

    def status(self):
        return dict(self.state)

    def install_update(self, build, feed, host, *, installation_busy=False):
        self.events.append("app_install")
        self.install_calls.append((build, feed, host, installation_busy))
        self.state = {"status": "downloading", "session_in_progress": True, "can_check": False}
        return self.status()

    def set_installation_busy(self, busy):
        self.installation_busy.append(busy)


class ControlledMaintenanceTask(QObject):
    progress = Signal(float, str)
    result = Signal(object)
    problem = Signal(str)
    finished = Signal()

    def __init__(self, function, args, kwargs, events):
        super().__init__()
        self.function, self.args, self.kwargs = function, args, kwargs
        self.events = events

    def start(self):
        self.events.append(self.function.__name__)

    def complete(self, result):
        self.result.emit(result)
        self.finished.emit()


class UpdateCoordinationTests(unittest.TestCase):
    OLD_BUILD = "101"
    NEW_BUILD = "102"
    FEED = "https://updates.example.invalid/appcast.xml"

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="leap-update-coordination-", dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.paths = AppPaths(root / "data", config_dir=root / "config")
        self.events, self.tasks, self.controllers = [], [], []
        self.enterContext(patch.object(Controller, "_initial_status", return_value=None))
        self.enterContext(patch("leap_desktop.controller.application_build", return_value=self.OLD_BUILD))
        self.enterContext(patch("leap_desktop.controller.MaintenanceTask", side_effect=self.make_task))
        self.addCleanup(self.cleanup_controllers)
        self.controller = self.make_controller()
        self.plan = {"schema_version": 1, "id": "a" * 32,
                     "targets": [{"id": "cell-counting", "installed_version": "1.0.0",
                                  "available_version": "1.0.1"}]}
        self.staged = dict(self.plan, staging_dir=str(self.paths.home / "updates/staged" / self.plan["id"]))

    def make_task(self, function, *args, **kwargs):
        task = ControlledMaintenanceTask(function, args, kwargs, self.events)
        self.tasks.append(task)
        return task

    def make_controller(self):
        controller = Controller(self.paths)
        controller._timer.stop()
        controller._updater = FakeUpdater(self.events)
        self.controllers.append(controller)
        return controller

    @staticmethod
    def release_process_lock(controller):
        # Model the descriptor closure caused by process termination at restart.
        if controller._update_lock is not None:
            controller._update_lock.__exit__(None, None, None)
            controller._update_lock = None

    def cleanup_controllers(self):
        for controller in self.controllers:
            controller._timer.stop()
            controller._notice_timer.stop()
            controller._closing = True
            self.release_process_lock(controller)
            controller.counting.close()
        self.wait_until(lambda: all(controller.counting.drained for controller in self.controllers))
        for controller in self.controllers:
            controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def wait_until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.application.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail("Counting preview work did not reach its expected boundary")

    def open_review(self):
        folder = self.paths.home / "review"
        folder.mkdir()
        image = QImage(32, 32, QImage.Format.Format_RGB32)
        image.fill(QColor("#505050"))
        self.assertTrue(image.save(str(folder / "channel.png")))
        analysis = {"schema_version": 1, "run_id": "review", "width": 32, "height": 32,
                    "z_count": 1, "channels": [{"index": 0, "name": "DAPI", "color": "#007AFF",
                                               "path": "channel.png"}],
                    "points": [{"id": "auto-1", "label_id": 1, "x": 8, "y": 8,
                                "z": None, "source": "auto"}]}
        path = folder / "analysis.json"
        path.write_text(json.dumps(analysis))
        editor = self.controller.counting
        editor.open_analysis(path)

        def preview_ready():
            if editor.imageUrl and editor.pendingFrameToken:
                self.assertFalse(QImage(QUrl(editor.imageUrl).toLocalFile()).isNull())
                editor.commitFrame(editor.imageUrl, editor.pendingFrameToken)
            return bool(editor.appliedImageUrl) and not editor.busy

        self.wait_until(preview_ready)
        return editor

    def checked(self, *, app=False, components=True, controller=None):
        controller = controller or self.controller
        controller.checkUpdates()
        task = self.tasks[-1]
        self.assertIs(task.function, Controller._check_component_releases)
        controller._updater.state = ({"status": "available", "session_in_progress": False, "can_check": True,
                                      "available_build": self.NEW_BUILD, "feed_url": self.FEED,
                                      "host_build": self.OLD_BUILD} if app else
                                     {"status": "up_to_date", "session_in_progress": False, "can_check": True})
        task.complete(self.component_result(components))
        return controller

    def component_result(self, updates=True):
        return {"status": "updates_available" if updates else "up_to_date",
                "updates": copy.deepcopy(self.plan["targets"]) if updates else [],
                "plan": copy.deepcopy(self.plan)}

    def pending_path(self):
        return self.paths.home / "updates/pending.json"

    def write_pending(self, value):
        path = self.pending_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    @contextmanager
    def installation_barrier(self, controller, barrier):
        owner, attribute, value = {
            "analysis": (controller, "_busy", True),
            "worker_cleanup": (controller, "_cleanup_pending", ("completed", "waiting", 0)),
            "open_review": (controller.counting, "_analysis_path", self.paths.home / "review.json"),
            "result_publication": (controller.counting, "_confirming", True),
            "retiring_worker": (controller.counting, "_processes", [object()]),
        }[barrier]
        old = getattr(owner, attribute)
        setattr(owner, attribute, value)
        try:
            yield
        finally:
            setattr(owner, attribute, old)

    def test_check_runs_during_analysis_but_waits_for_both_checks_and_worker_exit(self):
        controller = self.controller
        with maintenance.component_use_lock(self.paths.home):
            with self.installation_barrier(controller, "analysis"):
                controller.checkUpdates()
                self.assertTrue(controller.busy)
                self.assertEqual(self.events, ["app_check", "_check_component_releases"])
                task = self.tasks[-1]
                task.result.emit(self.component_result())
                self.assertFalse(controller.maintenanceBusy)
                self.assertIs(controller._update_check_thread, task)
                self.assertIsNone(controller._update_components)
                self.assertEqual(len(self.tasks), 1)
                task.finished.emit()
                controller._poll_updates()
                self.assertEqual(len(self.tasks), 1)  # Sparkle check is still running.
                controller._updater.state = {"status": "up_to_date", "session_in_progress": False}
                controller._poll_updates()
                self.assertTrue(controller.busy)
                self.assertEqual(len(self.tasks), 1)
        controller._poll_updates()
        self.assertIs(self.tasks[-1].function, update_manager.stage_component_updates)

    def test_check_keeps_existing_review_preview_available_and_editable(self):
        controller = self.controller
        editor = self.open_review()
        preview = editor.appliedImageUrl
        controller.checkUpdates()
        task = self.tasks[-1]
        self.assertTrue(controller.updatesBusy)
        self.assertFalse(controller.maintenanceBusy)
        self.assertIsNone(controller._maintenance_thread)
        self.assertIs(controller._update_check_thread, task)
        self.assertTrue(editor.available)
        self.assertFalse(editor.busy)
        self.assertEqual(editor.appliedImageUrl, preview)
        editor.setMode("add")
        editor.addPoint(12, 14)
        self.assertEqual(editor.mode, "add")
        self.assertEqual(editor.total, 2)
        self.assertEqual(editor.appliedImageUrl, preview)
        task.progress.emit(.5, "Checking release metadata")
        task.result.emit(self.component_result())
        self.assertFalse(controller.maintenanceBusy)
        self.assertIs(controller._update_check_thread, task)
        controller._updater.state = {"status": "up_to_date", "session_in_progress": False,
                                     "can_check": True}
        task.finished.emit()
        controller._poll_updates()
        self.assertIsNone(controller._update_check_thread)
        self.assertEqual(len(self.tasks), 1)
        self.assertTrue(editor.available)
        self.assertFalse(editor.busy)
        self.assertEqual(editor.total, 2)
        self.assertEqual(editor.appliedImageUrl, preview)

    def test_quit_waits_for_readonly_check_exit_without_starting_staging(self):
        controller = self.controller
        controller.checkUpdates()
        task = self.tasks[-1]
        controller._updater.state = {"status": "up_to_date", "session_in_progress": False,
                                     "can_check": True}
        updates_at_quit = []
        with patch("leap_desktop.controller.QTimer.singleShot") as schedule, \
                patch("leap_desktop.controller.QMessageBox.information") as information, \
                patch.object(QApplication, "quit", side_effect=lambda: updates_at_quit.append(controller.updatesBusy)) as quit_app:
            controller.requestQuit()
            self.assertTrue(controller._closing)
            self.assertIs(controller._update_check_thread, task)
            self.assertFalse(controller.maintenanceBusy)
            information.assert_not_called()
            quit_app.assert_not_called()
            schedule.assert_called_with(50, controller._finish_quit)
            task.result.emit(self.component_result())
            controller._finish_quit()
            quit_app.assert_not_called()
            task.finished.emit()
            controller._poll_updates()
            self.assertIsNone(controller._update_check_thread)
            self.assertEqual(len(self.tasks), 1)
            quit_app.assert_not_called()
            controller._finish_quit()
            quit_app.assert_called_once()
        self.assertEqual(updates_at_quit, [False])
        self.assertFalse(controller.updatesBusy)
        self.assertEqual(controller._updater.install_calls, [])
        self.assertEqual(len(self.tasks), 1)

    def test_staging_and_application_wait_for_analysis_review_and_publication(self):
        for barrier in ("analysis", "worker_cleanup", "open_review", "result_publication", "retiring_worker"):
            with self.subTest(barrier=barrier):
                controller = self.make_controller()
                with self.installation_barrier(controller, barrier):
                    self.checked(controller=controller)
                    checked_count = len(self.tasks)
                    controller._poll_updates()
                    self.assertEqual(len(self.tasks), checked_count)
                controller._poll_updates()
                staging = self.tasks[-1]
                self.assertIs(staging.function, update_manager.stage_component_updates)
                with self.installation_barrier(controller, barrier):
                    staging.complete({"status": "staged", "plan": self.staged})
                    controller._poll_updates()
                    self.assertIs(self.tasks[-1], staging)
                controller._poll_updates()
                applying = self.tasks[-1]
                self.assertIs(applying.function, update_manager.apply_staged_updates)
                self.assertEqual(applying.args, (self.paths.home, self.staged))
                applying.complete({"status": "ready", "message": "installed"})
                self.assertFalse(controller.updatesBusy)

    def test_combined_update_stages_then_installs_then_resumes_exact_plan_after_restart(self):
        controller = self.checked(app=True)
        staging = self.tasks[-1]
        self.assertIs(staging.function, update_manager.stage_component_updates)
        staging.result.emit({"status": "staged", "plan": self.staged})
        self.assertEqual(controller._updater.install_calls, [])
        self.assertTrue(controller.maintenanceBusy)
        staging.finished.emit()
        controller._updater.state["can_check"] = False
        controller._poll_updates()
        self.assertEqual(controller._updater.install_calls, [])
        controller._updater.state["can_check"] = True
        controller._poll_updates()
        self.assertEqual(self.events, ["app_check", "_check_component_releases",
                                      "stage_component_updates", "app_install"])
        self.assertEqual(controller._updater.install_calls,
                         [(self.NEW_BUILD, self.FEED, self.OLD_BUILD, True)])
        self.assertEqual(json.loads(self.pending_path().read_text()),
                         {"schema_version": 1, "expected_host_build": self.NEW_BUILD, "plan": self.staged})
        controller._poll_updates()
        self.assertFalse(controller._updater.installation_busy[-1])
        with self.assertRaises(maintenance.ComponentBusy):
            with maintenance.component_use_lock(self.paths.home):
                pass
        controller._updater.state = {"status": "completed", "session_in_progress": False}
        controller._poll_updates()
        self.assertFalse(controller.updatesBusy)
        self.assertIsNone(controller._update_lock)
        self.assertTrue(self.pending_path().exists())
        self.assertIs(self.tasks[-1], staging)
        self.release_process_lock(controller)
        with patch("leap_desktop.controller.application_build", return_value=self.NEW_BUILD):
            restarted = self.make_controller()
        restarted._resume_pending_update()
        self.assertTrue(restarted.updatesBusy)
        restarted._poll_updates()
        applying = self.tasks[-1]
        self.assertIs(applying.function, update_manager.apply_staged_updates)
        self.assertEqual(applying.args, (self.paths.home, self.staged))
        applying.complete({"status": "ready", "message": "installed"})
        self.assertFalse(restarted.updatesBusy)
        self.assertFalse(self.pending_path().exists())

    def test_app_install_gate_honors_external_use_lock_and_releases_after_failure(self):
        controller = self.checked(app=True, components=False)
        controller._poll_updates()
        self.assertEqual(controller._updater.install_calls[-1][-1], True)
        with maintenance.component_use_lock(self.paths.home):
            controller._poll_updates()
            self.assertTrue(controller._updater.installation_busy[-1])
            self.assertIsNone(controller._update_lock)
        controller._poll_updates()
        self.assertFalse(controller._updater.installation_busy[-1])
        with self.assertRaises(maintenance.ComponentBusy):
            with maintenance.component_maintenance_lock(self.paths.home):
                pass
        controller._updater.state = {"status": "failed", "message": "download failed", "session_in_progress": False}
        controller._poll_updates()
        self.assertFalse(controller.updatesBusy)
        self.assertIsNone(controller._update_lock)
        with maintenance.component_use_lock(self.paths.home):
            pass

    def test_failed_stage_stops_before_app_install_and_failed_apply_keeps_pending_for_retry(self):
        controller = self.checked(app=True)
        self.tasks[-1].complete({"status": "failed", "message": "package checksum mismatch"})
        self.assertFalse(controller.updatesBusy)
        self.assertEqual(controller._updater.install_calls, [])
        self.assertFalse(self.pending_path().exists())
        controller = self.checked()
        self.tasks[-1].complete({"status": "staged", "plan": self.staged})
        before = self.pending_path().read_bytes()
        controller._poll_updates()
        self.tasks[-1].problem.emit("disk full")
        self.tasks[-1].finished.emit()
        self.assertFalse(controller.updatesBusy)
        self.assertFalse(controller.maintenanceBusy)
        self.assertEqual(self.pending_path().read_bytes(), before)
        with maintenance.component_use_lock(self.paths.home):
            pass

    def test_wrong_host_build_preserves_pending_and_never_applies_components(self):
        path = self.write_pending({"schema_version": 1, "expected_host_build": self.NEW_BUILD, "plan": self.staged})
        before = path.read_bytes()
        self.controller._resume_pending_update()
        self.controller._poll_updates()
        self.assertFalse(self.controller.updatesBusy)
        self.assertEqual(self.tasks, [])
        self.assertEqual(path.read_bytes(), before)

    def test_incompatible_component_host_requirement_stops_before_download(self):
        for with_app, required in ((False, 102), (True, 103)):
            with self.subTest(with_app=with_app, required=required):
                self.plan["targets"][0]["minimum_host_build"] = required
                controller = self.make_controller()
                before = len(self.tasks)
                self.checked(app=with_app, controller=controller)
                controller._poll_updates()
                self.assertEqual(len(self.tasks), before + 1)
                self.assertIs(self.tasks[-1].function, Controller._check_component_releases)
                self.assertEqual(controller._updater.install_calls, [])
                self.assertFalse(controller.updatesBusy)

    def test_new_check_discards_previous_staging_before_fetch_and_preserves_failed_cleanup(self):
        for outcome in ("discarded", "failed"):
            with self.subTest(cleanup=outcome):
                path = self.write_pending({"schema_version": 1, "expected_host_build": self.NEW_BUILD,
                                           "plan": self.staged})
                before = path.read_bytes()
                controller = self.make_controller()
                controller.checkUpdates()
                task = self.tasks[-1]
                calls = []

                def discard(home, plan):
                    self.assertEqual((home, plan), (self.paths.home, self.staged))
                    calls.append("discard")
                    return {"status": outcome, "message": "cleanup " + outcome}

                def check(home, *, configuration, progress):
                    self.assertEqual(home, self.paths.home)
                    self.assertFalse(path.exists())
                    calls.append("check")
                    return self.component_result(False)

                with patch.object(update_manager, "discard_staged_updates", side_effect=discard), \
                        patch.object(update_manager, "check_component_updates", side_effect=check):
                    result = task.function(*task.args, **task.kwargs)
                controller._updater.state = {"status": "up_to_date", "session_in_progress": False,
                                             "can_check": True}
                task.complete(result)
                self.assertFalse(controller.updatesBusy)
                if outcome == "discarded":
                    self.assertEqual(calls, ["discard", "check"])
                    self.assertFalse(path.exists())
                else:
                    self.assertEqual(calls, ["discard"])
                    self.assertEqual(path.read_bytes(), before)

    def test_invalid_pending_records_report_failure_without_dispatching_updates(self):
        for value in ({"schema_version": 99}, [], None, "not a record"):
            with self.subTest(value=value):
                path = self.write_pending(value)
                before = path.read_bytes()
                self.controller._resume_pending_update()
                self.controller._poll_updates()
                self.assertFalse(self.controller.updatesBusy)
                self.assertEqual(self.tasks, [])
                self.assertEqual(path.read_bytes(), before)
                self.assertTrue(self.controller.updateStatusMessage)
        path.write_text("{truncated")
        self.controller._resume_pending_update()
        self.controller._poll_updates()
        self.assertEqual(self.tasks, [])
        self.assertFalse(self.controller.updatesBusy)


if __name__ == "__main__":
    unittest.main()
