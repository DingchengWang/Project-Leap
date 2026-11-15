"""External public AppKit probe used only by Cocoa tests and the visible harness."""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
import subprocess
import tempfile


class AppKitProbe:
    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="leap-appkit-probe-", dir="/private/tmp")
        library = Path(self._temporary.name) / "probe.dylib"
        subprocess.run(["xcrun", "clang", "-dynamiclib", "-fobjc-arc", "-Wall", "-Wextra", "-Werror",
                        "-framework", "AppKit", str(Path(__file__).with_suffix(".m")), "-o", str(library)], check=True)
        self._library = ctypes.CDLL(str(library))
        self._library.probe_enable_accessibility.argtypes = []
        self._library.probe_enable_accessibility.restype = None
        self._library.probe_enable_accessibility()
        self._library.probe_view_snapshot.argtypes = [ctypes.c_void_p]
        self._library.probe_view_snapshot.restype = ctypes.c_char_p
        self._library.probe_accessibility_press.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self._library.probe_accessibility_press.restype = ctypes.c_int
        self._library.probe_application_icon_png.argtypes = [ctypes.POINTER(ctypes.c_size_t)]
        self._library.probe_application_icon_png.restype = ctypes.c_void_p
        self._library.probe_view_png.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        self._library.probe_view_png.restype = ctypes.c_void_p

    def snapshot(self, window):
        return json.loads(self._library.probe_view_snapshot(int(window.winId())))

    def press(self, window, identifier):
        return bool(self._library.probe_accessibility_press(int(window.winId()), identifier.encode("utf-8")))

    def application_icon_png(self):
        length = ctypes.c_size_t()
        pointer = self._library.probe_application_icon_png(ctypes.byref(length))
        return ctypes.string_at(pointer, length.value)

    def view_png(self, window):
        length = ctypes.c_size_t()
        pointer = self._library.probe_view_png(int(window.winId()), ctypes.byref(length))
        return ctypes.string_at(pointer, length.value)

    def close(self):
        self._temporary.cleanup()
