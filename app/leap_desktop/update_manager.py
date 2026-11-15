"""Signed, frozen component-update plans and restart-safe staged packages."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tarfile
import uuid

from .release_catalog import (CatalogUnconfigured, fetch_catalog, read_signed_catalog,
                              HOST_REQUIREMENT_FIELDS, validate_host_requirements)

TARGET_FIELDS = ("version", "sha256", "bytes", "manifest_sha256", *HOST_REQUIREMENT_FIELDS)
PACKAGE_FIELDS = ("version", "sha256", "bytes", "manifest_sha256")


def _version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        raise ValueError("自动更新要求明确的数字组件版本。")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def newer_version(candidate, installed):
    return _version_tuple(candidate) > _version_tuple(installed)


def _failure(exc):
    from .maintenance import ComponentBusy, ComponentCancelled
    return {"status": "unconfigured" if isinstance(exc, CatalogUnconfigured) else "cancelled" if isinstance(exc, ComponentCancelled)
            else "blocked" if isinstance(exc, ComponentBusy) else "failed", "message": str(exc)}


def check_component_updates(app_home, configuration=None, progress=None, *, cancel=None):
    from .maintenance import _default_release_configuration, _notify, list_statuses, ComponentError
    try:
        _notify(progress, 0.0, "正在检查组件更新…")
        catalog = fetch_catalog(configuration or _default_release_configuration(), cancel=cancel)
        updates, available = [], []
        for state in list_statuses(app_home):
            latest = catalog.latest.get(state["id"])
            if latest is None:
                raise ValueError("组件发布目录缺少模块：" + state["id"])
            _url, package = catalog.select(state["id"], latest)
            dependencies = package.get("dependencies")
            if not isinstance(dependencies, dict):
                raise ValueError("组件发布目录缺少明确的共享依赖版本。")
            for identity, version in dependencies.items():
                catalog.select(identity, version)
            entry = {"id": state["id"], "installed_version": state.get("component_version", ""), "available_version": latest,
                     "target": {key: package[key] for key in TARGET_FIELDS}, "dependencies": dependencies,
                     **validate_host_requirements(package)}
            if not state["installed"]:
                available.append(entry)
            elif newer_version(latest, state["component_version"]):
                updates.append(entry)
        plan = {"schema_version": 1, "id": uuid.uuid4().hex, "catalog_url": catalog.configuration.catalog_url,
                "signed_catalog": catalog.envelope, "targets": updates}
        _notify(progress, 1.0, "组件更新检查完成。")
        return {"status": "updates_available" if updates else "up_to_date", "updates": updates, "available_modules": available,
                "plan": plan, "message": "发现可用模块更新。" if updates else "未发现可用的模块更新。"}
    except (OSError, ValueError, TypeError, AttributeError, ComponentError) as exc:
        return dict(_failure(exc), updates=[])


def _verified_plan(plan, configuration):
    from .maintenance import _default_release_configuration
    if (not isinstance(plan, dict) or plan.get("schema_version") != 1
            or not isinstance(plan.get("targets"), list) or not plan["targets"]
            or not re.fullmatch(r"[a-f0-9]{32}", plan.get("id", ""))):
        raise ValueError("组件更新计划无效。")
    configuration = configuration or _default_release_configuration()
    if plan.get("catalog_url") != configuration.catalog_url:
        raise ValueError("组件更新计划与内置发布地址不一致。")
    catalog = read_signed_catalog(plan.get("signed_catalog"), configuration)
    selected = {}
    targets = set()
    for target in plan["targets"]:
        if not isinstance(target, dict) or target.get("id") in targets:
            raise ValueError("组件更新计划包含无效或重复目标。")
        identity = target["id"]
        targets.add(identity)
        from .modules import get_module
        get_module(identity)
        version = target.get("available_version")
        source, package = catalog.select(identity, version)
        if (target.get("target") != {key: package[key] for key in TARGET_FIELDS}
                or any(target.get(key) != package[key] for key in HOST_REQUIREMENT_FIELDS)
                or target.get("dependencies") != package.get("dependencies")
                or not newer_version(version, target.get("installed_version"))):
            raise ValueError("组件更新计划与已签名发布内容不一致。")
        selected[identity] = (source, package)
        for dependency, dependency_version in target["dependencies"].items():
            entry = catalog.select(dependency, dependency_version)
            from .modules import SCIENCE_RUNTIME_ID
            selection_key = (dependency, dependency_version) if dependency == SCIENCE_RUNTIME_ID else dependency
            if selection_key in selected and selected[selection_key][1]["version"] != dependency_version:
                raise ValueError("组件更新计划要求互不兼容的共享依赖版本。")
            selected[selection_key] = entry
    return catalog, selected


def _staging_path(app_home, plan):
    from .paths import ensure_app_home
    home = ensure_app_home(app_home)
    parent = home / "updates" / "staged"
    for path in (home / "updates", parent, parent / plan["id"]):
        if path.is_symlink():
            raise ValueError("组件更新暂存目录不得为符号链接。")
    destination = parent / plan["id"]
    if plan.get("staging_dir") and Path(plan["staging_dir"]).absolute() != destination.absolute():
        raise ValueError("组件更新暂存位置与计划不一致。")
    return destination


def stage_component_updates(app_home, plan, configuration=None, progress=None, *, cancel=None):
    from . import maintenance as m
    temporary = None
    try:
        _catalog, selected = _verified_plan(plan, configuration)
        with m.maintenance_lock_fds(app_home):
            destination = _staging_path(app_home, plan)
            if destination.exists():
                raise ValueError("该更新计划已经暂存；请继续应用已有计划。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(".partial-" + plan["id"])
            temporary.mkdir()
            packages, science_versions = {}, {}
            target_ids = {target["id"] for target in plan["targets"]}
            for selection_key, (source, metadata) in selected.items():
                identity = selection_key[0] if isinstance(selection_key, tuple) else selection_key
                m._check_cancel(cancel)
                if identity not in target_ids:
                    current = m.science_status(app_home, metadata["version"]) if identity == m.SCIENCE_ID else m._quick_status(app_home, identity)
                    if (current.get("component_version") == metadata["version"] and current.get("runtime_descriptor")
                            and m._full_check(app_home, identity, version=metadata["version"] if identity == m.SCIENCE_ID else None)["status"] == "intact"):
                        continue
                with m._materialized_source(source, metadata, app_home, progress=progress, cancel=cancel) as archive:
                    m._source_manifest(archive, metadata, identity)
                    filename = identity + "-" + metadata["version"] + ".tar.gz"
                    shutil.copyfile(archive, temporary / filename)
                    entry = dict(metadata)
                    entry.pop("url", None)
                    entry["path"] = filename
                    if identity == m.SCIENCE_ID:
                        science_versions[metadata["version"]] = entry
                    else:
                        packages[identity] = entry
            m._check_cancel(cancel)
            m._atomic_json(temporary / "catalog.json", {"schema_version": 1, "packages": packages, "science_runtime_versions": science_versions})
            staged = dict(plan, staging_dir=str(destination))
            m._atomic_json(temporary / "plan.json", staged)
            temporary.rename(destination)
            temporary = None
            m._notify(progress, 1.0, "组件更新包已下载并校验，等待应用更新。")
            return {"status": "staged", "plan": staged, "message": "组件更新包已准备完成。"}
    except (OSError, ValueError, TypeError, AttributeError, m.ComponentError, tarfile.TarError, EOFError) as exc:
        return _failure(exc)
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)


def apply_staged_updates(app_home, plan, configuration=None, progress=None, *, cancel=None):
    from . import maintenance as m
    completed = []
    try:
        _catalog, selected = _verified_plan(plan, configuration)
        for target in plan["targets"]:
            validate_host_requirements(target["target"], require_current=True)
        destination = _staging_path(app_home, plan)
        # Verify exact package identities again after a possible application
        # restart; local staging metadata alone is not a trust anchor.
        with m.maintenance_lock_fds(app_home):
            sources = m._package_sources(destination)
            entries = [(identity, value) for identity, value in sources.items() if identity != m.SCIENCE_ID]
            entries += [(m.SCIENCE_ID, value) for value in getattr(sources, "science_versions", {}).values()]
            for identity, (archive, metadata) in entries:
                expected = selected.get((identity, metadata["version"]) if identity == m.SCIENCE_ID else identity)
                if (expected is None or any(metadata.get(key) != expected[1][key] for key in PACKAGE_FIELDS)
                        or metadata.get("dependencies", {}) != expected[1].get("dependencies", {})):
                    raise ValueError("暂存组件与已签名更新计划不一致。")
                m._source_manifest(archive, expected[1], identity)
            staged_identities = {(identity, metadata["version"]) for identity, (_archive, metadata) in entries}
            target_ids = {target["id"] for target in plan["targets"]}
            if any((target["id"], target["available_version"]) not in staged_identities for target in plan["targets"]):
                raise ValueError("暂存组件与已签名更新计划不一致。")
            for selection_key, (_source, metadata) in selected.items():
                identity = selection_key[0] if isinstance(selection_key, tuple) else selection_key
                if identity in target_ids or (identity, metadata["version"]) in staged_identities:
                    continue
                current = m.science_status(app_home, metadata["version"]) if identity == m.SCIENCE_ID else m._quick_status(app_home, identity)
                if (current.get("component_version") != metadata["version"] or not current.get("runtime_descriptor")
                        or m._full_check(app_home, identity, version=metadata["version"] if identity == m.SCIENCE_ID else None)["status"] != "intact"):
                    raise ValueError("共享组件在下载更新后发生变化；请重新准备更新包。")
            for target in plan["targets"]:
                current = m.component_status(app_home, module_id=target["id"])
                if current.get("component_version") not in (target["installed_version"], target["available_version"]):
                    raise ValueError("模块版本在计划建立后发生变化；请重新检查更新。")
            for target in plan["targets"]:
                m._check_cancel(cancel)
                current = m.component_status(app_home, module_id=target["id"])
                if current.get("component_version") == target["available_version"]:
                    if m._full_check(app_home, target["id"])["status"] == "intact" and current["status"] == "ready":
                        completed.append(target["id"])
                        continue
                    result = m._maintain(app_home, destination, progress=progress, module_id=target["id"], operation="repair", cancel=cancel, _lock_held=True, _defer_science_cleanup=True)
                else:
                    result = m._maintain(app_home, destination, progress=progress, module_id=target["id"], operation="update", cancel=cancel, expected_target=target["target"], _lock_held=True, _defer_science_cleanup=True)
                if result["status"] not in ("ready", "up_to_date", "intact"):
                    return dict(result, completed=completed, plan=plan)
                completed.append(target["id"])
            m._cleanup_science_generations(app_home)
            shutil.rmtree(destination)
        return {"status": "ready", "message": "模块更新完成。", "completed": completed, "plan_id": plan["id"]}
    except (OSError, ValueError, TypeError, AttributeError, m.ComponentError, tarfile.TarError, EOFError) as exc:
        return dict(_failure(exc), completed=completed, plan=plan)


def discard_staged_updates(app_home, plan):
    """Remove only this superseded transaction's owned staging directory."""
    from . import maintenance as m
    try:
        if (not isinstance(plan, dict) or plan.get("schema_version") != 1
                or not re.fullmatch(r"[a-f0-9]{32}", plan.get("id", ""))):
            raise ValueError("组件更新计划标识无效。")
        with m.maintenance_lock_fds(app_home):
            destination = _staging_path(app_home, plan)
            if destination.exists():
                if not destination.is_dir():
                    raise ValueError("组件更新暂存位置不是目录。")
                # rmtree removes child symlinks without traversing their targets;
                # _staging_path has already rejected symlinked ancestors/root.
                shutil.rmtree(destination)
        return {"status": "discarded", "plan_id": plan["id"], "message": "已清理被替代的组件更新包。"}
    except (OSError, ValueError, TypeError, m.ComponentError) as exc:
        return _failure(exc)
