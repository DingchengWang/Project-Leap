"""Collect only the app's reviewed Qt Quick module closure before signing."""
from pathlib import Path, PurePath
from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# QML debugger/profiler plugins are developer tooling, never a production import.
binaries = [(source, target) for source, target in binaries if 'qmltooling' not in Path(source).parts]
qml_root = Path(pyside6_library_info.location['QmlImportsPath']).resolve()
qml_destination = PurePath(pyside6_library_info.qt_rel_dir) / 'qml'
from build.release_policy import QML_MODULES
for module in sorted(QML_MODULES):
    qmldir = qml_root / module / 'qmldir'
    if not qmldir.is_file():
        raise RuntimeError('Required production QML module is missing: ' + module)
    plugin_binaries, plugin_datas = pyside6_library_info._process_qml_plugin(qmldir)
    if not plugin_binaries:
        raise RuntimeError('Required production QML module could not be collected: ' + module)
    for values, destination in ((plugin_binaries, binaries), (plugin_datas, datas)):
        for path in values:
            relative = path.relative_to(qml_root)
            target = qml_destination / (relative if path.is_dir() else relative.parent)
            destination.append((str(path), str(target)))
