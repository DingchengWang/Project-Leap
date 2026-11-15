"""Appearance and review-state contracts without scientific workers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QColorSpace, QImage
from PySide6.QtWidgets import QApplication
from leap_desktop.appearance import Appearance, ApplicationIcon, render_application_icon
from leap_desktop.native_style import navigation_state
from test_native_style import Backend, native_bridge_override


class AppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        bridge = patch("leap_desktop.appearance.bridge_path", return_value=Path(__file__))
        bridge.start()
        self.addCleanup(bridge.stop)

    def test_unsupported_platforms_fail_before_loading_the_bridge(self):
        for system, platform in (("darwin", "offscreen"), ("darwin", "minimal"), ("linux", "xcb")):
            with self.subTest(system=system, platform=platform), \
                 patch("leap_desktop.native_style.QGuiApplication.platformName", return_value=platform), \
                 patch("leap_desktop.native_style.sys.platform", system), \
                 patch("leap_desktop.appearance.ctypes.CDLL") as library:
                with self.assertRaises(RuntimeError):
                    Appearance(self.app, "dark")
                library.assert_not_called()

    def test_saved_mode_is_applied_before_native_snapshot_and_invalid_mode_is_rejected(self):
        library = Mock()
        library.leap_appearance_create.return_value = 101
        library.leap_appearance_snapshot.return_value = b'{"dark":true,"accent":[0.2,0.4,0.6,1],"reducedMotion":false}'
        schemes = []
        hints = Mock()
        library.leap_appearance_create.side_effect = lambda *_: schemes.append(hints.setColorScheme.call_args.args[0]) or 101
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.appearance.ctypes.CDLL", return_value=library), \
             patch.object(self.app, "styleHints", return_value=hints):
            appearance = Appearance(self.app, "dark")
        try:
            self.assertEqual(schemes, [Qt.ColorScheme.Dark])
            self.assertTrue(appearance.dark)
            with self.assertRaisesRegex(ValueError, "Unknown appearance mode"):
                appearance.setMode("sepia")
            self.assertEqual(appearance.mode, "dark")
            self.assertTrue(appearance.dark)
        finally:
            appearance.close()

    def test_brand_icon_preserves_shape_and_follows_accent_and_background(self):
        source = (ROOT / "app/leap_desktop/assets/icon.svg").read_bytes()
        images = [render_application_icon(source, QColor(accent), dark).convertToFormat(QImage.Format.Format_RGBA8888)
                  for accent, dark in (("#d43e80", False), ("#2a9858", False), ("#d43e80", True))]
        pixels = [bytes(image.bits()) for image in images]
        self.assertTrue(all(image.size() == images[0].size() for image in images))
        self.assertEqual(images[0].width(), 1024)
        self.assertEqual(pixels[0][3::4], pixels[1][3::4])
        self.assertEqual(pixels[0][3::4], pixels[2][3::4])
        for image, accent in zip(images, ("#d43e80", "#2a9858", "#d43e80")):
            self.assertEqual(image.pixelColor(510, 510), QColor(accent))
        self.assertEqual(images[0].pixelColor(150, 512), QColor("#ffffff"))
        self.assertEqual(images[2].pixelColor(150, 512), QColor("#161616"))
        self.assertNotEqual(pixels[0], pixels[1])

    def test_application_icon_publishes_identical_qt_and_native_pixels_and_updates_only_colors(self):
        import ctypes
        import shutil
        import tempfile
        from leap_desktop.paths import resource_root

        library = Mock()
        library.leap_appearance_create.return_value = 101
        initial = {"dark": False, "accent": [0.2, 0.4, 0.6, 1], "reducedMotion": False}
        library.leap_appearance_snapshot.return_value = json.dumps(initial).encode()
        published = []
        library.leap_application_icon_update.side_effect = lambda data, size: published.append(ctypes.string_at(data, size)) or 1
        previous = self.app.windowIcon()
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.appearance.ctypes.CDLL", return_value=library):
            appearance = Appearance(self.app)
        icon = None
        try:
            with patch("sys.frozen", False, create=True):
                icon = ApplicationIcon(self.app, appearance, resource_root())
            native_image = QImage.fromData(published[-1]).convertToFormat(QImage.Format.Format_RGBA8888)
            qt_image = self.app.windowIcon().pixmap(1024, 1024).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            self.assertEqual(bytes(native_image.bits()), bytes(qt_image.bits()))
            icon.close()
            icon = None
            with tempfile.TemporaryDirectory(prefix="leap-brand-") as directory:
                resources = Path(directory)
                asset = resources / "app/leap_desktop/assets/icon.svg"
                asset.parent.mkdir(parents=True)
                shutil.copyfile(ROOT / "app/leap_desktop/assets/icon.svg", asset)
                with patch("sys.frozen", True, create=True), patch("sys._MEIPASS", str(resources), create=True):
                    icon = ApplicationIcon(self.app, appearance, resource_root())
            self.assertEqual(published[0], published[1])
            callback = library.leap_appearance_create.call_args.args[0]
            callback(json.dumps({**initial, "reducedMotion": True}).encode(), None)
            self.assertEqual(len(published), 2)
            callback(json.dumps({**initial, "accent": [0.8, 0.1, 0.3, 1]}).encode(), None)
            self.assertEqual(len(published), 3)
            self.assertNotEqual(published[-1], published[-2])
            callback(json.dumps({**initial, "dark": True}).encode(), None)
            self.assertEqual(len(published), 4)
            self.assertEqual(QImage.fromData(published[-1]).pixelColor(150, 512), QColor("#161616"))
        finally:
            if icon is not None:
                icon.close()
            appearance.close()
            self.app.setWindowIcon(previous)

    def test_native_callback_updates_accent_and_motion_without_backend_signal(self):
        library = Mock()
        library.leap_appearance_create.return_value = 101
        initial = {"dark": False, "accent": [0.2, 0.4, 0.6, 1], "reducedMotion": False}
        library.leap_appearance_snapshot.return_value = json.dumps(initial).encode()
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.appearance.ctypes.CDLL", return_value=library):
            appearance = Appearance(self.app)
        try:
            changes = []
            appearance.changed.connect(lambda: changes.append(appearance.accent))
            update = {"dark": True, "accent": [0.8, 0.1, 0.3, 1], "reducedMotion": True}
            callback = library.leap_appearance_create.call_args.args[0]
            callback(json.dumps(update).encode(), None)
            self.assertTrue(appearance.dark)
            self.assertTrue(appearance.reducedMotion)
            self.assertEqual(appearance.accent, QColor.fromRgbF(*update["accent"]))
            self.assertEqual(len(changes), 1)
        finally:
            appearance.close()
            appearance.close()
        library.leap_appearance_destroy.assert_called_once_with(101)

    def test_color_conversion_error_releases_observer_instead_of_using_fixed_accent(self):
        library = Mock()
        library.leap_appearance_create.return_value = 101
        library.leap_appearance_snapshot.return_value = b'{"error":"sRGB conversion failed"}'
        with patch("leap_desktop.native_style.QGuiApplication.platformName", return_value="cocoa"), \
             patch("leap_desktop.native_style.sys.platform", "darwin"), \
             patch("leap_desktop.appearance.ctypes.CDLL", return_value=library):
            with self.assertRaisesRegex(RuntimeError, "sRGB conversion failed"):
                Appearance(self.app)
        library.leap_appearance_destroy.assert_called_once_with(101)

    def test_waiting_for_review_keeps_task_actions_without_active_progress(self):
        backend = Backend()
        backend.busy = backend.fijiAvailable = True
        backend.status = "waiting_review"
        state = navigation_state(backend)
        self.assertTrue(state["busy"])
        self.assertTrue(state["fijiAvailable"])
        self.assertFalse(state["showProgress"])
        backend.status = "running"
        self.assertTrue(navigation_state(backend)["showProgress"])


@unittest.skipUnless(sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM") == "cocoa",
                     "Requires Cocoa and the compiled AppKit appearance bridge")
class AppKitAppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cocoa_modes_and_external_appearance_update_without_opening_a_window(self):
        with native_bridge_override():
            appearance = Appearance(self.app, "dark")
        try:
            self.assertTrue(appearance.dark)
            self.assertTrue(appearance.accent.isValid())
            appearance.setMode("light")
            self.assertFalse(appearance.dark)
            # Change through Qt's native API instead of Appearance.setMode.
            # The AppKit observer must deliver the resulting effective state.
            self.app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
            self.app.processEvents()
            self.assertTrue(appearance.dark)
            appearance.setMode("system")
            self.assertEqual(appearance.dark, self.app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        finally:
            appearance.close()
            self.app.styleHints().unsetColorScheme()

    def test_native_views_follow_mode_and_review_hides_only_progress(self):
        from PySide6.QtTest import QTest
        import test_native_style

        with native_bridge_override():
            appearance = Appearance(self.app, "light")
        helper = test_native_style.AppKitNativeIntegrationTests()
        backend = Backend()
        backend.busy = backend.fijiAvailable = True
        backend.status = "waiting_review"
        try:
            with helper.hosted_sidebar(backend) as (style, _):
                for mode in ("dark", "light"):
                    appearance.setMode(mode)
                    helper.wait_for(lambda: all(view["dark"] == (mode == "dark")
                                               for view in helper.sidebar(style)["views"] if not view["hidden"]),
                                    "Visible native views did not follow the appearance")
                    state = helper.sidebar(style)
                    age = helper.age(style)
                    self.assertTrue(all(view["dark"] == (mode == "dark") for view in age["views"] if not view["hidden"]))
                    self.assertTrue(all(view["dark"] == (mode == "dark") for view in state["views"] if not view["hidden"]))
                self.assertFalse(any(row["role"] == "AXProgressIndicator" for row in state["elements"]))
                controls = {item["identifier"]: item for item in helper.actions(state)}
                for action in ("showFiji", "cancelAnalysis"):
                    self.assertTrue(controls[action]["enabled"])
                    self.assertGreater(controls[action]["frame"][2], 0)
        finally:
            appearance.close()
            self.app.styleHints().unsetColorScheme()

    def test_running_application_and_existing_sidebar_icons_follow_appearance_callbacks(self):
        from PySide6.QtTest import QTest
        import test_native_style

        with native_bridge_override():
            appearance = Appearance(self.app, "light")
        icon = ApplicationIcon(self.app, appearance, ROOT)
        helper = test_native_style.AppKitNativeIntegrationTests()
        probe = test_native_style.appkit_probe()

        def native_image(payload):
            image = QImage.fromData(payload)
            self.assertTrue(image.colorSpace().isValid())
            # AppKit snapshots use the display profile; compare in sRGB.
            return image.convertedToColorSpace(QColorSpace.NamedColorSpace.SRgb)

        def assert_color(actual, expected):
            self.assertLessEqual(max(abs(actual.red() - expected.red()), abs(actual.green() - expected.green()),
                                     abs(actual.blue() - expected.blue())), 1)

        def color_count(image, color):
            scale = image.width() / 236
            count = 0
            for y in range(round(24 * scale), round(64 * scale)):
                for x in range(round(22 * scale), round(58 * scale)):
                    pixel = image.pixelColor(x, y)
                    count += max(abs(pixel.red() - color.red()), abs(pixel.green() - color.green()),
                                 abs(pixel.blue() - color.blue())) <= 3
            return count

        try:
            with helper.hosted_sidebar(Backend()) as (style, _):
                # Inject the exact snapshot format delivered by the AppKit callback;
                # this changes only the isolated test app, not system preferences.
                for accent in (QColor("#d43e80"), QColor("#2a9858")):
                    appearance._dispatch(json.dumps({"dark": False, "accent": list(accent.getRgbF()),
                                                    "reducedMotion": False}).encode(), None)
                    QTest.qWait(60)
                    running_icon = native_image(probe.application_icon_png()).scaled(1024, 1024)
                    assert_color(running_icon.pixelColor(510, 510), accent)
                    sidebar = native_image(probe.view_png(style.navigationWindow))
                    self.assertGreater(color_count(sidebar, accent) / (sidebar.width() / 236) ** 2, 2,
                                       "The already-created sidebar must redraw its application icon")
                appearance.setMode("dark")
                QTest.qWait(60)
                running_icon = native_image(probe.application_icon_png()).scaled(1024, 1024)
                assert_color(running_icon.pixelColor(150, 512), QColor("#161616"))
        finally:
            icon.close()
            appearance.close()
            self.app.styleHints().unsetColorScheme()


if __name__ == "__main__":
    unittest.main()
