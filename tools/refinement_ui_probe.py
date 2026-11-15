"""Reusable Qt input and screenshot helpers for the refinement acceptance checks.

This module never launches an analysis worker or Fiji. Test fixtures must live in a
TemporaryDirectory or in an explicitly supplied dedicated verification directory.
A Qt screenshot does not include macOS native compositor layers; native glass is
verified separately on the real desktop.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import ExitStack
from pathlib import Path
import sys
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "cocoa")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QFont, QImage, QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from leap_desktop.appearance import Appearance, ApplicationIcon
from leap_desktop.native_style import create_native_style
from native_appkit_probe import AppKitProbe
import shiboken6


def visual_items(item):
    """Include Repeater delegates, which are not always QObject descendants."""
    result = [item]
    for child in item.childItems():
        result.extend(visual_items(child))
    return result


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture_previews(directory):
    """Create obvious, artificial ROI preview images for layout checks only."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    for name, color in (("Whole", "#283C72"), ("Soma", "#527C52"), ("Processes", "#8A5868")):
        path = directory / (name + "_ROI.png")
        image = QImage(200, 120, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        if not image.save(str(path)):
            raise RuntimeError("Fixture image could not be saved")
        result.append({"name": path.name, "path": str(path), "kind": "png"})
    return result


class QtUiHarness:
    """Drive a real QML window with mouse, keyboard and wheel input."""
    def __init__(self, backend, *, output=None, width=1210, height=820):
        self.app = QApplication.instance() or QApplication([])
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setFont(QFont("Helvetica Neue", 13))
        self.backend = backend
        library = os.environ.get("LEAP_TEST_NATIVE_LIBRARY")
        with ExitStack() as constructors:
            if library:
                for module in ("appearance", "native_style"):
                    constructors.enter_context(patch("leap_desktop." + module + ".bridge_path", return_value=Path(library)))
            self.appearance = Appearance(self.app, backend.appearanceMode)
            self.backend.appearanceModeChanged.connect(self.appearance.setMode)
            self.application_icon = ApplicationIcon(self.app, self.appearance, backend.paths.resources)
            self.native_style = create_native_style(self.app, backend)
        self.native_probe = AppKitProbe()
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda values: self.warnings.extend(str(v) for v in values))
        self.backend.locale.install_on(self.app, self.engine)
        self.engine.rootContext().setContextProperty("backend", backend)
        self.engine.rootContext().setContextProperty("nativeStyle", self.native_style)
        self.engine.rootContext().setContextProperty("appearance", self.appearance)
        self.engine.load(QUrl.fromLocalFile(str(backend.paths.qml)))
        if not self.engine.rootObjects():
            raise AssertionError("Production Main.qml did not load")
        self.window = self.engine.rootObjects()[0]
        self.window.setWidth(width)
        self.window.setHeight(height)
        self.output = Path(output) if output else None
        if self.output:
            self.output.mkdir(parents=True, exist_ok=True)
        self.events = []
        self.settle()
        self.window.raise_()
        self.window.requestActivate()
        if not QTest.qWaitForWindowActive(self.window, 2500):
            raise AssertionError("The Cocoa test window did not become active")

    def dispose(self):
        self.backend._timer.stop()
        self.app.removeTranslator(self.backend.locale._installed_translator)
        shiboken6.delete(self.engine)
        self.native_style.close()
        self.application_icon.close()
        self.backend.appearanceModeChanged.disconnect(self.appearance.setMode)
        self.appearance.close()
        self.native_probe.close()
        self.app.processEvents()

    def navigation_elements(self):
        return self.native_probe.snapshot(self.native_style.navigationWindow)["elements"]

    def press_navigation(self, identifier):
        self.wait_until(lambda: any(item["identifier"] == identifier for item in self.navigation_elements()))
        if not self.native_probe.press(self.native_style.navigationWindow, identifier):
            raise AssertionError("Native navigation rejected accessibility press: " + identifier)
        self.events.append({"input": "accessibilityPress", "control": identifier})
        self.settle()

    def settle(self, milliseconds=70):
        self.app.processEvents()
        QTest.qWait(milliseconds)
        self.app.processEvents()

    def wait_until(self, predicate, *, timeout_ms=2500):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if predicate():
                return
            self.settle(30)
        raise AssertionError("Expected Qt state did not appear before the deadline")

    def items(self):
        return visual_items(self.window.contentItem())

    def popup_items(self):
        # Retain wrappers: transient QQuickPopupWindow objects otherwise lose
        # their Python wrapper while callers are inspecting their child items.
        self._popup_windows = list(self.app.topLevelWindows())
        return [item for window in self._popup_windows
                if window is not self.window and isinstance(window, QQuickWindow) and window.isVisible()
                for item in visual_items(window.contentItem())]

    def find(self, name):
        found = self.window.findChild(QObject, name)
        if found is None:
            found = next((item for item in self.items() if item.objectName() == name), None)
        if found is None:
            raise AssertionError("Missing UI control: " + name)
        return found

    def named(self, prefix):
        return [item for item in self.items() if item.objectName().startswith(prefix)]

    def texts(self):
        return [item.property("text") for item in self.items()
                if item.isVisible() and isinstance(item.property("text"), str)]

    def in_view(self, item):
        if not item.isVisible() or item.width() <= 0 or item.height() <= 0:
            return False
        rectangle = item.mapRectToScene(item.boundingRect())
        target_window = self.window
        if rectangle.left() < 0 or rectangle.top() < 0 or rectangle.right() > target_window.width() or rectangle.bottom() > target_window.height():
            return False
        parent = item.parentItem()
        center = item.mapToScene(item.boundingRect().center())
        while parent:
            if parent.clip() and not parent.contains(parent.mapFromScene(center)):
                return False
            parent = parent.parentItem()
        return True

    def click(self, item_or_name, *, x_fraction=0.5, y_fraction=0.5, target_window=None):
        item = self.find(item_or_name) if isinstance(item_or_name, str) else item_or_name
        if not item.isEnabled() or not self.in_view(item):
            raise AssertionError("Control must be visible, enabled and inside its viewport before clicking: " + item.objectName())
        point = item.mapToScene(QPointF(item.width() * x_fraction, item.height() * y_fraction))
        name = item.objectName()
        QTest.mouseClick(target_window or self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(round(point.x()), round(point.y())))
        self.events.append({"input": "mouseClick", "control": name, "point": [point.x(), point.y()]})
        self.settle()

    def wheel(self, item_or_name, angle):
        item = self.find(item_or_name) if isinstance(item_or_name, str) else item_or_name
        point = item.mapToScene(item.boundingRect().center())
        event = QWheelEvent(point, QPointF(self.window.mapToGlobal(QPoint(round(point.x()), round(point.y())))),
                            QPoint(), QPoint(0, angle), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        self.app.sendEvent(self.window, event)
        self.events.append({"input": "wheel", "control": item.objectName(), "angle": angle, "accepted": event.isAccepted()})
        # Qt Quick animates wheel movement; wait for it before locating the next
        # control so a mouse click cannot land at its previous screen position.
        self.settle(350)
        self.wait_until(lambda: not any(item.property("moving") is True for item in self.items()))

    def capture(self, name):
        if not self.output:
            return None
        self.settle()
        path = self.output / (name + ".png")
        if not self.window.grabWindow().save(str(path)):
            raise AssertionError("Qt screenshot could not be saved")
        return {"path": str(path), "sha256": sha256(path), "width": self.window.width(), "height": self.window.height()}

    def capture_popups(self, name):
        if not self.output:
            return []
        self.popup_items()
        records = []
        for index, window in enumerate(self._popup_windows):
            if window.metaObject().className() != "QQuickPopupWindow" or not window.isVisible():
                continue
            path = self.output / (name + "-popup-" + str(index) + ".png")
            if not window.grabWindow().save(str(path)):
                raise AssertionError("Qt popup screenshot could not be saved")
            records.append({"path": str(path), "sha256": sha256(path)})
        return records

    def write_record(self, name, checks):
        if not self.output:
            return
        (self.output / (name + ".json")).write_text(json.dumps({
            "recorded_at": time.time(), "qt_platform": self.app.platformName(),
            "events": self.events, "checks": checks, "qml_warnings": self.warnings,
        }, ensure_ascii=False, indent=2) + "\n")


def make_fixture_seed(directory, component_id, *, needs_fiji=True):
    """Make a tiny schema-2 package; its launchers deliberately do no work."""
    from leap_desktop import maintenance
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = {"schema_version": 2, "component_id": component_id, "component_version": "1.0.1"}
    if component_id == "fiji":
        (directory / "Fiji").mkdir()
        executable = directory / "Fiji/launcher"
        descriptor["fiji_launcher"] = "Fiji/launcher"
    else:
        for folder in ("python/bin", "site-packages", "Models", "engine"):
            (directory / folder).mkdir(parents=True)
        executable = directory / "python/bin/python3"
        (directory / "Models/fixture-model").write_bytes(b"QA fixture only; not scientific weights\n")
        (directory / "engine/fixture.py").write_text("# QA fixture only; never executed\n")
        descriptor.update(python="python/bin/python3", site_packages="site-packages", models="Models", engine="engine")
        if needs_fiji:
            descriptor["fiji_component"] = "fiji"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    (directory / "runtime.json").write_text(json.dumps(descriptor) + "\n")
    maintenance.build_manifest(directory, component_id=component_id)
    return directory
