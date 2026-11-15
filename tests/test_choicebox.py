"""Production ChoiceBox contracts and opt-in real Cocoa NSMenu checks.

Offscreen checks cover models and Qt activation semantics. The Cocoa run loads a
small test-only observer of this process's public NSMenu popup entry point. It
uses QTest clicks and in-process NSApp keyboard events, not system input.

QT_QPA_PLATFORM=cocoa runtime/ui-env/bin/python -B -m unittest discover -s tests -p test_choicebox.py -v
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
if os.environ["QT_QPA_PLATFORM"] == "offscreen":
    os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import shiboken6

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/choicebox_harness.qml"
COCOA = sys.platform == "darwin" and os.environ["QT_QPA_PLATFORM"] == "cocoa"


class ChoiceHarness:
    def __init__(self):
        self.app = QApplication.instance() or QApplication([])
        self.app.setQuitOnLastWindowClosed(False)
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda values: self.warnings.extend(str(value) for value in values))
        self.engine.load(QUrl.fromLocalFile(str(FIXTURE)))
        assert self.engine.rootObjects(), self.warnings
        self.window = self.engine.rootObjects()[0]
        self.choice = self.window.findChild(QQuickItem, "choice")
        self.window.requestActivate()
        if COCOA and not QTest.qWaitForWindowActive(self.window, 1500):
            raise AssertionError("The Cocoa harness window must be active before native input")
        self.settle(80)

    def settle(self, milliseconds=30):
        self.app.processEvents()
        QTest.qWait(milliseconds)
        self.app.processEvents()

    def evaluate(self, expression):
        value = QQmlExpression(self.engine.rootContext(), self.window, expression)
        result = value.evaluate()
        if value.hasError():
            raise AssertionError(value.error().toString())
        return result[0]

    def snapshot(self):
        self.settle()
        return json.loads(self.evaluate("snapshot()"))

    def set(self, name, value):
        self.window.setProperty(name, value)
        self.settle()

    def click(self):
        point = self.choice.mapToScene(self.choice.boundingRect().center())
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(round(point.x()), round(point.y())))
        self.settle(60)

    def dispose(self):
        self.window.close()
        shiboken6.delete(self.engine)
        self.app.processEvents()


class ChoiceBoxContracts(unittest.TestCase):
    def setUp(self):
        self.ui = ChoiceHarness()
        self.addCleanup(self.ui.dispose)

    def test_selection_preserves_binding_and_emits_once_even_on_reselection(self):
        self.assertTrue(self.ui.snapshot()["nativeRequested"])
        self.ui.evaluate("triggerItem(2)")
        selected = self.ui.snapshot()
        self.assertEqual((selected["index"], selected["text"], selected["display"], selected["activations"]),
                         (2, "Ignore", "忽略", 1))
        self.assertEqual([item["checked"] for item in selected["items"]], [False, False, True])
        self.ui.set("selectedIndex", 1)
        self.assertEqual(self.ui.snapshot()["index"], 1)
        self.ui.evaluate("triggerItem(1)")
        again = self.ui.snapshot()
        self.assertEqual(again["activations"], 2)
        self.assertEqual([item["checked"] for item in again["items"]], [False, True, False])
        self.ui.set("selectedIndex", 0)
        self.assertEqual((self.ui.snapshot()["index"], self.ui.snapshot()["activations"]), (0, 2))
        self.assertFalse(self.ui.warnings)

    def test_integer_model_placeholder_and_model_replacement(self):
        self.ui.set("format", "time")
        self.ui.set("options", 4)
        self.ui.set("selectedIndex", -1)
        state = self.ui.snapshot()
        self.assertEqual([item["text"] for item in state["items"]], ["时间点 1", "时间点 2", "时间点 3", "时间点 4"])
        self.assertFalse(any(item["checked"] for item in state["items"]))
        self.ui.evaluate("triggerItem(3)")
        self.assertEqual((self.ui.snapshot()["index"], self.ui.snapshot()["display"]), (3, "时间点 4"))
        self.ui.set("language", "en")
        self.assertEqual(self.ui.snapshot()["display"], "Time point 4")
        self.ui.set("format", "series")
        self.ui.set("options", ["Series 1 · A", "Series 2 · B"])
        self.ui.set("selectedIndex", 0)
        self.ui.evaluate("triggerItem(1)")
        self.assertEqual((self.ui.snapshot()["text"], self.ui.snapshot()["activations"]), ("Series 2 · B", 2))
        self.ui.set("options", [])
        self.assertEqual((self.ui.snapshot()["index"], self.ui.snapshot()["items"]), (-1, []))
        self.assertFalse(self.ui.warnings)

    def test_disabled_control_blocks_pointer_and_keyboard_activation(self):
        self.ui.set("selectable", False)
        self.assertFalse(any(item["enabled"] for item in self.ui.snapshot()["items"]))
        self.ui.click()
        QTest.keyClick(self.ui.window, Qt.Key.Key_End)
        state = self.ui.snapshot()
        self.assertEqual((state["index"], state["activations"], state["popupVisible"]), (0, 0, False))
        self.ui.set("selectable", True)
        self.assertTrue(all(item["enabled"] for item in self.ui.snapshot()["items"]))
        self.assertFalse(self.ui.warnings)

    def test_closed_control_keyboard_keeps_existing_combo_behavior(self):
        self.ui.choice.forceActiveFocus()
        QTest.keyClick(self.ui.window, Qt.Key.Key_End)
        self.assertEqual((self.ui.snapshot()["index"], self.ui.snapshot()["activations"]), (2, 1))
        QTest.keyClick(self.ui.window, Qt.Key.Key_Home)
        self.assertEqual((self.ui.snapshot()["index"], self.ui.snapshot()["activations"]), (0, 2))
        self.assertFalse(self.ui.snapshot()["popupVisible"])
        self.assertFalse(self.ui.warnings)


@unittest.skipUnless(COCOA, "Native NSMenu evidence requires an explicit Cocoa run")
class ChoiceBoxNativeMenus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.styleHints().setColorScheme(Qt.ColorScheme.Light)
        cls.temp = tempfile.TemporaryDirectory(prefix="leap-native-menu-test-", dir="/private/tmp")
        dylib = Path(cls.temp.name) / "menu-probe.dylib"
        subprocess.run(["xcrun", "clang++", "-dynamiclib", "-fobjc-arc", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-framework", "AppKit", str(ROOT / "tests/native_choicebox_probe.mm"), "-o", str(dylib)], check=True)
        cls.probe = ctypes.CDLL(str(dylib))
        cls.probe.probe_prepare.argtypes = [ctypes.c_char_p]
        cls.probe.probe_result.restype = ctypes.c_char_p
        cls.probe.probe_install()
        cls.evidence = []

    @classmethod
    def tearDownClass(cls):
        cls.probe.probe_uninstall()
        output = os.environ.get("LEAP_CHOICE_EVIDENCE_DIR")
        if output:
            directory = Path(output)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "cocoa-contracts.json").write_text(json.dumps({
                "platform": cls.app.platformName(), "input_scope": "QTest window clicks and in-process NSApp keyboard events",
                "native_visual_and_real_pointer_acceptance": "separate root CUA review",
                "observations": cls.evidence,
            }, ensure_ascii=False, indent=2) + "\n")
        cls.temp.cleanup()

    def setUp(self):
        self.ui = ChoiceHarness()
        self.addCleanup(self.ui.dispose)

    def native_action(self, action, *, expected_index, expected_activations):
        before = self.ui.snapshot()
        self.probe.probe_prepare(action.encode())
        self.ui.click()
        native = json.loads(self.probe.probe_result())
        after = self.ui.snapshot()
        self.evidence.append({"test": self.id(), "action": action, "before": before, "native": native, "after": after})
        self.assertTrue(native.get("native_popup_intercepted"), native)
        self.assertFalse(native["watchdog_cancelled"], native)
        self.assertEqual(native["tracking"]["class"], "QCocoaNSMenu")
        self.assertEqual(native["tracking"]["appearance"], "NSAppearanceNameAqua")
        self.assertTrue(native["tracking"]["app_active"])
        actions = [event for event in native["events"] if event["input"] == "NSApplication.sendAction"]
        if action.startswith("keyboard:"):
            self.assertEqual(len(actions), 1, native)
            self.assertTrue(actions[0]["delivered"], native)
            self.assertEqual(actions[0]["item"], before["items"][expected_index]["text"])
        else:
            self.assertEqual(actions, [], native)
        self.assertEqual([item["title"] for item in native["tracking"]["items"]], [item["text"] for item in before["items"]])
        self.assertEqual([item["checked"] for item in native["tracking"]["items"]], [item["checked"] for item in before["items"]])
        self.assertEqual((after["index"], after["activations"]), (expected_index, expected_activations))
        self.assertFalse(after["pressed"])
        self.assertFalse(after["popupVisible"])
        self.assertFalse(self.ui.warnings)

    def test_marker_selection_escape_reopen_and_bound_index(self):
        self.native_action("keyboard:2", expected_index=2, expected_activations=1)
        self.assertEqual((self.ui.snapshot()["text"], self.ui.snapshot()["display"]), ("Ignore", "忽略"))
        self.native_action("escape", expected_index=2, expected_activations=1)
        self.native_action("keyboard:2", expected_index=2, expected_activations=2)
        self.ui.set("selectedIndex", 1)
        self.native_action("escape", expected_index=1, expected_activations=2)
        self.native_action("keyboard:0", expected_index=0, expected_activations=3)

    def test_time_series_language_models_and_disabled_recovery(self):
        self.ui.set("options", 4); self.ui.set("format", "time"); self.ui.set("selectedIndex", -1)
        self.native_action("keyboard:2", expected_index=2, expected_activations=1)
        self.assertEqual(self.ui.snapshot()["display"], "时间点 3")
        self.ui.set("options", ["Series 1 · A", "Series 2 · B"]); self.ui.set("format", "series"); self.ui.set("selectedIndex", 0)
        self.native_action("keyboard:1", expected_index=1, expected_activations=2)
        self.ui.set("options", ["English", "中文"]); self.ui.set("format", "language"); self.ui.set("selectedIndex", 0)
        self.native_action("keyboard:1", expected_index=1, expected_activations=3)
        self.ui.set("selectable", False)
        self.probe.probe_prepare(b"escape")
        self.ui.click()
        QTest.keyClick(self.ui.window, Qt.Key.Key_Home)
        self.assertEqual(json.loads(self.probe.probe_result()), {})
        self.assertEqual((self.ui.snapshot()["index"], self.ui.snapshot()["activations"]), (1, 3))
        self.ui.set("selectable", True)
        self.native_action("escape", expected_index=1, expected_activations=3)
        self.native_action("keyboard:0", expected_index=0, expected_activations=4)


if __name__ == "__main__":
    unittest.main()
