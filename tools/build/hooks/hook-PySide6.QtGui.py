"""Collect desktop GUI plugins without optional PDF/virtual-keyboard engines."""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
excluded = {'libqtvirtualkeyboardplugin.dylib', 'libqpdf.dylib'}
binaries = [(source, target) for source, target in binaries if Path(source).name not in excluded]
