"""Real Qt Quick input tests on disposable counting fixtures; no model or native app.

Uses production Controller, CountingEditor, Main.qml, and mouse/key events. All
fixture images, drafts and confirmations stay in a task-specific /private/tmp.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QInputDevice, QKeySequence, QNativeGestureEvent, QPointingDevice
from PySide6.QtQml import QQmlExpression
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from leap_desktop.controller import Controller, WorkerContext
from leap_desktop.paths import AppPaths
from tools.refinement_ui_probe import QtUiHarness, visual_items
import shiboken6


class CountingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="leap-counting-ui-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.paths = AppPaths(Path(self.temp.name) / "home")
        self.initial = patch.object(Controller, "_initial_status", return_value=None)
        self.initial.start(); self.addCleanup(self.initial.stop)
        self.backend = Controller(self.paths)
        self.backend._timer.stop(); self.backend._refresh_modules()
        self.backend._module_states["cell-counting"].update(installed=True, status="ready", message="")
        self.backend.selectModule("cell-counting")
        output = os.environ.get("LEAP_COUNTING_UI_EVIDENCE_DIR")
        self.ui = QtUiHarness(self.backend, output=Path(output) / self.id().split(".")[-1] if output else None)
        self.addCleanup(self.dispose)
        self.run_id = "fixture-counting"
        self.run_dir = self.paths.runs / self.run_id
        self.folder = self.run_dir / "counting"
        self.folder.mkdir(parents=True)
        for name in ("C1.png", "C2.png", "composite.png"):
            image = QImage(256, 256, QImage.Format.Format_RGB32)
            image.fill(QColor("#172F56" if name == "composite.png" else "#505050"))
            self.assertTrue(image.save(str(self.folder / name)))
        self.analysis = {"schema_version": 1, "run_id": self.run_id,
            "source_path": str(self.folder / "missing-original.tif"), "series_index": 0, "time_index": 0,
            "dapi_channel": 0, "width": 256, "height": 256, "z_count": 1, "auto_count": 3,
            "physical_size_um": {"X": .2, "Y": .2, "Z": 1}, "default_label_size": 16,
            "channel_roles": ["DAPI", "eGFP"], "preview_path": "composite.png", "labels_path": "labels.npz",
            "channels": [{"index": 0, "name": "DAPI", "role": "DAPI", "color": "#007AFF", "path": "C1.png"},
                         {"index": 1, "name": "eGFP", "role": "eGFP", "color": "#34C759", "path": "C2.png"}],
            "points": [{"id": "auto-1", "label_id": 1, "x": 64., "y": 64., "z": None, "source": "auto"},
                       {"id": "auto-2", "label_id": 2, "x": 64., "y": 64., "z": None, "source": "auto"},
                       {"id": "auto-7", "label_id": 7, "x": 180., "y": 180., "z": None, "source": "auto"}]}
        self.analysis_path = self.folder / "analysis.json"
        self.analysis_path.write_text(json.dumps(self.analysis))

    def dispose(self):
        self.backend._busy = False; self.backend._maintenance_busy = False
        self.backend.counting.close()
        self.ui.settle(150)
        self.ui.dispose(); shiboken6.delete(self.backend)

    def review(self):
        job = {"id": self.run_id, "module_id": "cell-counting", "module_version": "2.0.0" if self.analysis["schema_version"] == 2 else "1.0.0", "name": "UI fixture.tif",
               "source": self.analysis["source_path"], "created_at": "2026-10-19T12:00:00", "status": "waiting_review",
               "auto_count": self.analysis["auto_count"], "analysis_path": str(self.analysis_path)}
        (self.run_dir / "job.json").write_text(json.dumps(job))
        self.backend._jobs = [job]
        self.backend.openCountingJob(self.run_id)
        self.ui.wait_until(lambda: self.backend.counting.available and not self.backend.counting.busy)
        self.ui.settle(100)
        return self.backend.counting


    def scroll_to(self, item):
        item = self.ui.find(item) if isinstance(item, str) else item
        flick = self.ui.find("analysisScroll").property("contentItem")
        rect = item.mapRectToScene(item.boundingRect())
        viewport = flick.mapRectToScene(flick.boundingRect())
        target = flick.property("contentY") + rect.top() - viewport.top() - 12
        maximum = max(0, flick.property("contentHeight") - flick.height())
        flick.setProperty("contentY", max(0, min(maximum, target)))
        self.ui.settle()
        return item

    def point(self, x, y):
        canvas = self.ui.find("countCanvas")
        local = QPointF(canvas.property("imageLeft") + x * canvas.property("imageScale"),
                        canvas.property("imageTop") + y * canvas.property("imageScale"))
        point = canvas.mapToScene(local)
        return QPoint(round(point.x()), round(point.y()))

    def click_point(self, x, y, modifiers=Qt.NoModifier):
        QTest.mouseClick(self.ui.window, Qt.LeftButton, modifiers, self.point(x, y)); self.ui.settle()

    def labels(self):
        values = self.ui.find("countCanvas").property("labels")
        return values.toVariant() if hasattr(values, "toVariant") else values

    def click_label(self, ident, modifiers=Qt.NoModifier):
        label = next(item for item in self.labels() if item["id"] == ident)
        self.click_point(label["left"] + label["width"] / 2,
                         label["top"] + label["height"] / 2, modifiers)

    def test_coincident_labels_multiselect_delete_undo_redo_and_manual_add(self):
        editor = self.review()
        first, second = [next(p for p in self.labels() if p["id"] == key) for key in ("auto-1", "auto-2")]
        self.assertTrue(QRectF(first["left"], first["top"], first["width"], first["height"]).intersects(
            QRectF(second["left"], second["top"], second["width"], second["height"])))
        self.click_label("auto-1"); self.assertEqual(editor.selectedIds, ["auto-1"])
        self.click_label("auto-2", Qt.ControlModifier)
        self.assertEqual(set(editor.selectedIds), {"auto-1", "auto-2"})
        QTest.keyClick(self.ui.window, Qt.Key_Delete); self.ui.settle()
        self.assertEqual(editor.total, 1)
        self.ui.click("countUndo"); self.assertEqual(editor.total, 3)
        self.ui.click("countRedo"); self.assertEqual(editor.total, 1)
        self.ui.click("countAddMode")
        self.click_point(100, 150)
        self.assertEqual(editor.total, 2)
        manual = next(p for p in editor.points if p["source"] == "manual")
        self.assertAlmostEqual(manual["x"], 100, delta=1)
        self.assertAlmostEqual(manual["y"], 150, delta=1)
        self.assertIsNone(manual["z"])
        QTest.keySequence(self.ui.window, QKeySequence(QKeySequence.StandardKey.Undo)); self.ui.settle()
        self.assertEqual(editor.total, 1)
        QTest.keySequence(self.ui.window, QKeySequence(QKeySequence.StandardKey.Redo)); self.ui.settle()
        self.assertEqual(editor.total, 2)
        QTest.keyClick(self.ui.window, Qt.Key_V); self.ui.settle()
        self.assertEqual(editor.mode, "select")
        self.assertTrue((self.folder / "draft.json").is_file())
        self.assertFalse(self.ui.warnings)

    def test_drag_and_scroll_pan_preserve_image_coordinates_without_rectangle_selection(self):
        editor = self.review()
        canvas = self.ui.find("countCanvas")
        before = editor.points
        self.ui.click("countZoomIn")
        self.assertGreater(canvas.property("zoom"), 1)
        start, end = self.point(120, 120), self.point(140, 130)
        QTest.mousePress(self.ui.window, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(self.ui.window, end, 40)
        QTest.mouseRelease(self.ui.window, Qt.LeftButton, Qt.NoModifier, end)
        self.ui.settle()
        self.assertGreater(abs(canvas.property("panX")), 1)
        self.assertEqual(editor.selectedIds, [])
        zoom = canvas.property("zoom")
        self.ui.wheel("countCanvas", 120)
        self.assertEqual(canvas.property("zoom"), zoom)
        self.assertEqual(before, editor.points)
        self.ui.click("countFit")
        self.assertEqual(canvas.property("zoom"), 1)
        self.assertEqual(canvas.property("panX"), 0)
        self.assertFalse(self.ui.warnings)

    def test_native_touchpad_pinch_scales_around_the_gesture_centroid(self):
        editor = self.review()
        canvas = self.ui.find("countCanvas")
        local = QPointF(canvas.width() * .37, canvas.height() * .43)
        scene = canvas.mapToScene(local)
        global_position = QPointF(self.ui.window.mapToGlobal(QPoint(round(scene.x()), round(scene.y()))))
        before_x = (local.x() - canvas.property("imageLeft")) / canvas.property("imageScale")
        before_y = (local.y() - canvas.property("imageTop")) / canvas.property("imageScale")
        before_points = editor.points
        device = QPointingDevice("Prototype D touchpad fixture", 731,
            QInputDevice.DeviceType.TouchPad, QPointingDevice.PointerType.Finger,
            QInputDevice.Capability.Position, 2, 0)
        for kind, value in ((Qt.NativeGestureType.BeginNativeGesture, 0),
                            (Qt.NativeGestureType.ZoomNativeGesture, .2),
                            (Qt.NativeGestureType.EndNativeGesture, 0)):
            event = QNativeGestureEvent(kind, device, 2, scene, scene, global_position,
                                       value, QPointF(), 42)
            QApplication.sendEvent(self.ui.window, event)
            self.ui.settle(20)
        self.assertGreater(editor.viewZoom, 1)
        after_x = (local.x() - canvas.property("imageLeft")) / canvas.property("imageScale")
        after_y = (local.y() - canvas.property("imageTop")) / canvas.property("imageScale")
        self.ui.write_record("native-pinch-debug", {"zoom": editor.viewZoom, "pan": [editor.viewPanX, editor.viewPanY], "local": [local.x(), local.y()], "scene": [scene.x(), scene.y()], "before": [before_x, before_y], "after": [after_x, after_y]})
        self.assertAlmostEqual(after_x, before_x, delta=.02)
        self.assertAlmostEqual(after_y, before_y, delta=.02)
        self.assertEqual(editor.points, before_points)
        self.ui.write_record("native-pinch", {"zoom": editor.viewZoom,
            "image_anchor_before": [before_x, before_y], "image_anchor_after": [after_x, after_y]})
        self.assertFalse(self.ui.warnings)

    def test_multiselect_merge_button_shortcut_and_atomic_undo_redo(self):
        editor = self.review()
        original = editor.points
        self.assertFalse(self.ui.find("countMerge").isEnabled())
        self.click_label("auto-1")
        self.assertFalse(self.ui.find("countMerge").isEnabled())
        self.click_label("auto-2", Qt.ControlModifier)
        self.assertTrue(self.ui.find("countMerge").isEnabled())
        self.ui.click("countMerge")
        self.assertEqual(editor.total, 2)
        merged = next(point for point in editor.points if point["id"] != "auto-7")
        self.assertEqual(set(merged["parent_ids"]), {"auto-1", "auto-2"})
        self.assertEqual((merged["x"], merged["y"]), (64, 64))
        self.assertEqual(editor.selectedIds, [merged["id"]])
        self.assertFalse(self.ui.find("countMerge").isEnabled())
        self.ui.click("countUndo")
        self.assertEqual(editor.points, original)
        self.ui.click("countRedo")
        self.assertEqual(editor.total, 2)
        self.assertIn(merged["id"], [point["id"] for point in editor.points])
        self.ui.click("countUndo")
        self.click_label("auto-1"); self.click_label("auto-2", Qt.ControlModifier)
        QTest.keyClick(self.ui.window, Qt.Key_M); self.ui.settle()
        self.assertEqual(editor.total, 2)
        new_merged = next(point for point in editor.points if point["id"] != "auto-7")
        self.assertNotEqual(new_merged["id"], merged["id"])
        QTest.keySequence(self.ui.window, QKeySequence(QKeySequence.StandardKey.Undo)); self.ui.settle()
        self.assertEqual(editor.points, original)
        self.assertFalse(self.ui.warnings)

    def test_fluorescence_age_host_constraint_and_layout(self):
        # Native AppKit button dispatch is covered by native tests. This harness
        # verifies the QML host, controller invariant and layout without a fallback.
        self.backend._module_states["project-leap-2d"].update(installed=True, status="ready", message="")
        self.backend.selectModule("project-leap-2d")
        self.backend._image_info = {"path": str(self.folder / "fluorescence.tif"), "name": "Age fixture.tif",
            "series_count": 1, "selected_series": 0, "time_count": 1, "selected_time": 0,
            "channel_count": 3, "channel_hints": ["DAPI", "eGFP", "KCNN2"], "width": 256,
            "height": 256, "z_count": 4, "axes": "ZCYX", "dtype": "uint16"}
        self.backend._channel_roles = ["DAPI", "eGFP", "KCNN2"]
        self.backend.changed.emit(); self.ui.settle()
        self.assertEqual(self.backend.ageProfile, "auto")
        self.assertFalse(self.ui.find("fluorescenceAgeNotice").isVisible())
        self.backend.setAgeProfile("neonatal")
        self.backend._channel_roles = ["DAPI", "GFAP", "KCNN2"]
        self.backend.changed.emit(); self.ui.settle()
        self.assertFalse(self.ui.find("fluorescenceStart").isEnabled())
        self.assertTrue(self.ui.find("fluorescenceAgeNotice").isVisible())
        self.assertTrue(self.ui.find("fluorescenceInvalidAgeNotice").isVisible())
        self.backend.setAgeProfile("auto"); self.ui.settle()
        self.backend.setAgeProfile("neonatal"); self.ui.settle()
        self.assertEqual(self.backend.ageProfile, "auto")
        for language in ("en", "zh"):
            self.backend.setLanguage(language)
            for width, height in ((1210, 820), (900, 620)):
                self.ui.window.setWidth(width); self.ui.window.setHeight(height); self.ui.settle()
                rail = self.ui.find("fluorescenceAgeRail")
                rail_rect = rail.mapRectToScene(rail.boundingRect())
                drop = self.ui.find("sourceDropZone")
                self.assertLess(rail_rect.bottom(), drop.mapRectToScene(drop.boundingRect()).top())
                self.assertGreater(rail.width(), 220)
                self.assertLessEqual(rail_rect.right(), width)
                self.ui.capture("age-host-" + language + "-" + str(width))
        self.assertFalse(self.ui.warnings)

    def test_complete_channel_list_manual_mapping_and_display_preselection(self):
        self.backend._image_info = {"path": str(self.folder / "source.tif"), "name": "Seven channel fixture.tif",
            "series_count": 1, "selected_series": 0, "time_count": 1, "selected_time": 0,
            "channel_count": 7, "channel_hints": ["Source " + str(i + 1) for i in range(7)],
            "width": 256, "height": 256, "z_count": 1, "axes": "CYX"}
        self.backend._channel_roles = ["Other"] * 7
        self.backend.changed.emit(); self.ui.settle()
        self.assertEqual(len(self.ui.named("countSourceChannel_")), 7)
        self.assertEqual(self.ui.find("countDapiChoice").property("count"), 8)
        self.assertEqual([self.ui.find("countDisplayChannel_" + str(i)).property("text") for i in range(7)],
                         ["C" + str(i + 1) for i in range(7)])
        self.assertFalse(self.ui.find("countStart").isEnabled())
        choice = self.scroll_to("countDapiChoice")
        choice.forceActiveFocus()
        QTest.keyClick(self.ui.window, Qt.Key_Home)
        QTest.keyClick(self.ui.window, Qt.Key_Down); self.ui.settle()
        self.assertEqual(self.backend.countDapiChannel, 0)
        self.assertEqual(self.backend.countMappingMode, "manual")
        support = self.scroll_to("countSupportChoice")
        support.forceActiveFocus()
        QTest.keyClick(self.ui.window, Qt.Key_Home)
        QTest.keyClick(self.ui.window, Qt.Key_Down)
        QTest.keyClick(self.ui.window, Qt.Key_Down); self.ui.settle()
        self.assertEqual(self.backend.countSupportChannel, 1)
        marker = self.scroll_to("countSupportMarker")
        marker.forceActiveFocus()
        QTest.keyClick(self.ui.window, Qt.Key_Home); self.ui.settle()
        self.assertEqual(self.backend.countSupportMarker, "eGFP")
        checkbox = self.scroll_to("countDisplayChannel_6")
        previous = 6 in self.backend.countDisplayChannels
        self.ui.click(checkbox)
        self.assertEqual(6 in self.backend.countDisplayChannels, not previous)
        self.assertFalse(self.ui.warnings)

    def test_counting_analysis_progress_is_indeterminate_and_cancel_stops_it(self):
        self.backend._busy = True
        self.backend._worker_context = WorkerContext("cell-counting", "fixture-progress")
        self.backend._status = "running"
        self.backend.changed.emit(); self.ui.settle()
        bar = self.ui.find("countActivityBar")
        for progress in (-1, 0, .4, 1):
            self.backend._progress = progress
            self.backend.changed.emit(); self.ui.settle()
            self.assertEqual(self.backend.progress, progress)
            self.assertEqual(bar.property("progress"), -1)
            self.assertTrue(bar.property("animating"))
            dots = self.ui.named("activitySegment_")
            self.assertTrue(dots)
            self.assertTrue(all(dot.width() == dot.height() == 5 for dot in dots))
            self.assertFalse(any("本阶段" in str(item.property("text") or "") or
                                 "This stage" in str(item.property("text") or "") for item in self.ui.items()))
        for status in ("cancelling", "cancelled", "completed"):
            self.backend._status = status
            self.backend.changed.emit(); self.ui.settle()
            self.assertFalse(bar.property("animating"), status)
            self.assertFalse(bar.isVisible(), status)
        self.assertFalse(self.ui.warnings)

    def test_input_cards_share_surface_and_distribute_content_for_long_inputs(self):
        self.backend._image_info = {"path": str(self.folder / "source.tif"), "name": "Long counting image name " * 12 + ".tif",
            "series_count": 1, "selected_series": 0, "time_count": 1, "selected_time": 0,
            "channel_count": 8, "channel_hints": ["Metadata name " * 10 for _ in range(8)],
            "width": 256, "height": 256, "z_count": 1, "axes": "CYX",
            "dapi_channel": 0, "support_channel": 1, "support_marker": "eGFP",
            "display_channels": list(range(8)), "physical_size_um": {"x": .2, "y": .2},
            "thumbnail_url": (self.folder / "composite.png").as_uri()}
        self.backend._channel_roles = ["DAPI", "eGFP"] + ["Other"] * 6
        for language in ("zh", "en"):
            self.backend.setLanguage(language)
            for width, height in ((1210, 820), (900, 620)):
                self.ui.window.setWidth(width); self.ui.window.setHeight(height)
                for mode in ("light", "dark"):
                    self.backend.setAppearanceMode(mode)
                    self.backend.changed.emit(); self.ui.settle()
                    workflow = self.ui.find("countWorkflowCard")
                    status = self.ui.find("countStatusCard")
                    information = self.ui.find("countInformationCard")
                    display = self.ui.find("countDisplayCard")
                    self.assertEqual(workflow.property("color"), status.property("color"))
                    if self.ui.window.property("wide"):
                        self.assertAlmostEqual(display.mapToScene(QPointF(0, display.height())).y(),
                                               information.mapToScene(QPointF(0, information.height())).y(), delta=1)
                    for prefix, limit in (("countStatus", 16), ("countWorkflow", 56), ("countInformation", 16)):
                        group = self.ui.find(prefix + "Groups")
                        self.assertLessEqual(group.height(), group.property("implicitHeight") + limit + 1)
                        self.assertAlmostEqual(group.y() * 2 + group.height(), group.parentItem().height(), delta=1)
                    for card in (status, workflow, information, display):
                        for item in visual_items(card):
                            if item.isVisible() and item.inherits("QQuickText"):
                                self.assertGreaterEqual(item.mapRectToItem(card, item.boundingRect()).left(), -1)
                                self.assertLessEqual(item.mapRectToItem(card, item.boundingRect()).right(), card.width() + 1)
                    self.ui.capture("count-input-" + language + "-" + str(width) + "-" + mode)
        self.assertFalse(self.ui.warnings)

    def test_count_start_requires_physical_calibration_and_single_plane_needs_only_xy(self):
        self.backend._image_info = {"path": str(self.folder / "calibration.tif"), "name": "Synthetic calibration fixture.tif",
            "series_count": 1, "selected_series": 0, "time_count": 1, "selected_time": 0,
            "channel_count": 2, "channel_hints": ["DAPI", "eGFP"], "width": 256, "height": 256,
            "z_count": 4, "dapi_channel": 0, "support_channel": 1, "support_marker": "eGFP",
            "display_channels": [0, 1], "physical_size_um": {"x": .2, "y": .2}}
        self.backend._channel_roles = ["DAPI", "eGFP"]
        self.backend._series_index = 0; self.backend._time_index = 0
        self.backend._status = "ready"; self.backend._stage = ""; self.backend._message = ""
        self.backend.changed.emit(); self.ui.settle()
        self.assertFalse(self.backend.countCalibrationReady)
        self.assertFalse(self.ui.find("countStart").isEnabled())
        self.assertTrue(self.backend.countInputError)
        self.assertEqual(self.ui.find("countInputError").property("text"), self.backend.countInputError)
        self.ui.capture("missing-z-calibration")
        self.backend._image_info["z_count"] = 1
        self.backend.changed.emit(); self.ui.settle()
        self.assertTrue(self.backend.countCalibrationReady)
        self.assertTrue(self.ui.find("countStart").isEnabled())
        self.assertNotIn("z", self.backend.imageInfo["physical_size_um"])
        self.backend._image_info["physical_size_um"] = {"x": .2}
        self.backend.changed.emit(); self.ui.settle()
        self.assertFalse(self.ui.find("countStart").isEnabled())
        self.assertTrue(self.backend.countInputError)
        self.assertFalse(self.ui.warnings)

    def test_remove_imported_image_and_parameter_lock(self):
        self.backend._image_info = {"path": str(self.folder / "source.tif"), "name": "Imported fixture.tif",
            "series_count": 1, "selected_series": 0, "time_count": 1, "selected_time": 0,
            "channel_count": 2, "channel_hints": ["DAPI", "eGFP"], "width": 256, "height": 256, "z_count": 4}
        self.backend._channel_roles = ["DAPI", "eGFP"]
        self.backend.changed.emit(); self.ui.settle()
        self.assertTrue(self.ui.find("countRemoveImage").isEnabled())
        self.backend._status = "running"
        self.backend._busy = True
        self.backend.changed.emit(); self.ui.settle()
        for name in ("countRemoveImage", "countChangeImage", "countDapiChoice", "countSupportChoice", "countDisplayChannel_0"):
            self.assertFalse(self.ui.find(name).isEnabled(), name)
        self.backend._status = "ready"
        self.backend._busy = False
        self.backend.changed.emit(); self.ui.settle()
        self.ui.click("countRemoveImage")
        self.assertFalse(self.backend.imageInfo)
        self.assertFalse(self.ui.warnings)

    def test_review_layout_two_languages_two_sizes_and_saved_reopen(self):
        editor = self.review()
        self.assertEqual([self.ui.find("countVisibleChannel_" + str(i)).property("text") for i in range(2)], ["C1", "C2"])
        for language in ("en", "zh"):
            self.backend.setLanguage(language)
            for width, height in ((1210, 820), (900, 620)):
                self.ui.window.setWidth(width); self.ui.window.setHeight(height)
                self.ui.settle()
                canvas = self.ui.find("countCanvas")
                for name in ("countTotal", "countConfirm", "countCanvas", "countSelectMode", "countDelete", "countUndo", "countRedo", "countFit"):
                    item = self.ui.find(name)
                    rect = item.mapRectToScene(item.boundingRect())
                    self.assertGreaterEqual(rect.left(), 0, name)
                    self.assertLessEqual(rect.right(), width + 1, name)
                self.assertGreater(canvas.width(), 400)
                self.assertGreaterEqual(canvas.height(), 300)
                self.ui.capture(language + "-" + str(width))
        self.scroll_to("countConfirm"); self.ui.click("countConfirm")
        self.wait_saved()
        self.assertEqual(self.backend._jobs[0]["status"], "completed")
        self.assertEqual(self.backend._jobs[0]["count"], 3)
        self.assertTrue((self.folder / "results/latest.json").is_file())
        self.reopen_saved()
        self.assertEqual(editor.total, 3)
        self.assertTrue(editor.confirmedState)
        self.assertFalse(self.ui.warnings)

    def wait_saved(self):
        self.ui.wait_until(lambda: self.backend._jobs[0]["status"] == "completed"
                          and not self.backend.counting.available and not self.backend.counting.busy,
                          timeout_ms=6000)
        self.ui.settle()
        self.assertTrue(self.ui.find("countInputGrid").isVisible())
        self.assertFalse(self.backend.imageInfo)

    def reopen_saved(self):
        self.ui.press_navigation("page:jobs")
        self.ui.click("taskRow_" + self.run_id)
        self.ui.click("reopenCountingJob")
        self.ui.wait_until(lambda: self.backend.counting.available and not self.backend.counting.busy)
        self.ui.settle()

    def test_successful_save_returns_to_image_import_and_cancel_keeps_saved_results(self):
        editor = self.review()
        self.assertEqual(self.ui.named("countChooseNextImage"), [])
        self.ui.click("countConfirm")
        self.wait_saved()
        latest = self.folder / "results/latest.json"
        saved_pointer = latest.read_bytes()
        with patch.object(QFileDialog, "getOpenFileName", return_value=("", "")) as dialog:
            self.ui.click("countChooseImage")
        dialog.assert_called_once()
        self.assertFalse(editor.available)
        self.assertEqual(latest.read_bytes(), saved_pointer)
        for language in ("en", "zh"):
            self.backend.setLanguage(language)
            self.ui.window.setWidth(900); self.ui.window.setHeight(620); self.ui.settle()
            self.assertTrue(self.ui.find("countChooseImage").isEnabled())
            self.assertFalse(self.ui.find("countConfirm").isVisible())
            self.ui.capture("saved-waiting-image-" + language)
        self.reopen_saved()
        self.assertEqual(editor.total, 3)
        self.assertTrue(editor.confirmedState)
        self.assertEqual(latest.read_bytes(), saved_pointer)
        self.assertFalse(self.ui.warnings)

    def test_z_slider_channel_visibility_and_label_controls_do_not_recount(self):
        self.analysis["z_count"] = 3
        self.analysis_path.write_text(json.dumps(self.analysis))
        cache = self.folder / ".editor/slices"
        cache.mkdir(parents=True)
        for z in range(3):
            image = QImage(256, 256, QImage.Format.Format_RGB32)
            image.fill(QColor("#404040"))
            self.assertTrue(image.save(str(cache / f"dapi-z{z:05d}.png")))
        editor = self.review()
        for z in range(3):
            editor._slice_cache[editor._slice_key(z)] = cache / f"dapi-z{z:05d}.png"
        before = editor.points
        slider = self.scroll_to("countZSlider")
        slider.forceActiveFocus()
        QTest.keyClick(self.ui.window, Qt.Key_Right)
        QTest.keyClick(self.ui.window, Qt.Key_Right)
        self.ui.wait_until(lambda: editor.displayedZIndex == 2 and not editor.busy)
        expected_z = self.backend.locale.translate("Z 层 %1 / %2").replace("%1", "3").replace("%2", "3")
        self.assertEqual(self.ui.find("countZReadout").property("text"), expected_z)
        self.assertEqual(self.ui.named("countPreviewQuality"), [])
        self.assertEqual(editor.points, before)
        self.assertTrue(self.ui.find("countVisibleChannel_0").isEnabled())
        self.scroll_to("countAddMode"); self.ui.click("countAddMode")
        self.click_point(115, 130)
        self.assertEqual(editor.points[-1]["z"], 2)
        self.assertEqual(editor.total, 4)
        self.scroll_to("countMip"); self.ui.click("countMip")
        self.ui.wait_until(lambda: editor.zIndex == -1 and not editor.busy)
        count = editor.total
        self.scroll_to("countVisibleChannel_1"); self.ui.click("countVisibleChannel_1")
        self.ui.wait_until(lambda: not editor.busy)
        self.assertFalse(editor.channels[1]["visible"])
        self.assertEqual(editor.total, count)
        labels = self.scroll_to("countShowLabels"); self.ui.click(labels)
        self.assertFalse(editor.showLabels)
        size = self.scroll_to("countLabelSize")
        old_size = editor.labelSize
        size.forceActiveFocus()
        QTest.keyClick(self.ui.window, Qt.Key_Up); self.ui.settle()
        self.assertGreater(editor.labelSize, old_size)
        self.assertEqual(editor.total, count)
        self.assertFalse(self.ui.warnings)

    def test_z_slider_press_release_and_page_exit_end_scrubbing(self):
        self.analysis["z_count"] = 3
        self.analysis_path.write_text(json.dumps(self.analysis))
        editor = self.review()
        for z in range(3):
            editor._slice_cache[editor._slice_key(z, 0)] = self.folder / "composite.png"
        before = editor.points

        def press_slider():
            slider = self.scroll_to("countZSlider")
            handle = slider.property("handle")
            position = handle.mapToScene(handle.boundingRect().center())
            point = QPoint(round(position.x()), round(position.y()))
            QTest.mousePress(self.ui.window, Qt.LeftButton, Qt.NoModifier, point)
            self.ui.settle(30)
            self.assertTrue(editor.scrubbing)
            return point

        point = press_slider()
        QTest.mouseRelease(self.ui.window, Qt.LeftButton, Qt.NoModifier, point)
        self.ui.settle(30)
        self.assertFalse(editor.scrubbing)
        point = press_slider()
        self.backend.setPage("jobs"); self.ui.settle()
        self.assertFalse(editor.scrubbing)
        QTest.mouseRelease(self.ui.window, Qt.LeftButton, Qt.NoModifier, point)
        self.backend.setPage("analysis"); self.ui.settle()
        point = press_slider()
        self.backend._module_states["project-leap-2d"].update(installed=True, status="ready", message="")
        self.backend.selectModule("project-leap-2d"); self.ui.settle()
        self.assertFalse(editor.scrubbing)
        QTest.mouseRelease(self.ui.window, Qt.LeftButton, Qt.NoModifier, point)
        self.assertEqual(editor.points, before)
        self.assertFalse(self.ui.warnings)

    def test_thousand_points_real_paint_pan_and_anchor_hit_remain_responsive(self):
        self.analysis.update(width=1024, height=1024, auto_count=1000, default_label_size=18,
            points=[{"id": f"auto-{i + 1}", "label_id": i + 1, "x": float(32 + i % 40 * 24),
                     "y": float(64 + i // 40 * 36), "z": None, "source": "auto"} for i in range(1000)])
        self.analysis_path.write_text(json.dumps(self.analysis))
        for name in ("C1.png", "C2.png", "composite.png"):
            image = QImage(1024, 1024, QImage.Format.Format_RGB32)
            image.fill(QColor("#404040"))
            self.assertTrue(image.save(str(self.folder / name)))
        editor = self.review()
        canvas = self.ui.find("countCanvas")
        overlay = self.ui.find("countPointOverlay")
        self.ui.wait_until(lambda: len(self.labels()) == 1000, timeout_ms=15000)
        # Selection is explicit; rectangle selection is intentionally absent.
        editor._selected = {point["id"] for point in editor.points[:-1]}
        editor.selectPoint(editor.points[-1]["id"], True)
        self.ui.settle()
        self.assertEqual(len(editor.selectedIds), 1000)
        rendering = canvas.property("renderPoints")
        rendering = rendering.toVariant() if hasattr(rendering, "toVariant") else rendering
        self.assertEqual(sum(point["selected"] for point in rendering), 1000)
        paint_seconds = []
        for _ in range(3):
            painted = QSignalSpy(overlay.painted)
            start = time.perf_counter()
            expression = QQmlExpression(self.ui.engine.rootContext(), overlay, "requestPaint()")
            expression.evaluate()
            self.assertFalse(expression.hasError())
            if painted.count() == 0:
                self.assertTrue(painted.wait(2000), "The actual QML Canvas did not paint")
            paint_seconds.append(time.perf_counter() - start)
        self.assertLess(max(paint_seconds), .75, paint_seconds)
        self.assertLess(sorted(paint_seconds)[1], .25, paint_seconds)
        # Space-drag produces a real paint after every move without copying N points N times.
        canvas.forceActiveFocus()
        start = self.point(500, 500)
        pan_seconds = []
        QTest.keyPress(self.ui.window, Qt.Key_Space)
        QTest.mousePress(self.ui.window, Qt.LeftButton, Qt.NoModifier, start)
        for distance in (10, 20, 30):
            painted = QSignalSpy(overlay.painted)
            began = time.perf_counter()
            QTest.mouseMove(self.ui.window, QPoint(start.x() + distance, start.y() + distance), 1)
            if painted.count() == 0:
                self.assertTrue(painted.wait(2000))
            pan_seconds.append(time.perf_counter() - began)
        QTest.mouseRelease(self.ui.window, Qt.LeftButton, Qt.NoModifier, QPoint(start.x() + 30, start.y() + 30))
        QTest.keyRelease(self.ui.window, Qt.Key_Space)
        self.assertLess(max(pan_seconds), .75, pan_seconds)
        self.assertGreater(canvas.property("panX"), 25)
        self.assertEqual(editor.total, 1000)
        # Hide labels through the UI so the click reaches the anchor hit-test path.
        self.scroll_to("countShowLabels"); self.ui.click("countShowLabels")
        self.scroll_to("countFit"); self.ui.click("countFit")
        began = time.perf_counter()
        self.click_point(32, 64)
        hit_seconds = time.perf_counter() - began
        self.assertEqual(editor.selectedIds, ["auto-1"])
        self.assertLess(hit_seconds, .75)
        self.ui.write_record("thousand-point-response", {
            "points": 1000, "selected_during_paint": 1000, "paint_seconds": paint_seconds,
            "pan_seconds": pan_seconds, "anchor_hit_seconds": hit_seconds,
            "paint_signal": "QQuickCanvasItem.painted", "total_preserved": editor.total})
        self.assertFalse(self.ui.warnings)

    def test_body_only_unrecognized_explicit_accept_remove_undo_and_saved_identity(self):
        observation = {"id": "obs-body-8", "kind": "body", "artifact_id": "support_bodies", "label_id": 8,
            "bounds_zyx": [[0, 3], [120, 165], [100, 150]], "plane_anchors": {"1": [120, 140], "2": [122, 143]},
            "decision": "review", "reasons": ["synthetic_ui_fixture"], "metrics": {"fixture": True}}
        body = {"id": "body-only-cell", "cell_id": "body-only-cell", "source": "support_body", "status": "review",
            "label_artifact_id": "support_bodies", "label_id": 8, "x": 120, "y": 140, "z": 1,
            "source_observations": [observation], "plane_anchors": observation["plane_anchors"],
            "plane_anchor_sources": {z: {"kind": "observation", "observation_id": observation["id"]}
                                     for z in observation["plane_anchors"]},
            "association": {"status": "unmatched", "method": "native_3d_enclosure"}}
        self.analysis.update(schema_version=2, identity_model="multi_observation_cell_v1", z_count=3,
            display_channels=[0, 1], support_channel=1, support_marker="Biocytin", auto_count=0, labels_path="labels.npy",
            channel_roles=["DAPI", "Biocytin"], points=[body], label_artifacts={
                "dapi_nuclei": {"path": "labels.npy", "kind": "nucleus", "axes": "ZYX", "origin_zyx": [0, 0, 0], "shape_zyx": [3, 256, 256]},
                "support_bodies": {"path": "body-labels.npy", "kind": "body", "axes": "ZYX", "origin_zyx": [0, 0, 0], "shape_zyx": [3, 256, 256]}})
        self.analysis["channels"][1].update(name="Biocytin", role="Biocytin", color="#FFFFFF")
        # These identity sentinels are never decoded or used as scientific masks.
        for name in ("labels.npy", "body-labels.npy"):
            (self.folder / name).write_bytes(("immutable-ui-evidence-" + name).encode())
        self.analysis_path.write_text(json.dumps(self.analysis))
        self.backend.setLanguage("zh")
        editor = self.review()
        self.assertEqual((editor.total, editor.pendingCount), (0, 1))
        self.assertEqual(len(self.labels()), 0)
        self.assertTrue(self.ui.find("countConfirm").isEnabled())
        self.ui.capture("body-only-unrecognized")
        self.ui.click("countConfirm")
        self.wait_saved()
        initial = json.loads((self.folder / "results/latest.json").read_text())
        initial_session_path = self.folder / "results" / initial["session_path"]
        initial_session = json.loads(initial_session_path.read_text())
        self.assertEqual((initial_session["count"], initial_session["pending_count"]), (0, 1))
        with (initial_session_path.parent / initial_session["csv_path"]).open(newline="", encoding="utf-8") as stream:
            self.assertEqual(list(csv.DictReader(stream)), [])
        self.reopen_saved()
        self.click_point(120, 140)
        self.assertEqual((editor.total, editor.pendingCount), (0, 1))
        self.assertEqual(editor.selectedIds, ["body-only-cell"])
        self.ui.click("countAcceptSelected")
        self.assertEqual((editor.total, editor.pendingCount), (1, 0))
        self.assertEqual(editor.points[0]["id"], "body-only-cell")
        self.click_label("body-only-cell")
        accepted = editor.points
        self.ui.click("countDelete")
        self.assertEqual((editor.total, editor.neuronCount), (0, 0))
        self.assertEqual(editor.visiblePoints, [])
        self.assertEqual(editor.points[0]["status"], "accepted")
        self.assertTrue(editor.points[0]["deleted"])
        self.ui.click("countUndo")
        self.assertEqual(editor.points, accepted)
        self.ui.click("countConfirm")
        self.wait_saved()
        latest = json.loads((self.folder / "results/latest.json").read_text())
        session_path = self.folder / "results" / latest["session_path"]
        session = json.loads(session_path.read_text())
        self.assertEqual(session["count"], 1)
        self.assertEqual(session["points"][0]["source"], "support_body")
        self.assertEqual(session["points"][0]["source_observations"], [observation])
        self.assertFalse(editor.available)
        self.ui.capture("body-only-saved-waiting-image")
        self.assertFalse(self.ui.warnings)

    def three_class_review(self, *, include_neuron=False):
        self.analysis.update(schema_version=2, display_channels=[1], support_channel=1,
                             support_marker="eGFP", auto_count=1)
        for index, point in enumerate(self.analysis["points"]):
            if index == 1:
                point.update(x=110., y=100.)
            point.update(z=0, status="review" if index < 2 else "accepted",
                         plane_anchors={"0": [point["x"], point["y"]]})
        self.analysis["points"][0]["review_hint"] = "likely_astrocyte"
        if include_neuron:
            self.analysis["points"].append({"id": "neuron-99", "label_id": 99, "x": 220., "y": 220.,
                "z": 0, "source": "auto", "status": "excluded", "plane_anchors": {"0": [220., 220.]}})
        self.analysis_path.write_text(json.dumps(self.analysis))
        editor = self.review()
        self.ui.wait_until(lambda: editor.frameReady and not editor.busy
                          and self.ui.find("countConfirm").isEnabled())
        return editor

    def assert_four_category_header(self, counts, capture_prefix):
        categories = (("Astrocyte", "Astrocyte"), ("HighLikelihood", "High Likelihood"),
                      ("Unrecognized", "Unrecognized"), ("Neurons", "Neurons"))
        for language in ("en", "zh"):
            self.backend.setLanguage(language); self.ui.settle()
            for width, height in ((1210, 820), (900, 620)):
                self.ui.window.setWidth(width); self.ui.window.setHeight(height); self.ui.settle()
                total = self.ui.find("countTotal")
                total_rect = total.mapRectToScene(total.boundingRect())
                rectangles = []
                for (key, title), count in zip(categories, counts):
                    item = self.ui.find("countCategory_" + key)
                    self.assertTrue(item.isVisible())
                    self.assertEqual(item.property("text"), title + ": " + str(count))
                    self.assertGreaterEqual(item.width(), item.property("implicitWidth") - 1)
                    rect = item.mapRectToScene(item.boundingRect())
                    self.assertGreater(rect.top(), total_rect.bottom())
                    self.assertLessEqual(rect.right(), width - 15)
                    if rectangles:
                        self.assertLess(rectangles[-1].right(), rect.left())
                        self.assertAlmostEqual(rectangles[0].center().y(), rect.center().y(), delta=1)
                    rectangles.append(rect)
                summary = self.ui.find("countSummary")
                self.assertAlmostEqual(summary.mapRectToScene(summary.boundingRect()).left(), total_rect.left(), delta=1)
                self.assertEqual(self.ui.named("countDraftStatus"), [])
                self.assertEqual(self.ui.named("countUnrecognizedGroup"), [])
                self.assertFalse(any("Other unrecognized" in text for text in self.ui.texts()))
                confirm = self.ui.find("countConfirm")
                confirm_rect = confirm.mapRectToScene(confirm.boundingRect())
                self.assertAlmostEqual(confirm_rect.center().y(), total_rect.center().y(), delta=1)
                self.assertGreater(confirm_rect.left(), total_rect.right())
                self.ui.capture(capture_prefix + "-" + language + "-" + str(width))

    def test_four_horizontal_category_counts_do_not_double_count_high_likelihood(self):
        points = []
        for index in range(770):
            x, y = float(16 + index % 35 * 28), float(30 + index // 35 * 40)
            point = {"id": "fixture-" + str(index), "label_id": index + 1,
                     "x": x, "y": y, "z": 0, "source": "auto",
                     "status": "accepted" if index < 139 else "review",
                     "plane_anchors": {"0": [x, y]}}
            if 139 <= index < 152:
                point["review_hint"] = "likely_astrocyte"
            points.append(point)
        self.analysis.update(schema_version=2, width=1024, height=1024, points=points,
                             display_channels=[1], auto_count=139)
        self.analysis_path.write_text(json.dumps(self.analysis))
        for name in ("C1.png", "C2.png", "composite.png"):
            image = QImage(1024, 1024, QImage.Format.Format_RGB32)
            image.fill(QColor("#404040"))
            self.assertTrue(image.save(str(self.folder / name)))
        editor = self.review()
        self.ui.wait_until(lambda: editor.frameReady)
        self.assertFalse(editor.message)
        self.assertEqual(editor.pendingCount, 631)
        self.assert_four_category_header((139, 13, 618, 0), "four-categories-770")
        self.assertFalse(self.ui.warnings)

    def test_unrecognized_can_remain_when_saving_and_four_categories_stay_visible(self):
        editor = self.three_class_review(include_neuron=True)
        self.assertEqual((editor.total, editor.pendingCount, editor.neuronCount), (1, 2, 1))
        self.assertEqual((editor.likelyUnrecognizedCount, editor.ordinaryUnrecognizedCount), (1, 1))
        self.assertTrue(self.ui.find("countConfirm").isEnabled())
        self.assertEqual([p["id"] for p in editor.visiblePoints], ["auto-1", "auto-2", "auto-7", "neuron-99"])
        self.assertEqual(len(self.labels()), 1)
        self.assertEqual(self.ui.named("countShowExcluded"), [])
        self.assertEqual(self.ui.named("countPendingNotice"), [])
        self.assertEqual([c["index"] for c in editor.channels], [1])
        self.assertEqual(len(self.ui.named("countVisibleChannel_")), 1)
        pointer = self.ui.find("countCanvasPointer")
        QTest.mouseMove(self.ui.window, self.point(64, 64)); self.ui.settle(320)
        self.assertEqual(pointer.property("hoverHint"), self.backend.locale.translate("高Astrocyte可能性"))
        self.assertEqual((editor.total, editor.pendingCount), (1, 2))
        self.ui.capture("likely-unrecognized-hover")
        QTest.mouseMove(self.ui.window, self.point(110, 100)); self.ui.settle(320)
        self.assertEqual(pointer.property("hoverHint"), "")
        self.assert_four_category_header((1, 1, 1, 1), "four-categories")
        self.ui.window.setWidth(1210); self.ui.window.setHeight(820); self.ui.settle()
        self.click_point(220, 220)
        self.assertEqual((editor.total, editor.neuronCount), (1, 1))
        self.assertTrue(self.ui.find("countAcceptSelected").isEnabled())
        self.ui.click("countAcceptSelected")
        self.assertEqual((editor.total, editor.neuronCount), (2, 0))
        self.ui.click("countUndo")
        self.assertEqual((editor.total, editor.neuronCount), (1, 1))
        self.click_point(220, 220); self.ui.click("countDelete")
        self.assertEqual((editor.total, editor.neuronCount), (1, 0))
        neuron = next(p for p in editor.points if p["id"] == "neuron-99")
        self.assertEqual(neuron["status"], "excluded")
        self.assertTrue(neuron["deleted"])
        self.ui.click("countUndo")
        self.assertEqual((editor.total, editor.neuronCount), (1, 1))
        self.ui.click("countConfirm")
        self.wait_saved()
        self.assertFalse(editor.available)
        latest = json.loads((self.folder / "results/latest.json").read_text())
        session_path = self.folder / "results" / latest["session_path"]
        session = json.loads(session_path.read_text())
        self.assertEqual((session["count"], session["pending_count"]), (1, 2))
        self.assertEqual(len(session["points"]), 4)
        with (session_path.parent / session["csv_path"]).open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row["id"] for row in rows], ["auto-7"])
        self.reopen_saved()
        self.scroll_to("countToggleSettings"); self.ui.click("countToggleSettings")
        self.assertTrue(self.ui.find("countRunSettings").isVisible())
        self.assertFalse(self.ui.find("countInputGrid").isVisible())
        self.ui.capture("saved-with-unrecognized-readonly-settings")
        self.assertEqual((editor.total, editor.pendingCount), (1, 2))
        self.assertTrue(editor.confirmedState)
        self.assertFalse(self.ui.warnings)

    def test_only_high_likelihood_click_accepts_and_cmd_and_add_mode_preserve_class_rules(self):
        editor = self.three_class_review(include_neuron=True)
        original_ids = [p["id"] for p in editor.points]
        self.click_point(110, 100)
        self.assertEqual(editor.selectedIds, ["auto-2"])
        self.assertEqual((editor.total, editor.pendingCount), (1, 2))
        self.ui.click("countAcceptSelected")
        self.assertEqual((editor.total, editor.pendingCount), (2, 1))
        self.ui.click("countUndo")
        self.click_point(64, 64)
        self.assertEqual((editor.total, editor.pendingCount), (2, 1))
        self.assertEqual([p["id"] for p in editor.points], original_ids)
        self.assertEqual(editor.points[0]["source"], "auto")
        self.click_point(64, 64)
        self.assertEqual((editor.total, editor.pendingCount), (2, 1))
        self.ui.click("countUndo")
        self.assertEqual((editor.total, editor.pendingCount), (1, 2))
        self.click_point(200, 30)
        for x, y in ((64, 64), (110, 100), (180, 180), (220, 220)):
            self.click_point(x, y, Qt.ControlModifier)
        self.assertEqual(set(editor.selectedIds), set(original_ids))
        self.assertEqual((editor.total, editor.pendingCount, editor.neuronCount), (1, 2, 1))
        self.click_point(200, 30)
        self.click_point(64, 64, Qt.ControlModifier)
        self.click_point(110, 100, Qt.ControlModifier)
        self.ui.click("countAcceptSelected")
        self.assertEqual((editor.total, editor.pendingCount), (3, 0))
        self.assertEqual(sorted(int(label["text"]) for label in self.labels()), [1, 2, 3])
        self.ui.click("countUndo")
        self.ui.click("countAddMode")
        self.click_point(64, 64)
        self.assertEqual((editor.total, editor.pendingCount), (2, 1))
        self.assertEqual([p["id"] for p in editor.points], original_ids)
        self.click_point(110, 100)
        self.assertEqual(editor.selectedIds, ["auto-2"])
        self.assertEqual((editor.total, editor.pendingCount), (2, 1))
        self.click_point(220, 220)
        self.assertEqual(editor.selectedIds, ["neuron-99"])
        self.assertEqual((editor.total, editor.neuronCount), (2, 1))
        self.ui.click("countAcceptSelected")
        self.assertEqual((editor.total, editor.neuronCount), (3, 0))
        self.ui.click("countUndo")
        self.click_point(110, 160)
        self.assertEqual((editor.total, editor.pendingCount, editor.neuronCount), (3, 1, 1))
        self.assertEqual(len(editor.points), 5)
        self.assertEqual(editor.points[-1]["source"], "manual")
        self.assertFalse(self.ui.warnings)

    def test_mode_changes_do_not_repaint_points_and_pan_still_does(self):
        editor = self.review()
        self.ui.settle(180)
        overlay = self.ui.find("countPointOverlay")
        painted = QSignalSpy(overlay.painted)
        before = editor.points
        editor.setMode("add"); self.ui.settle()
        editor.setMode("select"); self.ui.settle()
        self.assertEqual(painted.count(), 0)
        editor.setView(editor.viewZoom, 8, 0)
        self.ui.wait_until(lambda: painted.count() > 0)
        self.assertEqual(editor.points, before)
        self.ui.write_record("overlay-repaint-scope", {"mode_change_paints": 0,
            "pan_paints": painted.count(), "points_unchanged": editor.points == before})
        self.assertFalse(self.ui.warnings)

    def test_module_navigation_preserves_count_review_and_view(self):
        editor = self.review()
        self.ui.click("countZoomIn")
        canvas = self.ui.find("countCanvas")
        zoom = canvas.property("zoom")
        editor.setView(zoom, 21, -17)
        self.backend._module_states["project-leap-2d"].update(installed=True, status="ready", message="")
        self.backend.selectModule("project-leap-2d"); self.ui.settle()
        self.assertTrue(editor.available)
        self.backend.selectModule("cell-counting"); self.ui.settle()
        self.assertTrue(editor.available)
        self.assertEqual(editor.total, 3)
        canvas = self.ui.find("countCanvas")
        self.assertEqual(canvas.property("zoom"), zoom)
        self.assertEqual(canvas.property("panX"), 21)
        self.assertEqual(canvas.property("panY"), -17)
        self.assertFalse(self.ui.warnings)

    def test_background_count_run_keeps_other_module_parameters_editable_but_blocks_new_start(self):
        self.backend._busy = True
        self.backend._worker_context = WorkerContext("cell-counting", "fixture-background-run")
        self.backend._status = "running"
        self.backend._stage = "正在分析"
        self.backend._module_states["project-leap-2d"].update(installed=True, status="ready", message="")
        self.backend.selectModule("project-leap-2d"); self.ui.settle()
        self.assertFalse(self.backend.activeModuleBusy)
        self.assertTrue(self.backend.busy)
        self.assertFalse(self.backend.parametersLocked)
        self.assertFalse(self.ui.find("fluorescenceChooseImage").isEnabled())
        self.assertEqual(self.backend.runningTask["moduleId"], "cell-counting")
        expected_stage = self.backend.locale.translate("正在分析")
        self.ui.wait_until(lambda: any(expected_stage in (item.get("label"), item.get("title"), item.get("value"))
                                      for item in self.ui.navigation_elements()))
        cancel = next(item for item in self.ui.navigation_elements() if item["identifier"] == "cancelAnalysis")
        self.assertTrue(cancel["enabled"])
        self.backend._image_info = {"path": str(self.folder / "cached.tif"), "name": "Already imported fixture.tif",
            "series_count": 1, "selected_series": 0, "time_count": 1, "selected_time": 0,
            "channel_count": 3, "channel_hints": ["DAPI", "eGFP", "KCNN2"], "width": 256,
            "height": 256, "z_count": 4, "axes": "ZCYX", "dtype": "uint16"}
        self.backend._channel_roles = ["DAPI", "eGFP", "KCNN2"]
        self.backend.changed.emit(); self.ui.settle()
        self.assertTrue(self.ui.find("markerSource_DAPI").isEnabled())
        self.assertFalse(self.ui.find("fluorescenceStart").isEnabled())
        self.backend._busy = False; self.backend._worker_context = None
        self.assertFalse(self.ui.warnings)

    def test_input_cards_and_fluorescence_support_cards_share_edges_and_equal_gaps(self):
        for language in ("en", "zh"):
            self.backend.setLanguage(language)
            for width, height in ((1210, 1120), (900, 620)):
                self.ui.window.setWidth(width); self.ui.window.setHeight(height); self.ui.settle()
                self.assertFalse(self.backend.imageInfo)
                drop = self.ui.find("countSourceDropZone")
                self.assertEqual(drop.height(), 190)
                self.assertEqual(drop.property("radius"), 10)
                self.assertTrue(self.ui.find("countChooseImage").isVisible())
                if width == 1210:
                    left = [self.ui.find(name) for name in ("countImageCard", "countMappingCard", "countDisplayCard")]
                    right = [self.ui.find(name) for name in ("countStatusCard", "countWorkflowCard", "countInformationCard")]
                    left_rects = [item.mapRectToScene(item.boundingRect()) for item in left]
                    right_rects = [item.mapRectToScene(item.boundingRect()) for item in right]
                    self.assertAlmostEqual(left_rects[0].top(), right_rects[0].top(), delta=1)
                    self.assertAlmostEqual(left_rects[-1].bottom(), right_rects[-1].bottom(), delta=1)
                    for rects in (left_rects, right_rects):
                        self.assertAlmostEqual(rects[1].top() - rects[0].bottom(), rects[2].top() - rects[1].bottom(), delta=1)
                self.ui.capture("count-empty-" + language + "-" + str(width))
        self.backend._image_info = {"path": str(self.folder / "source.tif"), "name": "Layout fixture.tif",
            "series_count": 1, "selected_series": 0, "time_count": 1, "selected_time": 0,
            "channel_count": 3, "channel_hints": ["DAPI", "eGFP", "KCNN2"], "width": 256,
            "height": 256, "z_count": 4, "axes": "ZCYX", "dtype": "uint16",
            "dapi_channel": 0, "support_channel": 1, "support_marker": "eGFP", "display_channels": [0, 1],
            "physical_size_um": {"x": .2, "y": .2, "z": 1.0}}
        self.backend._channel_roles = ["DAPI", "eGFP", "KCNN2"]
        self.backend._series_index = 0; self.backend._time_index = 0
        self.backend._status = "ready"; self.backend._stage = ""; self.backend._message = ""
        self.backend.changed.emit(); self.ui.settle()
        for language in ("en", "zh"):
            self.backend.setLanguage(language)
            for width, height in ((1210, 820), (900, 620)):
                self.ui.window.setWidth(width); self.ui.window.setHeight(height); self.ui.settle()
                for name in ("countImageCard", "countMappingCard", "countDisplayCard", "countStatusCard", "countWorkflowCard", "countInformationCard"):
                    item = self.ui.find(name)
                    rect = item.mapRectToScene(item.boundingRect())
                    self.assertGreater(item.width(), 240)
                    self.assertGreaterEqual(rect.left(), 0)
                    self.assertLessEqual(rect.right(), width + 1)
                self.ui.capture("count-input-" + language + "-" + str(width))
            self.ui.window.setWidth(1210); self.ui.window.setHeight(1120); self.ui.settle()
            self.ui.capture("count-input-full-" + language)
        self.backend._module_states["project-leap-2d"].update(installed=True, status="ready", message="")
        self.backend.selectModule("project-leap-2d")
        self.backend._image_info = {"path": str(self.folder / "fluorescence.tif"), "name": "Fluorescence fixture.tif",
            "series_count": 1, "selected_series": 0, "time_count": 1, "selected_time": 0,
            "channel_count": 3, "channel_hints": ["DAPI", "eGFP", "KCNN2"], "width": 256,
            "height": 256, "z_count": 4, "axes": "ZCYX", "dtype": "uint16"}
        self.backend._channel_roles = ["DAPI", "eGFP", "KCNN2"]
        self.backend.changed.emit()
        self.ui.window.setWidth(1210); self.ui.window.setHeight(820); self.ui.settle()
        cards = [self.ui.find(name) for name in ("fluorescenceStatusCard", "fluorescenceWorkflowCard", "fluorescenceInformationCard")]
        rects = [item.mapRectToScene(item.boundingRect()) for item in cards]
        left = self.ui.find("imageInputColumn")
        left_rect = left.mapRectToScene(left.boundingRect())
        self.assertAlmostEqual(rects[0].top(), left_rect.top(), delta=1)
        self.assertAlmostEqual(rects[-1].bottom(), left_rect.bottom(), delta=1)
        self.assertAlmostEqual(rects[1].top() - rects[0].bottom(), rects[2].top() - rects[1].bottom(), delta=1)
        self.ui.capture("fluorescence-even-gaps")
        self.assertFalse(self.ui.warnings)

    def test_update_check_button_runs_during_analysis_and_keeps_review_interactive(self):
        from test_update_coordination import ControlledMaintenanceTask, FakeUpdater
        events, checks = [], []
        self.backend._updater = FakeUpdater(events)

        def task_factory(function, *args, **kwargs):
            task = ControlledMaintenanceTask(function, args, kwargs, events)
            checks.append(task)
            return task

        def click_check():
            self.ui.press_navigation("page:settings")
            button = self.ui.find("checkUpdatesButton")
            for _ in range(8):
                if self.ui.in_view(button):
                    break
                self.ui.wheel("settingsScroll", -240)
            self.assertTrue(button.isEnabled())
            self.ui.click(button)
            self.assertTrue(self.backend.updatesBusy)
            self.assertFalse(self.backend.maintenanceBusy)
            self.assertFalse(button.isEnabled())

        def complete_check():
            self.backend._updater.state = {"status": "up_to_date", "session_in_progress": False, "can_check": True}
            checks[-1].complete({"status": "up_to_date", "updates": []})
            self.ui.settle()
            self.assertFalse(self.backend.updatesBusy)

        with patch("leap_desktop.controller.MaintenanceTask", side_effect=task_factory):
            self.backend._busy = True
            self.backend._worker_context = WorkerContext("cell-counting", "fixture-background-run")
            self.backend._status = "running"
            self.backend.changed.emit()
            click_check()
            self.assertTrue(self.backend.busy)
            complete_check()
            self.backend._busy = False
            self.backend._worker_context = None
            self.ui.press_navigation("page:analysis")
            editor = self.review()
            preview, count = editor.appliedImageUrl, editor.total
            click_check()
            self.ui.press_navigation("page:analysis")
            self.assertTrue(self.ui.find("countCanvas").isEnabled())
            self.ui.click("countAddMode")
            self.click_point(105, 125)
            self.assertEqual(editor.total, count + 1)
            self.assertEqual(editor.appliedImageUrl, preview)
            complete_check()
        self.assertEqual(len(checks), 2)
        self.assertFalse(self.ui.warnings, self.ui.warnings)

    def test_maintenance_disables_all_review_input_and_shortcuts(self):
        editor = self.review()
        self.ui.click("countAddMode"); self.click_point(100, 140)
        self.ui.click("countSelectMode")
        self.click_label("auto-1"); self.click_label("auto-2", Qt.ControlModifier)
        self.assertTrue(self.ui.find("countMerge").isEnabled())
        canvas = self.ui.find("countCanvas")
        controls = ["countConfirm", "countSelectMode", "countAddMode", "countMerge", "countDelete", "countUndo", "countRedo",
                    "countFit", "countZoomIn", "countZoomOut", "countMip", "countZSlider", "countShowLabels",
                    "countLabelSize", "countVisibleChannel_0", "countToggleSettings", "countCanvasPointer", "countAcceptSelected"]
        before = editor.points
        draft = (self.folder / "draft.json").read_bytes()
        zoom, pan = canvas.property("zoom"), canvas.property("panX")
        self.backend._maintenance_busy = True
        self.backend.changed.emit(); self.ui.settle()
        self.assertFalse(canvas.isEnabled())
        for name in controls:
            self.assertFalse(self.ui.find(name).isEnabled(), name)
        for label in self.ui.named("countPoint_"):
            self.assertFalse(label.isEnabled())
        # Direct QTest delivery still must not reach disabled controls or shortcuts.
        QTest.keySequence(self.ui.window, QKeySequence(QKeySequence.StandardKey.Undo))
        QTest.keySequence(self.ui.window, QKeySequence(QKeySequence.StandardKey.Redo))
        for key in (Qt.Key_Delete, Qt.Key_A, Qt.Key_V, Qt.Key_M, Qt.Key_Plus):
            QTest.keyClick(self.ui.window, key)
        self.click_point(120, 120)
        self.ui.wheel("countCanvas", 120)
        self.assertEqual(editor.points, before)
        self.assertEqual(canvas.property("zoom"), zoom)
        self.assertEqual(canvas.property("panX"), pan)
        self.assertEqual((self.folder / "draft.json").read_bytes(), draft)
        self.assertFalse((self.folder / "results/latest.json").exists())
        self.backend._maintenance_busy = False
        self.backend.changed.emit(); self.ui.settle()
        self.assertTrue(canvas.isEnabled())
        self.assertTrue(self.ui.find("countConfirm").isEnabled())
        self.assertFalse(self.ui.warnings)


if __name__ == "__main__":
    unittest.main()
