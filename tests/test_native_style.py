"""Contracts around native navigation without starting scientific workers."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication
from leap_desktop.native_style import NativeStyle, navigation_state, age_state


class Backend(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.locale = SimpleNamespace(translate=lambda value: value)
        self.installedModules = [{"id": "fluorescence", "name": "星形胶质细胞荧光强度分析", "hasError": False}]
        self.page = "analysis"
        self.activeModuleId = "fluorescence"
        self.busy = self.maintenanceBusy = self.fijiAvailable = False
        self.status = "idle"
        self.stage = "计算 ROI"
        self.maintenanceMessage = "安装进度"
        self.progress = -1.0
        self.appVersion = "1.0.0"
        self.running_task_override = None
        self.ageProfile = "auto"
        self.channelRoles = []
        self.imageInfo = {}
        self.parametersLocked = False
        self.setAgeProfile = Mock()
        self.setPage = Mock()
        self.selectModule = Mock()
        self.showFiji = Mock()
        self.cancelAnalysis = Mock()


    @property
    def runningTask(self):
        if self.running_task_override is not None:
            return self.running_task_override
        return ({"stage": self.stage, "status": self.status, "progress": self.progress,
                 "fijiAvailable": self.fijiAvailable} if self.busy else {})


class NativeStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        bridge = patch("leap_desktop.native_style.bridge_path", return_value=Path(__file__))
        bridge.start()
        self.addCleanup(bridge.stop)

    def test_unsupported_platforms_fail_before_loading_the_bridge(self):
        for system, platform in (("darwin", "offscreen"), ("darwin", "minimal"), ("linux", "xcb")):
            with self.subTest(system=system, platform=platform), \
                 patch("leap_desktop.native_style.QGuiApplication.platformName", return_value=platform), \
                 patch("leap_desktop.native_style.sys.platform", system), \
                 patch("leap_desktop.native_style.ctypes.CDLL") as library:
                with self.assertRaises(RuntimeError):
                    NativeStyle(Backend())
                library.assert_not_called()

    def test_mac_missing_bridge_is_an_error_not_an_opaque_replacement(self):
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.native_style.bridge_path", return_value=ROOT / "absent-test-bridge.dylib"):
            with self.assertRaisesRegex(RuntimeError, "bridge is missing"):
                NativeStyle(Backend())

    def test_native_action_routes_only_supported_actions(self):
        backend = Backend()
        style = SimpleNamespace(_backend=backend)
        for action in (b"page:settings", b"module:fluorescence", b"showFiji", b"cancelAnalysis", b"page:unknown"):
            NativeStyle._dispatch(style, action, None)
        backend.setPage.assert_called_once_with("settings")
        backend.selectModule.assert_called_once_with("fluorescence")
        backend.showFiji.assert_called_once_with()
        backend.cancelAnalysis.assert_called_once_with()

    def test_activity_remains_visible_while_navigating_to_settings(self):
        backend = Backend()
        backend.page = "settings"
        backend.busy = backend.fijiAvailable = True
        backend.status = "waiting_review"
        state = navigation_state(backend)
        self.assertEqual(state["page"], "settings")
        self.assertEqual(state["activity"], backend.stage)
        self.assertTrue(state["busy"])
        self.assertTrue(state["fijiAvailable"])
        backend.status = "cancelling"
        self.assertTrue(navigation_state(backend)["cancelling"])
        self.assertFalse(navigation_state(backend)["showProgress"])
        for status in ("completed", "cancelled", "failed", "ready", "idle"):
            backend.status = status
            self.assertFalse(navigation_state(backend)["showProgress"])

    def test_sidebar_task_comes_from_running_session_while_active_session_is_idle(self):
        backend = Backend()
        backend.stage = "Ready in another workbench"
        backend.running_task_override = {"status": "waiting_review", "stage": "Global running stage", "progress": .6, "fijiAvailable": True}
        state = navigation_state(backend)
        self.assertEqual(state["activity"], "Global running stage")
        self.assertEqual(state["progress"], -1)
        self.assertTrue(state["busy"])
        self.assertTrue(state["fijiAvailable"])

    def test_both_analysis_modules_publish_indeterminate_sidebar_progress(self):
        backend = Backend()
        backend.busy = True
        backend.status = "running"
        for module in ("project-leap-2d", "cell-counting"):
            backend.activeModuleId = module
            for progress in (-1, 0, .4, 1):
                backend.progress = progress
                state = navigation_state(backend)
                self.assertTrue(state["showProgress"])
                self.assertEqual(state["progress"], -1)
                self.assertEqual(backend.progress, progress)
        backend.status = "waiting_review"
        self.assertFalse(navigation_state(backend)["showProgress"])
        backend.status = "cancelling"
        self.assertTrue(navigation_state(backend)["cancelling"])
        backend.busy = False
        backend.maintenanceBusy = True
        state = navigation_state(backend)
        self.assertFalse(state["showProgress"])
        self.assertEqual(state["progress"], 0)

    def test_idle_has_no_activity_copy_but_maintenance_keeps_its_status(self):
        backend = Backend()
        idle = navigation_state(backend)
        self.assertEqual(idle["activityHeading"], "")
        self.assertEqual(idle["activity"], "")
        self.assertFalse(idle["busy"])
        self.assertFalse(idle["fijiAvailable"])
        backend.maintenanceBusy = True
        maintenance = navigation_state(backend)
        self.assertEqual(maintenance["activityHeading"], "当前任务")
        self.assertEqual(maintenance["activity"], backend.maintenanceMessage)

    def test_module_error_remains_selectable_and_explains_repair_location(self):
        backend = Backend()
        backend.installedModules[0].update(hasError=True, message="Missing runtime")
        state = navigation_state(backend)
        self.assertEqual(state["modules"][0]["id"], "fluorescence")
        self.assertTrue(state["modules"][0]["hasError"])
        self.assertEqual(state["modules"][0]["statusLabel"], "需要修复")
        self.assertIn("功能与维护", state["modules"][0]["tooltip"])
        self.assertEqual(state["modulesHeading"], "功能模块")
        self.assertNotIn("subtitle", state)

    def test_healthy_module_has_a_visible_ready_status(self):
        state = navigation_state(Backend())
        self.assertFalse(state["modules"][0]["hasError"])
        self.assertEqual(state["modules"][0]["statusLabel"], "已就绪")



    def test_payload_contains_localized_navigation_and_module_status(self):
        from leap_desktop.i18n import TranslationCatalog

        backend = Backend()
        catalog = TranslationCatalog()
        for translate in (lambda text: text, catalog.translate):
            backend.locale = SimpleNamespace(translate=translate)
            backend.installedModules = [
                {"id": "second", "name": "Second module", "hasError": True, "message": "Missing runtime"},
                {"id": "first", "name": "First module", "hasError": False},
            ]
            state = navigation_state(backend)
            self.assertEqual(state["navigation"], [translate(value) for value in
                             ("分析工作台", "任务与结果", "功能与维护", "设置")])
            self.assertEqual([module["id"] for module in state["modules"]], ["second", "first"])
            self.assertEqual([module["statusLabel"] for module in state["modules"]],
                             [translate("需要修复"), translate("已就绪")])
            self.assertIn(translate("请在功能与维护中修复模块。"), state["modules"][0]["tooltip"])


    def test_age_state_locks_running_parameters_and_disables_gfap_only_neonatal(self):
        backend = Backend()
        backend.activeModuleId = "project-leap-2d"
        self.assertEqual(age_state(backend)["enabled"], [True, True, True])
        backend.imageInfo = {"path": "input.tif"}
        backend.channelRoles = ["DAPI", "GFAP"]
        self.assertEqual(age_state(backend)["enabled"], [True, True, False])
        backend.parametersLocked = True
        self.assertEqual(age_state(backend)["enabled"], [False, False, False])
        backend.parametersLocked = False
        backend.channelRoles.append("eGFP")
        backend.ageProfile = "auto"
        self.assertEqual(age_state(backend)["selected"], "auto")
        self.assertEqual(age_state(backend)["enabled"], [True, True, True])

    def test_age_dispatch_requests_controller_and_never_changes_local_state(self):
        backend = Backend()
        style = SimpleNamespace(_backend=backend)
        NativeStyle._dispatch(style, b"age:neonatal", None)
        NativeStyle._dispatch(style, b"age:invalid", None)
        backend.setAgeProfile.assert_called_once_with("neonatal")
        self.assertEqual(backend.ageProfile, "auto")

    @staticmethod
    def bridge_library():
        return SimpleNamespace(**{
            "leap_glass_available": Mock(return_value=1),
            "leap_glass_create": Mock(return_value=123),
            "leap_glass_view": Mock(return_value=456),
            "leap_glass_update": Mock(return_value=1),
            "leap_glass_destroy": Mock(),
            "leap_glass_age_create": Mock(return_value=124),
            "leap_glass_age_view": Mock(return_value=457),
            "leap_glass_age_update": Mock(return_value=1),
            "leap_glass_age_destroy": Mock(),
        })

    def test_bridge_owns_native_view_after_foreign_window_wrapper(self):
        import json
        import shiboken6
        from PySide6.QtGui import QWindow

        backend = Backend()
        library = self.bridge_library()
        wrapper = QWindow()
        destroyed_after_wrapper = []
        library.leap_glass_destroy.side_effect = lambda handle: destroyed_after_wrapper.append(
            (handle, not shiboken6.isValid(wrapper)))
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.native_style.ctypes.CDLL", return_value=library), \
             patch("leap_desktop.native_style.QWindow.fromWinId", side_effect=[wrapper, QWindow()]):
            style = NativeStyle(backend)
        self.assertEqual(json.loads(library.leap_glass_update.call_args.args[1]), navigation_state(backend))
        style.refresh()
        library.leap_glass_update.assert_called_once()
        backend.busy = True
        backend.progress = .25
        backend.changed.emit()
        self.assertEqual(library.leap_glass_update.call_count, 2)
        style.close()
        style.close()
        self.assertEqual(destroyed_after_wrapper, [(123, True)])
        self.assertIsNone(style.navigationWindow)
        self.assertFalse(style.navigationAvailable)
        backend.changed.emit()
        self.assertEqual(library.leap_glass_update.call_count, 2)

    def test_failed_foreign_window_creation_releases_native_view(self):
        library = self.bridge_library()
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.native_style.ctypes.CDLL", return_value=library), \
             patch("leap_desktop.native_style.QWindow.fromWinId", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Qt could not host"):
                NativeStyle(Backend())
        library.leap_glass_destroy.assert_called_once_with(123)

    def test_failed_age_wrapper_creation_releases_both_native_handles(self):
        import shiboken6
        from PySide6.QtGui import QWindow
        library = self.bridge_library()
        sidebar = QWindow()
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.native_style.ctypes.CDLL", return_value=library), \
             patch("leap_desktop.native_style.QWindow.fromWinId", side_effect=[sidebar, None]):
            with self.assertRaisesRegex(RuntimeError, "native age selector"):
                NativeStyle(Backend())
        self.assertFalse(shiboken6.isValid(sidebar))
        library.leap_glass_age_destroy.assert_called_once_with(124)
        library.leap_glass_destroy.assert_called_once_with(123)

    def test_unavailable_native_material_is_a_visible_error(self):
        library = self.bridge_library()
        library.leap_glass_available.return_value = 0
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.native_style.ctypes.CDLL", return_value=library):
            with self.assertRaisesRegex(RuntimeError, "macOS 27"):
                NativeStyle(Backend())
        library.leap_glass_create.assert_not_called()

    def test_foreign_window_exception_releases_native_view(self):
        library = self.bridge_library()
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.native_style.ctypes.CDLL", return_value=library), \
             patch("leap_desktop.native_style.QWindow.fromWinId", side_effect=RuntimeError("Foreign window failed")):
            with self.assertRaisesRegex(RuntimeError, "Foreign window failed"):
                NativeStyle(Backend())
        library.leap_glass_destroy.assert_called_once_with(123)

    def test_rejected_initial_state_disconnects_and_releases_both_objects(self):
        import shiboken6
        from PySide6.QtGui import QWindow

        backend = Backend()
        library = self.bridge_library()
        library.leap_glass_update.return_value = 0
        wrapper = QWindow()
        wrapper_released_first = []
        library.leap_glass_destroy.side_effect = lambda _: wrapper_released_first.append(not shiboken6.isValid(wrapper))
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.native_style.ctypes.CDLL", return_value=library), \
             patch("leap_desktop.native_style.QWindow.fromWinId", side_effect=[wrapper, QWindow()]):
            with self.assertRaisesRegex(RuntimeError, "rejected the navigation state"):
                NativeStyle(backend)
        self.assertEqual(wrapper_released_first, [True])
        backend.changed.emit()
        library.leap_glass_update.assert_called_once()

    def test_native_entry_points_reject_a_worker_thread(self):
        import threading

        errors = []
        def worker():
            try:
                NativeStyle._main_thread()
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIn("main thread", errors[0])


@contextmanager
def native_bridge_override():
    """Keep alternate library selection entirely in the external test harness."""
    with ExitStack() as stack:
        path = os.environ.get("LEAP_TEST_NATIVE_LIBRARY")
        if path:
            for module in ("leap_desktop.native_style", "leap_desktop.appearance"):
                stack.enter_context(patch(module + ".bridge_path", return_value=Path(path)))
        yield


_APPKIT_PROBE = None


def appkit_probe():
    global _APPKIT_PROBE
    if _APPKIT_PROBE is None:
        import atexit
        from native_appkit_probe import AppKitProbe
        _APPKIT_PROBE = AppKitProbe()
        atexit.register(_APPKIT_PROBE.close)
    return _APPKIT_PROBE


@unittest.skipUnless(sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM") == "cocoa",
                     "Requires Cocoa and the compiled AppKit bridge")
class AppKitNativeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, message):
        from PySide6.QtTest import QTest
        for _ in range(100):
            if predicate():
                return
            QTest.qWait(20)
        self.fail(message)

    def sidebar(self, style):
        return appkit_probe().snapshot(style.navigationWindow)

    def age(self, style):
        return appkit_probe().snapshot(style.ageWindow)

    def press(self, style, identifier):
        window = style.ageWindow if identifier.startswith("age:") else style.navigationWindow
        return appkit_probe().press(window, identifier)

    @staticmethod
    def actions(state, prefix=""):
        return [item for item in state["elements"] if item["role"] in ("AXButton", "AXRadioButton")
                and item["identifier"].startswith(prefix)]

    @classmethod
    def selected(cls, state, prefix):
        return [item["identifier"] for item in cls.actions(state, prefix) if item.get("value") == 1]

    @contextmanager
    def hosted_sidebar(self, backend):
        import shiboken6
        from PySide6.QtQml import QQmlApplicationEngine

        appkit_probe()
        with native_bridge_override():
            style = NativeStyle(backend)
        engine = QQmlApplicationEngine()
        window = None
        try:
            engine.rootContext().setContextProperty("nativeStyle", style)
            engine.loadData(b'''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ApplicationWindow {
    visible: false; width: 900; height: 620
    property real sidebarWidth: 236
    property int testPage: 0
    property bool testAgeVisible: true
    RowLayout {
        anchors.fill: parent
        WindowContainer { Layout.minimumWidth: sidebarWidth; Layout.preferredWidth: sidebarWidth; Layout.maximumWidth: sidebarWidth; Layout.fillHeight: true; window: nativeStyle.navigationWindow }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            WindowContainer { visible: testAgeVisible; Layout.fillWidth: true; Layout.preferredHeight: 48; window: nativeStyle.ageWindow }
            StackLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; currentIndex: testPage
                Button { text: "First page action" }
                Button { text: "Second page action" }
            }
        }
    }
}''')
            self.assertTrue(engine.rootObjects())
            window = engine.rootObjects()[0]
            window.setFlags(Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
            window.setOpacity(0)
            window.show()
            self.wait_for(lambda: self.sidebar(style)["attached"], "Native view did not attach")
            self.wait_for(lambda: len(self.actions(self.sidebar(style), "page:")) == 4 and
                          all(item["frame"][2] > 0 for item in self.actions(self.sidebar(style), "page:")),
                          "Native accessibility layout did not settle")
            yield style, window
        finally:
            if window is not None:
                window.hide()
            shiboken6.delete(engine)
            try:
                if style.navigationAvailable:
                    self.wait_for(lambda: not self.sidebar(style)["attached"], "Native view outlived its host window")
            finally:
                style.close()

    def test_hidden_age_view_removes_window_accessibility_registration_and_restores_it(self):
        backend = Backend()
        backend.activeModuleId = "project-leap-2d"
        with self.hosted_sidebar(backend) as (style, window):
            def registered():
                return [row for row in self.age(style)["windowElements"] if row["identifier"].startswith("age:")]
            self.wait_for(lambda: len(registered()) == 3, "Age AX did not attach")
            window.setProperty("testAgeVisible", False)
            self.wait_for(lambda: self.age(style)["hidden"], "Age NSView did not hide")
            self.assertEqual(registered(), [])
            self.assertEqual(self.actions(self.age(style), "age:"), [])
            window.setProperty("testAgeVisible", True)
            self.wait_for(lambda: len(registered()) == 3, "Age AX did not restore")
            self.assertFalse(self.age(style)["hidden"])
            self.assertTrue(self.press(style, "age:mature"))

    def test_horizontal_age_view_uses_confirmed_state_and_disables_locked_options(self):
        backend = Backend()
        backend.activeModuleId = "project-leap-2d"
        with self.hosted_sidebar(backend) as (style, _):
            self.wait_for(lambda: self.age(style)["attached"], "Age selector did not attach")
            choices = self.actions(self.age(style), "age:")
            self.assertEqual(len(choices), 3)
            self.assertEqual(len({item["frame"][1] for item in choices}), 1)
            self.assertEqual([item["frame"][0] for item in choices], sorted(item["frame"][0] for item in choices))
            self.assertEqual(self.selected(self.age(style), "age:"), ["age:auto"])
            self.assertTrue(self.press(style, "age:mature"))
            backend.setAgeProfile.assert_called_once_with("mature")
            self.assertEqual(self.selected(self.age(style), "age:"), ["age:auto"])
            backend.ageProfile = "mature"
            backend.changed.emit()
            self.assertEqual(self.selected(self.age(style), "age:"), ["age:mature"])
            backend.imageInfo = {"path": "input.tif"}
            backend.channelRoles = ["DAPI", "GFAP"]
            backend.changed.emit()
            self.assertFalse(self.press(style, "age:neonatal"))
            backend.parametersLocked = True
            backend.changed.emit()
            self.assertFalse(self.press(style, "age:auto"))
            self.assertFalse(self.press(style, "age:mature"))
            self.assertTrue(all(not choice["enabled"] for choice in self.actions(self.age(style), "age:")))

    def test_startup_locale_and_installed_modules_reach_native_controls(self):
        from leap_desktop.i18n import TranslationCatalog

        backend = Backend()
        with self.hosted_sidebar(backend) as (style, _):
            self.assertEqual([row["identifier"] for row in self.actions(self.sidebar(style), "page:")],
                             ["page:analysis", "page:jobs", "page:maintenance", "page:settings"])
            for translate in (lambda text: text, TranslationCatalog().translate):
                backend.locale = SimpleNamespace(translate=translate)
                backend.installedModules = [
                    {"id": "healthy", "name": translate("星形胶质细胞荧光强度分析"), "hasError": False},
                    {"id": "broken", "name": "Missing module", "hasError": True},
                ]
                backend.activeModuleId = "healthy"
                style.refresh()
                self.wait_for(lambda: [item["title"] for item in self.actions(self.sidebar(style), "page:")] ==
                              navigation_state(backend)["navigation"] and
                              [item["identifier"] for item in self.actions(self.sidebar(style), "module:")] ==
                              ["module:healthy", "module:broken"], "Native locale or module list did not update")
                modules = self.actions(self.sidebar(style), "module:")
                self.assertEqual([item["identifier"] for item in modules], ["module:healthy", "module:broken"])
                self.assertEqual([item["title"] for item in modules], [item["name"] for item in backend.installedModules])
                self.assertIn(translate("已就绪"), modules[0]["help"])
                self.assertIn(translate("需要修复"), modules[1]["help"])
                self.assertIn(translate("请在功能与维护中修复模块。"), modules[1]["help"])
                self.assertEqual(self.selected(self.sidebar(style), "module:"), ["module:healthy"])

    def test_ax_dispatch_preserves_controller_authority_and_global_tasks(self):
        backend = Backend()
        backend.installedModules.append({"id": "other", "name": "Other", "hasError": False})
        def set_page(page):
            backend.page = page
            backend.changed.emit()
        def select_module(identifier):
            if not backend.busy:
                backend.activeModuleId = identifier
                backend.changed.emit()
        backend.setPage.side_effect = set_page
        backend.selectModule.side_effect = select_module
        with self.hosted_sidebar(backend) as (style, _):
            self.assertTrue(self.press(style, "page:settings"))
            backend.setPage.assert_called_once_with("settings")
            self.wait_for(lambda: self.selected(self.sidebar(style), "page:") == ["page:settings"], "Page state did not update")
            backend.busy = backend.fijiAvailable = True
            backend.status = "waiting_review"
            style.refresh()
            def task_actions():
                return [item["identifier"] for item in self.actions(self.sidebar(style))
                        if item["identifier"] in ("showFiji", "cancelAnalysis")]
            self.wait_for(lambda: set(task_actions()) == {"showFiji", "cancelAnalysis"}, "Global task AX actions were missing")
            self.assertEqual(len(task_actions()), 2)
            self.assertTrue(self.press(style, "module:other"))
            backend.selectModule.assert_called_once_with("other")
            self.assertEqual(self.selected(self.sidebar(style), "module:"), ["module:fluorescence"])
            self.assertTrue(self.press(style, "showFiji"))
            self.assertTrue(self.press(style, "cancelAnalysis"))
            backend.showFiji.assert_called_once_with()
            backend.cancelAnalysis.assert_called_once_with()
            backend.status = "cancelling"
            backend.fijiAvailable = False
            style.refresh()
            self.wait_for(lambda: not any(row["identifier"] == "showFiji" for row in self.actions(self.sidebar(style))), "Fiji action remained visible")
            self.assertFalse(self.press(style, "cancelAnalysis"))
            self.assertFalse(self.press(style, "showFiji"))
            self.assertFalse(self.press(style, "page:unknown"))
            self.assertFalse(self.press(style, "module:absent"))
            self.assertEqual(backend.cancelAnalysis.call_count, 1)

    def test_short_window_scrolls_six_modules_without_moving_task_actions(self):
        backend = Backend()
        backend.busy = backend.fijiAvailable = True
        backend.status = "running"
        backend.installedModules = [{"id": f"module-{index}", "name": f"Astrocyte Fluorescence Intensity Measurement {index}",
                                     "hasError": index % 2 == 1} for index in range(6)]
        backend.activeModuleId = "module-0"
        with self.hosted_sidebar(backend) as (style, window):
            for width in (182, 236):
                window.setProperty("sidebarWidth", width)
                self.wait_for(lambda: self.sidebar(style)["frame"][2] == width, "Native sidebar did not resize")
                self.wait_for(lambda: len(self.actions(self.sidebar(style), "module:")) == 6, "Module list did not settle")
                self.assertEqual(self.sidebar(style)["frame"][3], 620)
                def task_frames():
                    return {item["identifier"]: item["frame"] for item in self.actions(self.sidebar(style))
                            if item["identifier"] in ("showFiji", "cancelAnalysis")}
                self.wait_for(lambda: set(task_frames()) == {"showFiji", "cancelAnalysis"}, "Task controls did not settle")
                footer = task_frames()
                self.assertEqual(set(footer), {"showFiji", "cancelAnalysis"})
                for frame in footer.values():
                    self.assertGreater(frame[2], 0)
                    self.assertGreaterEqual(frame[1], 0)
                    self.assertLessEqual(frame[1] + frame[3], 620)
                # Real AX presses invoke the production control and reveal the requested row.
                self.assertTrue(self.press(style, "module:module-0"))
                self.wait_for(lambda: self.actions(self.sidebar(style), "module:")[0]["frame"][1] >= 0, "First module did not reveal")
                before = self.actions(self.sidebar(style), "module:")[-1]["frame"][1]
                self.assertTrue(self.press(style, "module:module-5"))
                self.wait_for(lambda: self.actions(self.sidebar(style), "module:")[-1]["frame"][1] < before - 10, "Module list did not scroll")
                self.assertEqual(footer, task_frames())

    def test_real_accessibility_tree_is_unique_and_qt_page_children_stay_dynamic(self):
        backend = Backend()
        with self.hosted_sidebar(backend) as (style, window):
            expected = ["page:analysis", "page:jobs", "page:maintenance", "page:settings", "module:fluorescence"]
            def action_ids():
                return [row["identifier"] for row in self.sidebar(style)["windowElements"]
                        if row["role"] in ("AXButton", "AXRadioButton") and row["identifier"] in expected]
            self.wait_for(lambda: all(action in action_ids() for action in expected), "Native AX actions were missing")
            self.assertEqual(action_ids(), expected)
            def window_titles():
                return [row["title"] for row in self.sidebar(style)["windowElements"]]
            self.assertIn("First page action", window_titles())
            window.setProperty("testPage", 1)
            self.wait_for(lambda: "Second page action" in window_titles(), "Qt AX children did not follow the visible page")
            self.assertNotIn("First page action", window_titles())
        self.assertFalse(style.navigationAvailable)
        self.assertIsNone(style.navigationWindow)

    def test_footer_version_and_copyright_share_one_visible_line(self):
        backend = Backend()
        with self.hosted_sidebar(backend) as (style, _):
            def text_rows():
                return {row.get("value"): row for row in self.sidebar(style)["elements"]
                        if row["role"] == "AXStaticText"}
            self.wait_for(lambda: "©2026 Dingcheng Wang" in text_rows(), "Copyright footer did not appear")
            rows = text_rows()
            version, copyright = rows["v" + backend.appVersion], rows["©2026 Dingcheng Wang"]
            self.assertEqual((version["fontSize"], copyright["fontSize"]), (11, 11))
            self.assertEqual(version["frame"][1], copyright["frame"][1])
            self.assertLess(version["frame"][0] + version["frame"][2], copyright["frame"][0])
            self.assertLessEqual(copyright["frame"][0] + copyright["frame"][2], 236)
            self.assertLessEqual(copyright["frame"][1] + copyright["frame"][3], 620)


if __name__ == "__main__":
    unittest.main()
