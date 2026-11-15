"""Host AppKit Liquid Glass navigation and age controls in a Qt Quick WindowContainer.

The native bridge owns its NSView hierarchy and AppKit controls. Qt owns only
the foreign QWindow wrapper. Destroy that wrapper before releasing the native
view; the caller must destroy the QML engine before close().
"""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QObject, Property, Signal
from PySide6.QtGui import QGuiApplication, QWindow

_ACTION = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_void_p)
_PAGES = ("analysis", "jobs", "maintenance", "settings")


def bridge_path():
    if getattr(sys, "frozen", False):
        for parent in Path(sys.executable).parents:
            if parent.suffix == ".app":
                return parent / "Contents/Frameworks/libLeapGlass.dylib"
        raise RuntimeError("Liquid Glass requires an application bundle.")
    return Path(__file__).resolve().parents[2] / "runtime/build/libLeapGlass.dylib"


def require_cocoa():
    if sys.platform != "darwin" or QGuiApplication.platformName() != "cocoa":
        raise RuntimeError("Project Leap requires macOS with the Cocoa window system.")


def navigation_state(backend):
    """Use the same controller state and locale as the scientific QML pages."""
    translate = backend.locale.translate
    modules = []
    for item in backend.installedModules:
        modules.append({"id": item["id"], "name": item["name"],
                        "hasError": bool(item.get("hasError")),
                        "statusLabel": translate("需要修复") if item.get("hasError") else translate("已就绪"),
                        "tooltip": (item.get("message", "") + "\n" + translate("请在功能与维护中修复模块。"))
                        if item.get("hasError") else item["name"]})
    task = backend.runningTask
    active = bool(task or backend.maintenanceBusy)
    return {"workspaceHeading": translate("工作区"),
            "navigationAccessibilityLabel": translate("侧栏导航"),
            "workspaceAccessibilityLabel": translate("主工作区"),
            "navigation": [translate(value) for value in ("分析工作台", "任务与结果", "功能与维护", "设置")],
            "page": backend.page, "modulesHeading": translate("功能模块"),
            "emptyModules": translate("暂无已安装模块"), "modules": modules,
            "activeModuleId": backend.activeModuleId,
            "activityHeading": translate("当前任务") if active else "",
            "activity": backend.maintenanceMessage if backend.maintenanceBusy else
                        task.get("stage", ""),
            "busy": bool(task), "showProgress": bool(task) and task.get("status") in ("inspecting", "importing", "running"),
            "cancelling": task.get("status") == "cancelling",
            "progress": -1 if task else 0, "fijiAvailable": bool(task.get("fijiAvailable")),
            "showFijiLabel": translate("打开 Fiji"), "cancelLabel": translate("取消分析"),
            "version": "v" + backend.appVersion}


def age_state(backend):
    """Serialize the active fluorescence controller state; auto maps to reset."""
    roles = backend.channelRoles
    gfap_only = bool(backend.imageInfo) and "DAPI" in roles and "GFAP" in roles and "eGFP" not in roles
    editable = backend.activeModuleId == "project-leap-2d" and not backend.parametersLocked
    return {"selected": backend.ageProfile, "labels": [backend.locale.translate("自动"), "Mature", "Neonatal"],
            "enabled": [editable, editable, editable and not gfap_only],
            "accessibilityLabel": backend.locale.translate("图像年龄")}


class NativeStyle(QObject):
    changed = Signal()

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._library = None
        self._handle = None
        self._window = None
        self._age_handle = None
        self._age_window = None
        self._last_age_state = None
        self._callback = None
        self._last_state = None
        self._connected = False
        self._main_thread()
        require_cocoa()
        path = bridge_path()
        if not path.is_file():
            raise RuntimeError("Liquid Glass bridge is missing: " + str(path))
        library = ctypes.CDLL(str(path))
        signatures = {
            "available": ([], ctypes.c_int), "create": ([_ACTION, ctypes.c_void_p], ctypes.c_void_p),
            "view": ([ctypes.c_void_p], ctypes.c_size_t),
            "update": ([ctypes.c_void_p, ctypes.c_char_p], ctypes.c_int),
            "destroy": ([ctypes.c_void_p], None),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(library, "leap_glass_" + name, None)
            if function is None:
                raise RuntimeError("The native bridge is outdated; rebuild or reinstall Project Leap.")
            function.argtypes, function.restype = arguments, result
        for name, (arguments, result) in signatures.items():
            if name == "available":
                continue
            function = getattr(library, "leap_glass_age_" + name, None)
            if function is None:
                raise RuntimeError("The native bridge is outdated; rebuild or reinstall Project Leap.")
            function.argtypes, function.restype = arguments, result
        if not library.leap_glass_available():
            raise RuntimeError("This application requires macOS 27 or newer for native Liquid Glass.")
        self._library = library
        self._callback = _ACTION(self._dispatch)
        self._handle = library.leap_glass_create(self._callback, None)
        if not self._handle:
            raise RuntimeError("AppKit could not create the Liquid Glass sidebar.")
        try:
            self._window = QWindow.fromWinId(library.leap_glass_view(self._handle))
            if self._window is None:
                raise RuntimeError("Qt could not host the native Liquid Glass sidebar.")
            backend.changed.connect(self.refresh)
            self._connected = True
            self.refresh()
            self._age_handle = library.leap_glass_age_create(self._callback, None)
            if not self._age_handle:
                raise RuntimeError("AppKit could not create the native age selector.")
            self._age_window = QWindow.fromWinId(library.leap_glass_age_view(self._age_handle))
            if self._age_window is None:
                raise RuntimeError("Qt could not host the native age selector.")
            self.refresh()
        except Exception:
            # Initialization errors remain errors; release partially owned
            # native resources in the same order as a normal shutdown.
            self.close()
            raise

    navigationWindow = Property(QObject, lambda self: self._window, notify=changed)
    navigationAvailable = Property(bool, lambda self: self._window is not None, notify=changed)
    ageWindow = Property(QObject, lambda self: self._age_window, notify=changed)
    ageAvailable = Property(bool, lambda self: self._age_window is not None, notify=changed)

    @staticmethod
    def _main_thread():
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Native Liquid Glass must run on the main thread.")

    def _dispatch(self, raw_action, _context):
        action = raw_action.decode("utf-8")
        if action.startswith("page:") and action[5:] in _PAGES:
            self._backend.setPage(action[5:])
        elif action.startswith("module:"):
            self._backend.selectModule(action[7:])
        elif action.startswith("age:") and action[4:] in ("auto", "mature", "neonatal"):
            self._backend.setAgeProfile(action[4:])
        elif action == "showFiji":
            self._backend.showFiji()
        elif action == "cancelAnalysis":
            self._backend.cancelAnalysis()

    def refresh(self):
        self._main_thread()
        if not self._handle:
            return
        state = json.dumps(navigation_state(self._backend), ensure_ascii=False).encode("utf-8")
        if state != self._last_state:
            if not self._library.leap_glass_update(self._handle, state):
                raise RuntimeError("The native sidebar rejected the navigation state.")
            self._last_state = state
        if self._age_handle:
            age = json.dumps(age_state(self._backend), ensure_ascii=False).encode("utf-8")
            if age != self._last_age_state:
                if not self._library.leap_glass_age_update(self._age_handle, age):
                    raise RuntimeError("The native age selector rejected controller state.")
                self._last_age_state = age

    def close(self):
        """Call after deleting the QML engine and its WindowContainer."""
        self._main_thread()
        if self._handle:
            import shiboken6
            if self._connected:
                self._backend.changed.disconnect(self.refresh)
                self._connected = False
            if self._age_window is not None and shiboken6.isValid(self._age_window):
                shiboken6.delete(self._age_window)
            self._age_window = None
            if self._age_handle:
                self._library.leap_glass_age_destroy(self._age_handle)
                self._age_handle = None
            if self._window is not None and shiboken6.isValid(self._window):
                shiboken6.delete(self._window)
            self._window = None
            self._library.leap_glass_destroy(self._handle)
            self._handle = None
            self.changed.emit()


def create_native_style(app, backend):
    return NativeStyle(backend, app)
