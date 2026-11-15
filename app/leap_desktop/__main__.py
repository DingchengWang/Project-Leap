"""Launch the native Qt Quick desktop application."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Project Leap")
    parser.add_argument("--data-dir")
    parser.add_argument("--config-dir")
    args = parser.parse_args(argv)

    from PySide6.QtCore import QUrl, QLockFile, QEvent, QObject
    from PySide6.QtGui import QFontDatabase
    # Register the QML-created window's Qt Quick Python bindings.
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .paths import AppPaths
    from .controller import Controller
    from .i18n import LocaleManager, TranslationCatalog
    from .native_style import create_native_style

    app = QApplication([sys.argv[0]])
    app.setApplicationName("Project Leap")
    app.setOrganizationName("Project Leap")
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPointSize(13)
    app.setFont(font)
    app.setQuitOnLastWindowClosed(True)
    try:
        paths = AppPaths(args.data_dir, config_dir=args.config_dir)
        locale = LocaleManager(paths.home)
    except (OSError, ValueError, RuntimeError) as exc:
        QMessageBox.critical(None, "Project Leap", TranslationCatalog().translate(str(exc)))
        return 2
    locale.install_on(app)
    instance_lock = QLockFile(str(paths.config_home / "application.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        QMessageBox.information(None, locale.translate("Project Leap 已在运行"), locale.translate("请使用已打开的 Project Leap 窗口。"))
        return 0
    qml_cache = paths.cache / "qml"
    qml_cache.mkdir(exist_ok=True)
    os.environ["QML_DISK_CACHE_PATH"] = str(qml_cache)
    os.environ["QSG_RHI_PIPELINE_CACHE_LOAD"] = str(paths.cache / "qt-pipeline-cache")
    os.environ["QSG_RHI_PIPELINE_CACHE_SAVE"] = str(paths.cache / "qt-pipeline-cache")
    backend = Controller(paths, locale=locale)
    backend.setParent(app)
    app.aboutToQuit.connect(backend.counting.close)
    from .appearance import Appearance, ApplicationIcon
    appearance = application_icon = native_style = None
    try:
        appearance = Appearance(app, mode=backend.appearanceMode)
        backend.appearanceModeChanged.connect(appearance.setMode)
        application_icon = ApplicationIcon(app, appearance, paths.resources)
        native_style = create_native_style(app, backend)
    except (OSError, ValueError, RuntimeError) as exc:
        if application_icon is not None:
            application_icon.close()
        if appearance is not None:
            appearance.close()
        instance_lock.unlock()
        QMessageBox.critical(None, "Project Leap", locale.translate("无法加载原生界面。请确认系统版本并重新安装应用。") + "\n\n" + str(exc))
        return 2
    engine = QQmlApplicationEngine()
    backend.locale.install_on(app, engine)
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("nativeStyle", native_style)
    engine.rootContext().setContextProperty("appearance", appearance)
    engine.load(QUrl.fromLocalFile(str(paths.qml)))
    if not engine.rootObjects():
        import shiboken6
        shiboken6.delete(engine)
        native_style.close()
        application_icon.close()
        appearance.close()
        instance_lock.unlock()
        return 2
    restart_requested = False

    def restart(_data_dir):
        nonlocal restart_requested
        restart_requested = True
        app.quit()

    backend.restartRequested.connect(restart)

    class QuitGuard(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Quit and (backend.busy or backend.maintenanceBusy or backend.updatesBusy or backend.counting.busy
                                                    or backend.counting.available or not backend.counting.drained):
                backend.requestQuit()
                return True
            return False

    quit_guard = QuitGuard(app)
    app.installEventFilter(quit_guard)

    result = app.exec()
    import shiboken6
    shiboken6.delete(engine)
    native_style.close()
    application_icon.close()
    appearance.close()
    instance_lock.unlock()
    if restart_requested:
        environment = os.environ.copy()
        environment.pop("PROJECT_LEAP_HOME", None)
        environment.pop("PROJECT_LEAP_CONFIG_HOME", None)
        if getattr(sys, "frozen", False):
            bundle = next(parent for parent in Path(sys.executable).resolve().parents if parent.suffix == ".app")
            subprocess.Popen(["/usr/bin/open", "-n", "-a", str(bundle), "--args", "--config-dir", str(paths.config_home)],
                             env=environment, start_new_session=True)
        else:
            subprocess.Popen([sys.executable, "-m", "leap_desktop", "--config-dir", str(paths.config_home)],
                             env=environment, start_new_session=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
