# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import json
from pathlib import Path

root = Path(os.environ["LEAP_BUILD_ROOT"])
name = os.environ["LEAP_BUILD_NAME"]
identity = os.environ.get("LEAP_SIGN_IDENTITY", "-")
sys.path.insert(0, str(root / "tools"))
from build.release_policy import app_payload
from release_support import load_settings, component_configuration, sparkle_configuration
settings = load_settings(root / "tools/build/release-settings.json")
application = settings["application"]
configuration = json.loads((root / "app/leap_desktop/release-config.json").read_text(encoding="utf-8"))
if (not isinstance(configuration, dict) or type(configuration.get("schema_version")) is not int
        or configuration != component_configuration(settings)):
    raise ValueError("The app component configuration differs from the public release settings")
sources = [(str(path), str(Path("app/leap_desktop") / path.relative_to(root / "app/leap_desktop").parent))
           for path in app_payload(root)]
notices = [(str(root / "LICENSE"), "ThirdPartyNotices/ProjectLeap"),
           (str(root / "NOTICE"), "ThirdPartyNotices/ProjectLeap")]
analysis = Analysis(
    [str(root / "tools/build/launcher.py")],
    pathex=[str(root / "app")],
    binaries=[],
    datas=sources + notices + [(str(root / "tools/build/licenses"), "ThirdPartyNotices/UI")],
    hiddenimports=["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickControls2"],
    hookspath=[str(root / "tools/build/hooks")], hooksconfig={}, runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtTest", "PySide6.QtGraphs", "PySide6.QtQuick3D", "leap_desktop.probe", "numpy", "torch", "torchvision", "scipy"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name=name,
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, argv_emulation=False, target_arch="arm64", codesign_identity=identity)
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name=name)
app = BUNDLE(collection, name=name + ".app", icon=str(root / "tools/build/ProjectLeap.icns"),
             bundle_identifier=application["bundle_identifier"], version=os.environ["LEAP_BUILD_VERSION"],
             info_plist={"CFBundleDisplayName":os.environ["LEAP_BUILD_DISPLAY_NAME"], "CFBundleShortVersionString":os.environ["LEAP_BUILD_VERSION"],
                         "CFBundleVersion":os.environ["LEAP_BUILD_NUMBER"],
                         "NSHighResolutionCapable":True, "LSMinimumSystemVersion":application["minimum_macos"],
                         **sparkle_configuration(settings),
                         "NSHumanReadableCopyright":"Copyright 2026 Dingcheng Wang. Apache License 2.0."})
