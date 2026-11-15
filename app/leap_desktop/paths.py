"""Application-owned data and a fixed, independently located configuration root."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path, PurePosixPath

APPLICATION_ID = "org.projectleap.prototype"
_BOUND_CONFIGS = {}


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def _isolated_config(home):
    home = Path(home).expanduser().absolute()
    return home.parent / ("." + home.name + ".leap-settings")


def ensure_app_home(path, create=True) -> Path:
    root = Path(path).expanduser().absolute()
    if root.is_symlink():
        raise ValueError("应用数据目录不能是符号链接。")
    resolved = root.resolve()
    protected = (Path.home() / "Desktop", Path.home() / "Applications" / "Project Leap 2D Support",
                 Path.home() / "Library" / "Caches" / "IHC2DAnalysis")
    if any(resolved == value.resolve() or value.resolve() in resolved.parents for value in protected):
        raise ValueError("应用数据目录必须与桌面材料及现有运行环境分开。")
    root = resolved
    marker = root / "application.json"
    if marker.exists():
        if marker.is_symlink():
            raise ValueError("应用身份标记不能是符号链接。")
        value = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("application") != APPLICATION_ID or value.get("schema_version") != 1:
            raise ValueError("该目录不属于 Project Leap。")
    elif root.exists() and any(root.iterdir()):
        raise ValueError("请选择新的空目录作为应用数据目录。")
    elif create:
        root.mkdir(parents=True, exist_ok=True)
        write_json(marker, {"schema_version": 1, "application": APPLICATION_ID})
    return root


def _configured_app_home(path):
    root = Path(path).expanduser().absolute()
    if not root.is_dir() or not (root / "application.json").is_file():
        raise ValueError("已配置的应用数据目录或身份标记不存在，请恢复该目录后重新启动。")
    return ensure_app_home(root, create=False)


def ensure_config_home(path, *, create=True):
    root = Path(path).expanduser().absolute()
    if root.is_symlink():
        raise ValueError("应用配置目录不能是符号链接。")
    root = root.resolve()
    marker = root / "configuration.json"
    if marker.is_symlink():
        raise ValueError("应用配置身份标记不能是符号链接。")
    if marker.exists():
        state = json.loads(marker.read_text(encoding="utf-8"))
        if state != {"schema_version": 1, "application": APPLICATION_ID, "kind": "configuration"}:
            raise ValueError("该目录不属于本应用配置。")
    elif root.exists() and any(root.iterdir()):
        raise ValueError("请选择新的空目录作为应用配置目录。")
    elif create:
        root.mkdir(parents=True, exist_ok=True)
        write_json(marker, {"schema_version": 1, "application": APPLICATION_ID, "kind": "configuration"})
    return root


def maintenance_lock_path(app_home):
    """The lock inode lives outside movable data and removable modules."""
    raw_home = Path(app_home).expanduser().absolute()
    if raw_home.is_symlink():
        raise ValueError("应用数据目录不能是符号链接。")
    home = raw_home.resolve()
    known_config = _BOUND_CONFIGS.get(home)
    if known_config is not None:
        storage = known_config / "storage.json"
        if storage.exists():
            active = json.loads(storage.read_text(encoding="utf-8"))
            if Path(active["data_dir"]).resolve() != home:
                raise ValueError("应用数据位置已改变，请重新启动此应用窗口。")
    home = _configured_app_home(home) if known_config is not None else ensure_app_home(home)
    marker = home / "application.json"
    state = json.loads(marker.read_text(encoding="utf-8"))
    config = ensure_config_home(known_config or state.get("config_dir") or _isolated_config(home))
    if config == home or config.is_relative_to(home) or home.is_relative_to(config):
        raise ValueError("应用配置目录和数据目录必须相互独立。")
    if "config_dir" not in state:
        state["config_dir"] = str(config)
        write_json(marker, state)
    _BOUND_CONFIGS[home] = config
    return config / "maintenance.lock"


class AppPaths:
    def __init__(self, data_dir=None, *, config_dir=None):
        self.resources = resource_root()
        self._packaged = bool(getattr(sys, "frozen", False))
        self._bundle = next((path for path in Path(sys.executable).absolute().parents
                             if path.suffix == ".app" and (path / "Contents/Info.plist").is_file()), None) if self._packaged else None
        explicit_home = data_dir or os.environ.get("PROJECT_LEAP_HOME")
        explicit_config = config_dir or os.environ.get("PROJECT_LEAP_CONFIG_HOME")
        if explicit_home is not None:
            # Validate ownership/protected locations before creating any settings.
            explicit_home = ensure_app_home(explicit_home, create=False)
        default = Path.home() / "Library" / "Application Support" / "Project Leap Prototype D"
        if explicit_config is None and explicit_home is not None:
            marker = Path(explicit_home).expanduser() / "application.json"
            existing = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() and not marker.is_symlink() else {}
            explicit_config = existing.get("config_dir") or _isolated_config(explicit_home)
        default_config = default.with_name(default.name + " Settings").resolve()
        self.config_home = ensure_config_home(explicit_config or default_config)
        self.is_isolated = self.config_home != default_config
        self.settings_file = self.config_home / "storage.json"
        if self.settings_file.is_symlink():
            raise ValueError("应用存储设置不能是符号链接。")
        settings_present = self.settings_file.exists()
        stored = json.loads(self.settings_file.read_text(encoding="utf-8")) if settings_present else {}
        if settings_present and (not isinstance(stored, dict) or stored.get("schema_version") != 1
                                 or stored.get("application") != APPLICATION_ID
                                 or not isinstance(stored.get("data_dir"), str) or not stored["data_dir"]):
            raise ValueError("应用存储设置无效。")
        selected_home = Path(explicit_home or stored.get("data_dir") or default).expanduser().absolute()
        if stored.get("data_dir") and Path(stored["data_dir"]).resolve() != selected_home.resolve():
            raise ValueError("数据位置已改变；请使用固定配置目录重新启动应用。")
        self.home = _configured_app_home(selected_home) if stored else ensure_app_home(selected_home)
        if self.config_home == self.home or self.config_home.is_relative_to(self.home) or self.home.is_relative_to(self.config_home):
            raise ValueError("应用配置目录和数据目录必须相互独立。")
        marker = self.home / "application.json"
        state = json.loads(marker.read_text(encoding="utf-8"))
        previous_config = state.get("config_dir")
        if previous_config and Path(previous_config).resolve() != self.config_home:
            raise ValueError("该应用数据已绑定其他配置目录。")
        state["config_dir"] = str(self.config_home)
        write_json(marker, state)
        self.previous_homes = tuple(state.get("previous_data_dirs", []))
        _BOUND_CONFIGS[self.home] = self.config_home
        if not stored:
            write_json(self.settings_file, {"schema_version": 1, "application": APPLICATION_ID, "data_dir": str(self.home)})
        self.runs = self.home / "runs"
        self.cache = self.home / "cache"
        self.check_owned_directory(self.runs)
        self.check_owned_directory(self.cache)
        self.runs.mkdir(exist_ok=True)
        self.cache.mkdir(exist_ok=True)
        self.app = self.resources / "app"
        self.qml = self.app / "leap_desktop" / "qml" / "Main.qml"

    @property
    def native_library(self):
        if self._packaged:
            if self._bundle is None:
                raise RuntimeError("应用原生组件需要有效的应用程序包。")
            return self._bundle / "Contents/Frameworks/libLeapGlass.dylib"
        return self.resources / "runtime/build/libLeapGlass.dylib"

    @property
    def release_configuration(self):
        from .release_catalog import load_configuration
        return load_configuration(self.resources, self.native_library)

    def package_source(self, recorded_source=None):
        """Find an available offline catalog; maintenance verifies package bytes."""
        candidates = []
        if self._packaged and self._bundle is not None:
            candidates.append(self._bundle.parent / "Project Leap Packages")
        if isinstance(recorded_source, (str, os.PathLike)) and str(recorded_source):
            candidates.append(self.resolve_managed_path(recorded_source))
        if not self._packaged:
            candidates.append(self.resources / "artifacts/release-1.0.0/packages")
        for candidate in candidates:
            try:
                candidate = Path(candidate).expanduser().absolute()
                if candidate.is_symlink() or not candidate.is_dir():
                    continue
                catalog = candidate / "catalog.json"
                if catalog.is_symlink() or not catalog.is_file():
                    continue
                data = json.loads(catalog.read_text(encoding="utf-8"))
                if (not isinstance(data, dict) or data.get("schema_version") != 1
                        or not isinstance(data.get("packages"), dict) or not data["packages"]):
                    continue
                valid = True
                for item in data["packages"].values():
                    relative = item.get("path") if isinstance(item, dict) else None
                    if (not isinstance(relative, str) or not relative or "\\" in relative
                            or PurePosixPath(relative).is_absolute()
                            or any(part in ("", ".", "..") for part in relative.split("/"))):
                        valid = False
                        break
                    target = candidate / relative
                    if (target.is_symlink() or not target.exists()
                            or not target.resolve().is_relative_to(candidate.resolve())):
                        valid = False
                        break
                if valid:
                    return candidate.resolve()
            except (OSError, ValueError, TypeError, RuntimeError):
                continue
        return None

    def resolve_managed_path(self, value):
        """Adapt operational paths without rewriting historical provenance."""
        path = Path(value)
        for previous in sorted(self.previous_homes, key=len, reverse=True):
            old = Path(previous)
            if path.is_absolute() and path.is_relative_to(old):
                return self.home / path.relative_to(old)
        return path

    def check_owned_directory(self, path):
        path = Path(path)
        try:
            parts = path.relative_to(self.home).parts
        except ValueError as exc:
            raise ValueError("目录不属于本应用数据目录。") from exc
        current = self.home
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("受管理数据目录不能使用符号链接。")
        if current.exists() and not current.is_dir():
            raise ValueError("受管理路径必须为目录。")
        return path


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
