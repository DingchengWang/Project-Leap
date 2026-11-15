"""Actual Cocoa/Qt Quick language checks with isolated components and no workers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "cocoa")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from leap_desktop.controller import Controller
from leap_desktop.i18n import LocaleManager
from leap_desktop.paths import AppPaths
from leap_desktop import maintenance
from tools.refinement_ui_probe import QtUiHarness, make_fixture_seed
import shiboken6


def visual_items(item):
    result = [item]
    for child in item.childItems():
        result.extend(visual_items(child))
    return result


class QmlLocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setFont(QFont("Helvetica Neue", 13))
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="leap-language-ui-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.paths = AppPaths(Path(self.temp.name) / "home")
        self.initial_status = patch.object(Controller, "_initial_status", return_value=None)
        self.initial_status.start()
        self.addCleanup(self.initial_status.stop)
        self.backend = Controller(self.paths)
        self.backend._timer.stop()
        bundle = Path(self.temp.name) / "Installers"
        for identifier in ("project-leap-2d", "fiji"):
            make_fixture_seed(bundle / identifier, identifier)
        (bundle / "catalog.json").write_text(json.dumps({"schema_version": 1, "packages": {
            identifier: {"version": "1.0.1", "path": identifier} for identifier in ("project-leap-2d", "fiji")}}))
        self.assertEqual(maintenance.install_component(self.paths.home, bundle)["status"], "ready")
        self.backend._refresh_modules()
        output = os.environ.get("LEAP_LOCALIZATION_UI_EVIDENCE_DIR")
        self.ui = QtUiHarness(self.backend, output=Path(output) / self.id().split(".")[-1] if output else None)
        self.engine, self.window = self.ui.engine, self.ui.window
        self.addCleanup(self.dispose)

    def dispose(self):
        self.ui.dispose()
        shiboken6.delete(self.backend)

    def visible_texts(self):
        return [item.property("text") for item in visual_items(self.window.contentItem())
                if item.property("visible") and isinstance(item.property("text"), str)]

    def settle(self):
        self.backend.changed.emit()
        self.app.processEvents()
        QTest.qWait(30)

    def click(self, item):
        point = item.mapToScene(item.boundingRect().center())
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
        self.app.processEvents()

    def choose_language(self, language):
        self.backend.setPage("settings")
        self.settle()
        choice = self.window.findChild(QObject, "languageChoice")
        self.assertTrue(choice.property("enabled"))
        # Exercise closed-control keyboard navigation. The native menu's
        # AppKit interaction is checked separately in test_choicebox.py.
        self.ui.press_navigation("page:settings")
        choice.forceActiveFocus(Qt.FocusReason.TabFocusReason)
        self.assertTrue(choice.property("activeFocus"))
        QTest.keyClick(self.window, Qt.Key_End if language == "zh" else Qt.Key_Home)
        self.settle()
        self.assertEqual(self.backend.language, language)

    def test_default_english_and_settings_language_and_data_actions(self):
        self.assertEqual(self.backend.language, "en")
        saved = json.loads((self.paths.home / "settings.json").read_text())
        self.assertEqual(saved["language"], "en")
        self.backend.setPage("settings")
        self.settle()
        texts = self.visible_texts()
        self.assertIn("Settings", texts)
        self.assertIn("Interface language", texts)
        self.assertTrue(self.window.findChild(QObject, "languageChoice"))
        self.assertTrue(self.window.findChild(QObject, "openDataFolderButton"))
        self.assertTrue(self.window.findChild(QObject, "uninstallButton"))
        settings = self.window.findChild(QObject, "settingsScroll")
        editable_controls = [i for i in visual_items(settings) if i.inherits("QQuickComboBox")]
        self.assertEqual(len(editable_controls), 2)
        for width, height in ((1210, 820), (900, 620)):
            self.window.setWidth(width); self.window.setHeight(height)
            for language, mode in (("en", "light"), ("zh", "light"), ("en", "dark"), ("zh", "dark")):
                self.backend.setLanguage(language); self.backend.setAppearanceMode(mode); self.settle()
                cards = [self.ui.find(name) for name in
                         ("languageAppearanceCard", "storageDataCard", "updatesUninstallRow", "aboutCard")]
                rects = [item.mapRectToScene(item.boundingRect()) for item in cards]
                for earlier, later in zip(rects, rects[1:]):
                    self.assertGreater(later.top(), earlier.bottom())
                updates = self.ui.find("softwareUpdatesCard")
                uninstall = self.ui.find("uninstallCard")
                update_rect = updates.mapRectToScene(updates.boundingRect())
                uninstall_rect = uninstall.mapRectToScene(uninstall.boundingRect())
                self.assertAlmostEqual(update_rect.top(), uninstall_rect.top(), delta=1)
                self.assertAlmostEqual(update_rect.bottom(), uninstall_rect.bottom(), delta=1)
                self.assertGreater(uninstall_rect.left(), update_rect.right())
                button = self.ui.find("uninstallButton")
                button_rect = button.mapRectToScene(button.boundingRect())
                self.assertAlmostEqual(button_rect.center().x(), uninstall_rect.center().x(), delta=1)
                self.assertAlmostEqual(button_rect.center().y(), uninstall_rect.center().y(), delta=1)
                labels = {"openDataFolderButton": ("Open application data", "打开软件数据"),
                          "chooseDataDirectoryButton": ("Change storage folder", "更改存储目录"),
                          "clearCacheButton": ("Clear cache", "清理缓存"),
                          "uninstallButton": ("Uninstall application", "卸载软件"),
                          "openLicensesButton": ("Licenses", "许可证")}
                for name, translations in labels.items():
                    button = self.ui.find(name)
                    self.assertEqual(button.property("text"), translations[language == "zh"])
                    button_rect = button.mapRectToScene(button.boundingRect())
                    for text in visual_items(button):
                        if text.inherits("QQuickText"):
                            text_rect = text.mapRectToScene(text.boundingRect())
                            self.assertGreaterEqual(text_rect.left(), button_rect.left())
                            self.assertLessEqual(text_rect.right(), button_rect.right())
                self.assertTrue(self.ui.find("aboutLicense").property("font").italic())
                texts = self.visible_texts()
                self.assertNotIn("© 2026 Dingcheng Wang", [item.property("text") for item in visual_items(self.ui.find("aboutCard"))])
                if language == "en":
                    self.assertEqual(self.ui.find("aboutDescription").property("text"),
                                     "A local research platform for IHC image analysis, featuring tools for astrocyte fluorescence intensity measurement and cell counting. More features are coming soon.")
                    self.assertEqual(self.ui.find("aboutDeveloper").property("text"),
                                     "- Developed by Dingcheng Wang | Neuroscience Undergraduate | The Ohio State University")
                    self.assertEqual(self.ui.find("aboutAffiliation").property("text"),
                                     "- Research affiliation: Min Zhou Lab | Department of Neuroscience | College of Medicine")
                    self.assertEqual(self.ui.find("aboutLicense").property("text"),
                                     "Original code of this app is licensed under the Apache License 2.0. Third-party components are governed by respective licenses.")
                    for removed in ("Choose the language used in the application interface.",
                                    "Follow the system appearance, or choose a light or dark interface.",
                                    "Check for updates to Project Leap and installed modules.",
                                    "Clear rebuildable interface caches and unused temporary files. Original images, analysis results and review data are kept."):
                        self.assertNotIn(removed, texts)
                flick = settings.property("contentItem")
                flick.setProperty("contentY", 0); self.ui.settle()
                self.ui.capture(f"settings-{language}-{mode}-{width}-top")
                flick.setProperty("contentY", max(0, flick.property("contentHeight") - flick.height())); self.ui.settle()
                self.ui.capture(f"settings-{language}-{mode}-{width}-bottom")
        self.assertFalse(self.ui.warnings)

    def test_actual_language_choice_refreshes_all_pages_and_persists(self):
        for width, height in ((1210, 820), (900, 620)):
            self.window.setWidth(width); self.window.setHeight(height)
            for language in ("zh", "en"):
                self.choose_language(language)
                self.assertEqual(LocaleManager(self.paths.home).language, language)
                expected = {"analysis": ("星形胶质细胞荧光强度分析", "Astrocyte Fluorescence Intensity Measurement"), "jobs": ("任务与结果", "Jobs & Results"),
                            "maintenance": ("功能与维护", "Modules & Maintenance"), "settings": ("设置", "Settings")}
                for page, labels in expected.items():
                    self.backend.setPage(page); self.settle()
                    texts = self.visible_texts()
                    self.assertIn(labels[0 if language == "zh" else 1], texts)
                    if language == "en":
                        self.assertFalse([text for text in texts if re.search(r"[\u3400-\u9fff]", text) and text != "中文"])

    def test_language_can_change_while_running_and_identifiers_stay_raw(self):
        self.backend._busy = True
        self.backend._status = "running"
        self.backend._stage = "计算细胞分割"
        self.backend._message = "计算细胞分割：已完成 2/5"
        self.backend._progress = 0.4
        self.backend._image_info = {"path": "/tmp/样本_α.tif", "name": "样本_α.tif", "series_count": 1,
                                    "selected_series": 0, "time_count": 1, "selected_time": 0, "channel_count": 3,
                                    "width": 64, "height": 64, "z_count": 4, "dtype": "uint16", "axes": "ZCYX",
                                    "channel_hints": ["DAPI", "eGFP", "KCNN2"], "physical_size_um": {"x": .5, "y": .5, "z": 1}}
        self.backend._channel_roles = ["DAPI", "eGFP", "KCNN2"]
        before = dict(self.backend._image_info)
        run_dir = self.paths.runs / "language-state-test"
        run_dir.mkdir()
        request_file = run_dir / "request.json"
        request_file.write_text(json.dumps({"schema_version": 1, "run_id": run_dir.name, "engine_args": [],
                                           "workspace": str(run_dir / "workspace"), "cache_root": str(run_dir / "cache")}))
        self.backend._run_dir = run_dir
        before_request = hashlib.sha256(request_file.read_bytes()).hexdigest()
        before_status = (self.backend.busy, self.backend.status, self.backend.progress)
        for language in ("zh", "en"):
            self.choose_language(language)
            self.backend.setPage("analysis"); self.settle()
            texts = self.visible_texts()
            self.assertNotIn("本阶段 40%", texts)
            self.assertNotIn("This stage 40%", texts)
            self.assertTrue(self.ui.find("fluorescenceActivityBar").property("indeterminate"))
            self.assertIn("样本_α.tif", texts)
            self.assertEqual(self.backend._channel_roles, ["DAPI", "eGFP", "KCNN2"])
            self.assertEqual(self.backend._image_info, before)
            self.assertTrue(self.backend.busy)
            self.assertEqual((self.backend.busy, self.backend.status, self.backend.progress), before_status)
            self.assertEqual(hashlib.sha256(request_file.read_bytes()).hexdigest(), before_request)

    def test_missing_marker_translation_preserves_physical_source_roles(self):
        self.backend._image_info = {"path": "/tmp/fixture.tif", "name": "fixture.tif", "series_count": 1,
                                    "time_count": 1, "channel_count": 3, "channel_hints": ["Channel 1", "Channel 2", "Channel 3"]}
        self.backend._channel_roles = ["Ignore", "eGFP", "KCNN2"]
        self.settle()
        for language, expected in (("zh", "未包含"), ("en", "Not included")):
            self.choose_language(language)
            self.backend.setPage("analysis"); self.settle()
            for marker in ("DAPI", "GFAP"):
                choice = next(item for item in visual_items(self.window.contentItem())
                              if item.objectName() == "markerSource_" + marker)
                self.assertEqual(choice.property("currentIndex"), 0)
                self.assertEqual(choice.property("displayText"), expected)
            self.assertEqual(self.backend.channelRoles, ["Ignore", "eGFP", "KCNN2"])

    def test_language_control_blocks_writes_only_during_migration(self):
        before = (self.paths.home / "settings.json").read_bytes()
        self.backend._maintenance_busy = True
        self.backend._maintenance_operation = "migration"
        self.backend.setPage("settings")
        self.settle()
        choice = self.window.findChild(QObject, "languageChoice")
        self.assertFalse(choice.property("enabled"))
        self.click(choice)
        QTest.keyClick(self.window, Qt.Key_End)
        self.settle()
        self.assertEqual(self.backend.language, "en")
        self.assertEqual((self.paths.home / "settings.json").read_bytes(), before)
        self.backend._maintenance_operation = "install"
        self.settle()
        self.assertTrue(choice.property("enabled"))
        self.choose_language("zh")
        self.assertEqual(LocaleManager(self.paths.home).language, "zh")


if __name__ == "__main__":
    unittest.main()
