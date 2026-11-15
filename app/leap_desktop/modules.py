"""The small, explicit catalog of analysis modules offered by this application.

Catalog entries describe available software. Installation and health are separate
facts recorded by maintenance; a damaged installation still owns its dependencies.
"""
from __future__ import annotations

import re

DEFAULT_MODULE_ID = "project-leap-2d"
COUNTING_MODULE_ID = "cell-counting"
MODEL_COMPONENT_ID = "cellpose-model"
SCIENCE_RUNTIME_ID = "science-runtime"
SCIENCE_RUNTIME_VERSION = "1.0.0"
MODEL_VERSION = "4.2.1.1"
MODEL_FILENAME = "cpsam_v2"
MODEL_SHA256 = "0f1cc3f7ecdd8a037a57c6c48d9d8921391be4cbce3fa9f13c3e3a2e1253c667"
CATALOG_FIELDS = frozenset(("id", "name", "version", "entrypoint", "import_module",
                            "github_url", "needs_fiji", "input_page"))

# Shared display order: fluorescence first, counting second, future modules
# appended in catalog order. Entries require an existing package and UI entry.
CATALOG = (
    {"id": DEFAULT_MODULE_ID, "name": "星形胶质细胞荧光强度分析", "version": "1.1.0",
     "entrypoint": "project_leap_2d.desktop_adapter",
     "github_url": "https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis",
     "needs_fiji": True,
     "input_page": "fluorescence",
     "import_module": "leap_desktop.import_worker"},
    {"id": COUNTING_MODULE_ID, "name": "细胞计数", "version": "1.0.0",
     "entrypoint": "leap_cell_counting", "import_module": "leap_cell_counting",
     "github_url": "", "needs_fiji": False, "input_page": "counting"},
)


def validate_module_id(module_id):
    if not isinstance(module_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", module_id):
        raise ValueError("模块标识无效。")
    return module_id


def list_modules():
    """Return independent dictionaries so UI consumers cannot mutate the catalog."""
    result, seen = [], set()
    for entry in CATALOG:
        module = dict(entry)
        if set(module) != CATALOG_FIELDS:
            raise ValueError("模块目录字段与当前格式不一致。")
        module_id = validate_module_id(module.get("id"))
        if module_id in ("fiji", MODEL_COMPONENT_ID, SCIENCE_RUNTIME_ID) or module_id in seen:
            raise ValueError("模块目录包含重复或保留的标识。")
        seen.add(module_id)
        for field in ("name", "version", "entrypoint", "input_page"):
            if not isinstance(module.get(field), str) or not module[field]:
                raise ValueError("模块目录缺少字段：" + field)
        if not isinstance(module.get("needs_fiji"), bool):
            raise ValueError("模块目录缺少 Fiji 依赖声明。")
        for field in ("github_url", "import_module"):
            if not isinstance(module[field], str):
                raise ValueError("模块目录字段与当前格式不一致。")
        result.append(module)
    return result


def get_module(module_id=DEFAULT_MODULE_ID):
    validate_module_id(module_id)
    for module in list_modules():
        if module["id"] == module_id:
            return module
    raise ValueError("未登记的模块：" + module_id)
