"""Application relocation and ownership-scoped, deferred uninstallation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile

from .paths import APPLICATION_ID, ensure_app_home, ensure_config_home
from .storage import migrate_app_data

# Runs are the sole product task/result store. Other user files are never derived
# from provenance, source-path strings, export destinations or downloaded DMGs.
BUNDLE_ID = "org.projectleap.prototype.d"
REMOVABLE_DATA_ENTRIES = ("components", "packages", "cache", "settings.json", "install-order.json", "module-health.json")


def current_application_bundle(executable=None):
    executable = Path(executable or sys.executable).absolute()
    for candidate in executable.parents:
        if candidate.suffix == ".app":
            if candidate.is_symlink() or not (candidate / "Contents/Info.plist").is_file():
                raise ValueError("无法确认当前应用包的归属。")
            with (candidate / "Contents/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            if (info.get("CFBundleIdentifier") != BUNDLE_ID or
                    executable != candidate / "Contents/MacOS" / info.get("CFBundleExecutable", "")):
                raise ValueError("当前可执行文件不属于 Project Leap 应用包。")
            if not os.access(candidate.parent, os.W_OK):
                raise ValueError("当前应用包所在位置不可写；请先将应用移至可写位置。")
            return candidate.resolve()
    raise ValueError("卸载仅适用于当前已打包的应用。")


def _identity(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("卸载目标不能是符号链接。")
    info = path.stat()
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino}


def prepare_uninstall(paths, keep_results, *, executable=None):
    """Construct exact owned deletion targets; perform no deletion."""
    bundle = current_application_bundle(executable)
    home = ensure_app_home(paths.home, create=False)
    config = ensure_config_home(paths.config_home, create=False)
    marker = json.loads((home / "application.json").read_text(encoding="utf-8"))
    if marker.get("config_dir") != str(config):
        raise ValueError("应用数据与固定配置目录不匹配。")
    roots = (home, config, bundle)
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(roots) for b in roots[i + 1:]):
        raise ValueError("应用、数据与配置目录必须相互独立。")
    targets = [bundle]
    if keep_results:
        targets.extend(home / name for name in REMOVABLE_DATA_ENTRIES if (home / name).exists() or (home / name).is_symlink())
        runs = home / "runs"
        paths.check_owned_directory(runs)
        for run in runs.iterdir():
            if not run.is_dir() or run.is_symlink():
                continue
            record = run / "job.json"
            if record.is_symlink():
                continue
            try:
                job = json.loads(record.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(job, dict) and job.get("status") == "completed" and (run / "cache").exists():
                targets.append(run / "cache")
    else:
        targets.append(home)
    library = Path.home() / "Library"
    for relative in (f"Caches/{BUNDLE_ID}", f"Preferences/{BUNDLE_ID}.plist",
                     f"Saved Application State/{BUNDLE_ID}.savedState", f"HTTPStorages/{BUNDLE_ID}"):
        native = library / relative
        if not paths.is_isolated and (native.exists() or native.is_symlink()):
            targets.append(native)
    # Configuration is removed last, so its lock inode survives all data removal.
    targets.append(config)
    return {"schema_version": 1, "application": APPLICATION_ID, "keep_results": bool(keep_results),
            "retained_data_dir": str(home) if keep_results else "", "targets": [_identity(path) for path in targets]}


def _helper_script(plan, parent_pid, helper_dir):
    quote = shlex.quote
    stat_command = "/usr/bin/stat -f '%d:%i'" if sys.platform == "darwin" else "/usr/bin/stat -c '%d:%i'"
    lines = ["#!/bin/sh", "set -eu", "umask 077", "count=0",
             f"while /bin/kill -0 {int(parent_pid)} 2>/dev/null; do",
             "  count=$((count + 1))", "  [ \"$count\" -le 600 ] || exit 70", "  /bin/sleep 1", "done"]
    for item in plan["targets"]:
        path = quote(item["path"])
        expected = quote(f"{item['device']}:{item['inode']}")
        lines += [f"[ ! -L {path} ] || exit 71", f"[ \"$({stat_command} {path})\" = {expected} ] || exit 72"]
    for item in plan["targets"]:
        lines.append("/bin/rm -rf -- " + quote(item["path"]))
    lines.append("/bin/rm -rf -- " + quote(str(helper_dir)))
    return "\n".join(lines) + "\n"


def schedule_uninstall(paths, keep_results, *, executable=None, parent_pid=None):
    """Inherit the same current and legacy locks used by component maintenance."""
    from .maintenance import ComponentBusy, ComponentError, maintenance_lock_fds
    helper_dir = None
    launched = False
    try:
        with maintenance_lock_fds(paths.home) as descriptors:
            plan = prepare_uninstall(paths, keep_results, executable=executable)
            helper_dir = Path(tempfile.mkdtemp(prefix="project-leap-uninstall-"))
            script = helper_dir / "cleanup.sh"
            script.write_text(_helper_script(plan, os.getpid() if parent_pid is None else parent_pid, helper_dir), encoding="utf-8")
            script.chmod(0o700)
            with (helper_dir / "cleanup.log").open("wb") as log:
                worker = subprocess.Popen(["/bin/sh", str(script)], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                          start_new_session=True, pass_fds=descriptors)
            launched = True
            return {"status": "scheduled", "message": "卸载已安排，将在应用退出后执行。", "helper_pid": worker.pid,
                    "retained_data_dir": plan["retained_data_dir"], "failure_log": str(helper_dir / "cleanup.log")}
    except (OSError, ValueError, TypeError, ComponentError, plistlib.InvalidFileException) as exc:
        return {"status": "blocked" if isinstance(exc, ComponentBusy) else "failed", "message": str(exc)}
    finally:
        if helper_dir is not None and not launched:
            shutil.rmtree(helper_dir)
