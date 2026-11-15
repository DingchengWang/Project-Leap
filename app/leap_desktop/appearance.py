"""Expose one effective appearance to Qt Quick and the AppKit controls.

Construct this object before loading any QML windows. Qt's Cocoa style hint
sets NSApplication.appearance; AppKit resolves the effective scheme and sRGB
accent, and its accessibility notification supplies the motion preference.
"""
from __future__ import annotations

import ctypes
import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Property, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .native_style import NativeStyle, bridge_path, require_cocoa

_CHANGE = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_void_p)
_SCHEMES = {"system": Qt.ColorScheme.Unknown, "light": Qt.ColorScheme.Light,
            "dark": Qt.ColorScheme.Dark}


class Appearance(QObject):
    changed = Signal()
    _nativeChanged = Signal(str)

    def __init__(self, app, mode="system"):
        super().__init__(app)
        NativeStyle._main_thread()
        if mode not in _SCHEMES:
            raise ValueError("Unknown appearance mode: " + str(mode))
        self._app = app
        self._hints = app.styleHints()
        self._mode = mode
        self._dark = False
        self._accent = QColor()
        self._reduced_motion = False
        self._library = None
        self._handle = None
        self._callback = None
        require_cocoa()
        # Qt assigns NSApplication.appearance, including nil for System.
        # Apply it before any native views or QML windows are created.
        self._hints.setColorScheme(_SCHEMES[mode])
        path = bridge_path()
        if not path.is_file():
            raise RuntimeError("Liquid Glass bridge is missing: " + str(path))
        library = ctypes.CDLL(str(path))
        for name, arguments, result in (
            ("create", [_CHANGE, ctypes.c_void_p], ctypes.c_void_p),
            ("snapshot", [ctypes.c_void_p], ctypes.c_char_p),
            ("destroy", [ctypes.c_void_p], None),
        ):
            function = getattr(library, "leap_appearance_" + name, None)
            if function is None:
                raise RuntimeError("The native bridge is outdated; rebuild or reinstall Project Leap.")
            function.argtypes, function.restype = arguments, result
        self._library = library
        self._nativeChanged.connect(self._acceptSnapshot)
        self._callback = _CHANGE(self._dispatch)
        self._handle = library.leap_appearance_create(self._callback, None)
        if not self._handle:
            raise RuntimeError("AppKit could not create the appearance observer.")
        try:
            self._refreshNative()
        except Exception:
            self.close()
            raise

    mode = Property(str, lambda self: self._mode, notify=changed)
    dark = Property(bool, lambda self: self._dark, notify=changed)
    accent = Property(QColor, lambda self: QColor(self._accent), notify=changed)
    reducedMotion = Property(bool, lambda self: self._reduced_motion, notify=changed)

    @Slot(str)
    def setMode(self, mode):
        NativeStyle._main_thread()
        if mode not in _SCHEMES:
            raise ValueError("Unknown appearance mode: " + str(mode))
        if mode == self._mode:
            return
        self._mode = mode
        self._hints.setColorScheme(_SCHEMES[mode])
        self._refreshNative()
        self.changed.emit()

    def _dispatch(self, raw_snapshot, _context):
        self._nativeChanged.emit(raw_snapshot.decode("utf-8"))

    def _refreshNative(self):
        raw = self._library.leap_appearance_snapshot(self._handle)
        if raw is None:
            raise RuntimeError("AppKit could not read the effective appearance.")
        self._acceptSnapshot(raw.decode("utf-8"))

    @Slot(str)
    def _acceptSnapshot(self, raw_snapshot):
        state = json.loads(raw_snapshot)
        if "error" in state:
            raise RuntimeError(state["error"])
        self._setState(state["dark"], QColor.fromRgbF(*state["accent"]), state["reducedMotion"])

    def _setState(self, dark, accent, reduced_motion):
        if (self._dark, self._accent, self._reduced_motion) == (dark, accent, reduced_motion):
            return
        self._dark, self._accent, self._reduced_motion = dark, accent, reduced_motion
        self.changed.emit()

    def close(self):
        NativeStyle._main_thread()
        if self._handle:
            self._library.leap_appearance_destroy(self._handle)
            self._handle = None
            self._callback = None
            self._nativeChanged.disconnect(self._acceptSnapshot)


def render_application_icon(source: bytes, accent: QColor, dark: bool) -> QImage:
    """Recolor the original astrocyte SVG without changing its paths or sizing."""
    if not accent.isValid():
        raise RuntimeError("The system accent color is invalid.")
    background = QColor("#161616" if dark else "#FFFFFF")
    tint = QColor.fromRgbF(*(background.getRgbF()[index] * 0.82 + accent.getRgbF()[index] * 0.18
                            for index in range(3)))
    svg = source.decode("utf-8")
    for original, color in (("#FFFFFF", background), ("#EAF0FD", background),
                            ("#D7E2F5", tint), ("#3C6EDD", accent),
                            ("#DFE9FD", tint), ("#6A8FE0", accent)):
        svg = svg.replace(original, color.name())
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise RuntimeError("The Project Leap application icon could not be loaded.")
    image = QImage(1024, 1024, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return image


class ApplicationIcon(QObject):
    """Keep Qt, running Dock and native sidebar icons on the same appearance."""
    def __init__(self, app, appearance, resource_directory):
        super().__init__(app)
        NativeStyle._main_thread()
        self._app = app
        self._appearance = appearance
        self._source = (Path(resource_directory) / "app/leap_desktop/assets/icon.svg").read_bytes()
        self._last_colors = None
        self._connected = False
        self._publish = getattr(appearance._library, "leap_application_icon_update", None)
        if self._publish is None:
            raise RuntimeError("The native bridge is outdated; rebuild or reinstall Project Leap.")
        self._publish.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self._publish.restype = ctypes.c_int
        self.refresh()
        appearance.changed.connect(self.refresh)
        self._connected = True

    @Slot()
    def refresh(self):
        NativeStyle._main_thread()
        colors = (self._appearance.dark, self._appearance.accent.rgba())
        if colors == self._last_colors:
            return
        image = render_application_icon(self._source, self._appearance.accent, self._appearance.dark)
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
            raise RuntimeError("The Project Leap application icon could not be rendered.")
        buffer.close()
        payload = ctypes.create_string_buffer(bytes(data))
        self._app.setWindowIcon(QIcon(QPixmap.fromImage(image)))
        if not self._publish(payload, len(data)):
            raise RuntimeError("AppKit could not update the Project Leap application icon.")
        self._last_colors = colors

    def close(self):
        NativeStyle._main_thread()
        if self._connected:
            self._appearance.changed.disconnect(self.refresh)
            self._connected = False
