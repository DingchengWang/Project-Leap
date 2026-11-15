"""Observational details, bounded history reads, and session ownership."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import PropertyMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from leap_desktop.run_details import (LOG_LIMIT, TEXT_READ_LIMIT, JSON_READ_LIMIT, MAX_LINE, RunDetails,
                                     ProgressReader, PROGRESS_READ_LIMIT, load_run_details)
from leap_desktop.controller import WorkerContext
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QProcess
from leap_desktop.paths import write_json
from test_counting_controller import CountingControllerFixture


class RunDetailsDataTests(unittest.TestCase):
    def test_only_matching_run_and_module_can_supply_model(self):
        details = RunDetails({"id": "one", "module_id": "cell-counting"})
        event = {"type": "stage", "run_id": "other", "model": {"name": "wrong", "device": "mps"}}
        self.assertFalse(details.event(event))
        event.update(run_id="one", module_id="project-leap-2d")
        self.assertFalse(details.event(event))
        self.assertEqual(details.snapshot()["rows"], [])
        event.update(module_id="cell-counting", model={"name": "actual", "device": "cpu", "parameters": {"normalize": False}})
        self.assertTrue(details.event(event))
        rows = {item["label"]: item["value"] for item in details.snapshot()["rows"]}
        self.assertEqual(rows["模型"], "actual")
        self.assertEqual(rows["实际推理设备"], "cpu")
        self.assertEqual(rows["参数 · normalize"], "false")

    def test_analysis_preserves_parallel_timing_and_does_not_sum_routes(self):
        details = RunDetails({"id": "one", "module_id": "cell-counting"})
        details.analysis({"run_id": "one", "model": {"name": "recorded"}, "timings": {
            "nuclear_route_seconds": 10.0, "body_route_seconds": 8.0, "total_seconds": 12.0,
            "route_times_overlap_and_must_not_be_added": True}})
        snapshot = details.snapshot()
        self.assertEqual(snapshot["elapsedSeconds"], 12.0)
        self.assertIn("不能相加", str(snapshot["rows"]))
        self.assertEqual(len(snapshot["timings"]), 3)
        details.freeze()
        self.assertEqual(details.snapshot()["elapsedSeconds"], 12.0)

    def test_incremental_console_retains_actual_parameters_and_bounded_partial_line(self):
        details = RunDetails({"id": "one", "module_id": "project-leap-2d"})
        details.console("[elapsed=1 s] Cellpose-SAM inference started | device=m")
        self.assertFalse(details.rows)
        details.console("ps | max_side=1024 | diameter=30 | cellprob=0.0\n")
        self.assertEqual(details.rows["实际推理设备"], "mps")
        details.console("  Candidate morphology/postprocess worker compute total: 2.4 s; median=1.2 s\n")
        self.assertEqual(details.timings["Candidate morphology/postprocess worker compute total"]["seconds"], 2.4)
        details.console("X" * (MAX_LINE * 3))
        self.assertLessEqual(len(details.partial_line), MAX_LINE)
        self.assertTrue(details.truncated)
        details.console("remaining\nwarning: retrying with smaller batch\n")
        self.assertEqual(len(details.warnings), 1)

    def test_legacy_history_never_fabricates_missing_metrics_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = {"id": "old", "module_id": "cell-counting", "status": "completed"}
            empty = load_run_details(root, job)
            self.assertTrue(empty["available"])
            self.assertEqual(empty["resources"], {})
            self.assertIsNone(empty["elapsedSeconds"])
            (root / "counting").mkdir()
            write_json(root / "counting/analysis.json", {"run_id": "old", "model": {"name": "legacy", "device": "cpu"},
                        "timings": {"total_seconds": 3.5}})
            log = root / "console.log"
            log.write_text("raw warning: no masks\n")
            before = log.read_bytes()
            loaded = load_run_details(root, job)
            self.assertEqual(log.read_bytes(), before)
            self.assertEqual(loaded["elapsedSeconds"], 3.5)
            self.assertEqual(loaded["resources"], {})
            self.assertTrue(loaded["history"])
            self.assertIn("legacy", str(loaded["rows"]))

    def test_large_history_keeps_bounded_tail_and_initial_model_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "console.log"
            with log.open("wb") as handle:
                handle.write(b"Cellpose-SAM inference started | device=mps | max_side=1024\n")
                handle.write(b"ordinary output\n" * (TEXT_READ_LIMIT // 7))
                handle.write(b"final diagnostic marker\n")
            original_size = log.stat().st_size
            result = load_run_details(root, {"id": "old", "module_id": "project-leap-2d"})
            self.assertLessEqual(len(result["logText"]), LOG_LIMIT)
            self.assertEqual(result["logText"], "")
            self.assertFalse(result["logTruncated"])
            self.assertNotIn("ordinary output", str(result["summarySections"]))
            self.assertTrue(result["detailsTruncated"])
            self.assertIn("mps", str(result["rows"]))
            self.assertEqual(log.stat().st_size, original_size)

    def test_live_metadata_is_announced_once_and_history_has_no_resources(self):
        details = RunDetails({"id": "one", "module_id": "cell-counting"}, live=True)
        model = {"name": "cpsam_v2", "version": "4.2.1.1", "device": "mps", "parameters": {"batch_size": 8}}
        details.model(model)
        self.assertIn(("Cellpose 软件包版本", "4.2.1.1"), details.take_live_updates())
        details.model(model)
        self.assertEqual(details.take_live_updates(), [])
        details.model({**model, "device": "cpu"})
        self.assertEqual(details.take_live_updates(), [("实际推理设备", "cpu")])
        details.current_resources = {"mode": "current", "cpu_percent": 42, "cpu_temperature_c": 55}
        details.freeze()
        self.assertEqual(details.snapshot()["resources"]["mode"], "stopped")
        self.assertEqual(details.snapshot(history=True)["resources"], {})
        self.assertNotIn("cpu_percent", str(details.persisted()))

    def test_freeze_preserves_incomplete_final_timing_until_console_drain(self):
        details = RunDetails({"id": "one", "module_id": "project-leap-2d"}, live=True)
        text = "padding\n" * 8190 + "Complete pipeline elapsed: 77.125 s\n"
        details.console(text[:65536])
        self.assertTrue(details.partial_line)
        details.freeze()
        details.console(text[65536:], final=True)
        self.assertEqual(details.snapshot()["elapsedSeconds"], 77.125)
        self.assertEqual(details.timings["Complete pipeline elapsed"]["seconds"], 77.125)

    def test_progress_reader_is_incremental_bounded_and_waits_for_complete_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "model-progress.jsonl"
            reader = ProgressReader()
            path.write_bytes(b'{"window_id":1')
            self.assertEqual(reader.read(root), [])
            with path.open("ab") as stream:
                stream.write(b'}\n')
            self.assertEqual(reader.read(root), [(path.name, {"window_id": 1})])
            self.assertEqual(reader.read(root), [])
            with path.open("ab") as stream:
                stream.write(b'{"window_id":2}\n' * 10000)
            previous = reader.offsets[path.name]
            reader.read(root)
            self.assertLessEqual(reader.offsets[path.name] - previous, PROGRESS_READ_LIMIT)
            self.assertTrue(reader.pending(root))

    def test_history_collects_existing_sample_report_and_confirmation_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "counting").mkdir()
            revision = root / "results/revision-003"
            revision.mkdir(parents=True)
            job = {"id": "one", "module_id": "cell-counting", "name": "sample.tif", "status": "completed",
                   "app_version": "1.2", "module_version": "2.3", "results_dir": str(revision),
                   "resource_summary": {"gpu_percent": 90}}
            write_json(root / "counting/analysis.json", {"run_id": "one", "width": 40, "height": 30, "z_count": 4,
                "calibration_sources": {"X": "OME", "Y": "OME", "Z": "OME"}, "physical_size_um": {"X": .2, "Y": .2, "Z": .5},
                "auto_count": 2, "pending_count": 1, "excluded_count": 0, "source_dtype": "uint16",
                "model": {"name": "cpsam_v2", "version": "4.2.1.1", "device": "mps"},
                "timings": {"total_seconds": 5.5}})
            write_json(revision / "Cell_Count_Session.json", {"run_id": "one", "count": 3, "revision": 3,
                "pending_count": 0, "excluded_count": 0, "deleted_count": 1, "confirmed_at": "2026-11-15"})
            (root / "console.log").write_text('Traceback (most recent call last):\n  File "/tmp/debug.py", line 42\nordinary raw output\n')
            snapshot = load_run_details(root, job)
            sections = {s["title"]: {r["label"]: r["value"] for r in s["rows"]} for s in snapshot["summarySections"]}
            self.assertEqual(sections["任务与软件"]["Cellpose 软件包版本"], "4.2.1.1")
            self.assertEqual(sections["样本与通道"]["图像尺寸"], "40 × 30 px")
            self.assertEqual(sections["审核与结果"]["已确认数量"], "3")
            self.assertEqual(sections["审核与结果"]["确认版本"], "3")
            self.assertEqual(snapshot["resources"], {})
            self.assertEqual(snapshot["logText"], "")
            self.assertNotIn("debug.py", str(sections))
            self.assertNotIn("ordinary raw output", str(sections))

    def test_request_shows_actual_source_channels_as_user_numbers(self):
        details = RunDetails({"id": "one", "module_id": "cell-counting"})
        details.request({"run_id": "one", "dapi_channel": 2, "support_channel": 0,
                         "display_channels": [2, 0], "channel_roles": ["GFAP", "Other", "DAPI"],
                         "series_index": 0, "time_index": 1})
        self.assertEqual(details.rows["DAPI 源通道"], "C3")
        self.assertEqual(details.rows["辅助源通道"], "C1")
        self.assertEqual(details.rows["显示源通道"], "C3, C1")
        self.assertEqual(details.rows["源通道角色"], "C1: GFAP, C2: Other, C3: DAPI")

    def test_fluorescence_history_uses_report_and_import_metadata_without_debug_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = root / "workspace/Result"
            result.mkdir(parents=True)
            write_json(root / "runtime_provenance.json", {"run_id": "one", "engine": {"version": "1.5"},
                "request_provenance": {"app_version": "2.1", "component_version": "3.2"},
                "models": {"cellpose": {"path": "/internal/runtime/models/cpsam_v2", "sha256": "abc123"},
                           "instanseg": {"path": "/internal/runtime/models/nuclei.pt", "sha256": "def456"}}})
            write_json(root / "import-result.json", {"run_id": "one", "elapsed_seconds": 1.25,
                "metadata": {"axes": "ZCYX", "shape": [20, 3, 40, 50], "dtype": "uint16", "series_index": 0,
                             "time_index": 0, "channel_roles": ["DAPI", "eGFP", "KCNN2"],
                             "physical_size_um": {"X": .2, "Y": .2, "Z": .5}, "calibration_sources": {"Z": "OME"}}})
            (result / "IHC_2D_Analysis_Report.txt").write_text(
                "Astrocyte Profile: mature\nZ Range: slices 11-29 (1-based inclusive)\n"
                "Actual Z Width: 9.5 um\nFinal ROI Count: 12\nManual Review Used: True\n"
                "Candidate Stage Wall Time: 10.0 s\nCandidate Worker Compute Total: 30.0 s\n"
                "\nCompartment Parameters:\n  soma_limit_um: 3.5\n\n[Fiji]\nStatus: Complete\n"
                "Final ROI Counts: Whole=12; Processes=12; Soma=12\n"
                "Manual ROI Review Used: True; Deleted Original IDs: [4]; Reverted Actions: 0\n"
                "Input Folder: /internal/debug/source\n")
            snapshot = load_run_details(root, {"id": "one", "module_id": "project-leap-2d", "status": "completed"})
            sections = {s["title"]: {r["label"]: r["value"] for r in s["rows"]} for s in snapshot["summarySections"]}
            self.assertEqual(sections["任务与软件"]["应用版本"], "2.1")
            self.assertEqual(sections["任务与软件"]["运行环境 Cellpose 模型权重"], "cpsam_v2")
            self.assertEqual(sections["任务与软件"]["运行环境 Cellpose 权重 SHA-256"], "abc123")
            self.assertEqual(sections["任务与软件"]["运行环境 InstanSeg 模型权重"], "nuclei.pt")
            self.assertEqual(sections["任务与软件"]["运行环境 InstanSeg 权重 SHA-256"], "def456")
            self.assertNotIn("模型", sections["任务与软件"])
            self.assertNotIn("细胞核模型", sections["任务与软件"])
            self.assertEqual(sections["分析步骤与参数"]["实际年龄配置"], "mature")
            self.assertEqual(sections["审核与结果"]["最终 ROI 数量"], "12")
            self.assertEqual(sections["审核与结果"]["最终分区 ROI 数量"], "Whole=12; Processes=12; Soma=12")
            self.assertEqual(sections["审核与结果"]["Fiji 审核状态"], "Complete")
            self.assertEqual(sections["分析步骤与参数"]["分区参数 · soma_limit_um"], "3.5")
            self.assertEqual(sections["耗时与重要事件"]["候选阶段实际经过时间"], "10.0 s")
            self.assertEqual(sections["耗时与重要事件"]["候选工作进程计算时间之和（并行）"], "30.0 s")
            self.assertNotIn("/internal", str(sections))


class RunDetailsControllerTests(CountingControllerFixture, unittest.TestCase):
    def test_events_follow_worker_session_and_ignore_other_run(self):
        run = self.start_fixture()
        self.controller._process_kind = "analysis"
        with (run / "events.jsonl").open("w") as handle:
            for run_id, model in (("different", "wrong"), (run.name, "actual")):
                handle.write(json.dumps({"type": "stage", "run_id": run_id, "stage": "count_inference",
                                        "message": "recorded model", "model": {"name": model, "device": "mps"}}) + "\n")
        self.controller._active_module_id = "project-leap-2d"
        self.controller._poll()
        self.assertFalse(self.controller.activeRunDetails["available"])
        self.controller._active_module_id = "cell-counting"
        snapshot = self.controller.activeRunDetails
        self.assertIn("actual", str(snapshot["rows"]))
        self.assertNotIn("wrong", str(snapshot["rows"]))
        previous = self.controller._event_offset
        self.controller._poll()
        self.assertEqual(self.controller._event_offset, previous)
        self.assertEqual(self.controller.logText.count("recorded model"), 1)

    def test_history_is_on_demand_and_latest_requested_job_wins(self):
        for identifier in ("first", "second"):
            folder = self.paths.runs / identifier
            folder.mkdir()
            write_json(folder / "job.json", {"id": identifier, "module_id": "project-leap-2d", "status": "completed"})
            (folder / "console.log").write_text(identifier + " diagnostic\n")
        self.controller._load_jobs()
        self.assertIsNone(self.controller._history_task)
        self.controller.loadJobRunDetails("first")
        self.controller.loadJobRunDetails("second")
        self.wait_until(lambda: not self.controller.jobRunDetails["loading"] and self.controller._history_task is None)
        self.assertEqual(self.controller.jobRunDetails["jobId"], "second")
        self.assertEqual(self.controller.jobRunDetails["logText"], "")
        self.assertIn("second", str(self.controller.jobRunDetails["summarySections"]))
        self.assertTrue(self.controller._history_pool.waitForDone(1000))

    def test_sampling_visibility_and_summary_are_bound_to_original_module(self):
        run = self.start_fixture()
        self.controller._process_kind = "analysis"
        monitor = self.controller._resource_monitor
        with patch.object(monitor, "setActive") as activate:
            self.controller.setRunDetailsVisible(True)
            activate.assert_called_with(True)
            self.controller._resource_snapshot({"timestamp": 10., "cpu_percent": 30., "cpu_cores": [30.]})
            self.controller._save_job()
            saved = json.loads((run / "job.json").read_text())
            self.assertNotIn("resource_summary", saved)
            self.controller._active_module_id = "project-leap-2d"
            self.controller._details_state_changed()
            activate.assert_called_with(False)
            self.controller._resource_snapshot({"timestamp": 11., "cpu_percent": 80.})
            self.assertFalse(self.controller.activeRunDetails["available"])
            self.controller._active_module_id = "cell-counting"
            self.assertEqual(self.controller.activeRunDetails["resources"]["cpu_percent"], 30.)
            self.assertEqual(self.controller.activeRunDetails["resources"]["mode"], "stopped")

    def test_review_freezes_elapsed_and_confirmation_resets_live_details(self):
        run, _ = self.review_fixture()
        details = self.controller.activeRunDetails
        self.assertTrue(details["available"])
        self.assertEqual(details["status"], "waiting_review")
        elapsed = details["elapsedSeconds"]
        self.application.processEvents()
        self.assertEqual(self.controller.activeRunDetails["elapsedSeconds"], elapsed)
        self.controller.counting.confirm()
        self.wait_until(lambda: self.controller.status == "idle")
        self.assertFalse(self.controller.activeRunDetails["available"])
        self.assertEqual(self.controller.logText, "")
        saved = json.loads((run / "job.json").read_text())
        self.assertIn("run_details", saved)

    def test_reopened_counting_review_restores_details_without_rewriting_logs(self):
        run, _ = self.review_fixture()
        (run / "console.log").write_text("historic diagnostic\n")
        before = (run / "console.log").read_bytes(), (run / "job.json").read_bytes()
        restarted = self.make_controller()
        restarted.openCountingJob(run.name)
        self.wait_until(lambda: restarted._history_task is None and not restarted.counting.busy)
        self.assertEqual(restarted.logText, "")
        self.assertTrue(restarted.activeRunDetails["history"])
        self.assertTrue(restarted.activeRunDetails["summarySections"])
        self.assertEqual(restarted.activeRunDetails["jobId"], run.name)
        self.assertEqual(((run / "console.log").read_bytes(), (run / "job.json").read_bytes()), before)
        self.assertTrue(restarted._history_pool.waitForDone(1000))

    def test_quit_waits_for_monitor_drain(self):
        monitor = self.controller._resource_monitor
        with patch.object(monitor, "shutdown", return_value=False) as shutdown, \
                patch.object(monitor, "isDrained", return_value=False), \
                patch.object(QApplication, "quit") as quit_app, \
                patch("leap_desktop.controller.QTimer.singleShot") as later:
            self.controller._finish_quit()
            shutdown.assert_called_once()
            quit_app.assert_not_called()
            later.assert_called_once_with(50, self.controller._finish_quit)

    def test_review_selection_and_preview_changes_do_not_rebuild_details(self):
        self.review_fixture()
        self.controller._details_state_changed()
        changes = []
        self.controller.runDetailsChanged.connect(lambda: changes.append(True))
        before = self.controller.activeRunDetails
        for _ in range(100):
            # The editor uses this signal for selection, view and frame updates.
            self.controller.counting.changed.emit()
        self.assertEqual(changes, [])
        self.assertEqual(self.controller.activeRunDetails, before)
        self.controller._stage = "已确认"
        self.controller.changed.emit()
        self.assertEqual(len(changes), 1)

    def test_final_summary_survives_large_analysis_and_other_visible_module(self):
        run = self.start_fixture()
        analysis_path = self.finish_fixture(run, dispatch=False)
        analysis = json.loads(analysis_path.read_text())
        analysis.update(model={"name": "actual-final-model", "device": "mps", "parameters": {"batch_size": 8}},
                        timings={"total_seconds": 12.5, "nuclear_route_seconds": 9.0,
                                 "body_route_seconds": 8.0, "route_times_overlap_and_must_not_be_added": True},
                        large_record="x" * JSON_READ_LIMIT)
        write_json(analysis_path, analysis)
        self.assertGreater(analysis_path.stat().st_size, JSON_READ_LIMIT)
        other_run = self.paths.runs / "unrelated-fluorescence"
        other_run.mkdir()
        other_job = {"id": other_run.name, "module_id": "project-leap-2d", "status": "completed"}
        write_json(other_run / "job.json", other_job)
        other_before = (other_run / "job.json").read_bytes()
        other = self.controller._session_for("project-leap-2d")
        other.job, other.run_dir = other_job, other_run
        self.controller._active_module_id = "project-leap-2d"
        self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.wait_until(lambda: not self.controller.counting.busy)
        saved = json.loads((run / "job.json").read_text())
        self.assertEqual(saved["status"], "waiting_review")
        self.assertIn("actual-final-model", str(saved["run_details"]["rows"]))
        self.assertEqual(saved["run_details"]["elapsedSeconds"], 12.5)
        self.assertEqual(len(saved["run_details"]["timings"]), 3)
        self.assertEqual((other_run / "job.json").read_bytes(), other_before)
        result = load_run_details(run, saved)
        self.assertIn("actual-final-model", str(result["rows"]))
        self.assertEqual(result["elapsedSeconds"], 12.5)
        self.assertEqual(len(result["timings"]), 3)
        self.assertTrue(result["detailsTruncated"])
        self.assertFalse(any("display limit" in message for message in result["warnings"]))

    def test_summary_write_failure_does_not_change_review_outcome(self):
        run = self.start_fixture()
        self.finish_fixture(run, dispatch=False)
        self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        with patch("leap_desktop.controller.write_json", side_effect=OSError("synthetic summary write failure")):
            self.controller._finish_detail_observation("cell-counting", run.name)
        self.wait_until(lambda: not self.controller.counting.busy)
        self.assertEqual(self.controller.status, "waiting_review")
        self.assertTrue(self.controller.counting.available)
        self.assertIn("synthetic summary write failure", self.controller.logText)
        self.assertEqual(json.loads((run / "job.json").read_text())["status"], "waiting_review")

    def test_progress_events_and_existing_diagnostics_append_without_reordering(self):
        run = self.start_fixture()
        before = self.controller.logText
        (run / "counting").mkdir()
        (run / "counting/model-progress.jsonl").write_text(json.dumps({"window_id": 7, "primary_instances": 3,
            "model_batch_seconds": .25, "actual_network_tiles": 1}) + "\n")
        (run / "events.jsonl").write_text(json.dumps({"run_id": run.name, "type": "progress", "stage": "count_inference",
            "message": "Processing local evidence", "current": 8, "total": 12}) + "\n")
        self.controller._poll()
        self.assertTrue(self.controller.logText.startswith(before))
        self.assertIn("(8/12)", self.controller.logText)
        self.assertIn("0.250 s", self.controller.logText)
        after = self.controller.logText
        self.controller._poll()
        self.assertEqual(self.controller.logText, after)

    def test_finished_console_drains_large_tail_in_bounded_turns_and_decodes_utf8(self):
        run = self.start_fixture()
        self.finish_fixture(run, dispatch=False)
        text = ("ordinary line\n" * 110000) + "final Unicode 标定 😀\nComplete pipeline elapsed: 77.125 s\n"
        (run / "console.log").write_text(text)
        self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertLess(self.controller._console_offset, (run / "console.log").stat().st_size)
        self.wait_until(lambda: self.controller._session_for("cell-counting").details_finalized)
        self.assertEqual(self.controller._console_offset, (run / "console.log").stat().st_size)
        self.assertIn("final Unicode 标定 😀", self.controller.logText)
        self.assertNotIn("�", self.controller.logText)
        self.assertEqual(self.controller.status, "waiting_review")
        previous = (run / "job.json").read_bytes()
        self.controller._finish_detail_observation("cell-counting", run.name)
        self.assertEqual((run / "job.json").read_bytes(), previous)

    def test_late_observer_cannot_write_into_replaced_run_or_change_progress(self):
        run = self.start_fixture()
        (run / "console.log").write_text('{"type":"progress","current":5,"total":10}\n')
        old = self.controller._job["id"]
        self.controller._job = {**self.controller._job, "id": "replacement"}
        before = self.controller.logText, self.controller._progress
        self.controller._finish_detail_observation("cell-counting", old)
        self.assertEqual((self.controller.logText, self.controller._progress), before)

    def test_unicode_split_across_console_read_boundaries_is_preserved(self):
        run = self.start_fixture()
        self.controller._reset_log()
        text = "x" * 65535 + "标定 😀\n"
        (run / "console.log").write_text(text)
        self.controller._read_console()
        self.controller._read_console()
        self.assertEqual(self.controller.logText, text)

    def test_rapid_confirmation_preserves_final_analysis_before_console_drain(self):
        run = self.start_fixture()
        analysis_path = self.finish_fixture(run, dispatch=False)
        analysis = json.loads(analysis_path.read_text())
        analysis.update(model={"name": "final-model"}, timings={"total_seconds": 12.25})
        write_json(analysis_path, analysis)
        (run / "console.log").write_text("remaining output\n" * 100000)
        self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertFalse(self.controller._active_session.details_finalized)
        result = run / "results/revision-001"
        result.mkdir(parents=True)
        self.controller._apply_counting_confirmation(run.name, {"count": 2, "revision": 1, "results_dir": str(result)})
        job = json.loads((run / "job.json").read_text())
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["run_details"]["elapsedSeconds"], 12.25)
        self.assertIn("final-model", str(job["run_details"]["summarySections"]))
        self.controller._finish_detail_observation("cell-counting", run.name)
        self.assertEqual(json.loads((run / "job.json").read_text()), job)

    def test_quit_preserves_loaded_final_summary_without_draining_large_console(self):
        run = self.start_fixture()
        analysis_path = self.finish_fixture(run, dispatch=False)
        analysis = json.loads(analysis_path.read_text())
        analysis.update(model={"name": "quit-final-model"}, timings={"total_seconds": 18.5})
        write_json(analysis_path, analysis)
        (run / "console.log").write_text("remaining output\n" * 100000)
        self.controller._process_finished(0, QProcess.ExitStatus.NormalExit)
        offset = self.controller._console_offset
        self.controller._closing = True
        with patch.object(self.controller._resource_monitor, "shutdown"), \
                patch.object(self.controller._resource_monitor, "isDrained", return_value=True), \
                patch.object(type(self.controller.counting), "drained", new_callable=PropertyMock, return_value=True), \
                patch.object(QApplication, "quit"):
            self.controller._finish_quit()
        saved = json.loads((run / "job.json").read_text())
        self.assertEqual(saved["run_details"]["elapsedSeconds"], 18.5)
        self.assertIn("quit-final-model", str(saved["run_details"]))
        self.assertEqual(self.controller._console_offset, offset)

    def test_late_observer_does_not_rewrite_reopened_same_run(self):
        run, _ = self.review_fixture()
        self.controller._session.run_details = RunDetails(self.controller._job)
        self.controller._session.details_finalized = False
        before = (run / "job.json").read_bytes(), self.controller.logText
        self.controller._finish_detail_observation("cell-counting", run.name)
        self.assertEqual(((run / "job.json").read_bytes(), self.controller.logText), before)



if __name__ == "__main__":
    unittest.main()
