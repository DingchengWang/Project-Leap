"""Run-details UI checks with disposable jobs and real Qt Quick controls.

Run with QT_QPA_PLATFORM=cocoa. Resource snapshots are synthetic; no analysis,
Fiji, model inference, or resource polling is started by these checks.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.refinement_ui_probe import QtUiHarness, visual_items
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtQml import QQmlExpression
from PySide6.QtWidgets import QApplication
from leap_desktop.controller import Controller, WorkerContext
from leap_desktop.paths import AppPaths, write_json
from leap_desktop.run_details import RunDetails
import shiboken6


class RunDetailsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="leap-run-details-ui-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        initial = patch.object(Controller, "_initial_status", return_value=None)
        initial.start(); self.addCleanup(initial.stop)
        self.backend = Controller(AppPaths(Path(self.temp.name) / "home"))
        self.backend._timer.stop()
        self.monitor = patch.object(self.backend._resource_monitor, "setActive")
        self.monitor.start(); self.addCleanup(self.monitor.stop)
        evidence = os.environ.get("LEAP_RUN_DETAILS_UI_EVIDENCE_DIR")
        self.ui = QtUiHarness(self.backend, output=Path(evidence) / self.id().split(".")[-1] if evidence else None)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.backend._busy = False
        self.ui.dispose()
        shiboken6.delete(self.backend)

    def scroll_to(self, item, page="analysisScroll"):
        item = self.ui.find(item) if isinstance(item, str) else item
        flick = self.ui.find(page).property("contentItem")
        rect = item.mapRectToScene(item.boundingRect())
        viewport = flick.mapRectToScene(flick.boundingRect())
        target = flick.property("contentY") + rect.top() - viewport.top() - 12
        flick.setProperty("contentY", max(0, min(max(0, flick.property("contentHeight") - flick.height()), target)))
        self.ui.settle()
        return item

    def start_fixture(self):
        job = {"id": "ui-running", "module_id": "project-leap-2d", "name": "UI fixture",
               "status": "running", "stage": "计算细胞分割", "created_at": "2026-11-15T12:00:00"}
        self.backend._job = job
        self.backend._status = "running"
        self.backend._stage = job["stage"]
        self.backend._busy = True
        self.backend._worker_context = WorkerContext(job["module_id"], job["id"])
        self.backend._ensure_run_details(live=True)
        self.backend.changed.emit()
        self.ui.settle()

    def test_idle_card_alignment_and_exact_gap_at_both_sizes(self):
        card, loader = self.ui.find("liveRunDetails"), self.ui.find("moduleInputLoader")
        for width, height in ((1210, 820), (900, 620)):
            self.ui.window.setWidth(width); self.ui.window.setHeight(height)
            for language in ("en", "zh"):
                self.backend.setLanguage(language); self.ui.settle()
                self.assertAlmostEqual(card.x(), loader.x(), delta=.1)
                self.assertAlmostEqual(card.width(), loader.width(), delta=.1)
                self.assertAlmostEqual(card.y() - loader.y() - loader.height(), 18, delta=.1)
                texts = [item.property("text") for item in visual_items(card)
                         if item.isVisible() and isinstance(item.property("text"), str)]
                self.assertEqual(texts, [self.backend.locale.translate("运行细节"),
                                         self.backend.locale.translate("运行时会显示程序内部进程")])
                self.scroll_to(card)
                self.ui.capture(f"idle-{language}-{width}")
        self.assertFalse(self.ui.warnings, self.ui.warnings)

    def test_live_resources_unknown_values_and_hidden_log_freeze(self):
        self.start_fixture()
        self.backend._append_log("".join(f"QA line {i:03d}: synthetic display fixture.\n" for i in range(140)))
        self.ui.settle()
        self.scroll_to("logFlick")
        self.assertTrue(self.backend._run_details_visible)
        self.assertEqual(self.ui.find("runCpuTotal").property("text"), "—")
        self.assertEqual(self.ui.find("runGpuTotal").property("text"), "—")
        self.assertEqual(self.ui.find("runMemory").property("text"), "— / —")
        self.backend._resource_snapshot({"timestamp": 1.0, "cpu_percent": 42, "cpu_cores": [0, 25, 50, 100], "cpu_core_types": ["E", "E", "P", None],
            "cpu_temperature_c": 43.5, "gpu_temperature_c": 44.25,
            "gpu_percent": None, "gpu_core_count": 16, "gpu_group_count": 2, "memory_used_bytes": 8 * 2**30,
            "memory_total_bytes": 16 * 2**30, "swap_used_bytes": 0})
        self.ui.settle()
        self.assertEqual(self.ui.find("runCpuTotal").property("text"), "42%")
        self.assertEqual(self.ui.find("runGpuTotal").property("text"), "—")
        self.assertEqual(self.ui.find("runMemory").property("text"), "8.0 GiB / 16.0 GiB")
        self.assertEqual(self.ui.find("runSwap").property("text"), "SWAP  0.0 GiB")
        self.assertEqual(self.ui.find("runCpuTemperature").property("text"), "Avg. temperature 43.5 °C")
        self.assertEqual(self.ui.find("runGpuTemperature").property("text"), "Avg. temperature 44.3 °C")
        self.assertEqual(self.ui.find("runGpuCores").property("text"), "16 cores · 2 groups")
        e_fill, p_fill = self.ui.find("runCpuCoreFill_1"), self.ui.find("runCpuCoreFill_2")
        self.assertNotEqual(e_fill.property("color"), p_fill.property("color"))
        self.assertEqual(p_fill.property("color"), self.ui.appearance.accent)
        for width, height, mode in ((1210, 820, "light"), (900, 620, "dark")):
            self.ui.window.setWidth(width); self.ui.window.setHeight(height)
            self.backend.setAppearanceMode(mode); self.ui.settle()
            self.scroll_to("liveRunDetails")
            self.ui.capture(f"running-resources-{mode}-{width}")
            resource_grid = self.ui.find("runResourceGrid")
            self.assertEqual(resource_grid.property("columns"), 3 if width == 1210 else 1)
        self.ui.window.setWidth(1210); self.ui.window.setHeight(820)
        self.backend.setAppearanceMode("light"); self.ui.settle()
        self.scroll_to("logFlick")
        text, flick = self.ui.find("logText"), self.ui.find("logFlick")
        self.assertEqual(text.property("text"), self.backend.logText)
        self.assertTrue(text.property("selectByMouse"))
        self.assertTrue(flick.property("followLatest"))
        self.assertAlmostEqual(flick.property("contentY"), flick.property("contentHeight") - flick.height(), delta=2)
        flick.setProperty("contentY", 300); self.ui.settle()
        self.assertFalse(flick.property("followLatest"))
        self.backend._append_log("visible incremental update\n"); self.ui.settle()
        self.assertAlmostEqual(flick.property("contentY"), 300, delta=2)
        self.ui.capture("running-resources-and-log")
        before = text.property("text")
        self.backend.setPage("jobs"); self.ui.settle()
        self.assertFalse(self.backend._run_details_visible)
        self.backend._append_log("hidden update remains in backend\n"); self.ui.settle()
        self.assertEqual(text.property("text"), before)
        self.assertNotEqual(text.property("text"), self.backend.logText)
        self.backend.setPage("analysis"); self.ui.settle()
        self.assertEqual(text.property("text"), self.backend.logText)
        self.assertAlmostEqual(flick.property("contentY"), 300, delta=2)
        self.ui.window.showMinimized(); self.ui.settle(150)
        self.assertFalse(self.backend._run_details_visible)
        before = text.property("text")
        self.backend._append_log("minimized update remains in backend\n"); self.ui.settle()
        self.assertEqual(text.property("text"), before)
        self.ui.window.showNormal(); self.ui.settle(150)
        self.assertTrue(self.backend._run_details_visible)
        self.assertEqual(text.property("text"), self.backend.logText)
        # Scrolling the card completely outside the viewport also stops updates.
        outer = self.ui.find("analysisScroll").property("contentItem")
        outer.setProperty("contentY", 0); self.ui.window.setHeight(620); self.ui.settle()
        self.assertFalse(self.ui.find("liveRunDetails").property("displayActive"))
        self.assertFalse(self.backend._run_details_visible)
        self.assertFalse(self.ui.warnings, self.ui.warnings)

    def test_follow_latest_and_hidden_resume_preserve_each_log_line_once(self):
        self.start_fixture()
        self.backend._append_log("".join(f"Initial line {index:03d}\n" for index in range(160)))
        self.ui.settle(); self.scroll_to("logFlick")
        flick, text = self.ui.find("logFlick"), self.ui.find("logText")
        self.ui.wheel(flick, 120)
        self.assertFalse(flick.property("followLatest"))
        paused_y = flick.property("contentY")
        self.backend._append_log("Visible while paused\n"); self.ui.settle()
        self.assertAlmostEqual(flick.property("contentY"), paused_y, delta=2)
        self.scroll_to("runLogFollowLatest"); self.ui.click("runLogFollowLatest")
        self.assertTrue(flick.property("followLatest"))
        self.assertAlmostEqual(flick.property("contentY"), flick.property("contentHeight") - flick.height(), delta=2)
        self.backend.setPage("jobs"); self.ui.settle()
        self.backend._append_log("Hidden first line\n")
        self.backend.setPage("analysis")
        self.backend._append_log("Before queued synchronize\n")
        self.ui.settle()
        self.assertEqual(text.property("text"), self.backend.logText)
        self.assertEqual(text.property("text").count("Before queued synchronize"), 1)
        self.backend.setPage("jobs"); self.ui.settle()
        self.backend._reset_log("".join(f"Hidden bounded {i:04d} 😀 " + "x" * 90 + "\n" for i in range(1200)))
        self.backend.setPage("analysis")
        self.backend._append_log("Trimmed before queued synchronize 😀\n")
        self.ui.settle()
        self.assertEqual(text.property("text"), self.backend.logText)
        self.assertTrue(flick.property("followLatest"))
        self.assertAlmostEqual(flick.property("contentY"), flick.property("contentHeight") - flick.height(), delta=2)
        self.assertFalse(self.ui.warnings, self.ui.warnings)

    def test_history_is_collapsed_and_loaded_only_on_request(self):
        job_id = "ui-history"
        run_dir = self.backend.paths.runs / job_id
        run_dir.mkdir(parents=True)
        job = {"id": job_id, "module_id": "project-leap-2d", "app_version": "UI fixture",
            "name": "Historical fixture", "status": "completed", "stage": "分析完成",
            "created_at": "2026-11-15T12:00:00"}
        details = RunDetails(job)
        details._row("模型", "Cellpose-SAM fixture")
        details._row("参数 · cellprob_threshold", 0.0)
        details._timing("inference", 1.25, "Model inference")
        job["run_details"] = details.persisted()
        write_json(run_dir / "job.json", job)
        (run_dir / "console.log").write_text("historical console text\n", encoding="utf-8")
        self.backend._load_jobs(); self.backend.changed.emit()
        self.backend.setPage("jobs"); self.ui.settle()
        self.ui.click("taskRow_" + job_id)
        loader = self.ui.find("jobRunDetailsLoader_" + job_id)
        self.assertFalse(loader.property("active"))
        self.assertEqual(self.backend._history_generation, 0)
        self.assertFalse(self.ui.named("jobRunDetails_"))
        toggle = self.scroll_to("jobRunDetailsToggle_" + job_id, "jobsScroll")
        self.ui.click(toggle)
        self.ui.wait_until(lambda: self.backend.jobRunDetails.get("jobId") == job_id
                           and not self.backend.jobRunDetails.get("loading"))
        self.assertTrue(loader.property("active"))
        self.assertEqual(self.backend._history_generation, 1)
        history = self.ui.find("jobRunDetails_" + job_id)
        visible = [item for item in visual_items(history) if item.isVisible()]
        self.assertFalse(any(item.objectName() in ("runResourceGrid", "logFlick", "logText") for item in visible))
        self.assertEqual(len([item for item in visible if item.objectName().startswith("historySummaryTitle_")]), 5)
        self.assertFalse(any("historical console text" in str(item.property("text") or "") for item in visible))
        self.assertFalse(self.backend._run_details_visible)
        self.scroll_to(history, "jobsScroll")
        self.ui.capture("history-expanded")
        self.scroll_to(toggle, "jobsScroll"); self.ui.click(toggle)
        self.assertFalse(loader.property("active"))
        self.assertFalse(self.ui.named("jobRunDetails_"))
        self.assertFalse(self.ui.warnings, self.ui.warnings)

    def test_metadata_and_resources_do_not_rewrite_the_append_only_log(self):
        self.start_fixture()
        self.backend._append_log("".join(f"Unique log line {i:04d}: preserved original console text.\n" for i in range(300)))
        self.ui.settle(); self.scroll_to("logFlick")
        card, flick, text = self.ui.find("liveRunDetails"), self.ui.find("logFlick"), self.ui.find("logText")
        card_height = card.height()
        flick.setProperty("contentY", 500); self.ui.settle()

        def visible_fragment():
            expression = QQmlExpression(self.ui.engine.rootContext(), text,
                "text.substring(positionAt(0, %s), positionAt(0, %s) + 80)" %
                (flick.property("contentY") + 3, flick.property("contentY") + 3))
            value, _ = expression.evaluate()
            self.assertFalse(expression.hasError(), expression.error().toString())
            return value

        before = visible_fragment()
        details = self.backend._active_session.run_details
        for index in range(128):
            details._row("Recorded model parameter %03d" % index, "saved configuration " * 12)
        for index in range(96):
            details._timing("stage-%03d" % index, 1.25, "Candidate morphology/postprocess worker compute total " * 5)
        details._message("warnings", "Synthetic recorded warning.")
        self.backend.runDetailsChanged.emit(); self.ui.settle()
        self.assertAlmostEqual(card.height(), card_height, delta=.1)
        self.assertEqual(flick.height(), 170)
        self.assertEqual(visible_fragment(), before)
        self.assertEqual(text.property("text"), self.backend.logText)
        self.assertNotIn("Recorded model parameter 127", text.property("text"))
        self.assertNotIn("Candidate morphology/postprocess worker compute total", text.property("text"))
        updates = QSignalSpy(text.textChanged)
        for index in range(10):
            self.backend._resource_snapshot({"timestamp": index + 1.0, "cpu_percent": index,
                "cpu_cores": [index], "gpu_percent": None})
        self.ui.settle()
        self.assertEqual(updates.count(), 0)
        self.backend._append_log("appended while metadata is present\n"); self.ui.settle()
        self.assertEqual(visible_fragment(), before)
        self.assertTrue(text.property("text").endswith(self.backend.logText))
        self.backend._reset_log("".join(f"Bounded line {index:04d} 😀 " + "x" * 90 + "\n" for index in range(1200)))
        self.ui.settle()
        flick.setProperty("contentY", max(0, flick.property("contentHeight") - flick.height() - 300))
        self.ui.settle(); before = visible_fragment()
        for index in range(12):
            self.backend._append_log(f"More bounded tail {index:02d} 😀 " + "y" * 100 + "\n")
        self.ui.settle()
        self.assertEqual(visible_fragment(), before)
        self.assertTrue(text.property("text").endswith(self.backend.logText))
        self.assertNotIn("Recorded model parameter 000", text.property("text"))
        self.assertIn(self.backend.locale.translate("运行记录较长，此处仅显示部分内容。"), self.ui.texts())
        self.ui.window.setWidth(900); self.ui.window.setHeight(620); self.ui.settle()
        self.scroll_to("logFlick"); flick.setProperty("contentY", 0); self.ui.settle()
        self.ui.capture("append-only-log-bounded")
        self.assertLessEqual(flick.mapRectToScene(flick.boundingRect()).right(), 900)
        self.assertFalse(self.ui.warnings, self.ui.warnings)


class CountingRunDetailsUiTests(unittest.TestCase):
    # Reuse the existing counting fixture without inheriting its unrelated tests.
    import test_counting_ui as fixtures
    setUpClass = classmethod(fixtures.CountingUiTests.setUpClass.__func__)
    setUp = fixtures.CountingUiTests.setUp
    dispose = fixtures.CountingUiTests.dispose
    review = fixtures.CountingUiTests.review
    scroll_to = fixtures.CountingUiTests.scroll_to
    three_class_review = fixtures.CountingUiTests.three_class_review
    point = fixtures.CountingUiTests.point
    click_point = fixtures.CountingUiTests.click_point

    def test_review_details_stay_below_the_complete_module_page(self):
        self.review()
        card = self.ui.find("liveRunDetails")
        loader = self.ui.find("moduleInputLoader")
        for width, height in ((1210, 820), (900, 620)):
            self.ui.window.setWidth(width); self.ui.window.setHeight(height)
            for language in ("en", "zh"):
                self.backend.setLanguage(language); self.ui.settle()
                self.assertTrue(card.isVisible())
                self.assertAlmostEqual(card.x(), loader.x(), delta=.1)
                self.assertAlmostEqual(card.width(), loader.width(), delta=.1)
                self.assertAlmostEqual(card.y() - loader.y() - loader.height(), 18, delta=.1)
                canvas = self.ui.find("countCanvas")
                self.assertGreater(card.mapToScene(card.boundingRect().topLeft()).y(),
                                   canvas.mapToScene(canvas.boundingRect().bottomLeft()).y())
                self.scroll_to(card)
                self.ui.capture(f"count-review-details-{language}-{width}")
        self.assertFalse(self.ui.warnings, self.ui.warnings)

    def test_terminal_console_drain_keeps_hidden_log_frozen_during_review_input(self):
        self.analysis["z_count"] = 3
        editor = self.three_class_review()
        for z in range(3):
            editor._slice_cache[editor._slice_key(z)] = self.folder / "composite.png"
        self.ui.wait_until(lambda: self.backend._history_task is None)
        session = self.backend._active_session
        session.run_details = RunDetails(session.job, live=True)
        session.run_details.freeze()
        session.details_finalized = False
        self.backend._append_log("Visible current-run log before review.\n")
        self.backend.changed.emit(); self.backend.runDetailsChanged.emit(); self.ui.settle()
        self.scroll_to("liveRunDetails")
        text = self.ui.find("logText")
        before = text.property("text")
        slider = self.scroll_to("countZSlider")
        outer = self.ui.find("analysisScroll").property("contentItem")
        slider_bottom = slider.mapRectToScene(slider.boundingRect()).bottom()
        viewport_bottom = outer.mapRectToScene(outer.boundingRect()).bottom()
        outer.setProperty("contentY", max(0, outer.property("contentY") + slider_bottom - viewport_bottom + 30))
        self.ui.settle()
        self.assertTrue(self.ui.in_view(slider))
        self.assertFalse(self.ui.find("liveRunDetails").property("displayActive"))
        lines = ("terminal fixture output " + "x" * 80 + "\n") * 19000
        console = self.run_dir / "console.log"
        console.write_text(lines, encoding="utf-8")
        self.assertGreater(console.stat().st_size, 1024 * 1024)
        session.console_offset = 0
        text_updates = QSignalSpy(text.textChanged)
        geometry_updates = QSignalSpy(text.implicitHeightChanged)
        beats = []
        heartbeat = QTimer(); heartbeat.setInterval(5)
        heartbeat.timeout.connect(lambda: beats.append(time.monotonic()))
        heartbeat.start()
        try:
            self.backend._finish_detail_observation("cell-counting", self.run_id)
            self.assertGreater(session.console_offset, 0)
            self.assertLess(session.console_offset, console.stat().st_size)
            slider.forceActiveFocus()
            started = time.monotonic()
            QTest.keyClick(self.ui.window, Qt.Key_Right)
            self.ui.wait_until(lambda: editor.displayedZIndex == 1 and not editor.busy, timeout_ms=500)
            z_ms = (time.monotonic() - started) * 1000
            QTest.keyClick(self.ui.window, Qt.Key_Left)
            self.ui.wait_until(lambda: editor.displayedZIndex == 0 and not editor.busy, timeout_ms=500)
            self.assertTrue(self.ui.in_view(self.ui.find("countCanvas")))
            self.assertFalse(self.ui.find("liveRunDetails").property("displayActive"))
            self.assertFalse(session.details_finalized)
            started = time.monotonic()
            QTest.mouseClick(self.ui.window, Qt.LeftButton, Qt.NoModifier, self.point(110, 100))
            self.ui.wait_until(lambda: editor.selectedIds == ["auto-2"], timeout_ms=500)
            click_ms = (time.monotonic() - started) * 1000
            self.ui.wait_until(lambda: session.details_finalized, timeout_ms=5000)
        finally:
            heartbeat.stop()
        max_gap_ms = max((right - left for left, right in zip(beats, beats[1:])), default=0) * 1000
        self.assertEqual(session.console_offset, console.stat().st_size)
        self.assertEqual(text_updates.count(), 0)
        self.assertEqual(geometry_updates.count(), 0)
        self.assertEqual(text.property("text"), before)
        self.assertLess(z_ms, 250)
        self.assertLess(click_ms, 250)
        self.assertLess(max_gap_ms, 250)
        self.ui.write_record("terminal-drain-input", {"console_bytes": console.stat().st_size,
            "z_response_ms": round(z_ms, 3), "selection_response_ms": round(click_ms, 3),
            "max_heartbeat_gap_ms": round(max_gap_ms, 3), "log_text_updates": text_updates.count(),
            "log_height_updates": geometry_updates.count()})
        self.assertFalse(self.ui.warnings, self.ui.warnings)

    def test_review_interaction_does_not_refresh_run_details_or_relayout_log(self):
        self.analysis["z_count"] = 3
        editor = self.three_class_review()
        for z in range(3):
            editor._slice_cache[editor._slice_key(z)] = self.folder / "composite.png"
        self.backend._append_log("Saved counting run fixture.\n")
        # The fixture reopens a saved analysis. Recreate the current-run
        # observation to exercise the separate live-to-review presentation.
        details = RunDetails(self.backend._job, live=True)
        details.freeze()
        self.backend._active_session.run_details = details
        details._row("模型", "ui-fixture")
        self.backend.changed.emit(); self.backend.runDetailsChanged.emit(); self.ui.settle()
        card = self.scroll_to("liveRunDetails")
        text = self.ui.find("logText")
        self.assertTrue(text.property("text").endswith(self.backend.logText))
        pointers = [shiboken6.getCppPointer(item)[0] for item in visual_items(card)]
        detail_updates = QSignalSpy(self.backend.runDetailsChanged)
        text_updates = QSignalSpy(text.textChanged)
        with patch.object(self.backend._resource_monitor, "setActive") as monitor:
            slider = self.scroll_to("countZSlider")
            slider.forceActiveFocus()
            QTest.keyClick(self.ui.window, Qt.Key_Right)
            self.ui.wait_until(lambda: editor.displayedZIndex == 1 and not editor.busy)
            QTest.keyClick(self.ui.window, Qt.Key_Left)
            self.ui.wait_until(lambda: editor.displayedZIndex == 0 and not editor.busy)
            self.scroll_to("countCanvas")
            self.click_point(110, 100)
            self.assertEqual(editor.selectedIds, ["auto-2"])
            self.scroll_to("countAcceptSelected"); self.ui.click("countAcceptSelected")
            self.assertEqual(editor.total, 2)
            editor.selectPoint("auto-2", False)
            editor.selectPoint("auto-7", True)
            self.ui.settle()
            self.assertEqual(set(editor.selectedIds), {"auto-2", "auto-7"})
            self.scroll_to("countDelete"); self.ui.click("countDelete")
            self.assertEqual(editor.total, 0)
            self.ui.click("countUndo"); self.assertEqual(editor.total, 2)
            self.ui.click("countRedo"); self.assertEqual(editor.total, 0)
            self.ui.click("countUndo"); self.assertEqual(editor.total, 2)
            self.assertFalse(any(call.args == (True,) for call in monitor.call_args_list))
        self.scroll_to(card)
        self.assertEqual(detail_updates.count(), 0)
        self.assertEqual(text_updates.count(), 0)
        self.assertEqual([shiboken6.getCppPointer(item)[0] for item in visual_items(card)], pointers)
        self.ui.capture("review-details-static-after-interaction")
        self.assertFalse(self.ui.warnings, self.ui.warnings)


if __name__ == "__main__":
    unittest.main()
