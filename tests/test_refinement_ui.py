"""Refinement UI acceptance with real Qt input and disposable package fixtures.

Run: QT_QPA_PLATFORM=cocoa runtime/test-env/bin/python -B -m unittest discover -s tests -p test_refinement_ui.py -v
Set LEAP_UI_EVIDENCE_DIR to retain screenshots and event records. No scientific
analysis or Fiji process is launched. Native macOS glass is a separate real-screen gate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.refinement_ui_probe import QtUiHarness, fixture_previews, make_fixture_seed
from PySide6.QtCore import QPointF, QUrl
from PySide6.QtQml import QQmlExpression
from PySide6.QtWidgets import QApplication, QMessageBox
from leap_desktop import maintenance, modules
from leap_desktop.controller import Controller, WorkerContext
from leap_desktop.paths import AppPaths, write_json
import shiboken6


FIXTURE_ID = "qa-second-module"
FIXTURE_CATALOG = {
    "id": FIXTURE_ID, "name": "QA second module", "version": "1.0.1",
    "entrypoint": "qa_second_module.adapter", "github_url": "", "needs_fiji": True,
    "input_page": "qa-second-input",
    "import_module": "qa_second_module.import_worker",
}


class RefinementUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="leap-refinement-qa-", dir="/private/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = AppPaths(self.root / "AppData")
        self.catalog_patch = patch.object(modules, "CATALOG", (*modules.CATALOG, FIXTURE_CATALOG))
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)
        self.bundle = self.root / "Installers"
        for identifier in (modules.DEFAULT_MODULE_ID, FIXTURE_ID, "fiji"):
            make_fixture_seed(self.bundle / identifier, identifier)
        write_json(self.bundle / "catalog.json", {"schema_version": 1, "packages": {
            identifier: {"version": "1.0.1", "path": identifier}
            for identifier in (modules.DEFAULT_MODULE_ID, FIXTURE_ID, "fiji")}})
        initial_status = patch.object(Controller, "_initial_status", return_value=None)
        initial_status.start()
        self.addCleanup(initial_status.stop)
        self.backend = Controller(self.paths)
        self.backend._timer.stop()
        self.backend._refresh_modules()
        self.ui = None
        self.addCleanup(self.dispose)

    def dispose(self):
        if self.ui:
            self.ui.dispose()
        shiboken6.delete(self.backend)

    def show(self, **kwargs):
        evidence = os.environ.get("LEAP_UI_EVIDENCE_DIR")
        output = Path(evidence) / self.id().split(".")[-1] if evidence else None
        self.ui = QtUiHarness(self.backend, output=output, **kwargs)
        return self.ui

    def install(self, *identifiers):
        for identifier in identifiers:
            result = maintenance.install_component(self.paths.home, self.bundle, module_id=identifier)
            self.assertEqual(result["status"], "ready", result)
        self.backend._refresh_modules()
        self.backend.changed.emit()
        if self.ui:
            self.ui.settle()

    def navigate(self, page):
        self.ui.press_navigation("page:" + page)
        self.assertEqual(self.backend.page, page)

    def add_job(self, identifier, module_id, *, created_at="2026-10-18T12:00:00Z"):
        directory = self.paths.runs / identifier
        results = directory / "workspace/Result"
        images = fixture_previews(results)
        (results / "report.txt").write_text("Artificial QA result; no measurements.\n")
        write_json(directory / "job.json", {"id": identifier, "name": "QA image " + identifier,
                   "module_id": module_id, "module_version": "1.0.1", "status": "completed",
                   "stage": "分析完成", "message": "QA fixture", "created_at": created_at,
                   "results_dir": str(results)})
        self.backend._load_jobs()
        self.backend.changed.emit()
        return images

    def reveal(self, item, scroll_name="analysisScroll"):
        for _ in range(24):
            if self.ui.in_view(item):
                return
            rectangle = item.mapRectToScene(item.boundingRect())
            self.ui.wheel(scroll_name, 240 if rectangle.top() < 0 else -240)
        self.fail("Control could not be reached by scrolling: " + item.objectName())

    def test_zero_modules_has_no_sidebar_entries_and_obsolete_chrome_is_absent(self):
        ui = self.show()
        self.assertEqual(self.backend.installedModules, [])
        self.assertFalse([item for item in ui.navigation_elements() if item["identifier"].startswith("module:")])
        self.assertEqual(self.backend.appVersion, "1.0.0")
        texts = ui.texts()
        self.assertNotIn("Image analysis workspace", texts)
        self.assertNotIn("Local workspace", texts)
        self.assertNotIn("Data folder", texts)
        self.assertNotIn("Project Leap 2D", texts)
        self.assertIn("Astrocyte Fluorescence Intensity Measurement", texts)
        self.assertIn("Image import", texts)
        self.assertFalse(ui.warnings, ui.warnings)
        ui.write_record("zero-modules", {"module_entries": 0, "screenshot": ui.capture("zero-modules")})

    def test_actual_module_clicks_follow_catalog_order_and_isolate_input_route(self):
        # Catalog order is primary, fixture; installation order deliberately reverses it.
        self.install(FIXTURE_ID, modules.DEFAULT_MODULE_ID)
        ui = self.show()
        fixture_page = self.root / "QaInput.qml"
        fixture_page.write_text('import QtQuick\nItem { objectName: "qaSecondInput"; property var workspace; implicitHeight: 100; Text { text: "Independent QA input page" } }\n')
        page_sources = {"fluorescence": QUrl.fromLocalFile(str(ROOT / "app/leap_desktop/qml/pages/FluorescenceInput.qml")).toString(),
                        "qa-second-input": QUrl.fromLocalFile(str(fixture_page)).toString()}
        ui.window.setProperty("moduleInputPages", ui.engine.toScriptValue(page_sources))
        entries = [item for item in ui.navigation_elements() if item["identifier"].startswith("module:")]
        self.assertEqual([item["identifier"] for item in entries],
                         ["module:" + modules.DEFAULT_MODULE_ID, "module:" + FIXTURE_ID])
        prior_source = {"name": "prior-module-source.tif", "path": "/tmp/never-opened.tif"}
        prior_roles = ["DAPI", "GFAP", "KCNN2"]
        self.backend._image_info = prior_source
        self.backend._channel_roles = prior_roles
        for identifier, route in ((FIXTURE_ID, "qa-second-input"), (modules.DEFAULT_MODULE_ID, "fluorescence"),
                                  (FIXTURE_ID, "qa-second-input"), (modules.DEFAULT_MODULE_ID, "fluorescence")):
            ui.press_navigation("module:" + identifier)
            self.assertEqual(self.backend.activeModuleId, identifier)
            self.assertEqual(self.backend.activeModuleInputPage, route)
            self.assertEqual(self.backend.imageInfo, prior_source if identifier == modules.DEFAULT_MODULE_ID else {})
            self.assertEqual(self.backend.channelRoles, prior_roles if identifier == modules.DEFAULT_MODULE_ID else [])
            self.assertEqual(ui.find("moduleInputLoader").property("source").toString(), page_sources[route])
            self.assertEqual(bool(ui.named("sourceDropZone")), route == "fluorescence")
            self.assertEqual(bool(ui.named("qaSecondInput")), route == "qa-second-input")
        ui.write_record("module-order-and-routing", {"screenshot": ui.capture("two-modules")})

    def test_corruption_keeps_installed_navigation_with_error_indicator(self):
        self.install(modules.DEFAULT_MODULE_ID)
        state = maintenance.component_status(self.paths.home)
        model = Path(state["runtime_descriptor"]["models"]) / "fixture-model"
        model.write_bytes(b"deliberately damaged QA fixture\n")
        self.backend._refresh_modules()
        self.assertTrue(self.backend.installedModules[0]["hasError"])
        ui = self.show()
        item = next(item for item in ui.navigation_elements()
                    if item["identifier"] == "module:" + modules.DEFAULT_MODULE_ID)
        self.assertIn(self.backend.locale.translate("需要修复"), item["help"])
        ui.press_navigation(item["identifier"])
        self.assertEqual(self.backend.activeModuleId, modules.DEFAULT_MODULE_ID)
        ui.write_record("damaged-module", {"screenshot": ui.capture("damaged-module")})

    def test_whole_task_row_expands_three_previews_below_it(self):
        self.install(modules.DEFAULT_MODULE_ID, FIXTURE_ID)
        self.add_job("qa-primary", modules.DEFAULT_MODULE_ID)
        self.add_job("qa-second", FIXTURE_ID, created_at="2026-10-18T11:00:00Z")
        ui = self.show()
        self.navigate("jobs")
        row = ui.find("taskRow_qa-primary")
        # Click the empty right side of the whole row, away from its label.
        ui.click(row, x_fraction=0.92)
        self.assertEqual(self.backend.selectedJobId, "qa-primary")
        previews = [item for item in ui.named("jobPreview_qa-primary_") if item.isVisible()]
        self.assertEqual(len(previews), 3)
        ui.wait_until(lambda: all(ui.engine.newQObject(item).property("status").toInt() == 1 for item in previews))
        row_bottom = row.mapToScene(QPointF(0, row.height())).y()
        self.assertTrue(all(item.mapToScene(QPointF(0, 0)).y() >= row_bottom for item in previews))
        names = {Path(item.property("source").toLocalFile()).name for item in previews}
        self.assertEqual(names, {"Whole_ROI.png", "Soma_ROI.png", "Processes_ROI.png"})
        ui.capture("three-previews-expanded")
        ui.window.setWidth(900); ui.window.setHeight(620); ui.settle()
        preview_y = [round(item.mapToScene(QPointF(0, 0)).y()) for item in previews]
        self.assertEqual(len(set(preview_y)), 1)
        strip = ui.find("jobPreviewStrip_qa-primary")
        self.assertLessEqual(strip.mapRectToScene(strip.boundingRect()).right(), ui.window.width())
        self.assertTrue(all(item.height() <= 120 for item in previews))
        self.reveal(strip, "jobsScroll")
        ui.capture("narrow-preview-row")
        self.assertFalse(ui.find("jobRunDetailsLoader_qa-primary").property("active"))
        self.reveal(row, "jobsScroll")
        ui.click(row, x_fraction=0.92)
        self.assertEqual(self.backend.selectedJobId, "")
        self.assertFalse([item for item in ui.named("jobPreview_qa-primary_") if item.isVisible()])
        ui.write_record("task-row-expansion", {"preview_sources": sorted(names)})

    def test_many_previews_scroll_horizontally_and_open_the_original(self):
        self.install(modules.DEFAULT_MODULE_ID)
        self.add_job("qa-many", modules.DEFAULT_MODULE_ID)
        results = self.paths.runs / "qa-many" / "workspace/Result"
        source = results / "Whole_ROI.png"
        for index in range(5):
            (results / f"Extra_preview_{index:02d}.png").write_bytes(source.read_bytes())
        before = {path.name: path.read_bytes() for path in results.iterdir() if path.is_file()}
        ui = self.show(); self.navigate("jobs")
        ui.window.setWidth(900); ui.window.setHeight(620); ui.settle()
        ui.click("taskRow_qa-many")
        strip = ui.find("jobPreviewStrip_qa-many")
        self.reveal(strip, "jobsScroll")
        previews = ui.named("jobPreview_qa-many_")
        self.assertEqual(len(previews), 8)
        self.assertGreater(strip.property("contentWidth"), strip.width())
        self.assertEqual(len({round(item.mapToScene(QPointF(0, 0)).y()) for item in previews}), 1)
        strip.setProperty("contentX", strip.property("contentWidth") - strip.width()); ui.settle()
        last = ui.find("jobPreviewButton_qa-many_7")
        with patch("leap_desktop.controller.QDesktopServices.openUrl", return_value=True) as opened:
            ui.click(last)
            self.assertEqual(len(opened.call_args_list), 1)
            opened_path = Path(opened.call_args.args[0].toLocalFile())
            self.assertEqual(opened_path, Path(previews[-1].property("source").toLocalFile()))
        self.assertEqual({path.name: path.read_bytes() for path in results.iterdir() if path.is_file()}, before)
        self.assertFalse(ui.find("jobRunDetailsLoader_qa-many").property("active"))
        ui.capture("many-previews-horizontal-end")
        self.assertFalse(ui.warnings, ui.warnings)

    def test_critical_actions_reachable_at_both_window_sizes(self):
        self.install(modules.DEFAULT_MODULE_ID)
        self.backend._busy = True
        self.backend._status = "waiting_review"
        self.backend._worker_context = WorkerContext(modules.DEFAULT_MODULE_ID, "qa-running-task")
        self.backend._fiji_pid = 99999999  # No process is launched or signaled by this test.
        ui = self.show()
        calls = []
        with patch.object(Controller, "showFiji", lambda self: calls.append("fiji")), \
             patch.object(Controller, "cancelAnalysis", lambda self: calls.append("cancel")):
            for width, height in ((1210, 820), (900, 620)):
                ui.window.setWidth(width); ui.window.setHeight(height); ui.settle()
                ui.press_navigation("showFiji")
                ui.press_navigation("cancelAnalysis")
                self.assertEqual(calls[-2:], ["fiji", "cancel"])
                ui.capture("critical-actions-" + str(width))
        ui.write_record("critical-actions", {"calls": calls})

    def test_log_appends_and_stage_changes_preserve_user_scroll_intent(self):
        self.install(modules.DEFAULT_MODULE_ID)
        ui = self.show()
        self.backend._append_log("".join("QA line %03d: no scientific analysis.\n" % i for i in range(140)))
        ui.settle()
        flick = ui.find("logFlick")
        # Position the outer scrollbar before exercising the nested log. The
        # page-center wheel target can land on a native channel choice control.
        outer = ui.find("analysisScroll").property("contentItem")
        outer.setProperty("contentY", max(0, outer.property("contentHeight") - outer.height()))
        ui.settle()
        self.assertTrue(ui.in_view(flick))
        ui.wheel(flick, -12000)
        maximum = lambda: max(0, flick.property("contentHeight") - flick.height())
        ui.wait_until(lambda: abs(flick.property("contentY") - maximum()) <= 2)
        self.backend._append_log("QA following tail\n")
        ui.settle()
        self.assertAlmostEqual(flick.property("contentY"), maximum(), delta=2)
        ui.wheel(flick, 480)
        ui.settle()
        old_position = flick.property("contentY")
        self.assertGreater(maximum() - old_position, 10)
        for stage in ("计算 ROI", "测量荧光强度", "保存结果与最终 ROI"):
            self.backend._stage = stage
            self.backend.changed.emit()
            self.backend._append_log("QA phase update while reading previous output\n")
            ui.settle()
            self.assertAlmostEqual(flick.property("contentY"), old_position, delta=2)
        ui.capture("reading-earlier-log")
        ui.wheel(flick, -12000)
        ui.wait_until(lambda: abs(flick.property("contentY") - maximum()) <= 2)
        self.backend._append_log("QA tail following restored\n")
        ui.settle()
        self.assertAlmostEqual(flick.property("contentY"), maximum(), delta=2)
        ui.write_record("log-scroll", {"old_position_retained": old_position,
                                      "tail_position": flick.property("contentY"),
                                      "screenshot": ui.capture("following-log-tail")})

        # Exercise bounded-buffer trimming using surrogate-pair characters too.
        # The oracle is the visible text itself, not merely a stable contentY.
        self.backend._reset_log("".join("line %04d 😀 " % i + "x" * 87 + "\n" for i in range(1200)))
        ui.settle()
        ui.wheel(flick, -120000)
        ui.wait_until(lambda: abs(flick.property("contentY") - maximum()) <= 2)
        for _ in range(5):
            self.backend._append_log("sequential tail 😀 " + "y" * 90 + "\n")
        ui.settle()
        self.assertAlmostEqual(flick.property("contentY"), maximum(), delta=2)
        ui.wheel(flick, 960)
        text_item = ui.find("logText")

        def visible_fragment():
            y = flick.property("contentY") + 3
            expression = QQmlExpression(ui.engine.rootContext(), text_item,
                "text.substring(positionAt(0, %s), positionAt(0, %s) + 60)" % (y, y))
            value, _ = expression.evaluate()
            self.assertFalse(expression.hasError(), expression.error().toString())
            return value

        before = visible_fragment()
        for _ in range(12):
            self.backend._append_log("more tail 😀 " + "z" * 100 + "\n")
        ui.settle()
        self.assertEqual(visible_fragment(), before)
        self.assertEqual(text_item.property("text"), self.backend.logText)
        self.assertLessEqual(len(self.backend.logText), 120000)

    def test_module_remove_action_preserves_jobs_and_only_last_fiji_prompt(self):
        self.install(modules.DEFAULT_MODULE_ID, FIXTURE_ID)
        images = self.add_job("qa-history", modules.DEFAULT_MODULE_ID)
        saved = {Path(item["path"]): Path(item["path"]).read_bytes() for item in images}
        job_file = self.paths.runs / "qa-history/job.json"
        saved[job_file] = job_file.read_bytes()
        ui = self.show()
        dialogs, choices = [], []
        def answer_dialog(_parent, title, text, *_args):
            dialogs.append({"title": title, "text": text})
            self.assertTrue(choices, "An unexpected confirmation was requested")
            return choices.pop(0)

        # Answer only the authorization boundary externally. The real QML
        # action, controller, maintenance worker and temporary files execute.
        responder = patch.object(QMessageBox, "question", side_effect=answer_dialog)
        responder.start()
        self.addCleanup(responder.stop)
        self.navigate("maintenance")
        choices.append(QMessageBox.StandardButton.Yes)
        first = ui.find("removeModule_" + modules.DEFAULT_MODULE_ID)
        self.reveal(first, "maintenanceScroll")
        ui.click(first)
        ui.wait_until(lambda: not self.backend.maintenanceBusy and not maintenance.component_status(self.paths.home)["installed"])
        self.assertEqual(len(dialogs), 1, dialogs)
        self.assertEqual(maintenance.fiji_status(self.paths.home)["status"], "ready")
        self.assertTrue(all(path.read_bytes() == content for path, content in saved.items()))
        group = next(group for group in self.backend.jobGroups if group["moduleId"] == modules.DEFAULT_MODULE_ID)
        self.assertFalse(group["installed"])
        self.assertEqual(group["jobs"][0]["id"], "qa-history")
        self.navigate("maintenance")
        choices.extend((QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No))
        second = ui.find("removeModule_" + FIXTURE_ID)
        self.reveal(second, "maintenanceScroll")
        ui.click(second)
        ui.wait_until(lambda: not self.backend.maintenanceBusy and not maintenance.component_status(self.paths.home, module_id=FIXTURE_ID)["installed"])
        self.assertEqual(len(dialogs), 3, dialogs)
        self.assertIn("Fiji", dialogs[-1]["text"])
        self.assertEqual(maintenance.fiji_status(self.paths.home)["status"], "ready")
        self.assertTrue(all(path.read_bytes() == content for path, content in saved.items()))
        self.navigate("jobs")
        self.assertIn("Module uninstalled", ui.texts())
        ui.write_record("module-removal", {"dialogs": dialogs, "preserved_files": sorted(str(path) for path in saved),
                                          "screenshot": ui.capture("uninstalled-history")})

    def test_uninstall_dialog_dispatches_each_explicit_mode(self):
        ui = self.show()
        self.navigate("settings")
        modes = []
        with patch.object(Controller, "requestUninstall", lambda self, remove_all: modes.append(remove_all)):
            for button_name, expected in (("uninstallKeepDataButton", False), ("uninstallRemoveDataButton", True)):
                ui.click("uninstallButton")
                ui.capture_popups(button_name)
                ui.popup_items()
                popup = next(window for window in ui._popup_windows
                             if window.metaObject().className() == "QQuickPopupWindow" and window.isVisible())
                ui.click(button_name, target_window=popup)
                self.assertEqual(modes[-1], expected)
        ui.write_record("uninstall-mode-dispatch", {"modes": modes,
                        "scope": "QML to controller boolean dispatch only; no uninstall helper is launched"})


if __name__ == "__main__":
    unittest.main()
