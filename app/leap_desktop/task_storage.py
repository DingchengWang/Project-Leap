"""Application-owned task storage; callers stop every consumer before mutation.

All operations are synchronous and Qt-free so the controller can run them on its
maintenance worker. Allocated-byte figures are estimates, not reclaimable space:
APFS clones can share physical extents that st_blocks cannot identify.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat

from .maintenance import ComponentBusy, component_maintenance_lock


_CACHE_PATHS = ("cache", "counting/.editor", "workspace/Sample Image")
_SOURCE_RECORDS = ("job.json", "request.json", "import-request.json", "import-result.json", "counting/analysis.json")


def _runs_root(paths):
    paths.check_owned_directory(paths.runs)
    return paths.runs.resolve(strict=True)


def _run(paths, identifier):
    if (not isinstance(identifier, str) or not identifier or identifier in (".", "..")
            or "/" in identifier or "\\" in identifier or "\x00" in identifier):
        raise ValueError("任务 ID 必须是单个目录名。")
    runs = _runs_root(paths)
    root = runs / identifier
    if root.is_symlink() or root.resolve(strict=True).parent != runs or not root.is_dir():
        raise ValueError("任务目录必须是 runs 下的真实直接子目录。")
    return root


def _run_directories(paths):
    for entry in sorted(_runs_root(paths).iterdir()):
        if entry.is_symlink():
            raise ValueError("任务目录不能是符号链接：" + entry.name)
        if entry.is_dir():
            yield _run(paths, entry.name)


def _walk(root):
    """Yield owned entries without descending through symbolic links."""
    yield root, root.lstat()
    if root.is_dir() and not root.is_symlink():
        def fail(error):
            raise error
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
            for name in dirs + files:
                path = Path(directory) / name
                yield path, path.lstat()


def _size(root, seen=None):
    seen = set() if seen is None else seen
    allocated = logical = 0
    for _, info in _walk(root):
        identity = info.st_dev, info.st_ino
        if identity in seen:
            continue
        seen.add(identity)
        allocated += info.st_blocks * 512
        if stat.S_ISREG(info.st_mode):
            logical += info.st_size
    return allocated, logical


def _cache_targets(paths, root):
    targets = []
    for relative in _CACHE_PATHS:
        path = root / relative
        paths.check_owned_directory(path)
        if path.exists():
            targets.append(path)
    return targets


def task_storage_usage(paths, progress=None):
    """Return per-task allocated/logical bytes, with hard links counted once."""
    try:
        roots = list(_run_directories(paths))
        tasks, seen = [], set()
        total = 0
        for index, root in enumerate(roots):
            allocated, logical = _size(root)
            cache_seen = set()
            cache = sum(_size(path, cache_seen)[0] for path in _cache_targets(paths, root))
            total += _size(root, seen)[0]
            tasks.append({"id": root.name, "allocated_bytes": allocated,
                          "logical_bytes": logical, "cache_bytes": cache})
            if progress:
                progress((index + 1) / max(1, len(roots)), "正在统计任务占用…")
        paths.check_owned_directory(paths.cache)
        global_cache = _size(paths.cache)[0]
        total += _size(paths.cache, seen)[0]
        return {"status": "inspected", "tasks": tasks, "allocated_bytes": total,
                "global_cache_bytes": global_cache, "is_estimate": True,
                "message": "已统计应用内部任务及缓存占用。"}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        return {"status": "failed", "tasks": [], "message": str(exc)}


def _source_values(value, key=""):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _source_values(child, child_key)
    elif isinstance(value, list):
        for child in value:
            yield from _source_values(child, key)
    elif key in ("source", "source_path") and isinstance(value, str) and value and "\x00" not in value:
        yield value


def _resolved_reference(paths, value, base):
    reference = Path(value).expanduser()
    if not reference.is_absolute():
        reference = base / reference
    reference = paths.resolve_managed_path(reference)
    # References to already missing files do not lose data through deletion.
    return reference.resolve() if reference.exists() else None


def _source_references(paths, current_sources, excluded_runs=()):
    for value in current_sources:
        if value:
            yield _resolved_reference(paths, value, paths.home), "当前图像"
    for run in _run_directories(paths):
        if run.name in excluded_runs:
            continue
        for relative in _SOURCE_RECORDS:
            record = run / relative
            paths.check_owned_directory(record.parent)
            if record.is_symlink():
                raise ValueError("任务输入记录不能是符号链接：" + str(record))
            if record.exists():
                value = json.loads(record.read_text(encoding="utf-8"))
                for source in _source_values(value):
                    yield _resolved_reference(paths, source, record.parent), record


def _assert_unreferenced(targets, references):
    for reference, origin in references:
        if reference is not None and any(reference == target or reference.is_relative_to(target)
                                         for target in targets):
            raise ValueError("待清理目录仍被引用：" + str(origin) + " → " + str(reference))


def _identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino


def _remove_checked(paths, path, identity):
    paths.check_owned_directory(path.parent)
    if _identity(path) != identity:
        raise ValueError("待删除目录已发生变化：" + str(path))
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    if path.exists() or path.is_symlink():
        raise OSError("未能完整删除目录：" + str(path))


def delete_tasks(paths, identifiers, *, current_sources=(), progress=None):
    """Delete exact run roots only, after validating the entire requested batch."""
    deleted = []
    try:
        if not isinstance(identifiers, (list, tuple)) or not identifiers:
            raise ValueError("请选择要删除的任务。")
        with component_maintenance_lock(paths.home):
            identifiers = list(dict.fromkeys(identifiers))
            roots = [_run(paths, identifier) for identifier in identifiers]
            identities = [_identity(root) for root in roots]
            _assert_unreferenced(roots, _source_references(paths, current_sources, identifiers))
            for index, (root, identity) in enumerate(zip(roots, identities)):
                _remove_checked(paths, root, identity)
                deleted.append(root.name)
                if progress:
                    progress((index + 1) / len(roots), "正在删除所选任务…")
        return {"status": "deleted", "deleted_ids": deleted,
                "message": "已删除所选任务的应用内部数据；外部原图和导出文件保留。"}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        return {"status": "blocked" if isinstance(exc, ComponentBusy) else "failed",
                "deleted_ids": deleted, "message": str(exc)}


def _clear_cache(paths, identifiers, current_sources, progress, *, global_cache):
    cleared, removed = [], []
    try:
        with component_maintenance_lock(paths.home):
            roots = list(_run_directories(paths)) if identifiers is None else [_run(paths, item) for item in identifiers]
            completed = []
            for root in roots:
                record = root / "job.json"
                if record.is_symlink():
                    raise ValueError("任务记录不能是符号链接：" + root.name)
                if not record.exists():
                    continue
                value = json.loads(record.read_text(encoding="utf-8"))
                if isinstance(value, dict) and value.get("status") == "completed":
                    completed.append(root)
            task_targets = [(root, _cache_targets(paths, root)) for root in completed]
            targets = [target for _, items in task_targets for target in items]
            references = list(_source_references(paths, current_sources))
            global_targets = []
            if global_cache:
                paths.check_owned_directory(paths.cache)
                for entry in paths.cache.iterdir():
                    if not any(reference is not None and (reference == entry or reference.is_relative_to(entry))
                               for reference, _ in references):
                        global_targets.append(entry)
                targets.extend(global_targets)
            identities = {target: _identity(target) for target in targets}
            _assert_unreferenced(targets, references)
            for index, (root, items) in enumerate(task_targets):
                for target in items:
                    _remove_checked(paths, target, identities[target])
                    removed.append(str(target))
                cleared.append(root.name)
                if progress:
                    progress((index + 1) / max(1, len(task_targets)), "正在清理已完成任务的临时数据…")
            for target in global_targets:
                _remove_checked(paths, target, identities[target])
                removed.append(str(target))
        return {"status": "cleared", "cleared_ids": cleared, "removed_paths": removed,
                "message": "已清理预览缓存及已完成任务的临时数据；源文件和结果保留。"}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        return {"status": "blocked" if isinstance(exc, ComponentBusy) else "failed",
                "cleared_ids": cleared, "removed_paths": removed, "message": str(exc)}


def clear_disposable_cache(paths, *, current_sources=(), progress=None):
    """Clear global Qt cache and only completed tasks' known temporary roots."""
    return _clear_cache(paths, None, current_sources, progress, global_cache=True)


def cleanup_completed_task(paths, identifier, *, current_sources=(), progress=None):
    """Release one completed task's temporary roots after consumers detach."""
    return _clear_cache(paths, [identifier], current_sources, progress, global_cache=False)
