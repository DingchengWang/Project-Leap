"""Offscreen Qt contract checks; no real images, models, Fiji or installers.

Run with runtime/ui-env/bin/python and QT_QPA_PLATFORM=offscreen.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, PropertyMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QMessageBox
from leap_desktop import maintenance, modules, paths, version
from leap_desktop.controller import Controller, WorkerContext
from leap_desktop.release_catalog import ReleaseConfiguration


class ControllerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="leap-controller-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = paths.AppPaths(self.root / "app-home")
        self.initial_status = patch.object(Controller, "_initial_status", return_value=None)
        self.initial_status.start()
        self.addCleanup(self.initial_status.stop)
        self.controller = Controller(self.paths)
        self.controller._timer.stop()
        self.addCleanup(self.controller.deleteLater)
        self.controller._component_info = {"status": "ready", "runtime_descriptor": {
            "root": str(self.root / "components"), "python": str(self.root / "unused-python"),
            "site_packages": str(self.root / "site-packages"), "models": str(self.root / "models"),
            "engine": str(self.root / "module-engine"), "fiji_root": str(self.root / "shared-fiji"),
            "fiji_launcher": str(self.root / "unused-fiji"), "component_version": "1.0.1",
        }}

    def test_bad_new_inspection_preserves_completed_job(self):
        completed = self.paths.runs / "completed-1"
        completed.mkdir()
        record = {"id": "completed-1", "name": "previous source", "status": "completed",
                  "stage": "分析完成", "message": "saved", "created_at": "2026-10-17T00:00:00Z",
                  "results_dir": str(completed / "workspace/Result")}
        job_file = completed / "job.json"
        paths.write_json(job_file, record)
        original_bytes = job_file.read_bytes()
        self.controller._job, self.controller._run_dir = dict(record), completed
        self.controller._status = "completed"
        fake_image = self.root / "bad.tif"
        fake_image.write_bytes(b"invalid TIFF fixture; never decoded")
        with patch.object(self.controller, "_launch", side_effect=RuntimeError("Fixture inspection failure")):
            self.controller.inspectImage(str(fake_image))
        self.assertEqual(job_file.read_bytes(), original_bytes)
        self.assertEqual(self.controller.status, "failed")
        self.assertIsNone(self.controller._job)
        self.assertIsNone(self.controller._run_dir)

    def test_cancelled_inspection_does_not_decode_or_become_ready(self):
        self.controller._busy = True
        self.controller._status = "cancelling"
        self.controller._process_kind = "inspect"
        self.controller._inspect_buffer = "invalid JSON that must not be decoded after cancellation"
        self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.controller.status, "cancelled")
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.imageInfo, {})
        self.assertIsNone(self.controller._job)
        self.assertNotRegex(self.controller.message, r"[\u4e00-\u9fff]")

    def test_multi_series_and_time_require_explicit_selection(self):
        series = []
        for index, time_count in ((0, 1), (1, 2)):
            series.append({"series_index": index, "name": "Image " + str(index + 1),
                           "axes": "TCZYX", "shape": [time_count, 3, 5, 12, 16],
                           "channel_count": 3, "time_count": time_count,
                           "channel_hints": ["DAPI", "eGFP", "KCNN2"],
                           "physical_size_um": {"X": 0.1, "Y": 0.1, "Z": 0.25}})
        inspected = {"path": str(self.root / "unused.tif"), "name": "source",
                     "series_count": 2, "series": series}
        self.controller._process_kind = "inspect"
        self.controller._inspect_buffer = json.dumps(inspected)
        self.controller._busy = True
        self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertIsNone(self.controller._series_index)
        with patch.object(self.controller, "_launch") as launch:
            self.controller.startAnalysis()
            launch.assert_not_called()
        self.assertEqual(list(self.paths.runs.iterdir()), [])
        self.controller.selectSeries(1)
        self.assertEqual(self.controller.channelRoles, ["DAPI", "eGFP", "KCNN2"])
        self.assertEqual(self.controller.imageInfo["name"], "source")
        self.assertEqual(self.controller.imageInfo["series_name"], "Image 2")
        self.assertIsNone(self.controller._time_index)
        self.controller.selectTime(1)
        self.assertEqual(self.controller._time_index, 1)
        self.assertEqual(self.controller.imageInfo["physical_size_um"], {"x": 0.1, "y": 0.1, "z": 0.25})
        self.controller.setChannelRole(1, "GFAP")
        self.assertEqual(self.controller.channelRoles, ["DAPI", "GFAP", "KCNN2"])

    def test_worker_environment_uses_actual_descriptor_root(self):
        descriptor = self.controller._runtime()
        environment = self.controller._environment(descriptor)
        self.assertEqual(environment.value("PROJECT_LEAP_COMPONENT_ROOT"), descriptor["root"])
        self.assertEqual(environment.value("CELLPOSE_LOCAL_MODELS_PATH"), descriptor["models"])
        self.assertEqual(environment.value("PYTHONDONTWRITEBYTECODE"), "1")
        self.assertEqual(environment.value("PROJECT_LEAP_FIJI_ROOT"), descriptor["fiji_root"])
        self.assertEqual(environment.value("PYTHONPATH").split(os.pathsep)[0], descriptor["engine"])

    def test_maintenance_never_starts_while_analysis_or_update_busy(self):
        with patch("leap_desktop.controller.MaintenanceTask") as task:
            self.controller._busy = True
            self.controller.installModule()
            self.controller.repairModule()
            task.assert_not_called()
            self.controller._busy = False
            self.controller._updater = Mock()
            self.controller._updater.status.return_value = {"session_in_progress": True}
            self.controller.installModule()
            task.assert_not_called()

    def test_declining_quit_keeps_active_task(self):
        self.controller._busy = True
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No), \
                patch.object(self.controller, "cancelAnalysis") as cancel, \
                patch.object(QApplication, "quit") as quit_app:
            self.controller.requestQuit()
            cancel.assert_not_called()
            quit_app.assert_not_called()
        self.assertTrue(self.controller.busy)

    def test_worker_crash_does_not_allow_maintenance_with_owned_session_alive(self):
        # This disposable Python process stands in for an owned Fiji session;
        # the test never discovers or signals any unrelated process.
        owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 start_new_session=True)
        try:
            run = self.paths.runs / "crashed-worker"
            run.mkdir()
            self.controller._run_dir = run
            self.controller._job = {"id": run.name, "name": "fixture", "status": "running",
                                    "created_at": "2026-10-17T00:00:00Z"}
            self.controller._busy = True
            self.controller._process_kind = "analysis"
            self.controller._use_lock = maintenance.component_use_lock(self.paths.home)
            self.controller._use_lock.__enter__()
            (run / "events.jsonl").write_text(json.dumps({
                "schema_version": 1, "run_id": run.name, "sequence": 1,
                "timestamp": "2026-10-17T00:00:00Z", "type": "process",
                "stage": "fiji_starting", "message": "owned fixture session",
                "pid": owned.pid, "pgid": owned.pid, "process_type": "fiji", "process_status": "started",
            }) + "\n")
            self.controller._process_finished(1, QProcess.ExitStatus.CrashExit)
            with patch("leap_desktop.controller.MaintenanceTask") as task:
                self.controller.installModule()
                self.assertTrue(owned.poll() is not None or not task.called,
                                "Maintenance started while the crashed worker's owned session was still alive")
            if owned.poll() is None:
                with self.assertRaises(maintenance.ComponentBusy):
                    with maintenance._component_lock(self.paths.home, exclusive=True):
                        self.fail("Component lock was released while its owned session remained alive")
        finally:
            if owned.poll() is None:
                owned.terminate()
            owned.wait(timeout=5)
            self.controller._release_lock()

    def test_cache_cleanup_preserves_source_results_and_failed_recovery(self):
        complete = self.paths.runs / "done"
        failed = self.paths.runs / "failed"
        for run in (complete, failed):
            for relative in ("cache", "workspace/Sample Image", "workspace/Result", "workspace/Analysis Package/Run State"):
                (run / relative).mkdir(parents=True)
                (run / relative / "sentinel").write_text(relative)
        external_source = self.root / "external-source.tif"
        external_source.write_bytes(b"untouched original")
        self.controller._jobs = [{"id": "done", "status": "completed"}, {"id": "failed", "status": "failed"}]
        for job in self.controller._jobs:
            paths.write_json(self.paths.runs / job["id"] / "job.json", job)
        self.controller.clearDisposableCache()
        deadline = time.monotonic() + 5
        while self.controller.maintenanceBusy and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.application.processEvents()
        while self.controller.maintenanceBusy and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.controller.maintenanceBusy)
        self.assertFalse((complete / "cache/sentinel").exists())
        self.assertFalse((complete / "workspace/Sample Image/sentinel").exists())
        for run in (complete, failed):
            self.assertTrue((run / "workspace/Result/sentinel").is_file())
            self.assertTrue((run / "workspace/Analysis Package/Run State/sentinel").is_file())
        self.assertTrue((failed / "cache/sentinel").is_file())
        self.assertTrue((failed / "workspace/Sample Image/sentinel").is_file())
        self.assertEqual(external_source.read_bytes(), b"untouched original")

    def test_application_installation_holds_component_lock_until_session_finishes(self):
        updater = Mock()
        updater.install_update.return_value = {"status": "downloading", "message": "fixture update"}
        updater.status.return_value = {"status": "available", "session_in_progress": False, "can_check": True}
        self.controller._updater = updater
        self.controller._update_candidate = {"available_build": "2", "feed_url": "https://release-fixture.invalid/appcast.xml", "host_build": "1"}
        self.controller._updates_phase = "waiting_app"
        try:
            self.controller._poll_updates()
            updater.install_update.assert_called_once_with("2", "https://release-fixture.invalid/appcast.xml", "1", installation_busy=True)
            updater.status.return_value = {"status": "downloading", "session_in_progress": True}
            self.controller._poll_updates()
            updater.set_installation_busy.assert_called_with(False)
            with self.assertRaises(maintenance.ComponentBusy):
                with maintenance.component_use_lock(self.paths.home):
                    self.fail("Scientific worker entered while Sparkle session owns components")
            updater.status.return_value = {"status": "failed", "message": "fixture session ended"}
            self.controller._poll_updates()
            self.assertIsNone(self.controller._update_lock)
            with maintenance.component_use_lock(self.paths.home):
                pass
        finally:
            if self.controller._update_lock:
                self.controller._update_lock.__exit__(None, None, None)
                self.controller._update_lock = None

    def test_application_installation_gate_waits_for_another_worker_lock(self):
        updater = Mock()
        updater.status.return_value = {"status": "downloading", "session_in_progress": True}
        self.controller._updater = updater
        self.controller._updates_phase = "app_install"
        with maintenance.component_use_lock(self.paths.home):
            self.controller._poll_updates()
            updater.set_installation_busy.assert_called_with(True)
        self.assertIsNone(self.controller._update_lock)
        try:
            self.controller._poll_updates()
            updater.set_installation_busy.assert_called_with(False)
            self.assertIsNotNone(self.controller._update_lock)
        finally:
            self.controller._finish_updates("fixture finished")

    def test_update_checks_can_run_while_analysis_holds_components(self):
        updater = Mock()
        updater.status.return_value = {"status": "idle", "feed_url": "https://release-fixture.invalid/appcast.xml"}
        self.controller._updater = updater
        self.controller._busy = True
        configuration = ReleaseConfiguration("https://release-fixture.invalid/catalog.json", "fixture-key")
        with maintenance.component_use_lock(self.paths.home), \
                patch.object(paths.AppPaths, "release_configuration", new_callable=PropertyMock, return_value=configuration), \
                patch("leap_desktop.controller.MaintenanceTask") as check:
            self.controller.checkUpdates()
        updater.check_for_updates.assert_called_once_with()
        self.assertEqual(check.call_args.args, (Controller._check_component_releases, self.paths.home))
        self.assertEqual(check.call_args.kwargs, {"configuration": configuration})
        self.assertIs(self.controller._update_check_thread, check.return_value)
        self.assertFalse(self.controller.maintenanceBusy)
        self.assertTrue(self.controller.busy)
        self.assertEqual(self.controller._updates_phase, "checking")
        self.assertIsNone(self.controller._update_lock)

    def test_unconfigured_update_click_has_no_side_effects(self):
        pending = self.paths.home / "updates/pending.json"
        pending.parent.mkdir(parents=True)
        pending.write_text('{"plan": {"id": "keep-pending-update"}}')
        before = pending.read_bytes()
        self.controller._update_status = "previous update status"
        self.controller._update_candidate = {"available_build": "18"}
        self.controller._update_plan = {"id": "previous-plan"}
        self.controller._image_info = {"path": "unchanged-source.tif"}
        changed = []
        self.controller.changed.connect(lambda: changed.append(True))
        for state in (None, {"status": "unconfigured", "feed_url": ""}, {"status": "unavailable"}):
            with self.subTest(state=state):
                updater = Mock(bundle_path=None) if state else None
                if updater:
                    updater.status.return_value = state
                self.controller._updater = updater
                with patch.object(paths.AppPaths, "release_configuration", new_callable=PropertyMock,
                                  return_value=ReleaseConfiguration()), \
                        patch("leap_desktop.controller.MaintenanceTask") as task:
                    self.controller.checkUpdates()
                task.assert_not_called()
                if updater:
                    updater.check_for_updates.assert_not_called()
                self.assertEqual(self.controller._updates_phase, "")
                self.assertEqual(self.controller._update_status, "previous update status")
                self.assertEqual(self.controller._update_candidate, {"available_build": "18"})
                self.assertEqual(self.controller._update_plan, {"id": "previous-plan"})
                self.assertIsNone(self.controller._update_check_thread)
                self.assertIsNone(self.controller._update_lock)
                self.assertEqual(self.controller.imageInfo, {"path": "unchanged-source.tif"})
                self.assertEqual(pending.read_bytes(), before)
                self.assertEqual(changed, [])

    def test_app_only_update_configuration_starts_existing_check(self):
        updater = Mock()
        updater.status.return_value = {"status": "idle", "feed_url": "https://release-fixture.invalid/appcast.xml"}
        self.controller._updater = updater
        with patch.object(paths.AppPaths, "release_configuration", new_callable=PropertyMock,
                          return_value=ReleaseConfiguration()), \
                patch("leap_desktop.controller.MaintenanceTask") as task:
            self.controller.checkUpdates()
        updater.check_for_updates.assert_called_once_with()
        task.return_value.start.assert_called_once_with()
        self.assertEqual(self.controller._updates_phase, "checking")

    def test_update_configuration_and_runtime_failures_remain_visible(self):
        cases = (
            (ReleaseConfiguration("https://release-fixture.invalid/catalog.json"),
             {"status": "unconfigured", "feed_url": ""}, "component signing key missing"),
            (ReleaseConfiguration(), {"status": "failed", "message": "Sparkle load failed"}, "Sparkle load failed"),
            (ReleaseConfiguration(), {"status": "unavailable", "message": "Sparkle bridge missing"}, "Sparkle bridge missing"),
            (ReleaseConfiguration(), {"status": "unconfigured", "feed_url": "https://release-fixture.invalid/appcast.xml"},
             "component signing key missing"),
        )
        for configuration, state, expected in cases:
            with self.subTest(state=state, configuration=configuration):
                updater = Mock(bundle_path=self.root / "Project Leap.app")
                updater.status.return_value = state
                self.controller._updater = updater
                with patch.object(paths.AppPaths, "release_configuration", new_callable=PropertyMock,
                                  return_value=configuration), \
                        patch("leap_desktop.controller.MaintenanceTask") as task:
                    self.controller.checkUpdates()
                task.return_value.start.assert_called_once_with()
                self.controller._update_components = {"status": "unconfigured", "updates": [],
                                                      "message": "component signing key missing"}
                self.controller._poll_updates()
                self.assertEqual(self.controller.updateStatusMessage, expected)
                self.assertEqual(self.controller._updates_phase, "")

    def _saved_running_job(self, kind):
        run = self.paths.runs / ("recovery-" + kind)
        run.mkdir()
        record = {"id": run.name, "name": "fixture source", "source": str(self.root / "source.tif"),
                  "status": "running" if kind == "analysis" else "importing",
                  "created_at": "2026-10-17T00:00:00Z", "worker": {"pid": 99999, "kind": kind},
                  "owned_processes": []}
        paths.write_json(run / "job.json", record)
        self.controller._load_jobs()
        return run, record

    def test_reconnect_live_analysis_then_load_committed_outcome(self):
        run, record = self._saved_running_job("analysis")
        try:
            with patch.object(self.controller, "_matches_process", return_value=True):
                self.controller._recover_live_job()
            self.assertTrue(self.controller.busy)
            self.assertEqual(self.controller._detached_pid, record["worker"]["pid"])
            with self.assertRaises(maintenance.ComponentBusy):
                with maintenance.component_maintenance_lock(self.paths.home):
                    self.fail("Recovered live worker lost its component use protection")
            results = run / "workspace/Result"
            results.mkdir(parents=True)
            (results / "IHC_2D_Analysis_Report.txt").write_text("committed fixture")
            paths.write_json(run / "outcome.json", {
                "schema_version": 1, "run_id": run.name, "status": "completed",
                "results_dir": str(results), "message": "saved", "artifacts": {}, "processes": [],
            })
            with patch.object(self.controller, "_matches_process", return_value=False):
                self.controller._poll()
            self.assertFalse(self.controller.busy)
            self.assertEqual(self.controller.status, "completed")
            self.assertEqual(json.loads((run / "job.json").read_text())["status"], "completed")
            self.assertEqual((results / "IHC_2D_Analysis_Report.txt").read_text(), "committed fixture")
            with maintenance.component_maintenance_lock(self.paths.home):
                pass
        finally:
            self.controller._release_lock()

    def test_reconnect_import_preserves_source_when_starting_engine(self):
        run, record = self._saved_running_job("import")
        try:
            with patch.object(self.controller, "_matches_process", return_value=True):
                self.controller._recover_live_job()
            paths.write_json(run / "import-result.json", {
                "status": "completed", "source": record["source"], "files": {}, "metadata": {},
            })
            with patch.object(self.controller, "_matches_process", return_value=False), \
                    patch.object(self.controller, "_launch") as launch:
                self.controller._poll()
            launch.assert_called_once()
            request = json.loads((run / "request.json").read_text())
            self.assertEqual(request["provenance"]["source"], record["source"])
            self.assertEqual(self.controller.status, "running")
        finally:
            self.controller._release_lock()

    def test_replayed_stale_process_event_cannot_signal_unrelated_session(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                     start_new_session=True)
        try:
            run, record = self._saved_running_job("analysis")
            (run / "events.jsonl").write_text(json.dumps({
                "schema_version": 1, "run_id": run.name, "sequence": 1,
                "timestamp": "2026-10-17T00:00:00Z", "type": "process",
                "stage": "cell_edit", "message": "stale PID fixture",
                "pid": unrelated.pid, "pgid": unrelated.pid,
                "process_type": "cell_edit", "process_status": "started",
            }) + "\n")
            with patch.object(self.controller, "_matches_process", side_effect=lambda pid, identity: pid == 99999):
                self.controller._recover_live_job()
            paths.write_json(run / "outcome.json", {
                "schema_version": 1, "run_id": run.name, "status": "completed",
                "results_dir": str(run / "workspace/Result"), "message": "saved",
                "artifacts": {}, "processes": [],
            })
            with patch.object(self.controller, "_matches_process", return_value=False):
                self.controller._poll()
            try:
                unrelated.wait(timeout=0.15)
            except subprocess.TimeoutExpired:
                pass
            else:
                self.fail("Replaying a stale process event terminated a session whose identity did not match the task")
        finally:
            if unrelated.poll() is None:
                unrelated.terminate()
            unrelated.wait(timeout=5)
            self.controller._release_lock()

    def test_worker_console_survives_abrupt_gui_process_exit(self):
        script = r'''
import json, os, sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
sys.path.insert(0, sys.argv[1])
from leap_desktop.paths import AppPaths
from leap_desktop.controller import Controller
app = QApplication([])
Controller._initial_status = lambda self: None
paths = AppPaths(Path(sys.argv[2]))
c = Controller(paths)
c._timer.stop()
c._run_dir = paths.runs / "log-survival"
c._run_dir.mkdir()
c._component_info = {"status":"ready", "runtime_descriptor":{
    "root":str(paths.home / "components"), "python":sys.executable,
    "engine":str(paths.home / "module-engine"),
    "site_packages":str(paths.home / "unused-site"), "models":str(paths.home / "unused-models")}}
c._launch("analysis", ["-c", "import time; time.sleep(0.3); print('after-gui-exit', flush=True)"])
if not c._process.waitForStarted(5000):
    raise RuntimeError(c._process.errorString())
print(json.dumps({"pid":int(c._process.processId()), "log":str(c._run_dir / "console.log")}), flush=True)
os._exit(0)
'''
        process = subprocess.run([sys.executable, "-B", "-c", script, str(ROOT / "app"),
                                  str(self.root / "abrupt-gui-home")],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        record = json.loads(process.stdout.strip())
        output = Path(record["log"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if output.exists() and "after-gui-exit" in output.read_text():
                break
            time.sleep(0.02)
        else:
            try:
                os.kill(record["pid"], 15)
            except ProcessLookupError:
                pass
            self.fail("Worker output did not survive the GUI's abrupt exit")

    def _module_states(self):
        second = {**modules.CATALOG[0], "id": "table-review", "name": "Table Review",
                  "input_page": "table-review", "needs_fiji": False, "entrypoint": "table_review.adapter",
                  "import_module": "table_review.importer"}
        catalog = patch.object(modules, "CATALOG", (*modules.CATALOG, second))
        catalog.start()
        self.addCleanup(catalog.stop)
        self.controller._module_states = {
            "project-leap-2d": {**self.controller._component_info, "id": "project-leap-2d", "installed": True},
            "table-review": {"id": "table-review", "installed": True, "status": "ready", "component_version": "2.0"},
        }

    def test_module_selection_routes_input_and_does_not_reuse_tiff_for_other_modules(self):
        self._module_states()
        self.controller._image_info = {"path": "previous.tif"}
        self.controller.setPage("maintenance")
        self.controller.selectModule("table-review")
        self.assertEqual(self.controller.page, "analysis")
        self.assertEqual(self.controller.activeModuleId, "table-review")
        self.assertEqual(self.controller.activeModuleInputPage, "table-review")
        self.assertEqual(self.controller.imageInfo, {})
        with patch.object(self.controller, "_launch") as launch:
            self.controller.inspectImage(str(self.root / "unknown.csv"))
            self.controller.startAnalysis()
        launch.assert_not_called()
        self.assertEqual(self.controller._module_faults, {})

    def test_running_task_keeps_its_module_context_while_navigation_changes(self):
        self._module_states()
        self.controller._busy = True
        self.controller._worker_context = WorkerContext("project-leap-2d", None)
        self.controller._image_info = {"path": "running-source.tif"}
        self.controller.selectModule("table-review")
        self.assertEqual(self.controller.activeModuleId, "table-review")
        self.assertEqual(self.controller._worker_context.module_id, "project-leap-2d")
        self.assertTrue(self.controller.busy)
        self.assertFalse(self.controller.activeModuleBusy)
        self.assertEqual(self.controller.imageInfo, {})
        self.controller.selectModule("project-leap-2d")
        self.assertEqual(self.controller.activeModuleId, "project-leap-2d")
        self.assertEqual(self.controller.imageInfo["path"], "running-source.tif")

    def test_repair_dispatches_requested_module_without_changing_analysis_context(self):
        self._module_states()
        configuration = ReleaseConfiguration("https://release-fixture.invalid/catalog.json", "fixture-key")
        with patch.object(paths.AppPaths, "release_configuration", new_callable=PropertyMock, return_value=configuration), \
                patch.object(self.paths, "package_source") as source, \
                patch("leap_desktop.controller.MaintenanceTask") as task:
            self.controller.repairModule("table-review")
        source.assert_not_called()
        self.assertEqual(task.call_args.args[0], maintenance.repair_component)
        self.assertIsNone(task.call_args.args[2])
        self.assertEqual(task.call_args.kwargs, {"module_id": "table-review", "configuration": configuration})
        self.assertEqual(self.controller.activeModuleId, "project-leap-2d")
        self.assertEqual(self.controller._maintenance_module_id, "table-review")

    def test_unconfigured_install_passes_explicit_unconfigured_state_without_file_picker(self):
        self._module_states()
        configuration = ReleaseConfiguration()
        with patch.object(paths.AppPaths, "release_configuration", new_callable=PropertyMock, return_value=configuration), \
                patch.object(self.paths, "package_source", return_value=None), \
                patch("leap_desktop.controller.QFileDialog.getExistingDirectory", return_value="") as choose, \
                patch("leap_desktop.controller.MaintenanceTask") as task:
            self.controller.installModule("project-leap-2d")
        choose.assert_not_called()
        self.assertEqual(task.call_args.args, (maintenance.install_component, self.paths.home, None))
        self.assertEqual(task.call_args.kwargs, {"module_id": "project-leap-2d", "configuration": configuration})
        self.assertEqual(self.controller._module_faults, {})

    def test_failed_repair_retains_navigation_and_retries_signed_release_without_file_picker(self):
        self._module_states()
        self.controller._maintenance_operation = "repair"
        self.controller._maintenance_module_id = "project-leap-2d"
        with patch.object(self.controller, "_refresh_modules"):
            self.controller._maintenance_done({"status": "failed", "message": "manifest mismatch"})
        item = next(item for item in self.controller.installedModules if item["id"] == "project-leap-2d")
        self.assertNotIn("sourceSelectionRequired", item)
        self.assertEqual(item["message"], "manifest mismatch")
        configuration = ReleaseConfiguration("https://release-fixture.invalid/catalog.json", "fixture-key")
        with patch.object(paths.AppPaths, "release_configuration", new_callable=PropertyMock, return_value=configuration), \
                patch.object(self.paths, "package_source") as source, \
                patch("leap_desktop.controller.QFileDialog.getExistingDirectory") as choose, \
                patch("leap_desktop.controller.MaintenanceTask") as task:
            self.controller.repairModule("project-leap-2d")
        source.assert_not_called()
        choose.assert_not_called()
        self.assertIsNone(task.call_args.args[2])
        self.assertIs(task.call_args.kwargs["configuration"], configuration)

    def test_available_module_shows_catalog_version_before_installation(self):
        self.controller._module_states = {"project-leap-2d": {
            "id": "project-leap-2d", "installed": False, "status": "not_installed", "component_version": ""}}
        self.assertEqual(self.controller.availableModules[0]["version"], modules.get_module()["version"])

    def test_module_information_opens_the_selected_github_url(self):
        with patch("leap_desktop.controller.QDesktopServices.openUrl") as open_url:
            self.controller.openModuleGitHub(modules.DEFAULT_MODULE_ID)
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.args[0].toString(), modules.get_module()["github_url"])

    def test_module_information_without_url_or_known_module_does_nothing(self):
        with patch("leap_desktop.controller.QDesktopServices.openUrl") as open_url:
            self.controller.openModuleGitHub(modules.COUNTING_MODULE_ID)
            self.controller.openModuleGitHub("unknown-module")
        open_url.assert_not_called()

    def test_legacy_jobs_and_uninstalled_module_history_remain_grouped(self):
        self._module_states()
        self.controller._module_states["table-review"]["installed"] = False
        for identifier, module_id in (("legacy", None), ("table-old", "table-review")):
            root = self.paths.runs / identifier
            root.mkdir()
            job = {"id": identifier, "name": identifier, "status": "completed"}
            if module_id:
                job["module_id"] = module_id
            paths.write_json(root / "job.json", job)
        self.controller._load_jobs()
        groups = {item["moduleId"]: item for item in self.controller.jobGroups}
        self.assertTrue(groups["project-leap-2d"]["installed"])
        self.assertFalse(groups["table-review"]["installed"])
        self.assertEqual(groups["project-leap-2d"]["jobs"][0]["module_id"], "project-leap-2d")
        self.assertEqual(groups["table-review"]["jobs"][0]["id"], "table-old")

    def test_new_job_records_module_and_version_without_changing_scientific_arguments(self):
        self.controller._image_info = {"path": str(self.root / "source.tif"), "name": "source.tif"}
        self.controller._channel_roles = ["DAPI", "GFAP", "KCNN2"]
        self.controller._series_index = self.controller._time_index = 0
        try:
            with patch.object(self.controller, "_launch"):
                self.controller.startAnalysis()
                self.controller._start_engine({"status": "completed"})
            record = json.loads((self.controller._run_dir / "job.json").read_text())
            request = json.loads((self.controller._run_dir / "request.json").read_text())
            self.assertEqual(record["module_id"], "project-leap-2d")
            self.assertEqual(record["module_version"], "1.0.1")
            self.assertEqual(request["engine_args"], [])
            self.assertNotIn("auto_measure", request)
        finally:
            self.controller._release_lock()

    def test_failed_new_launch_preflight_preserves_previous_completed_job(self):
        run = self.paths.runs / "previous-success"
        run.mkdir()
        previous = {"id": run.name, "name": "saved", "status": "completed", "module_id": "project-leap-2d"}
        paths.write_json(run / "job.json", previous)
        original = (run / "job.json").read_bytes()
        self.controller._job, self.controller._run_dir = previous, run
        self.controller._image_info = {"path": str(self.root / "source.tif"), "name": "source.tif"}
        self.controller._channel_roles = ["DAPI", "GFAP", "KCNN2"]
        self.controller._series_index = self.controller._time_index = 0
        self.controller._component_info = {"status": "damaged", "runtime_descriptor": None}
        self.controller.startAnalysis()
        self.assertEqual((run / "job.json").read_bytes(), original)
        self.assertIsNone(self.controller._job)

    def test_historical_result_actions_are_bound_to_job_id_and_include_three_pngs(self):
        jobs = []
        for identifier in ("first", "second"):
            result = self.paths.runs / identifier / "workspace/Result"
            result.mkdir(parents=True)
            for region in ("Whole", "Soma", "Processes"):
                (result / (region + ".png")).write_bytes(b"fixture")
            jobs.append({"id": identifier, "name": identifier, "status": "completed", "results_dir": str(result)})
        self.controller._jobs = jobs
        self.controller.selectJob("first")
        first_image = self.controller.resultInfo["images"][0]["path"]
        self.assertEqual(len(self.controller.resultInfo["images"]), 3)
        self.controller.selectJob("second")
        with patch("leap_desktop.controller.QDesktopServices.openUrl") as open_url:
            self.controller.openArtifactForJob("second", first_image)
            open_url.assert_not_called()
            self.controller.openResultsForJob("first")
            self.assertEqual(open_url.call_args.args[0].toLocalFile(), jobs[0]["results_dir"])
        self.controller.selectJob("second")
        self.assertEqual(self.controller.selectedJobId, "")
        self.assertEqual(self.controller.resultInfo, {})

    def test_migrated_results_resolve_old_saved_paths_without_rewriting_provenance(self):
        old = self.root / "old-app-home"
        self.paths.previous_homes = [str(old)]
        actual = self.paths.runs / "migrated" / "workspace/Result"
        actual.mkdir(parents=True)
        (actual / "Whole.png").write_bytes(b"fixture")
        saved_path = str(old / "runs/migrated/workspace/Result")
        job = {"id": "migrated", "name": "migrated", "status": "completed", "results_dir": saved_path}
        self.controller._jobs = [job]
        self.controller.selectJob("migrated")
        self.assertEqual(self.controller.resultInfo["results_dir"], str(actual))
        self.assertEqual(self.controller.resultInfo["images"][0]["path"], str(actual / "Whole.png"))
        self.assertEqual(job["results_dir"], saved_path)

    def test_console_append_does_not_emit_global_changed_or_reset(self):
        changed, appended, reset = [], [], []
        self.controller.changed.connect(lambda: changed.append(True))
        self.controller.logAppended.connect(appended.append)
        self.controller.logReset.connect(reset.append)
        self.controller._process_kind = "analysis"
        self.controller._consume_text("line one\n")
        self.controller._consume_text("line two\n")
        self.assertEqual(changed, [])
        self.assertEqual(appended, ["line one\n", "line two\n"])
        self.assertEqual(reset, [])
        self.assertEqual(self.controller.logText, "line one\nline two\n")

    def test_success_notice_expires_but_error_notice_remains(self):
        self.controller._notice("repair succeeded", "success")
        self.controller._clear_success_notice()
        self.assertEqual(self.controller.maintenanceMessage, "")
        self.controller._notice("repair failed", "error")
        self.controller._clear_success_notice()
        self.assertEqual(self.controller.maintenanceMessage, "repair failed")

    def test_intact_repair_card_resets_on_return_without_clearing_fault_or_toast(self):
        self._module_states()
        identifier = modules.DEFAULT_MODULE_ID
        self.controller._module_states[identifier]["message"] = "组件已安装。"
        installed = [dict(state) for state in self.controller._module_states.values()]
        self.controller._mark_module_fault("previous runtime failure")
        fault_bytes = (self.paths.home / "module-health.json").read_bytes()
        result_message = "完整校验通过，组件无需修复。"
        for select_module in (False, True):
            with self.subTest(select_module=select_module), \
                    patch("leap_desktop.controller.maintenance.list_statuses", return_value=installed) as refresh, \
                    patch("leap_desktop.controller.QTimer.singleShot"):
                self.controller._activate_module(identifier)
                self.controller.setPage("maintenance")
                self.controller._maintenance_operation = "repair"
                self.controller._maintenance_module_id = identifier
                self.controller._maintenance_done({"status": "intact", "message": result_message})
                refresh.assert_called_once_with(self.paths.home)
                self.assertEqual(self.controller._module_states[identifier]["message"], result_message)
                self.controller.setPage("maintenance")
                self.assertEqual(self.controller._module_states[identifier]["message"], result_message)
                if select_module:
                    self.controller.selectModule("table-review")
                else:
                    self.controller.setPage("settings")
                self.controller.setPage("maintenance")
                self.assertEqual(self.controller._module_states[identifier], installed[0])
                self.assertEqual(self.controller._maintenance_message, result_message)
                self.assertTrue(self.controller._notice_timer.isActive())
                self.assertEqual((self.paths.home / "module-health.json").read_bytes(), fault_bytes)
                self.assertTrue(self.controller.installedModules[0]["hasError"])
                if not select_module:
                    self.assertIs(self.controller._component_info, self.controller._module_states[identifier])
                refresh.assert_called_once_with(self.paths.home)

    def test_intact_repair_card_outlives_toast_until_page_return(self):
        self._module_states()
        identifier = modules.DEFAULT_MODULE_ID
        self.controller._module_states[identifier]["message"] = "组件已安装。"
        installed = [dict(state) for state in self.controller._module_states.values()]
        self.controller.setPage("maintenance")
        self.controller._maintenance_operation = "repair"
        self.controller._maintenance_module_id = identifier
        with patch("leap_desktop.controller.maintenance.list_statuses", return_value=installed), \
                patch("leap_desktop.controller.QTimer.singleShot"):
            self.controller._maintenance_done({"status": "intact", "message": "verification succeeded"})
        self.controller._clear_success_notice()
        self.assertEqual(self.controller.maintenanceMessage, "")
        self.assertEqual(self.controller._module_states[identifier]["message"], "verification succeeded")
        self.controller.setPage("analysis")
        self.controller.setPage("maintenance")
        self.assertEqual(self.controller._module_states[identifier]["message"], "组件已安装。")

    def test_page_return_preserves_newer_module_failure_or_busy_state(self):
        self._module_states()
        identifier = modules.DEFAULT_MODULE_ID
        for status, message, busy in (("ready", "repair failed", False),
                                      ("damaged", "verification succeeded", False),
                                      ("repairing", "checking files", True),
                                      ("ready", "verification succeeded", True)):
            with self.subTest(status=status, message=message, busy=busy):
                state = {**self.controller._module_states[identifier], "status": status, "message": message}
                self.controller._module_states[identifier] = state
                self.controller._module_success_messages[identifier] = ("verification succeeded", "组件已安装。")
                self.controller._maintenance_busy = busy
                self.controller._maintenance_progress = .4
                self.controller.setPage("settings")
                self.controller.setPage("maintenance")
                self.assertEqual(self.controller._module_states[identifier], state)
                self.assertEqual(self.controller.maintenanceBusy, busy)
                self.assertEqual(self.controller.maintenanceProgress, .4)

    def test_new_component_operation_discards_previous_success_message(self):
        self._module_states()
        identifier = modules.DEFAULT_MODULE_ID
        self.controller._module_success_messages[identifier] = ("verification succeeded", "组件已安装。")
        with patch("leap_desktop.controller.MaintenanceTask"):
            self.controller.repairModule(identifier)
        self.assertNotIn(identifier, self.controller._module_success_messages)
        self.assertEqual(self.controller._module_states[identifier]["status"], "repairing")

    def test_log_capacity_trims_incrementally_using_qml_text_positions(self):
        trimmed, appended, reset = [], [], []
        self.controller.logTrimmed.connect(trimmed.append)
        self.controller.logAppended.connect(appended.append)
        self.controller.logReset.connect(reset.append)
        self.controller._log_text = "😀" + "a" * 119999
        self.controller._append_log("x")
        self.assertEqual(trimmed, [2])
        self.assertEqual(appended, ["x"])
        self.assertEqual(reset, [])
        self.assertEqual(self.controller.logText, "a" * 119999 + "x")
        self.controller._append_log("z" * 130000)
        self.assertEqual(trimmed[-1], 120000)
        self.assertEqual(appended[-1], "z" * 120000)
        self.assertEqual(self.controller.logText, "z" * 120000)

    def test_input_rejection_and_cancellation_do_not_set_component_fault(self):
        for index, (status, code) in enumerate((("cancelled", "user_cancelled"), ("failed", "engine_check_stopped"))):
            run = self.paths.runs / ("outcome-" + str(index))
            run.mkdir()
            self.controller._run_dir = run
            self.controller._job = {"id": run.name, "name": "fixture", "module_id": "project-leap-2d"}
            self.controller._process_kind = "analysis"
            paths.write_json(run / "outcome.json", {"status": status, "error_code": code, "message": "fixture"})
            self.controller._process_finished(1, QProcess.ExitStatus.NormalExit)
            self.assertEqual(self.controller._module_faults, {})

    def test_requested_cancellation_without_terminal_record_never_marks_module_broken(self):
        for kind in ("import", "analysis"):
            run = self.paths.runs / ("cancel-" + kind)
            run.mkdir()
            self.controller._run_dir = run
            self.controller._job = {"id": run.name, "name": "fixture", "module_id": "project-leap-2d"}
            self.controller._status = "cancelling"
            self.controller._process_kind = kind
            self.controller._busy = True
            self.controller._process_finished(130, QProcess.ExitStatus.CrashExit)
            self.assertEqual(self.controller.status, "cancelled")
            self.assertFalse(self.controller.busy)
            self.assertEqual(self.controller._module_faults, {})

    def test_cancelled_import_cannot_start_engine_even_if_copy_just_completed(self):
        run = self.paths.runs / "cancel-import-after-copy"
        run.mkdir()
        self.controller._run_dir = run
        self.controller._job = {"id": run.name, "name": "fixture", "module_id": "project-leap-2d"}
        self.controller._status, self.controller._process_kind = "cancelling", "import"
        paths.write_json(run / "import-result.json", {"status": "completed"})
        with patch.object(self.controller, "_start_engine") as start:
            self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        start.assert_not_called()
        self.assertEqual(self.controller.status, "cancelled")

    def test_inspection_protocol_failure_sets_fault_but_reported_input_error_does_not(self):
        self.controller._process_kind = "inspect"
        self.controller._inspect_buffer = json.dumps({"status": "failed", "message": "invalid TIFF"})
        self.controller._process_finished(1, QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.controller._module_faults, {})
        self.controller._process_kind = "inspect"
        self.controller._inspect_buffer = "ModuleNotFoundError: No module named 'numpy'"
        self.controller._process_finished(1, QProcess.ExitStatus.NormalExit)
        self.assertIn("project-leap-2d", self.controller._module_faults)
        self.assertIn("ModuleNotFoundError", self.controller.logText)

    def test_actual_completed_outcome_clears_previous_runtime_fault(self):
        run = self.paths.runs / "healthy-after-retry"
        run.mkdir()
        self.controller._run_dir = run
        self.controller._job = {"id": run.name, "name": "fixture", "module_id": "project-leap-2d"}
        self.controller._process_kind, self.controller._status = "analysis", "running"
        self.controller._mark_module_fault("previous transient failure")
        paths.write_json(run / "outcome.json", {"status": "completed", "message": "saved"})
        self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.controller.status, "completed")
        self.assertEqual(self.controller._module_faults, {})

    def test_technical_failure_sets_fault_and_successful_repair_clears_it(self):
        self._module_states()
        self.controller._mark_module_fault("broken executable")
        self.assertTrue(self.controller.installedModules[0]["hasError"])
        self.controller._maintenance_operation = "repair"
        self.controller._maintenance_module_id = "project-leap-2d"
        with patch.object(self.controller, "_refresh_modules"):
            self.controller._maintenance_done({"status": "ready", "message": "repaired"})
        self.assertFalse(self.controller.installedModules[0]["hasError"])
        self.assertEqual(json.loads((self.paths.home / "module-health.json").read_text()), {})

    def test_migration_requests_restart_only_after_confirmed_success(self):
        seen = []
        self.controller.restartRequested.connect(seen.append)
        self.controller._maintenance_operation = "migration"
        self.controller._maintenance_done({"status": "failed", "message": "copy failed"})
        self.assertEqual(seen, [])
        self.controller._maintenance_done({"status": "migrated", "message": "verified",
                                           "restart_required": True, "data_dir": str(self.root / "new-home")})
        self.assertEqual(seen, [str(self.root / "new-home")])

    def test_language_remains_editable_during_analysis_and_installation_but_not_migration(self):
        initial = self.controller.language
        alternative = "zh" if initial == "en" else "en"
        self.controller._busy = True
        self.assertTrue(self.controller.languageEditable)
        self.controller.setLanguage(alternative)
        self.assertEqual(self.controller.language, alternative)
        self.controller._busy = False
        self.controller._maintenance_busy = True
        self.controller._maintenance_operation = "install"
        self.assertTrue(self.controller.languageEditable)
        self.controller.setLanguage(initial)
        self.assertEqual(self.controller.language, initial)
        settings = self.paths.home / "settings.json"
        before = settings.read_bytes()
        self.controller._maintenance_operation = "migration"
        self.assertFalse(self.controller.languageEditable)
        self.controller.setLanguage(alternative)
        self.assertEqual(self.controller.language, initial)
        self.assertEqual(settings.read_bytes(), before)
        self.controller._maintenance_busy = False
        self.assertTrue(self.controller.languageEditable)

    def test_maintenance_result_does_not_release_gate_until_thread_exits(self):
        seen = []

        def operation(*, progress):
            progress(1.0, "verified")
            return {"status": "migrated", "message": "verified", "restart_required": True,
                    "data_dir": str(self.root / "new-home")}

        self.controller._run_maintenance(operation, operation="migration")
        task = self.controller._maintenance_thread
        self.controller.restartRequested.connect(lambda path: seen.append((path, task.isRunning())))
        deadline = time.monotonic() + 5
        while self.controller.maintenanceBusy and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.005)
        self.assertFalse(self.controller.maintenanceBusy)
        self.assertEqual(seen, [(str(self.root / "new-home"), False)])

    def test_uninstall_mode_maps_to_helper_and_only_scheduled_result_quits(self):
        with patch("leap_desktop.lifecycle.schedule_uninstall", return_value={"status": "blocked", "message": "busy"}) as schedule, \
                patch.object(QApplication, "quit") as quit_app:
            self.controller.requestUninstall(False)
            schedule.assert_called_once_with(self.paths, keep_results=True)
            quit_app.assert_not_called()
            schedule.return_value = {"status": "scheduled", "message": "scheduled"}
            self.controller.requestUninstall(True)
            self.assertEqual(schedule.call_args.kwargs, {"keep_results": False})
            quit_app.assert_called_once()


class ApplicationPathContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="leap-home-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_protected_empty_subdirectories_are_rejected_before_creation(self):
        fake_user = self.root / "user"
        protected = [fake_user / "Desktop/Batch 1/new-empty-app",
                     fake_user / "Applications/Project Leap 2D Support/new-empty-app",
                     fake_user / "Library/Caches/IHC2DAnalysis/new-empty-app"]
        with patch.object(paths.Path, "home", return_value=fake_user):
            for destination in protected:
                with self.subTest(destination=str(destination)):
                    with self.assertRaises(ValueError):
                        paths.ensure_app_home(destination)
                    self.assertFalse(destination.exists())

    def test_marker_schema_error_is_explicit(self):
        for index, marker in enumerate(({}, [], {"application": paths.APPLICATION_ID, "schema_version": 99})):
            root = self.root / str(index)
            root.mkdir()
            (root / "application.json").write_text(json.dumps(marker))
            with self.assertRaisesRegex(ValueError, "不属于"):
                paths.ensure_app_home(root)

    def test_managed_runs_and_cache_cannot_be_external_symlinks(self):
        for name in ("runs", "cache"):
            home = paths.ensure_app_home(self.root / ("home-" + name))
            external = self.root / ("external-" + name)
            external.mkdir()
            sentinel = external / "sentinel"
            sentinel.write_text("external data")
            (home / name).symlink_to(external, target_is_directory=True)
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    paths.AppPaths(home)
                self.assertEqual(sentinel.read_text(), "external data")

    def test_frozen_app_version_comes_from_its_actual_bundle(self):
        bundle = self.root / "中文 Project Leap.app"
        contents = bundle / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        executable = contents / "MacOS/Project Leap"
        executable.write_bytes(b"fixture, never executed")
        with (contents / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleShortVersionString": "0.2.7", "CFBundleVersion": "17"}, stream)
        with patch.object(version.sys, "frozen", True, create=True), \
                patch.object(version.sys, "executable", str(executable)):
            self.assertEqual(version.application_version(), "0.2.7")


if __name__ == "__main__":
    unittest.main()
