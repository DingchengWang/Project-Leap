"""Small main-thread ctypes boundary to Sparkle's native macOS update flow."""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
import sys
import threading


class SparkleUpdater:
    def __init__(self, bundle_path=None):
        self._library = None
        self._state = {"status": "unavailable", "message": "应用更新仅在已打包的 macOS 应用中可用。",
                       "can_check": False, "session_in_progress": False}
        self.bundle_path = Path(bundle_path) if bundle_path else self._application_bundle()

    @staticmethod
    def _application_bundle():
        for candidate in Path(sys.executable).parents:
            if candidate.suffix == ".app" and (candidate / "Contents/Info.plist").is_file():
                return candidate
        return None

    @staticmethod
    def _main_thread():
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Sparkle 必须在应用主线程调用。")

    def start(self):
        self._main_thread()
        if self._library is not None:
            return self.status()
        if sys.platform != "darwin" or self.bundle_path is None:
            return dict(self._state)
        bridge = self.bundle_path / "Contents/Frameworks/libLeapSparkle.dylib"
        if not bridge.is_file():
            self._state = {"status": "unavailable", "message": "应用内缺少 Sparkle 更新桥。",
                           "can_check": False, "session_in_progress": False}
            return dict(self._state)
        try:
            library = ctypes.CDLL(str(bridge))
            for function in ("leap_sparkle_start", "leap_sparkle_check"):
                getattr(library, function).argtypes = []
                getattr(library, function).restype = ctypes.c_int
            library.leap_sparkle_install.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
            library.leap_sparkle_install.restype = ctypes.c_int
            library.leap_sparkle_set_installation_busy.argtypes = [ctypes.c_int]
            library.leap_sparkle_set_installation_busy.restype = None
            library.leap_sparkle_state_json.argtypes = []
            library.leap_sparkle_state_json.restype = ctypes.c_char_p
            self._library = library
            library.leap_sparkle_start()
            return self.status()
        except (OSError, AttributeError) as exc:
            self._state = {"status": "failed", "message": "Sparkle 加载失败：" + str(exc),
                           "can_check": False, "session_in_progress": False}
            return dict(self._state)

    def status(self):
        self._main_thread()
        if self._library is not None:
            self._state = json.loads(self._library.leap_sparkle_state_json().decode("utf-8"))
        return dict(self._state)

    def set_installation_busy(self, busy):
        """Allow downloads to proceed, retaining installation until the app is idle."""
        self._main_thread()
        if self._library is not None:
            self._library.leap_sparkle_set_installation_busy(int(bool(busy)))

    def check_for_updates(self):
        """Inspect the appcast without downloading or installing an update."""
        self._main_thread()
        self.start()
        if self._library is not None:
            self._library.leap_sparkle_check()
        return self.status()

    def install_update(self, expected_build, expected_feed, expected_host_build, *, installation_busy=False):
        """Execute the exact candidate from the preceding completed inspection."""
        self._main_thread()
        for value in (expected_build, expected_feed, expected_host_build):
            if not isinstance(value, str) or not value or "\x00" in value:
                raise ValueError("应用更新计划缺少有效的版本或更新源。")
        self.start()
        if self._library is not None:
            self._library.leap_sparkle_set_installation_busy(int(bool(installation_busy)))
            self._library.leap_sparkle_install(expected_build.encode("utf-8"), expected_feed.encode("utf-8"),
                                             expected_host_build.encode("utf-8"))
        return self.status()
