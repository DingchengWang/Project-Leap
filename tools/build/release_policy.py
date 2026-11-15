"""Explicit production payload policy shared by builders and verification."""
from pathlib import Path

# Every first-party UI payload file is reviewed here. New files must be deliberate.
APP_FILES = frozenset({
    '__init__.py',
    '__main__.py',
    'appearance.py',
    'assets/icon.svg',
    'controller.py',
    'counting.py',
    'docs/licenses.html',
    'docs/references.html',
    'i18n.py',
    'import_worker.py',
    'importer.py',
    'lifecycle.py',
    'locales/en.json',
    'maintenance.py',
    'modules.py',
    'native_style.py',
    'paths.py',
    'qml/Main.qml',
    'qml/components/ActionButton.qml',
    'qml/components/ActivityBar.qml',
    'qml/components/ChoiceBox.qml',
    'qml/components/CountCanvas.qml',
    'qml/components/Icon.qml',
    'qml/components/InfoCell.qml',
    'qml/components/JobDetails.qml',
    'qml/components/NativeAgeSelector.qml',
    'qml/components/RunDetails.qml',
    'qml/components/StatusBadge.qml',
    'qml/components/Surface.qml',
    'qml/components/Theme.qml',
    'qml/components/qmldir',
    'qml/pages/CountingInput.qml',
    'qml/pages/FluorescenceInput.qml',
    'release-config.json',
    'release_catalog.py',
    'resource_monitor.py',
    'run_details.py',
    'storage.py',
    'task_storage.py',
    'update_manager.py',
    'updater.py',
    'version.py',
})

QML_MODULES = frozenset({
    'QtQml',
    'QtQml/Models',
    'QtQml/WorkerScript',
    'QtQuick',
    'QtQuick/Window',
    'QtQuick/Layouts',
    'QtQuick/Templates',
    'QtQuick/Controls',
    'QtQuick/Controls/impl',
    'QtQuick/Controls/Basic',
    'QtQuick/Controls/Basic/impl',
    'QtQuick/Dialogs',
    'QtQuick/Dialogs/quickimpl',
    'Qt/labs/folderlistmodel',
    'QtQuick/Shapes',
})

MODULE_HOST_CONTRACT = {"host_api": 1, "minimum_host_build": 1, "platform": "macos-arm64"}

SCIENCE_RUNTIME_VERSION = "1.0.0"
MODULE_VERSIONS = {"project-leap-2d": "1.1.1", "cell-counting": "1.0.0"}
SCIENTIFIC_VERSIONS = {"project-leap-2d": "1.0.1", "cell-counting": "1.0.0"}
SCIENCE_RUNTIME_DEPENDENCY = {"id": "science-runtime", "version": SCIENCE_RUNTIME_VERSION}


def app_payload(root):
    base = Path(root) / "app/leap_desktop"
    missing = sorted(name for name in APP_FILES if not (base / name).is_file())
    if missing:
        raise ValueError("Missing allowlisted app source files: " + ", ".join(missing))
    return [base / name for name in sorted(APP_FILES)]


def excluded_seed_entry(name):
    """Remove standalone test suites/fixtures; retain imported testing helpers.

    numpy.testing, scipy._lib._testutils, torch.testing and similar modules are
    public runtime imports. A name containing 'test' alone is not an exclusion.
    """
    parts = Path(name).parts
    if not parts or parts[0] not in {"python", "site-packages"}:
        return False
    # PyTorch 2.8 imports logging_tensor from checkpoint during import torch;
    # FSDP and Dynamo also import FakeProcessGroup from this nominal test tree.
    # Keep those runtime dependencies, not the standalone internal test suite.
    internal = "site-packages/torch/testing/_internal"
    helpers = {internal, internal + "/__init__.py", internal + "/logging_tensor.py",
               internal + "/distributed", internal + "/distributed/__init__.py", internal + "/distributed/fake_pg.py"}
    if name.startswith(internal + "/") and name not in helpers:
        return True
    return any(part in {"tests", "test", "testdata", "test_data", "benchmarks"} for part in parts) or parts[-1] == "conftest.py"
