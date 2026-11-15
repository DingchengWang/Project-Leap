"""Four marker rows backed by the real Controller's physical-source mapping.

All images and state in this test are disposable UI fixtures. No importer,
analysis worker, Fiji process, or original scientific result is run or modified.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.refinement_ui_probe import QtUiHarness, visual_items, fixture_previews
from PySide6.QtCore import QPointF
from PySide6.QtQml import QQmlExpression
from PySide6.QtWidgets import QApplication
from leap_desktop.controller import Controller
from leap_desktop.paths import AppPaths
from leap_desktop import modules
import shiboken6

KEYS = ("DAPI", "eGFP", "GFAP", "Target")
COLORS = ("#007aff", "#34c759", "#ffd60a", "#ff3b30")


class MarkerMappingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="leap-marker-ui-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.paths = AppPaths(Path(self.temp.name) / "home")
        self.initial = patch.object(Controller, "_initial_status", return_value=None)
        self.initial.start(); self.addCleanup(self.initial.stop)
        self.backend = Controller(self.paths)
        self.backend._timer.stop(); self.backend._refresh_modules()
        self.backend._module_states[modules.DEFAULT_MODULE_ID].update(installed=True, status="ready", message="")
        self.backend._component_info = self.backend._module_states[modules.DEFAULT_MODULE_ID]
        self.preview = fixture_previews(Path(self.temp.name) / "preview")[0]["path"]
        output = os.environ.get("LEAP_MARKER_UI_EVIDENCE_DIR")
        self.ui = QtUiHarness(self.backend, output=Path(output) / self.id().split(".")[-1] if output else None)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.backend._busy = False
        self.backend._maintenance_busy = False
        self.ui.dispose(); shiboken6.delete(self.backend)

    def configure(self, count=0, roles=None, *, status="ready", busy=False):
        self.backend._busy = busy
        self.backend._status = status if count else "idle"
        self.backend._pending_measurement_marker = ""
        self.backend._image_info = {"path": str(Path(self.temp.name) / "UI fixture.tif"), "name": "UI fixture.tif",
            "series_count": 1, "selected_series": 0, "time_count": 1, "selected_time": 0,
            "channel_count": count, "channel_hints": ["Source " + str(i + 1) for i in range(count)],
            "width": 128, "height": 128, "z_count": 2, "dtype": "uint16", "axes": "ZCYX",
            "physical_size_um": {"x": .5, "y": .5, "z": 1}, "thumbnail_url": Path(self.preview).as_uri(), "preview_channel_index": 0, "preview_z_index": 0} if count else {}
        self.backend._channel_roles = list(roles) if roles is not None else ["Ignore"] * count
        self.backend._stage = "正在分析" if busy else ""
        self.backend._message = ""
        self.backend._progress = .5 if busy else 0
        self.backend.changed.emit(); self.ui.settle(100)

    def source(self, marker):
        return self.ui.find("markerSource_" + marker)

    def expression(self, item, code):
        expression = QQmlExpression(self.ui.engine.rootContext(), item, code)
        result = expression.evaluate()
        self.assertFalse(expression.hasError(), expression.error().toString())
        return result[0]

    def options(self, choice):
        return json.loads(self.expression(choice, "(function() { var result=[]; for(var i=0;i<count;++i) result.push(textAt(i)); return JSON.stringify(result) })()"))

    def activate(self, choice, index):
        self.assertTrue(choice.isEnabled())
        self.expression(choice, "popup.itemAt(" + str(index) + ").click()")
        self.ui.settle()

    def assert_rows(self):
        rows = self.ui.named("markerMappingRow_")
        self.assertEqual([row.objectName() for row in rows], ["markerMappingRow_" + key for key in KEYS])
        for row, color in zip(rows, COLORS):
            dots = [item for item in visual_items(row) if item.inherits("QQuickRectangle") and item.width() == 7 and item.height() == 7]
            self.assertEqual(len(dots), 1)
            self.assertEqual(dots[0].property("color").name(), color)
        return rows

    def test_no_image_has_four_disabled_sources_and_no_guessed_target(self):
        for language, missing, placeholder in (("zh", "未包含", "请选择标记物"), ("en", "Not included", "Select a marker")):
            self.backend.setLanguage(language); self.configure()
            self.assert_rows()
            self.assertEqual(self.backend.markerSourceIndices, [-1, -1, -1, -1])
            self.assertEqual(self.backend.measurementMarker, "")
            for key in KEYS:
                choice = self.source(key)
                self.assertFalse(choice.isEnabled())
                self.assertEqual(choice.property("currentIndex"), 0)
                self.assertEqual(self.options(choice), [missing])
            target = self.ui.find("measurementMarkerChoice")
            self.assertFalse(target.isEnabled())
            self.assertEqual(target.property("currentIndex"), -1)
            self.assertEqual(target.property("displayText"), placeholder)
            self.assertFalse(target.property("contentItem").property("truncated"))
        self.assertFalse(self.ui.warnings)

    def test_three_real_sources_have_four_roles_without_a_fabricated_c4(self):
        self.configure(3, ["DAPI", "eGFP", "KCNN2"])
        self.assert_rows()
        self.assertEqual(self.backend.markerSourceIndices, [0, 1, -1, 2])
        for key, index in zip(KEYS, (1, 2, 0, 3)):
            self.assertEqual(self.source(key).property("currentIndex"), index)
            choices = self.options(self.source(key))
            self.assertEqual(len(choices), 4)
            self.assertEqual(choices[1:], ["C1", "C2", "C3"])
        self.assertEqual(self.ui.find("measurementMarkerChoice").property("currentText"), "KCNN2")
        self.assertEqual(self.backend.channelRoles, ["DAPI", "eGFP", "KCNN2"])
        self.assertFalse(self.ui.warnings)

    def test_all_six_sources_remain_selectable_and_target_has_two_explicit_steps(self):
        self.configure(6)
        self.assert_rows()
        target = self.source("Target")
        marker = self.ui.find("measurementMarkerChoice")
        self.assertEqual(marker.property("currentIndex"), -1)
        self.assertFalse(target.isEnabled())
        self.assertEqual(len(self.options(target)), 7)
        self.assertEqual(self.options(target)[1:], ["C1", "C2", "C3", "C4", "C5", "C6"])
        self.assertEqual(self.ui.find("channelMappingSourceState").property("text"),
                         self.backend.locale.translate("%1 个源通道").replace("%1", "6"))
        self.activate(marker, 1)  # Explicitly choose KCNN2, without assigning a source.
        self.assertEqual(self.backend.measurementMarker, "KCNN2")
        self.assertEqual(self.backend.markerSourceIndices, [-1, -1, -1, -1])
        self.assertTrue(target.isEnabled())
        self.activate(target, 6)
        self.assertEqual(self.backend.markerSourceIndices, [-1, -1, -1, 5])
        self.assertEqual(self.backend.channelRoles, ["Ignore"] * 5 + ["KCNN2"])
        self.activate(marker, 3)
        self.assertEqual(self.backend.measurementMarker, "KCNJ10")
        self.assertEqual(self.backend.markerSourceIndices[3], 5)
        self.assertEqual(self.source("Target").property("currentIndex"), 6)
        self.assertEqual(self.backend.channelRoles, ["Ignore"] * 5 + ["KCNJ10"])
        self.activate(target, 0)
        self.assertEqual(self.backend.markerSourceIndices, [-1, -1, -1, -1])
        self.assertEqual(self.backend.channelRoles, ["Ignore"] * 6)
        self.assertFalse(self.ui.warnings)

    def test_occupying_an_assigned_source_clears_the_other_row_without_swap(self):
        self.configure(3, ["DAPI", "eGFP", "KCNN2"])
        changes = []
        self.backend.changed.connect(lambda: changes.append(list(self.backend.channelRoles)))
        self.activate(self.source("GFAP"), 2)
        self.assertEqual(changes, [["DAPI", "GFAP", "KCNN2"]])
        self.assertEqual(self.source("eGFP").property("currentIndex"), 0)
        self.assertEqual(self.source("GFAP").property("currentIndex"), 2)
        changes.clear()
        self.activate(self.source("Target"), 1)
        self.assertEqual(changes, [["KCNN2", "GFAP", "Ignore"]])
        self.assertEqual(self.source("DAPI").property("currentIndex"), 0)
        self.assertEqual(self.source("Target").property("currentIndex"), 1)
        self.assertEqual(len(self.backend.channelRoles), 3)
        self.assertFalse(self.ui.warnings)

    def test_running_preserves_selections_and_disables_both_choice_types(self):
        self.configure(3, ["DAPI", "eGFP", "KCNN2"], status="running", busy=True)
        for key, index in zip(KEYS, (1, 2, 0, 3)):
            self.assertFalse(self.source(key).isEnabled())
            self.assertEqual(self.source(key).property("currentIndex"), index)
        self.assertFalse(self.ui.find("measurementMarkerChoice").isEnabled())
        self.assertEqual(self.ui.find("measurementMarkerChoice").property("currentText"), "KCNN2")
        self.assertEqual(self.backend.channelRoles, ["DAPI", "eGFP", "KCNN2"])
        self.assertFalse(self.ui.warnings)

    def test_analysis_progress_is_indeterminate_and_review_or_cancel_stops_it(self):
        self.configure(3, ["DAPI", "eGFP", "KCNN2"], status="running", busy=True)
        bar = self.ui.find("fluorescenceActivityBar")
        for progress in (-1, 0, .4, 1):
            self.backend._progress = progress
            self.backend.changed.emit(); self.ui.settle()
            self.assertEqual(self.backend.progress, progress)
            self.assertEqual(bar.property("progress"), -1)
            self.assertTrue(bar.property("indeterminate"))
            self.assertTrue(bar.property("animating"))
            dots = self.ui.named("activitySegment_")
            self.assertTrue(dots)
            self.assertTrue(all(dot.width() == dot.height() == 5 for dot in dots))
            self.assertFalse(any("本阶段" in str(item.property("text") or "") or
                                 "This stage" in str(item.property("text") or "") for item in self.ui.items()))
        for status in ("waiting_review", "cancelling", "cancelled", "completed"):
            self.backend._status = status
            self.backend.changed.emit(); self.ui.settle()
            self.assertFalse(bar.property("animating"), status)
            self.assertFalse(bar.isVisible(), status)
        self.backend._status = "running"
        self.backend.changed.emit(); self.ui.settle()
        appearance = self.ui.appearance
        appearance._dispatch(json.dumps({"dark": appearance.dark, "accent": list(appearance.accent.getRgbF()),
                                         "reducedMotion": True}).encode(), None)
        self.ui.settle()
        self.assertFalse(bar.property("animating"))
        self.assertFalse(bar.isVisible())
        # Shared ActivityBar still supports measured maintenance/update progress.
        bar.setProperty("progress", .4)
        self.ui.settle()
        fill = self.ui.find("activityDeterminateFill")
        self.assertTrue(fill.isVisible())
        self.assertAlmostEqual(fill.width(), bar.width() * .4, delta=.1)
        self.assertFalse(self.ui.warnings)

    def test_clean_header_and_four_rows_in_four_states_languages_and_sizes(self):
        records = []
        for language in ("zh", "en"):
            self.backend.setLanguage(language)
            for width, height in ((1210, 820), (900, 620)):
                self.ui.window.setWidth(width); self.ui.window.setHeight(height)
                for state in ("empty", "imported", "running", "completed"):
                    self.configure(0 if state == "empty" else 3, None if state == "empty" else ["DAPI", "eGFP", "KCNN2"],
                                   status={"empty": "idle", "imported": "ready", "running": "running", "completed": "completed"}[state], busy=state == "running")
                    rows = self.assert_rows()
                    self.assertTrue(all(abs(row.height() - (47 if state == "empty" else 42)) < 1 for row in rows))
                    title = self.ui.find("channelMappingTitle")
                    header = self.ui.find("channelMappingHeader")
                    status = self.ui.find("channelMappingSourceState")
                    self.assertFalse(any(item.objectName().startswith("channelLegend") for item in self.ui.items()))
                    self.assertEqual(title.property("font").pixelSize(), 14)
                    self.assertEqual(status.isVisible(), state != "empty")
                    if state != "empty":
                        header_rect = header.mapRectToScene(header.boundingRect())
                        self.assertGreaterEqual(status.mapRectToScene(status.boundingRect()).top(), header_rect.bottom())
                    else:
                        self.assertEqual(status.property("text"), "")
                    for item in [title, *rows, *(self.source(key) for key in KEYS), self.ui.find("measurementMarkerChoice")]:
                        rect = item.mapRectToScene(item.boundingRect())
                        self.assertGreaterEqual(rect.left(), 0)
                        self.assertLessEqual(rect.right(), width + 1)
                    slots = self.ui.named("markerMappingSlot_")
                    self.assertLess(max(slot.height() for slot in slots) - min(slot.height() for slot in slots), 1.1)
                    for slot, row in zip(slots, rows):
                        self.assertAlmostEqual(row.y() * 2 + row.height(), slot.height(), delta=1)
                    mapping = self.ui.find("channelMappingCard")
                    support = self.ui.find("analysisSupportColumn")
                    information = self.ui.find("fluorescenceInformationCard")
                    gap = mapping.mapToScene(QPointF(0, mapping.height())).y() - information.mapToScene(QPointF(0, information.height())).y()
                    if self.ui.window.property("wide"):
                        self.assertAlmostEqual(gap, 0, delta=1)
                        left = self.ui.find("imageInputColumn")
                        self.assertAlmostEqual(left.mapToScene(QPointF(0, 0)).y(), support.mapToScene(QPointF(0, 0)).y(), delta=1)
                    else:
                        self.assertAlmostEqual(mapping.height(), mapping.property("implicitHeight"), delta=1)
                    if state != "empty":
                        expected = self.backend.locale.translate("%1 个源通道").replace("%1", "3")
                        self.assertEqual(status.property("text"), expected)
                    flick = self.ui.find("analysisScroll").property("contentItem")
                    top = mapping.mapToScene(QPointF(0, 0)).y()
                    maximum = max(0, flick.property("contentHeight") - flick.height())
                    flick.setProperty("contentY", max(0, min(maximum, flick.property("contentY") + top - 30)))
                    self.ui.settle()
                    screenshot = self.ui.capture(f"{language}-{width}-{state}")
                    records.append({"language": language, "size": [width, height], "state": state, "rows": 4,
                                    "physical_sources": 0 if state == "empty" else 3, "source_status_visible": status.isVisible(),
                                    "wide_bottom_difference": round(gap, 3) if self.ui.window.property("wide") else None,
                                    "screenshot": screenshot})
        self.assertFalse(self.ui.warnings)
        self.ui.write_record("four-marker-layout", {"cases": records})


if __name__ == "__main__":
    unittest.main()
