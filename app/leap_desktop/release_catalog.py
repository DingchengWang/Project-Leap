"""Authenticated component release metadata and HTTPS transport, without Qt.

The publisher signs the exact JSON payload bytes using Ed25519. Trust comes
from a public key embedded at build time, never from the downloaded document.
Every transport URL, including redirect destinations, must use HTTPS.
"""
from __future__ import annotations

import base64
import binascii
import ctypes
from dataclasses import dataclass
import json
from pathlib import Path
import re
import ssl
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener


class CatalogError(ValueError):
    pass


class CatalogUnconfigured(CatalogError):
    pass


@dataclass(frozen=True)
class ReleaseConfiguration:
    catalog_url: str = ""
    public_key: str = ""
    native_library: Path | None = None

    def require_configured(self):
        if not self.catalog_url or not self.public_key:
            raise CatalogUnconfigured("此版本尚未配置组件发布地址和签名公钥。")
        validate_url(self.catalog_url)
        _base64(self.public_key, 32, "发布公钥")


def load_configuration(resources, native_library):
    path = Path(resources) / "app/leap_desktop/release-config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise CatalogError("内置发布配置无效。")
    # A shipped JSON file cannot opt into HTTP or substitute native code.
    return ReleaseConfiguration(data.get("catalog_url", ""), data.get("public_key", ""), Path(native_library))


def validate_url(value):
    parsed = urlsplit(str(value))
    if (not parsed.hostname or parsed.username or parsed.password or parsed.fragment
            or parsed.scheme != "https"):
        raise CatalogError("组件发布和下载地址必须使用 HTTPS。")
    return str(value)


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Reject a downgrade before opening the destination, including every hop.
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    # python.org runtimes do not necessarily contain a default CA bundle.
    # This macOS application uses the OS-shipped trust bundle explicitly.
    context = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return build_opener(_SafeRedirect(), HTTPSHandler(context=context)).open(
        validate_url(url), timeout=30)


def _base64(value, length, label):
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise CatalogError(label + "编码无效。") from exc
    if len(decoded) != length:
        raise CatalogError(label + "长度无效。")
    return decoded


def verify_signature(payload, signature, configuration):
    key = _base64(configuration.public_key, 32, "发布公钥")
    signature_bytes = _base64(signature, 64, "目录签名")
    if configuration.native_library is None:
        raise CatalogError("组件签名验证组件不可用。")
    library = ctypes.CDLL(str(configuration.native_library))
    verify = library.leap_verify_ed25519
    verify.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                       ctypes.c_void_p, ctypes.c_size_t]
    verify.restype = ctypes.c_int32
    key_buffer = ctypes.create_string_buffer(key)
    signature_buffer = ctypes.create_string_buffer(signature_bytes)
    payload_buffer = ctypes.create_string_buffer(payload)
    if verify(key_buffer, len(key), signature_buffer, len(signature_bytes), payload_buffer, len(payload)) != 1:
        raise CatalogError("组件发布目录签名验证失败；未下载或更改组件。")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError("发布目录包含重复字段。")
        result[key] = value
    return result


HOST_API = 1
RELEASE_PLATFORM = "macos-arm64"
HOST_REQUIREMENT_FIELDS = ("host_api", "minimum_host_build", "platform")


def validate_host_requirements(item, *, require_current=False):
    """Validate the supported module interface and minimum application build."""
    api, minimum, platform = (item.get(key) for key in HOST_REQUIREMENT_FIELDS)
    if type(api) is not int or api != HOST_API:
        raise CatalogError("模块要求不受支持的主程序接口版本。")
    if platform != RELEASE_PLATFORM:
        raise CatalogError("模块平台与当前 macOS Apple Silicon 版本不兼容。")
    if type(minimum) is not int or minimum < 1:
        raise CatalogError("模块缺少有效的最低主程序构建版本。")
    if require_current:
        from .version import application_build
        current = application_build()
        if not isinstance(current, str) or not re.fullmatch(r"[0-9]+", current):
            raise CatalogError("无法确定当前主程序构建版本。")
        if int(current) < minimum:
            raise CatalogError("此模块要求主程序构建版本至少为 " + str(minimum) + "；请先更新 Project Leap。")
    return {key: item[key] for key in HOST_REQUIREMENT_FIELDS}


class ReleaseCatalog:
    def __init__(self, payload, configuration):
        from .modules import validate_module_id, get_module, SCIENCE_RUNTIME_ID, MODEL_COMPONENT_ID
        if (not isinstance(payload, dict) or payload.get("schema_version") != 2
                or not isinstance(payload.get("packages"), list)
                or not isinstance(payload.get("latest"), dict)):
            raise CatalogError("组件发布目录格式无效。")
        self.configuration = configuration
        self.latest = dict(payload["latest"])
        self.packages = {}
        for item in payload["packages"]:
            if not isinstance(item, dict):
                raise CatalogError("组件发布条目无效。")
            identity = validate_module_id(item.get("id"))
            if identity not in ("fiji", SCIENCE_RUNTIME_ID, MODEL_COMPONENT_ID):
                get_module(identity)
                validate_host_requirements(item)
            version = item.get("version", "")
            if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version):
                raise CatalogError("组件发布版本无效。")
            if ((identity, version) in self.packages or not isinstance(item.get("bytes"), int)
                    or isinstance(item["bytes"], bool) or item["bytes"] <= 0
                    or not re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", ""))
                    or not re.fullmatch(r"[a-f0-9]{64}", item.get("manifest_sha256", ""))):
                raise CatalogError("组件发布条目缺少唯一版本、有效长度或 SHA-256。")
            location = item.get("url", item.get("path"))
            if not isinstance(location, str) or not location:
                raise CatalogError("组件发布条目缺少下载地址。")
            address = validate_url(urljoin(configuration.catalog_url, location))
            self.packages[(identity, version)] = (address, dict(item))
        for identity, version in self.latest.items():
            validate_module_id(identity)
            if (identity, version) not in self.packages:
                raise CatalogError("组件最新版本没有对应的安装包。")

    def select(self, identity, version=None):
        version = version or self.latest.get(identity)
        try:
            return self.packages[(identity, version)]
        except KeyError as exc:
            raise CatalogError("发布目录中没有所需组件版本：" + identity + " " + str(version or "")) from exc


def fetch_catalog(configuration, *, cancel=None):
    configuration.require_configured()
    from .maintenance import _check_cancel
    _check_cancel(cancel)
    with open_url(configuration.catalog_url) as response:
        envelope_bytes = response.read(4 * 1024 * 1024 + 1)
    _check_cancel(cancel)
    if len(envelope_bytes) > 4 * 1024 * 1024:
        raise CatalogError("组件发布目录超过大小限制。")
    return read_signed_catalog(json.loads(envelope_bytes, object_pairs_hook=_object), configuration)


def read_signed_catalog(envelope, configuration):
    configuration.require_configured()
    if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
        raise CatalogError("组件发布目录签名封装无效。")
    try:
        payload = base64.b64decode(envelope.get("payload"), validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise CatalogError("组件发布目录载荷编码无效。") from exc
    verify_signature(payload, envelope.get("signature"), configuration)
    catalog = ReleaseCatalog(json.loads(payload, object_pairs_hook=_object), configuration)
    catalog.envelope = envelope
    return catalog
