"""Verified data relocation. Scientific records are copied without alteration."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import uuid

from .paths import APPLICATION_ID, ensure_app_home, write_json


def _copy_preserving_sparse(source, destination):
    """Keep sparse data sparse; never permit copyfile's dense-copy fallback."""
    info = os.stat(source, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_blocks * 512 >= info.st_size:
        return shutil.copy2(source, destination, follow_symlinks=False)
    if sys.platform != "darwin":
        raise OSError("稀疏应用数据迁移需要 macOS copyfile 支持。")
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    copyfile = library.copyfile
    copyfile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
    copyfile.restype = ctypes.c_int
    # copyfile.h: EXCL, NOFOLLOW_SRC, NOFOLLOW_DST, DATA_SPARSE. Do not add
    # COPYFILE_DATA: copyfile(3) permits a dense fallback when that flag is set.
    flags = (1 << 17) | (1 << 18) | (1 << 19) | (1 << 27)
    if copyfile(os.fsencode(source), os.fsencode(destination), None, flags):
        code = ctypes.get_errno()
        raise OSError(code, "无法保持稀疏文件迁移；目标文件系统必须支持稀疏复制：" + os.strerror(code), str(source))
    shutil.copystat(source, destination, follow_symlinks=False)
    return str(destination)


def _publish_directory(paths, source, destination):
    """Publish through the macOS coordinator on the maintenance worker thread."""
    library = ctypes.CDLL(str(paths.native_library))
    try:
        publish = library.leap_storage_publish_directory
    except AttributeError as exc:
        raise RuntimeError("应用存储迁移组件缺少必要接口。") from exc
    publish.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_char), ctypes.c_size_t]
    publish.restype = ctypes.c_int
    error = ctypes.create_string_buffer(4096)
    if publish(os.fsencode(source), os.fsencode(destination), error, len(error)):
        raise OSError("无法发布迁移数据目录：" + error.value.decode("utf-8", errors="replace"))


def tree_manifest(root):
    """Inventory bytes, modes and internal links without following links."""
    root = Path(root).resolve()
    entries = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve().is_relative_to(root) or not path.exists():
                    raise ValueError("应用数据含外部或失效符号链接：" + relative)
                entries[relative] = ("link", target)
            elif stat.S_ISDIR(info.st_mode):
                entries[relative] = ("directory", stat.S_IMODE(info.st_mode))
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                        digest.update(chunk)
                entries[relative] = ("file", info.st_size, stat.S_IMODE(info.st_mode), digest.hexdigest())
            else:
                raise ValueError("应用数据含不支持的文件类型：" + relative)
    return entries


def migrate_app_data(paths, destination, progress=None):
    """Copy, verify, activate the fixed pointer, then remove the old copy."""
    from .maintenance import ComponentBusy, component_maintenance_lock
    stage = None
    target = None
    target_created = False
    activated = False
    old = paths.home
    def report(fraction, message):
        if progress:
            progress(fraction, message)
    try:
        with component_maintenance_lock(old):
            target = ensure_app_home(destination, create=False)
            if target == old or target.is_relative_to(old) or old.is_relative_to(target):
                raise ValueError("新数据目录必须与当前数据目录相互独立。")
            config = paths.config_home
            if target == config or target.is_relative_to(config) or config.is_relative_to(target):
                raise ValueError("新数据目录不能包含固定配置目录。")
            if target.exists() and any(target.iterdir()):
                raise ValueError("迁移目标必须是新的空目录。")
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = target.with_name("." + target.name + ".migrating-" + uuid.uuid4().hex)
            report(0.05, "正在校验现有应用数据…")
            original = tree_manifest(old)
            report(0.20, "正在复制组件、任务与结果…")
            shutil.copytree(old, stage, symlinks=True, copy_function=_copy_preserving_sparse)
            report(0.70, "正在核对完整数据副本…")
            if tree_manifest(stage) != original or tree_manifest(old) != original:
                raise ValueError("迁移校验失败；原数据目录仍可用。")
            marker = stage / "application.json"
            state = json.loads(marker.read_text(encoding="utf-8"))
            previous = state.get("previous_data_dirs", [])
            state["previous_data_dirs"] = list(dict.fromkeys([*previous, str(old)]))
            write_json(marker, state)
            _publish_directory(paths, stage, target)
            stage = None
            target_created = True
            report(0.95, "正在启用已验证的数据目录…")
            write_json(paths.settings_file, {"schema_version": 1, "application": APPLICATION_ID, "data_dir": str(target)})
            activated = True
            cleanup_warning = ""
            try:
                shutil.rmtree(old)
            except OSError as exc:
                cleanup_warning = str(exc)
            result = {"status": "migrated", "message": "应用数据迁移完成，正在重新启动。", "data_dir": str(target),
                      "config_dir": str(config), "restart_required": True, "previous_environment_preserved": bool(cleanup_warning)}
            if cleanup_warning:
                result["cleanup_warning"] = cleanup_warning
                result["message"] = "新数据目录已启用；旧副本未完全清除：" + cleanup_warning
            return result
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        return {"status": "blocked" if isinstance(exc, ComponentBusy) else "failed", "message": str(exc),
                "previous_environment_preserved": not activated, "restart_required": activated}
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
        if target_created and not activated and target is not None:
            shutil.rmtree(target)
