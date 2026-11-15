"""Install, update, repair and remove modules from authenticated releases.

The seed and each installed generation contain a complete, fixed SHA-256
manifest. A successful transaction changes only a small current.json pointer.
Analysis holds component_use_lock until its worker and Fiji have both exited.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack, nullcontext
from concurrent.futures import ThreadPoolExecutor
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
from urllib.parse import urlsplit
import uuid

from .modules import (DEFAULT_MODULE_ID, COUNTING_MODULE_ID, MODEL_COMPONENT_ID,
                      MODEL_VERSION, MODEL_FILENAME, MODEL_SHA256, SCIENCE_RUNTIME_ID, SCIENCE_RUNTIME_VERSION,
                      get_module, list_modules, validate_module_id)

COMPONENT_ID = DEFAULT_MODULE_ID
FIJI_ID = "fiji"
MODEL_ID = MODEL_COMPONENT_ID
SCIENCE_ID = SCIENCE_RUNTIME_ID
SHARED_IDS = (FIJI_ID, MODEL_ID, SCIENCE_ID)
MANIFEST_NAME = "content-manifest.json"
DESCRIPTOR_PATHS = ("python", "site_packages", "fiji_launcher", "models")


class ComponentError(RuntimeError):
    """The requested maintenance operation cannot safely be completed."""


class ComponentBusy(ComponentError):
    """An analysis or another maintenance operation owns the component lock."""


class ComponentCancelled(ComponentError):
    """Cancellation before activation leaves the previous generation in place."""


def _check_cancel(cancel):
    if cancel is not None and cancel():
        raise ComponentCancelled("组件维护已取消。")


def model_dependency():
    return {"id": MODEL_ID, "version": MODEL_VERSION, "filename": MODEL_FILENAME,
            "sha256": MODEL_SHA256}


def science_dependency():
    return {"id": SCIENCE_ID, "version": SCIENCE_RUNTIME_VERSION}


def _science_dependency(value):
    if not isinstance(value, dict) or set(value) != {"id", "version"} or value.get("id") != SCIENCE_ID:
        raise ComponentError("模块科学运行环境依赖无效。")
    _version({"component_version": value.get("version")})
    return dict(value)


def _model_dependency(value):
    if value != model_dependency():
        raise ComponentError("模块模型依赖与固定 cpsam_v2 版本和 SHA-256 不一致。")
    return dict(value)


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ComponentError("组件清单包含无效路径。")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("..", ".") for part in value.split("/")):
        raise ComponentError("组件路径必须位于组件目录内。")
    if path.as_posix() != value:
        raise ComponentError("组件清单包含非规范路径。")
    return Path(*path.parts)


def _version(descriptor):
    version = descriptor.get("component_version")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version):
        raise ComponentError("组件版本字段无效。")
    return version


def _descriptor(root, component_id=None):
    path = root / "runtime.json"
    if path.is_symlink():
        raise ComponentError("runtime.json 不得为符号链接。")
    descriptor = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(descriptor, dict):
        raise ComponentError("运行时描述无效。")
    identity = descriptor.get("component_id")
    validate_module_id(identity)
    if component_id is not None and identity != component_id:
        raise ComponentError("安装源不属于所选组件。")
    schema = descriptor.get("schema_version")
    if schema not in (1, 2, 3, 4) or (schema == 1 and identity != COMPONENT_ID):
        raise ComponentError("运行时版本格式不受支持。")
    _version(descriptor)
    if identity == FIJI_ID:
        keys = ("fiji_launcher",)
    elif identity == SCIENCE_ID:
        if schema != 4:
            raise ComponentError("共享科学运行环境必须使用格式 4。")
        keys = ("python", "site_packages")
    elif identity == MODEL_ID:
        keys = ("models",)
        _model_dependency({"id": identity, "version": _version(descriptor),
                           "filename": descriptor.get("model_file"), "sha256": descriptor.get("model_sha256")})
    else:
        get_module(identity)
        keys = DESCRIPTOR_PATHS if schema == 1 else ("engine",) if schema == 4 else ("python", "site_packages", "engine")
        from .release_catalog import validate_host_requirements, HOST_REQUIREMENT_FIELDS
        if schema == 4 or any(key in descriptor for key in HOST_REQUIREMENT_FIELDS):
            validate_host_requirements(descriptor)
        if schema == 4:
            _science_dependency(descriptor.get("science_runtime"))
            if any(key in descriptor for key in ("python", "site_packages")) or any((root / name).exists() for name in ("python", "site-packages")):
                raise ComponentError("模块不得包含共享科学运行环境的专用副本。")
        if schema == 2:
            keys += ("models",)
        elif schema >= 3:
            _model_dependency(descriptor.get("model_component"))
            if "models" in descriptor or (root / "Models").exists():
                raise ComponentError("共享模型格式不得包含模块专用模型副本。")
        if schema >= 2 and get_module(identity)["needs_fiji"] and descriptor.get("fiji_component") != FIJI_ID:
            raise ComponentError("模块没有声明共享 Fiji 组件。")
    resolved = dict(descriptor)
    for key in keys:
        entry = root / _relative_path(descriptor.get(key))
        if not entry.resolve().is_relative_to(root.resolve()):
            raise ComponentError("运行时路径越出组件目录：" + key)
        is_directory = key in ("site_packages", "models", "engine")
        if not (entry.is_dir() if is_directory else entry.is_file()):
            raise ComponentError("组件缺少运行时路径：" + key)
        if not is_directory and not os.access(entry, os.X_OK):
            raise ComponentError("组件启动文件缺少执行权限：" + key)
        resolved[key] = str(entry.absolute())
    resolved["root"] = str(root.absolute())
    if identity == FIJI_ID:
        resolved["fiji_root"] = str(root.absolute())
    elif identity == MODEL_ID:
        weight = Path(resolved["models"]) / MODEL_FILENAME
        if weight.is_symlink() or not weight.is_file():
            raise ComponentError("共享模型缺少 cpsam_v2 权重文件。")
    return resolved


def _inventory(root, *, hashes):
    """Walk without following links; internal relative links are intentional."""
    result = {}
    root = Path(root)
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == MANIFEST_NAME:
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve().is_relative_to(root.resolve()):
                    raise ComponentError("组件含外部符号链接：" + relative)
                if not path.exists():
                    raise ComponentError("组件含失效符号链接：" + relative)
                result[relative] = {"type": "symlink", "target": target}
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = {"type": "directory", "mode": stat.S_IMODE(info.st_mode)}
            elif stat.S_ISREG(info.st_mode):
                item = {"type": "file", "bytes": info.st_size, "mode": stat.S_IMODE(info.st_mode)}
                if hashes:
                    item["sha256"] = _digest(path)
                else:
                    item["mtime_ns"] = info.st_mtime_ns
                result[relative] = item
            else:
                raise ComponentError("组件含不支持的文件类型：" + relative)
    return result


def build_manifest(seed_path, component_id=None):
    """Packaging helper; call only after the isolated seed is finalized."""
    root = Path(seed_path).absolute()
    descriptor = _descriptor(root, component_id)
    manifest = {"schema_version": 1, "component_id": descriptor["component_id"],
                "component_version": _version(descriptor), "entries": _inventory(root, hashes=True)}
    _atomic_json(root / MANIFEST_NAME, manifest)
    return manifest


def _read_manifest(root, component_id=None):
    path = root / MANIFEST_NAME
    if path.is_symlink():
        raise ComponentError("组件清单不得为符号链接。")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return _validate_manifest(manifest, component_id)


def _validate_manifest(manifest, component_id=None):
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(manifest.get("entries"), dict) or not manifest["entries"]):
        raise ComponentError("组件内容清单无效。")
    validate_module_id(manifest.get("component_id"))
    if component_id is not None and manifest["component_id"] != component_id:
        raise ComponentError("安装源不属于所选组件。")
    _version(manifest)
    for relative, item in manifest["entries"].items():
        _relative_path(relative)
        if relative == MANIFEST_NAME or not isinstance(item, dict) or item.get("type") not in ("file", "directory", "symlink"):
            raise ComponentError("组件内容清单含无效项目。")
        if item["type"] == "file" and not re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", "")):
            raise ComponentError("组件内容清单缺少 SHA-256。")
        if item["type"] == "file" and (not isinstance(item.get("bytes"), int) or item["bytes"] < 0):
            raise ComponentError("组件内容清单缺少有效文件长度。")
        if item["type"] in ("file", "directory") and (not isinstance(item.get("mode"), int) or not 0 <= item["mode"] <= 0o777):
            raise ComponentError("组件内容清单包含无效文件权限。")
        if item["type"] == "symlink" and (not isinstance(item.get("target"), str) or not item["target"]):
            raise ComponentError("组件内容清单包含无效链接目标。")
    return manifest


def _archive_members(archive):
    members = {}
    for member in archive.getmembers():
        name = member.name
        while name.startswith("./"):
            name = name[2:]
        if name in ("", ".") and member.isdir():
            continue
        name = name.rstrip("/") if member.isdir() else name
        _relative_path(name)
        if name in members:
            raise ComponentError("组件压缩包含重复路径：" + name)
        if not (member.isfile() or member.isdir() or member.issym()):
            raise ComponentError("组件压缩包含不支持的文件类型：" + name)
        members[name] = member
    return members


def _archive_manifest(seed, component_id=None):
    with tarfile.open(seed, "r:gz") as archive:
        members = _archive_members(archive)
        entry = members.get(MANIFEST_NAME)
        if entry is None or not entry.isfile() or entry.size > 32 * 1024 * 1024:
            raise ComponentError("组件压缩包缺少有效内容清单。")
        with archive.extractfile(entry) as stream:
            manifest = _validate_manifest(json.load(stream), component_id)
        if set(members) != set(manifest["entries"]) | {MANIFEST_NAME}:
            raise ComponentError("组件压缩包与固定内容清单不一致。")
        for name, expected in manifest["entries"].items():
            member = members[name]
            kind = "file" if member.isfile() else "directory" if member.isdir() else "symlink"
            if expected["type"] != kind:
                raise ComponentError("组件压缩包文件类型不一致：" + name)
            if kind == "file" and member.size != expected["bytes"]:
                raise ComponentError("组件压缩包文件长度不一致：" + name)
            if kind != "symlink" and member.mode != expected["mode"]:
                raise ComponentError("组件压缩包权限不一致：" + name)
            if kind == "symlink" and member.linkname != expected["target"]:
                raise ComponentError("组件压缩包链接不一致：" + name)
        return manifest


def _extract_seed(seed, destination):
    """Extract plain files first, then internal symlinks; never use extractall."""
    destination.mkdir()
    with tarfile.open(seed, "r:gz") as archive:
        members = _archive_members(archive)
        for name, member in sorted(members.items(), key=lambda pair: (pair[0].count("/"), pair[0])):
            if member.isdir():
                (destination / _relative_path(name)).mkdir(parents=True, exist_ok=True)
        for name, member in members.items():
            if member.isfile():
                output = destination / _relative_path(name)
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, output.open("xb") as target:
                    shutil.copyfileobj(source, target, 4 * 1024 * 1024)
                output.chmod(member.mode)
        for name, member in members.items():
            if member.issym():
                output = destination / _relative_path(name)
                if os.path.isabs(member.linkname) or not (output.parent / member.linkname).resolve().is_relative_to(destination.resolve()):
                    raise ComponentError("组件压缩包含外部符号链接：" + name)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.symlink_to(member.linkname)
        for name, member in members.items():
            if member.isdir():
                (destination / _relative_path(name)).chmod(member.mode)


def verify_seed(seed_path, component_id=None):
    """Full byte-and-membership verification; never executes seed code."""
    root = Path(seed_path).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ComponentError("请提供有效的组件安装目录。")
    manifest = _read_manifest(root, component_id)
    if _inventory(root, hashes=True) != manifest["entries"]:
        raise ComponentError("组件内容校验失败；安装源可能损坏。")
    descriptor = _descriptor(root, manifest["component_id"])
    if _version(descriptor) != _version(manifest):
        raise ComponentError("运行时与内容清单的版本不一致。")
    if manifest["component_id"] == MODEL_ID:
        relative = (Path(descriptor["models"]) / MODEL_FILENAME).relative_to(root).as_posix()
        # The complete inventory above has already hashed these exact bytes.
        if manifest["entries"].get(relative, {}).get("sha256") != MODEL_SHA256:
            raise ComponentError("共享 cpsam_v2 权重 SHA-256 校验失败。")
    return manifest


def _atomic_json(path, value, *, activation=False):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Persist the directory entry, not just the new file's contents.
        try:
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            if not activation:
                raise
            # The pointer has already atomically changed to a fully verified
            # generation. Do not claim that the old pointer is still active.
            return str(exc)
    finally:
        if temporary.exists():
            temporary.unlink()


def _managed_root(app_home, module_id=COMPONENT_ID, *, create=False):
    from .paths import ensure_app_home
    validate_module_id(module_id)
    home = ensure_app_home(app_home, create=create)
    root = home / "components" / module_id
    for path in (home, home / "components", root):
        if path.is_symlink():
            raise ComponentError("受管理组件目录不得为符号链接。")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def component_lock_path(app_home):
    """A stable lock outside removable module/data directories."""
    from .paths import maintenance_lock_path
    return maintenance_lock_path(app_home)


@contextmanager
def maintenance_lock_fds(app_home, *, exclusive=True):
    """Hold the stable and extant legacy locks; helpers may inherit these FDs.

    Closing the parent's descriptors does not release an inherited flock until
    the helper also closes them. This context never creates a module directory.
    """
    path = component_lock_path(app_home)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    legacy_fds = []
    try:
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            # An already running worker from the previous application version
            # still owns this old lock. Honor it during the one-way upgrade.
            legacy = _managed_root(app_home, COMPONENT_ID) / "component.lock"
            if legacy.exists() or legacy.is_symlink():
                legacy_fd = os.open(legacy, os.O_RDWR | os.O_NOFOLLOW)
                legacy_fds.append(legacy_fd)
                fcntl.flock(legacy_fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise ComponentBusy("分析或维护正在使用组件；请在任务及 Fiji 结束后重试。") from exc
            raise
        yield (fd, *legacy_fds)
    finally:
        for legacy_fd in legacy_fds:
            os.close(legacy_fd)
        os.close(fd)


@contextmanager
def _component_lock(app_home, *, exclusive, module_id=COMPONENT_ID):
    with maintenance_lock_fds(app_home, exclusive=exclusive):
        yield _managed_root(app_home, module_id, create=True)


@contextmanager
def component_use_lock(app_home):
    """Keep open for the complete worker AND Fiji lifetime."""
    with _component_lock(app_home, exclusive=False):
        yield


@contextmanager
def component_maintenance_lock(app_home):
    with _component_lock(app_home, exclusive=True):
        yield


def _current(root, module_id=COMPONENT_ID):
    pointer = root / "current.json"
    if not pointer.exists() and not pointer.is_symlink():
        return None
    if pointer.is_symlink():
        raise ComponentError("组件激活记录不得为符号链接。")
    state = json.loads(pointer.read_text(encoding="utf-8"))
    return _validated_generation(root, state, module_id)


def _validated_generation(root, state, module_id):
    """Validate the same owned generation constraint for current/recovery records."""
    if not isinstance(state, dict):
        raise ComponentError("组件激活记录无效。")
    generation = state.get("generation", "")
    if (state.get("schema_version") != 1 or state.get("component_id") != module_id
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", generation)):
        raise ComponentError("组件激活记录无效。")
    _version(state)
    directory = root / "generations" / generation
    if (root / "generations").is_symlink() or directory.is_symlink():
        raise ComponentError("组件代目录不得为符号链接。")
    return state, directory


def _legacy_model_generation(root):
    """A valid current pointer wins; a broken pointer may use its saved receipt."""
    try:
        current = _current(root, root.name)
    except (OSError, ValueError, TypeError, ComponentError):
        current = None
    if current is not None and current[1].is_dir():
        return current
    backup = root / "installation.json"
    if backup.is_symlink() or not backup.is_file():
        return None
    return _validated_generation(root, json.loads(backup.read_text(encoding="utf-8")), root.name)


def _record(root):
    """The committed pointer is authoritative; the backup only aids recovery."""
    try:
        current = _current(root, root.name)
        if current is not None:
            return dict(current[0], installed=True)
    except (OSError, ValueError, TypeError, ComponentError):
        pass
    installed = False
    for name in ("installation.json", "current.json"):
        path = root / name
        if path.is_symlink():
            installed = True
            continue
        if path.exists():
            installed = True
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if name == "installation.json" and isinstance(value, dict):
                    return dict(value, installed=True)
            except (OSError, ValueError, TypeError):
                pass
    return {"installed": installed}


def _next_sequence(home):
    path = Path(home) / "install-order.json"
    if path.is_symlink():
        raise ComponentError("安装顺序记录不得为符号链接。")
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"next_install_seq": 1}
    sequence = value.get("next_install_seq") if isinstance(value, dict) else None
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ComponentError("安装顺序记录损坏。")
    _atomic_json(path, {"schema_version": 1, "next_install_seq": sequence + 1})
    return sequence


def _quick_status(app_home, component_id, *, generation_state=None):
    root = _managed_root(app_home, component_id)
    record = dict(generation_state, installed=True) if generation_state is not None else _record(root)
    result = {"id": component_id, "installed": record["installed"],
              "install_seq": record.get("install_seq", 0), "runtime_descriptor": None,
              "component_version": record.get("component_version", ""), "source": record.get("source", ""),
              "fiji_version": record.get("fiji_version", "")}
    if not record["installed"]:
        return dict(result, status="not_installed", message="尚未安装组件。")
    try:
        current = _validated_generation(root, generation_state, component_id) if generation_state is not None else _current(root, component_id)
        if current is None:
            raise ComponentError("组件激活记录缺失；请修复当前版本。")
        state, directory = current
        manifest = _read_manifest(directory, component_id)
        if _digest(directory / MANIFEST_NAME) != state.get("manifest_sha256"):
            raise ComponentError("组件内容清单已改变；请修复当前版本。")
        receipt_path = root / "generations" / (state["generation"] + ".receipt.json")
        if receipt_path.is_symlink():
            raise ComponentError("组件安装记录不得为符号链接。")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if _inventory(directory, hashes=False) != receipt:
            raise ComponentError("组件文件缺失或已改变；请修复当前版本。")
        descriptor = _descriptor(directory, component_id)
        from .release_catalog import validate_host_requirements, HOST_REQUIREMENT_FIELDS
        if component_id not in SHARED_IDS and (descriptor["schema_version"] == 4 or any(key in descriptor for key in HOST_REQUIREMENT_FIELDS)):
            validate_host_requirements(descriptor, require_current=True)
        if _version(descriptor) != _version(state) or _version(manifest) != _version(state):
            raise ComponentError("组件版本记录不一致。")
        if component_id != FIJI_ID and descriptor["schema_version"] == 1:
            raise ComponentError("现有组件缺少独立分析程序；请使用外置安装包修复当前版本。")
        return dict(result, status="ready", message="组件已安装。", runtime_descriptor=descriptor,
                    integrity_check="metadata", component_version=_version(state),
                    install_seq=state.get("install_seq", result["install_seq"]))
    except (OSError, ValueError, TypeError, ComponentError) as exc:
        return dict(result, status="damaged", message=str(exc))


def fiji_status(app_home):
    try:
        return _quick_status(app_home, FIJI_ID)
    except (OSError, ValueError, TypeError, ComponentError) as exc:
        return {"id": FIJI_ID, "installed": False, "status": "damaged",
                "message": str(exc), "runtime_descriptor": None}


def model_status(app_home):
    try:
        return _quick_status(app_home, MODEL_ID)
    except (OSError, ValueError, TypeError, ComponentError) as exc:
        return {"id": MODEL_ID, "installed": False, "status": "damaged",
                "message": str(exc), "runtime_descriptor": None}


def _science_generation(app_home, version):
    root = _managed_root(app_home, SCIENCE_ID)
    try:
        current = _current(root, SCIENCE_ID)
    except (OSError, ValueError, TypeError, ComponentError):
        current = None
    if current is not None and current[0]["component_version"] == version:
        return current
    generations = root / "generations"
    if generations.is_symlink():
        raise ComponentError("科学运行环境代目录不得为符号链接。")
    candidates = []
    for path in generations.glob("*.state.json") if generations.is_dir() else ():
        if path.is_symlink():
            raise ComponentError("科学运行环境版本记录不得为符号链接。")
        state = json.loads(path.read_text(encoding="utf-8"))
        saved = _validated_generation(root, state, SCIENCE_ID)
        if path.name != state["generation"] + ".state.json":
            raise ComponentError("科学运行环境版本记录与文件名不一致。")
        if state["component_version"] == version:
            candidates.append((path.stat().st_mtime_ns, saved))
    candidates.sort(key=lambda item: item[0], reverse=True)
    for _time, candidate in candidates:
        if _quick_status(app_home, SCIENCE_ID, generation_state=candidate[0])["status"] == "ready":
            return candidate
    return candidates[0][1] if candidates else None


def science_status(app_home, version=None):
    try:
        if version is None:
            return _quick_status(app_home, SCIENCE_ID)
        selected = _science_generation(app_home, version)
        if selected is None:
            return {"id": SCIENCE_ID, "installed": False, "status": "not_installed",
                    "component_version": version, "message": "所需科学运行环境尚未安装。", "runtime_descriptor": None}
        return _quick_status(app_home, SCIENCE_ID, generation_state=selected[0])
    except (OSError, ValueError, TypeError, ComponentError) as exc:
        return {"id": SCIENCE_ID, "installed": False, "status": "damaged",
                "message": str(exc), "runtime_descriptor": None}


def component_status(app_home, seed_path=None, module_id=COMPONENT_ID):
    """Read-only integrity metadata check; full hashes are checked on maintenance."""
    module = get_module(module_id)
    try:
        status = _quick_status(app_home, module_id)
        if status["status"] == "ready" and status["runtime_descriptor"].get("science_runtime"):
            dependency = _science_dependency(status["runtime_descriptor"]["science_runtime"])
            shared = science_status(app_home, dependency["version"])
            if shared["status"] != "ready" or shared.get("component_version") != dependency["version"]:
                status.update(status="damaged", message="共享科学运行环境缺失、损坏或版本不匹配；请修复当前模块。", runtime_descriptor=None)
            else:
                descriptor = dict(status["runtime_descriptor"])
                descriptor.update({key: shared["runtime_descriptor"][key] for key in ("python", "site_packages")})
                descriptor["science_root"] = shared["runtime_descriptor"]["root"]
                status["runtime_descriptor"] = descriptor
        if status["status"] == "ready" and status["runtime_descriptor"].get("model_component"):
            shared = model_status(app_home)
            if shared["status"] != "ready":
                status.update(status="damaged", message="共享 Cellpose 模型缺失或损坏；请修复当前模块。", runtime_descriptor=None)
            else:
                descriptor = dict(status["runtime_descriptor"])
                _model_dependency(descriptor["model_component"])
                descriptor["models"] = shared["runtime_descriptor"]["models"]
                descriptor["model_root"] = shared["runtime_descriptor"]["root"]
                status["runtime_descriptor"] = descriptor
        if status["status"] == "ready" and module["needs_fiji"]:
            fiji = fiji_status(app_home)
            if fiji["status"] != "ready" or status.get("fiji_version") and fiji.get("component_version") != status["fiji_version"]:
                status.update(status="damaged", message="共享 Fiji 缺失、损坏或版本不匹配；请完成更新或修复当前模块。", runtime_descriptor=None)
            else:
                descriptor = dict(status["runtime_descriptor"])
                descriptor.update({key: fiji["runtime_descriptor"][key] for key in ("fiji_root", "fiji_launcher")})
                status["runtime_descriptor"] = descriptor
        return dict(module, **status)
    except (OSError, ValueError, TypeError, ComponentError) as exc:
        # An unsafe managed path is never adopted or recursively removed.
        return dict(module, installed=False, status="damaged", message=str(exc), runtime_descriptor=None)


def list_statuses(app_home):
    return [component_status(app_home, module_id=module["id"]) for module in list_modules()]


def _notify(progress, fraction, message):
    if progress is not None:
        progress(fraction, message)


class _OfflineSources(dict):
    def __init__(self):
        super().__init__()
        self.science_versions = {}


def _package_sources(source_path):
    if source_path is None or not str(source_path):
        raise ComponentError("请选择完整的离线组件安装目录。")
    if urlsplit(str(source_path)).scheme in ("http", "https"):
        raise ComponentError("在线组件安装必须使用应用内置的签名发布配置。")
    source = Path(source_path).expanduser().absolute()
    if source.is_symlink():
        raise ComponentError("组件安装源不得为符号链接。")
    catalog = source / "catalog.json"
    if source.is_dir() and catalog.exists():
        if catalog.is_symlink():
            raise ComponentError("安装源目录不得使用符号链接清单。")
        data = json.loads(catalog.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("packages"), dict):
            raise ComponentError("离线安装目录清单无效。")
        result = _OfflineSources()
        for component_id, item in data["packages"].items():
            validate_module_id(component_id)
            if not isinstance(item, dict) or "url" in item:
                raise ComponentError("离线安装目录只接受本地组件包。")
            target = source / _relative_path(item.get("path"))
            if target.is_symlink() or not target.resolve().is_relative_to(source.resolve()):
                raise ComponentError("离线安装包路径越出安装源目录。")
            result[component_id] = (target, item)
            if component_id == SCIENCE_ID and item.get("version"):
                result.science_versions[item["version"]] = (target, item)
        versions = data.get("science_runtime_versions", {})
        if not isinstance(versions, dict):
            raise ComponentError("离线科学运行环境版本目录无效。")
        for version, item in versions.items():
            _version({"component_version": version})
            if not isinstance(item, dict) or "url" in item or item.get("version") != version:
                raise ComponentError("离线科学运行环境版本条目无效。")
            target = source / _relative_path(item.get("path"))
            if target.is_symlink() or not target.resolve().is_relative_to(source.resolve()):
                raise ComponentError("离线运行环境路径越出安装源目录。")
            result.science_versions[version] = (target, item)
            result.setdefault(SCIENCE_ID, (target, item))
        return result
    manifest = verify_seed(source) if source.is_dir() else _archive_manifest(source)
    return {manifest["component_id"]: (source, {})}


@contextmanager
def _materialized_source(source, metadata, app_home, *, progress=None, cancel=None):
    """Fetch only a requested component; always discard partial or failed bytes."""
    _check_cancel(cancel)
    if isinstance(source, Path):
        yield source
        return
    with tempfile.TemporaryDirectory(prefix=".package-download-", dir=app_home) as temporary:
        archive = Path(temporary) / "package.tar.gz"
        expected = metadata["bytes"]
        actual, digest = 0, hashlib.sha256()
        from .release_catalog import open_url
        with open_url(source) as response, archive.open("xb") as target:
            while True:
                _check_cancel(cancel)
                block = response.read(1024 * 1024)
                if not block:
                    break
                actual += len(block)
                if actual > expected:
                    raise ComponentError("下载的安装包超过目录声明长度。")
                target.write(block)
                digest.update(block)
                _notify(progress, 0.02 + 0.18 * actual / expected, "正在下载并校验组件安装包…")
        if actual != expected or digest.hexdigest() != metadata["sha256"]:
            raise ComponentError("下载的安装包长度或 SHA-256 不匹配。")
        _check_cancel(cancel)
        yield archive


def _source_manifest(source, metadata, component_id):
    if source.is_file():
        if "bytes" in metadata and source.stat().st_size != metadata["bytes"]:
            raise ComponentError("安装包长度与目录清单不一致。")
        if "sha256" in metadata and _digest(source) != metadata["sha256"]:
            raise ComponentError("安装包 SHA-256 与目录清单不一致。")
    manifest = verify_seed(source, component_id) if source.is_dir() else _archive_manifest(source, component_id)
    if "manifest_sha256" in metadata:
        if source.is_dir():
            actual_manifest_hash = _digest(source / MANIFEST_NAME)
        else:
            with tarfile.open(source, "r:gz") as archive:
                member = _archive_members(archive)[MANIFEST_NAME]
                with archive.extractfile(member) as stream:
                    actual_manifest_hash = hashlib.sha256(stream.read()).hexdigest()
        if actual_manifest_hash != metadata["manifest_sha256"]:
            raise ComponentError("组件清单 SHA-256 与发布目录不一致。")
    if metadata.get("version", _version(manifest)) != _version(manifest):
        raise ComponentError("安装包版本与目录清单不一致。")
    return manifest


def _source_descriptor(source, manifest):
    if source.is_dir():
        payload = (source / "runtime.json").read_bytes()
    else:
        with tarfile.open(source, "r:gz") as archive:
            entry = _archive_members(archive).get("runtime.json")
            if entry is None or not entry.isfile() or entry.size > 1024 * 1024:
                raise ComponentError("组件缺少有效运行时描述。")
            with archive.extractfile(entry) as stream:
                payload = stream.read()
    if hashlib.sha256(payload).hexdigest() != manifest["entries"].get("runtime.json", {}).get("sha256"):
        raise ComponentError("运行时描述 SHA-256 校验失败。")
    descriptor = json.loads(payload)
    if not isinstance(descriptor, dict):
        raise ComponentError("运行时描述无效。")
    if descriptor.get("schema_version", 0) >= 3:
        _model_dependency(descriptor.get("model_component"))
    return descriptor


def _activate(root, source, metadata, manifest, *, sequence, original_source, progress=None):
    """Prepare a generation, then commit its single current.json pointer."""
    stage = None
    committed = False
    component_id, version = manifest["component_id"], _version(manifest)
    generations = root / "generations"
    if generations.is_symlink():
        raise ComponentError("组件代目录不得为符号链接。")
    generations.mkdir(exist_ok=True)
    generation = version + "-" + uuid.uuid4().hex
    active = generations / generation
    receipt_path = generations / (generation + ".receipt.json")
    version_state_path = generations / (generation + ".state.json")
    try:
        stage = generations / ("staging-" + generation)
        _notify(progress, 0.25, "正在准备独立组件副本…")
        if source.is_dir():
            shutil.copytree(source, stage, symlinks=True)
        else:
            _extract_seed(source, stage)
        _notify(progress, 0.70, "正在校验新组件副本…")
        if verify_seed(stage, component_id) != manifest:
            raise ComponentError("复制期间安装源发生变化；保留原组件。")
        if metadata.get("manifest_sha256", _digest(stage / MANIFEST_NAME)) != _digest(stage / MANIFEST_NAME):
            raise ComponentError("组件清单 SHA-256 与安装目录不一致。")
        descriptor = _descriptor(stage, component_id)
        if component_id not in SHARED_IDS and descriptor["schema_version"] not in (2, 3, 4):
            raise ComponentError("请选择包含独立分析程序的新格式安装包。")
        receipt = _inventory(stage, hashes=False)
        os.replace(stage, active)
        stage = None
        _atomic_json(receipt_path, receipt)
        state = {"schema_version": 1, "component_id": component_id, "component_version": version,
                 "generation": generation, "manifest_sha256": _digest(active / MANIFEST_NAME),
                 "install_seq": sequence, "source": str(original_source)}
        if component_id not in SHARED_IDS:
            state["needs_fiji"] = get_module(component_id)["needs_fiji"]
            if state["needs_fiji"] and metadata.get("dependencies", {}).get(FIJI_ID):
                state["fiji_version"] = metadata["dependencies"][FIJI_ID]
            for dependency in ("model_component", "science_runtime"):
                if descriptor.get(dependency):
                    state[dependency] = descriptor[dependency]
        resolved_descriptor = _descriptor(active, component_id)
        if component_id == SCIENCE_ID:
            try:
                previous = _current(root, SCIENCE_ID)
            except (OSError, ValueError, TypeError, ComponentError):
                previous = None
            if previous is not None:
                _atomic_json(generations / (previous[0]["generation"] + ".state.json"), previous[0])
            _atomic_json(version_state_path, state)
        _notify(progress, 0.96, "正在激活已验证组件…")
        warning = _atomic_json(root / "current.json", state, activation=True)
        committed = True
        # current.json remains authoritative if persisting the redundant recovery
        # record fails after commit. Never report a completed activation as failed.
        try:
            _atomic_json(root / "installation.json", state)
        except OSError as exc:
            warning = warning or str(exc)
        result = {"status": "ready", "message": "组件安装完成。", "installed": True,
                  "id": component_id, "install_seq": sequence,
                  "runtime_descriptor": resolved_descriptor, "integrity_check": "sha256",
                  "component_version": version}
        if warning:
            result["durability_warning"] = warning
        return result
    except (OSError, ValueError, TypeError, ComponentError, tarfile.TarError, EOFError) as exc:
        cleanup_errors = []
        if not committed:
            for path in (stage, active):
                if path is not None:
                    try:
                        _remove_owned_tree(path)
                    except OSError as cleanup_error:
                        cleanup_errors.append(str(cleanup_error))
            try:
                receipt_path.unlink(missing_ok=True)
                version_state_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                cleanup_errors.append(str(cleanup_error))
        if cleanup_errors:
            raise ComponentError(str(exc) + "；未提交组件清理失败：" + "; ".join(cleanup_errors)) from exc
        raise


def _ensure_fiji(app_home, sources, original_source, *, repair=False, required_version=None, progress=None, cancel=None, checked=None):
    current = fiji_status(app_home)
    if current["status"] == "ready" and (required_version is None or current.get("component_version") == required_version):
        if checked is not None and checked.get("status") == "intact":
            return
        if checked is None:
            try:
                verify_seed(Path(current["runtime_descriptor"]["root"]), FIJI_ID)
                return
            except (OSError, ValueError, TypeError, ComponentError):
                pass
    if FIJI_ID not in sources:
        raise ComponentError("共享 Fiji 尚未就绪；请选择包含 Fiji 的完整外置安装源目录。")
    source, metadata = sources[FIJI_ID]
    with _materialized_source(source, metadata, app_home, progress=progress, cancel=cancel) as local:
        manifest = _source_manifest(local, metadata, FIJI_ID)
        version = _version(manifest)
        if required_version is not None and version != required_version:
            raise ComponentError("Fiji 安装包版本不符合模块指定依赖。")
        installed_version = current.get("component_version")
        if repair and required_version is None and installed_version and installed_version != version:
            raise ComponentError("修复共享 Fiji 必须使用模块已安装时的依赖版本。")
        root = _managed_root(app_home, FIJI_ID, create=True)
        _activate(root, local, metadata, manifest, sequence=0, original_source=original_source, progress=progress)


def _activation_records(root):
    records = {}
    for name in ("current.json", "installation.json"):
        path = root / name
        if path.is_symlink():
            raise ComponentError("组件激活记录不得为符号链接。")
        records[name] = path.read_bytes() if path.exists() else None
    return records


def _restore_fiji_activation(root, records):
    """Undo a dependency change when its module did not activate successfully."""
    if _activation_records(root) == records:
        return
    current = _current(root, FIJI_ID)
    for name, payload in records.items():
        path = root / name
        if payload is None:
            path.unlink(missing_ok=True)
            continue
        temporary = path.with_name(name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        restored = _current(root, FIJI_ID)
    except (OSError, ValueError, TypeError, ComponentError):
        restored = None
    if current is not None and (restored is None or current[0]["generation"] != restored[0]["generation"]):
        _remove_owned_tree(current[1])
        (root / "generations" / (current[0]["generation"] + ".receipt.json")).unlink(missing_ok=True)


def _reuse_installed_model(app_home, *, progress=None):
    """Promote only verified weights from this managed home, never external Support."""
    for module in list_modules():
        root = _managed_root(app_home, module["id"])
        try:
            current = _legacy_model_generation(root)
            if current is None:
                continue
            state, directory = current
            manifest = _read_manifest(directory, module["id"])
            if _digest(directory / MANIFEST_NAME) != state.get("manifest_sha256"):
                continue
            weight = directory / "Models" / MODEL_FILENAME
            expected = manifest["entries"].get("Models/" + MODEL_FILENAME, {})
            if (directory.joinpath("Models").is_symlink() or weight.is_symlink() or not weight.is_file()
                    or expected.get("sha256") != MODEL_SHA256 or _digest(weight) != MODEL_SHA256):
                continue
        except (OSError, ValueError, TypeError, ComponentError):
            continue
        shared_root = _managed_root(app_home, MODEL_ID, create=True)
        with tempfile.TemporaryDirectory(prefix=".model-reuse-", dir=app_home) as temporary:
            seed = Path(temporary)
            (seed / "Models").mkdir()
            # A temporary read-only source link avoids a second weight copy.
            # Activation always copies bytes into an independent generation.
            os.link(weight, seed / "Models" / MODEL_FILENAME)
            _atomic_json(seed / "runtime.json", {"schema_version": 2, "component_id": MODEL_ID,
                "component_version": MODEL_VERSION, "models": "Models", "model_file": MODEL_FILENAME,
                "model_sha256": MODEL_SHA256})
            model_manifest = build_manifest(seed, MODEL_ID)
            _activate(shared_root, seed, {}, model_manifest, sequence=0,
                      original_source="verified-managed-model", progress=progress)
        return True
    return False


def _ensure_model(app_home, sources, original_source, *, progress=None, cancel=None, checked=None):
    current = model_status(app_home)
    if current["status"] == "ready" and checked is None:
        # Full verification occurs during maintenance, never on ordinary UI reads.
        try:
            verify_seed(Path(current["runtime_descriptor"]["root"]), MODEL_ID)
            return
        except (OSError, ValueError, TypeError, ComponentError):
            pass
    _check_cancel(cancel)
    if _reuse_installed_model(app_home, progress=progress):
        return
    if MODEL_ID not in sources:
        raise ComponentError("共享 Cellpose 模型尚未就绪；请选择包含模型的完整安装源。")
    source, metadata = sources[MODEL_ID]
    with _materialized_source(source, metadata, app_home, progress=progress, cancel=cancel) as local:
        manifest = _source_manifest(local, metadata, MODEL_ID)
        if _version(manifest) != MODEL_VERSION:
            raise ComponentError("共享 Cellpose 模型版本不符合模块依赖。")
        root = _managed_root(app_home, MODEL_ID, create=True)
        _activate(root, local, metadata, manifest, sequence=0, original_source=original_source, progress=progress)


def _retain_legacy_model(app_home, root, progress):
    """Do not delete the only owned weight when its activation metadata is broken."""
    generations = root / "generations"
    if generations.is_symlink():
        raise ComponentError("组件代目录不得为符号链接。")
    has_legacy_weight = False
    for directory in generations.iterdir() if generations.is_dir() else ():
        if directory.is_symlink() or not directory.is_dir():
            continue
        models = directory / "Models"
        if models.is_symlink():
            raise ComponentError("旧模型目录不得为符号链接；请先修复当前模块，再删除。")
        weight = models / MODEL_FILENAME
        if weight.exists() or weight.is_symlink():
            has_legacy_weight = True
            break
    if not has_legacy_weight:
        return
    shared = model_status(app_home)
    if shared["status"] == "ready":
        try:
            verify_seed(Path(shared["runtime_descriptor"]["root"]), MODEL_ID)
            return
        except (OSError, ValueError, TypeError, ComponentError):
            pass
    if not _reuse_installed_model(app_home, progress=progress):
        raise ComponentError("无法安全保留旧版 Cellpose 模型；请先修复当前模块，再删除。")


def _full_check(app_home, identity, *, version=None):
    """Validate bytes, membership, trusted metadata and host compatibility."""
    try:
        root = _managed_root(app_home, identity)
        current = _science_generation(app_home, version) if identity == SCIENCE_ID and version else _current(root, identity)
        if current is None:
            raise ComponentError("组件尚未安装。")
        state, directory = current
        manifest = verify_seed(directory, identity)
        if _digest(directory / MANIFEST_NAME) != state.get("manifest_sha256") or _version(manifest) != _version(state):
            raise ComponentError("组件清单或版本与安装记录不一致。")
        descriptor = _descriptor(directory, identity)
        from .release_catalog import validate_host_requirements, HOST_REQUIREMENT_FIELDS
        if identity not in SHARED_IDS and (descriptor["schema_version"] == 4 or any(key in descriptor for key in HOST_REQUIREMENT_FIELDS)):
            validate_host_requirements(descriptor, require_current=True)
        # Metadata is only a fast UI signal. After hashing every file, accept
        # harmless timestamp changes by updating the non-authoritative receipt.
        receipt = root / "generations" / (state["generation"] + ".receipt.json")
        expected = _inventory(directory, hashes=False)
        if receipt.is_symlink():
            raise ComponentError("组件安装记录不得为符号链接。")
        try:
            unchanged = json.loads(receipt.read_text(encoding="utf-8")) == expected
        except (OSError, ValueError):
            unchanged = False
        if not unchanged:
            _atomic_json(receipt, expected)
        return {"id": identity, "status": "intact", "runtime_descriptor": descriptor}
    except (OSError, ValueError, TypeError, ComponentError) as exc:
        return {"id": identity, "status": "damaged", "message": str(exc)}


def _check_installed_group(app_home, module_id, record, report):
    dependencies = [module_id]
    if get_module(module_id)["needs_fiji"]:
        dependencies.append(FIJI_ID)
    # Installation records retain dependency ownership even if runtime.json
    # itself has been damaged. Legacy schema 2 continues to own local models.
    if record.get("model_component"):
        dependencies.append(MODEL_ID)
    if record.get("science_runtime"):
        dependencies.append(SCIENCE_ID)
    report(0.02, "正在完整校验模块及共享组件…")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="component-integrity") as executor:
        checks = list(executor.map(lambda identity: _full_check(app_home, identity, version=record.get("science_runtime", {}).get("version") if identity == SCIENCE_ID else None), dependencies))
    report(0.10, "组件完整性校验完成。")
    return {check["id"]: check for check in checks}


def _ensure_science(app_home, sources, original_source, dependency, *, checked=None, progress=None, cancel=None):
    dependency = _science_dependency(dependency)
    current = science_status(app_home, dependency["version"])
    if current["status"] == "ready" and current.get("component_version") == dependency["version"]:
        if checked is not None and checked.get("status") == "intact":
            return
        if checked is None and _full_check(app_home, SCIENCE_ID, version=dependency["version"])["status"] == "intact":
            return
    if SCIENCE_ID not in sources:
        raise ComponentError("安装源缺少共享科学运行环境。")
    source, metadata = getattr(sources, "science_versions", {}).get(dependency["version"], sources[SCIENCE_ID])
    with _materialized_source(source, metadata, app_home, progress=progress, cancel=cancel) as local:
        manifest = _source_manifest(local, metadata, SCIENCE_ID)
        if _version(manifest) != dependency["version"]:
            raise ComponentError("科学运行环境版本不符合模块依赖。")
        _activate(_managed_root(app_home, SCIENCE_ID, create=True), local, metadata, manifest,
                  sequence=0, original_source=original_source, progress=progress)


def _cleanup_science_generations(app_home):
    """Keep exactly the runtime generations still referenced by installed modules."""
    root = _managed_root(app_home, SCIENCE_ID)
    if not root.exists():
        return
    components = Path(app_home) / "components"
    required = set()
    for module_root in components.iterdir():
        if module_root.name in SHARED_IDS:
            continue
        if module_root.is_symlink():
            raise ComponentError("受管理组件目录不得为符号链接。")
        record = _record(module_root)
        if not record["installed"]:
            continue
        dependency = record.get("science_runtime")
        if dependency is None:
            # Legacy modules carry their own interpreter. A damaged ownership
            # record must not be mistaken for proof that no shared version is used.
            selected_module = _legacy_model_generation(module_root)
            if selected_module is None:
                raise ComponentError("模块依赖记录缺失；未清理共享科学运行环境。")
            manifest = _read_manifest(selected_module[1], module_root.name)
            descriptor = _source_descriptor(selected_module[1], manifest)
            dependency = descriptor.get("science_runtime")
        if dependency is not None:
            required.add(_science_dependency(dependency)["version"])
    if not required:
        _remove_owned_tree(root)
        return
    retained = {}
    for version in required:
        selected = _science_generation(app_home, version)
        if selected is None:
            raise ComponentError("仍被模块使用的科学运行环境版本记录缺失；未清理运行环境。")
        retained[selected[0]["generation"]] = selected[0]
    try:
        current = _current(root, SCIENCE_ID)
    except (OSError, ValueError, TypeError, ComponentError):
        current = None
    if current is None or current[0]["generation"] not in retained:
        selected = next(iter(retained.values()))
        _atomic_json(root / "current.json", selected)
        _atomic_json(root / "installation.json", selected)
    generations = root / "generations"
    for path in generations.iterdir():
        if path.is_symlink():
            raise ComponentError("科学运行环境代目录不得为符号链接。")
        if path.is_dir() and path.name not in retained:
            _remove_owned_tree(path)
            (generations / (path.name + ".receipt.json")).unlink(missing_ok=True)
            (generations / (path.name + ".state.json")).unlink(missing_ok=True)


def _default_release_configuration():
    from .paths import resource_root
    from .release_catalog import load_configuration
    import sys
    resources = resource_root()
    if getattr(sys, "frozen", False):
        bundle = next((parent for parent in Path(sys.executable).parents if parent.suffix == ".app"), None)
        library = bundle / "Contents/Frameworks/libLeapGlass.dylib" if bundle else resources / "libLeapGlass.dylib"
    else:
        library = resources / "runtime/build/libLeapGlass.dylib"
    return load_configuration(resources, library)


def _maintain(app_home, seed_path, *, operation, progress, module_id, cancel=None, configuration=None, expected_target=None, _lock_held=False, _defer_science_cleanup=False):
    from .release_catalog import CatalogUnconfigured, fetch_catalog, validate_host_requirements, HOST_REQUIREMENT_FIELDS

    def report(fraction, message):
        _check_cancel(cancel)
        _notify(progress, fraction, message)
        _check_cancel(cancel)

    try:
        module = get_module(module_id)
        lock = nullcontext(_managed_root(app_home, module_id, create=True)) if _lock_held else _component_lock(app_home, exclusive=True, module_id=module_id)
        with lock as root, ExitStack() as stack:
            record = _record(root)
            repair, updating = operation == "repair", operation == "update"
            if (repair or updating) and not record["installed"]:
                raise ComponentError("尚未安装模块；请先安装。")
            if record["installed"] and operation == "install":
                status = component_status(app_home, module_id=module_id)
                if status["status"] != "ready":
                    raise ComponentError("模块已安装但需要修复；请使用修复当前版本。")
                return status
            checks = _check_installed_group(app_home, module_id, record, report) if repair else {}
            if checks and all(check["status"] == "intact" for check in checks.values()):
                status = component_status(app_home, module_id=module_id)
                if status["status"] == "ready":
                    return dict(status, status="intact", integrity_check="sha256",
                                message="完整校验通过，组件无需修复。", verified_components=list(checks))
            report(0.12, "正在获取并验证组件发布目录…" if seed_path is None else "正在读取离线安装目录…")
            catalog = None
            if seed_path is None:
                catalog = fetch_catalog(configuration or _default_release_configuration(), cancel=cancel)
                wanted = record.get("component_version") if repair else None
                sources = {module_id: catalog.select(module_id, wanted)}
                original_source = catalog.configuration.catalog_url
            else:
                sources = _package_sources(seed_path)
                original_source = str(seed_path)
            if module_id not in sources:
                raise ComponentError("安装源中没有所选模块。")
            source, metadata = sources[module_id]
            if any(key in metadata for key in HOST_REQUIREMENT_FIELDS):
                validate_host_requirements(metadata, require_current=True)
            if expected_target is not None:
                for field in ("version", "sha256", "bytes", "manifest_sha256"):
                    if metadata.get(field) != expected_target.get(field):
                        raise ComponentError("组件发布内容在检查后已改变；请重新检查更新。")
            source = stack.enter_context(_materialized_source(source, metadata, app_home, progress=report, cancel=cancel))
            if source.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(source.resolve()):
                raise ComponentError("安装源与受管理组件目录必须相互独立。")
            manifest = _source_manifest(source, metadata, module_id)
            descriptor = _source_descriptor(source, manifest)
            if descriptor.get("schema_version") == 4 or any(key in descriptor for key in HOST_REQUIREMENT_FIELDS):
                descriptor_host = validate_host_requirements(descriptor, require_current=True)
                if any(key in metadata for key in HOST_REQUIREMENT_FIELDS) and descriptor_host != validate_host_requirements(metadata):
                    raise ComponentError("模块主程序兼容条件与发布目录不一致。")
            if "dependencies" in metadata:
                declared = metadata["dependencies"]
                if not isinstance(declared, dict):
                    raise ComponentError("发布目录共享依赖声明无效。")
                expected_ids = set()
                if descriptor.get("model_component"):
                    expected_ids.add(MODEL_ID)
                    if declared.get(MODEL_ID) != descriptor["model_component"]["version"]:
                        raise ComponentError("发布目录模型版本与模块不一致。")
                if descriptor.get("science_runtime"):
                    expected_ids.add(SCIENCE_ID)
                    if declared.get(SCIENCE_ID) != descriptor["science_runtime"]["version"]:
                        raise ComponentError("发布目录科学运行环境版本与模块不一致。")
                if module["needs_fiji"]:
                    expected_ids.add(FIJI_ID)
                if set(declared) != expected_ids:
                    raise ComponentError("发布目录共享依赖与模块不一致。")
            version = _version(manifest)
            previous_version = record.get("component_version") or module["version"]
            if repair and previous_version != version:
                raise ComponentError("修复必须使用已安装的同一版本组件。")
            if updating:
                from .update_manager import newer_version
                if version == previous_version:
                    return dict(component_status(app_home, module_id=module_id), status="up_to_date", message="未发现可用的模块更新。")
                if not newer_version(version, previous_version):
                    raise ComponentError("模块更新不接受较旧版本。")
            sequence = record.get("install_seq", 0) if record["installed"] else _next_sequence(Path(app_home))
            if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
                raise ComponentError("模块安装顺序记录损坏。")
            dependencies = [FIJI_ID] if module["needs_fiji"] else []
            if descriptor.get("schema_version", 0) >= 3:
                dependencies.append(MODEL_ID)
            if descriptor.get("schema_version") == 4:
                dependencies.append(SCIENCE_ID)
            if catalog is not None:
                for identity in dependencies:
                    wanted = descriptor["science_runtime"]["version"] if identity == SCIENCE_ID else MODEL_VERSION if identity == MODEL_ID else None
                    if identity == FIJI_ID:
                        wanted = metadata.get("dependencies", {}).get(FIJI_ID) or _record(_managed_root(app_home, FIJI_ID)).get("component_version")
                    sources[identity] = catalog.select(identity, wanted)
            required_fiji = metadata.get("dependencies", {}).get(FIJI_ID)
            if repair and record.get("fiji_version"):
                if required_fiji is not None and required_fiji != record["fiji_version"]:
                    raise ComponentError("修复源的 Fiji 依赖与已安装模块记录不一致。")
                required_fiji = record["fiji_version"]
            newly_installed = [identity for identity in dependencies if not _record(_managed_root(app_home, identity))["installed"]]
            previous_fiji = _activation_records(_managed_root(app_home, FIJI_ID)) if FIJI_ID in dependencies and FIJI_ID not in newly_installed else None
            try:
                if FIJI_ID in dependencies:
                    _ensure_fiji(app_home, sources, original_source, repair=repair, required_version=required_fiji, progress=report, cancel=cancel, checked=checks.get(FIJI_ID))
                if MODEL_ID in dependencies and checks.get(MODEL_ID, {}).get("status") != "intact":
                    _ensure_model(app_home, sources, original_source, progress=report, cancel=cancel, checked=checks.get(MODEL_ID))
                if SCIENCE_ID in dependencies:
                    _ensure_science(app_home, sources, original_source, descriptor["science_runtime"],
                                    checked=checks.get(SCIENCE_ID), progress=report, cancel=cancel)
                if repair and checks.get(module_id, {}).get("status") == "intact":
                    result = component_status(app_home, module_id=module_id)
                    result["integrity_check"] = "sha256"
                else:
                    result = _activate(root, source, metadata, manifest, sequence=sequence,
                                       original_source=original_source, progress=report)
            except (OSError, ValueError, TypeError, ComponentError, tarfile.TarError, EOFError) as exc:
                if previous_fiji is not None:
                    try:
                        _restore_fiji_activation(_managed_root(app_home, FIJI_ID), previous_fiji)
                    except (OSError, ValueError, TypeError, ComponentError) as cleanup_error:
                        raise ComponentError(str(exc) + "；Fiji 激活记录恢复失败：" + str(cleanup_error)) from exc
                for identity in newly_installed:
                    try:
                        _remove_owned_tree(_managed_root(app_home, identity))
                    except OSError as cleanup_error:
                        raise ComponentError(str(exc) + "；本次新增共享组件回滚失败：" + str(cleanup_error)) from exc
                if not _defer_science_cleanup:
                    try:
                        _cleanup_science_generations(app_home)
                    except (OSError, ValueError, TypeError, ComponentError) as cleanup_error:
                        raise ComponentError(str(exc) + "；科学运行环境清理失败：" + str(cleanup_error)) from exc
                raise
            if not _defer_science_cleanup:
                try:
                    _cleanup_science_generations(app_home)
                except (OSError, ValueError, TypeError, ComponentError) as cleanup_error:
                    result["cleanup_warning"] = str(cleanup_error)
            resolved = component_status(app_home, module_id=module_id)
            result["runtime_descriptor"] = resolved["runtime_descriptor"]
            if resolved["status"] != "ready":
                raise ComponentError(resolved["message"])
            result["message"] = "同版本组件修复完成。" if repair else "模块更新完成。" if updating else "模块安装完成。"
            result["operation"] = operation
            try:
                _notify(progress, 1.0, result["message"])
            except Exception:
                pass
            return dict(module, **result)
    except (OSError, ValueError, TypeError, ComponentError, tarfile.TarError, EOFError, AttributeError) as exc:
        status = "unconfigured" if isinstance(exc, CatalogUnconfigured) else "blocked" if isinstance(exc, ComponentBusy) else "cancelled" if isinstance(exc, ComponentCancelled) else "failed"
        return {"id": module_id, "status": status, "message": str(exc),
                "runtime_descriptor": None, "previous_module_preserved": True}


def install_component(app_home, seed_path=None, progress=None, module_id=COMPONENT_ID, *, cancel=None, configuration=None):
    return _maintain(app_home, seed_path, operation="install", progress=progress, module_id=module_id, cancel=cancel, configuration=configuration)


def repair_component(app_home, seed_path=None, progress=None, module_id=COMPONENT_ID, *, cancel=None, configuration=None):
    return _maintain(app_home, seed_path, operation="repair", progress=progress, module_id=module_id, cancel=cancel, configuration=configuration)


def update_component(app_home, seed_path=None, progress=None, module_id=COMPONENT_ID, *, cancel=None, configuration=None, expected_target=None):
    return _maintain(app_home, seed_path, operation="update", progress=progress, module_id=module_id, cancel=cancel, configuration=configuration, expected_target=expected_target)


def _fiji_users(app_home):
    """Count installed records, including damaged modules and retained catalog IDs."""
    components = Path(app_home) / "components"
    if components.is_symlink():
        raise ComponentError("受管理组件目录不得为符号链接。")
    users = []
    catalog = {module["id"]: module for module in list_modules()}
    for root in sorted(components.iterdir()) if components.exists() else []:
        if root.name in SHARED_IDS:
            continue
        if root.is_symlink():
            raise ComponentError("受管理组件目录不得为符号链接。")
        record = _record(root)
        needs_fiji = catalog[root.name]["needs_fiji"] if root.name in catalog else record.get("needs_fiji", True)
        if record["installed"] and needs_fiji:
            users.append(root.name)
    return users


def _model_users(app_home):
    """Installed records own dependencies even when their runtime is damaged."""
    components = Path(app_home) / "components"
    if components.is_symlink():
        raise ComponentError("受管理组件目录不得为符号链接。")
    users = []
    for root in sorted(components.iterdir()) if components.exists() else []:
        if root.name in SHARED_IDS:
            continue
        if root.is_symlink():
            raise ComponentError("受管理组件目录不得为符号链接。")
        record = _record(root)
        if record["installed"] and (root.name in (COMPONENT_ID, COUNTING_MODULE_ID) or record.get("model_component")):
            users.append(root.name)
    return users


def _remove_owned_tree(path):
    if path.is_symlink():
        # Removing the link never follows or deletes its external target.
        path.unlink()
    elif path.exists():
        if not path.is_dir():
            raise ComponentError("受管理路径必须为目录。")
        shutil.rmtree(path)


def _retain_legacy_fiji(app_home, root, progress):
    if fiji_status(app_home)["installed"]:
        return
    try:
        current = _current(root, root.name)
    except (OSError, ValueError, TypeError, ComponentError):
        current = None
    if current is None or not current[1].is_dir():
        # A broken pointer must not silently delete an unseparated legacy Fiji.
        # Inspect owned directories only; split modules contain no Fiji payload.
        generations = root / "generations"
        if generations.is_dir() and not generations.is_symlink():
            for directory in generations.iterdir():
                source = directory / "Fiji"
                if (directory.is_dir() and not directory.is_symlink()
                        and source.is_dir() and not source.is_symlink()):
                    raise ComponentError("无法定位旧版 Fiji 的有效激活记录；请先修复当前模块，再删除。")
        return
    _state, directory = current
    source = directory / "Fiji"
    if not source.exists():
        return  # A split module owns no Fiji payload, even if its metadata broke.
    if source.is_symlink():
        raise ComponentError("旧 Fiji 目录不得为符号链接。")
    valid, relative = False, None
    try:
        runtime = directory / "runtime.json"
        if runtime.is_symlink():
            raise ComponentError("旧运行时描述不得为符号链接。")
        descriptor = json.loads(runtime.read_text(encoding="utf-8"))
        if not isinstance(descriptor, dict) or descriptor.get("schema_version") != 1:
            raise ComponentError("旧运行时描述损坏。")
        relative = _relative_path(descriptor.get("fiji_launcher"))
        if relative.parts[0] != "Fiji":
            raise ComponentError("旧 Fiji 目录格式不受支持。")
        original = _read_manifest(directory, root.name)
        actual = _inventory(source, hashes=True)
        expected = {key[len("Fiji/"):]: value for key, value in original["entries"].items() if key.startswith("Fiji/")}
        valid = actual == expected
    except (OSError, ValueError, TypeError, ComponentError):
        # A damaged legacy Fiji may be retained for the user's subsequent keep /
        # remove decision. It receives no executable current.json activation.
        valid = False
    fiji_root = _managed_root(app_home, FIJI_ID, create=True)
    if not valid:
        generations = fiji_root / "generations"
        if generations.is_symlink():
            raise ComponentError("组件代目录不得为符号链接。")
        generations.mkdir(exist_ok=True)
        retained = generations / ("retained-" + uuid.uuid4().hex)
        _notify(progress, 0.15, "正在保留待修复的共享 Fiji…")
        try:
            shutil.copytree(source, retained / "Fiji", symlinks=True)
            _atomic_json(fiji_root / "installation.json", {"schema_version": 1, "component_id": FIJI_ID,
                "component_version": "20260718.0417", "source": "legacy-installed-component",
                "install_seq": 0, "retained_generation": retained.name, "verified": False})
        except (OSError, ValueError, TypeError):
            _remove_owned_tree(retained)
            raise
        return
    temporary = fiji_root / ("legacy-source-" + uuid.uuid4().hex)
    temporary.mkdir()
    try:
        _notify(progress, 0.15, "正在独立保留共享 Fiji…")
        shutil.copytree(source, temporary / "Fiji", symlinks=True)
        _atomic_json(temporary / "runtime.json", {"schema_version": 2, "component_id": FIJI_ID,
            "component_version": "20260718.0417", "fiji_launcher": relative.as_posix(),
            "distribution_snapshot": "20260718-0417"})
        manifest = build_manifest(temporary, FIJI_ID)
        _activate(fiji_root, temporary, {}, manifest, sequence=0, original_source="legacy-installed-component")
    finally:
        _remove_owned_tree(temporary)


def remove_component(app_home, module_id=COMPONENT_ID, progress=None):
    try:
        get_module(module_id)
        with _component_lock(app_home, exclusive=True, module_id=module_id) as root:
            cache = Path(app_home) / "cache"
            if cache.is_symlink():
                raise ComponentError("缓存目录不得为符号链接。")
            for path in (cache / "modules" / module_id, cache / module_id):
                if path.parent.is_symlink():
                    raise ComponentError("模块缓存父目录不得为符号链接。")
            _retain_legacy_fiji(app_home, root, progress)
            _retain_legacy_model(app_home, root, progress)
            _notify(progress, 0.5, "正在删除模块及其专用运行环境…")
            _remove_owned_tree(root)
            for path in (cache / "modules" / module_id, cache / module_id):
                _remove_owned_tree(path)
            runtime_cleanup_warning = None
            try:
                _cleanup_science_generations(app_home)
            except (OSError, ValueError, TypeError, ComponentError) as cleanup_error:
                runtime_cleanup_warning = str(cleanup_error)
            users = _fiji_users(app_home)
            model_users = _model_users(app_home)
            model_installed = model_status(app_home)["installed"]
            result = {"id": module_id, "status": "removed", "installed": False,
                      "runtime_descriptor": None, "message": "模块已删除，历史结果已保留。",
                      "remaining_fiji_users": users, "offer_remove_fiji": not users and fiji_status(app_home)["installed"],
                      "remaining_model_users": model_users, "offer_remove_model": not model_users and model_installed,
                      "model_retained": model_installed, "model_retained_for_users": bool(model_users and model_installed)}
            if runtime_cleanup_warning:
                result["cleanup_warning"] = runtime_cleanup_warning
            try:
                _notify(progress, 1.0, result["message"])
            except Exception:
                pass
            return result
    except (OSError, ValueError, TypeError, ComponentError) as exc:
        return {"id": module_id, "status": "blocked" if isinstance(exc, ComponentBusy) else "failed",
                "message": str(exc), "runtime_descriptor": None}


def remove_fiji(app_home, progress=None):
    try:
        with _component_lock(app_home, exclusive=True, module_id=FIJI_ID) as root:
            users = _fiji_users(app_home)
            if users:
                return {"status": "blocked", "message": "仍有已安装模块需要 Fiji。", "remaining_fiji_users": users}
            cache = Path(app_home) / "cache"
            if cache.is_symlink():
                raise ComponentError("缓存目录不得为符号链接。")
            _remove_owned_tree(root)
            _remove_owned_tree(cache / "fiji")
            try:
                _notify(progress, 1.0, "共享 Fiji 已删除。")
            except Exception:
                pass
            return {"id": FIJI_ID, "status": "removed", "installed": False, "message": "共享 Fiji 已删除。"}
    except (OSError, ValueError, TypeError, ComponentError) as exc:
        return {"id": FIJI_ID, "status": "blocked" if isinstance(exc, ComponentBusy) else "failed", "message": str(exc)}


def remove_model(app_home, progress=None):
    """Explicit last-consumer cleanup; ordinary module removal always keeps it."""
    try:
        with _component_lock(app_home, exclusive=True, module_id=MODEL_ID) as root:
            users = _model_users(app_home)
            if users:
                return {"id": MODEL_ID, "status": "blocked", "message": "仍有已安装模块需要共享 Cellpose 模型。",
                        "remaining_model_users": users}
            _remove_owned_tree(root)
            try:
                _notify(progress, 1.0, "共享 Cellpose 模型已删除。")
            except Exception:
                pass  # The removal has completed; notification cannot undo it.
            return {"id": MODEL_ID, "status": "removed", "installed": False, "message": "共享 Cellpose 模型已删除。"}
    except (OSError, ValueError, TypeError, ComponentError) as exc:
        return {"id": MODEL_ID, "status": "blocked" if isinstance(exc, ComponentBusy) else "failed", "message": str(exc)}
