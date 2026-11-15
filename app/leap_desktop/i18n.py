"""Display-only Chinese/English localization; scientific identifiers stay intact.

Chinese application messages are canonical. English translations match complete
messages or audited templates, never arbitrary words inside paths or logs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from string import Formatter
import uuid

from PySide6.QtCore import QObject, Property, QTranslator, Signal, Slot

from .paths import ensure_app_home, resource_root

SUPPORTED_LANGUAGES = ("en", "zh")
DEFAULT_LANGUAGE = "en"

_QT_BUTTON_CONTEXTS = frozenset(("QPlatformTheme", "QMessageBox", "QDialogButtonBox"))
_QT_ZH_BUTTONS = {
    "OK": "确定", "&OK": "确定", "Yes": "是", "&Yes": "是", "No": "否", "&No": "否",
    "Cancel": "取消", "&Cancel": "取消", "Close": "关闭", "&Close": "关闭",
    "Save": "保存", "&Save": "保存", "Open": "打开", "&Open": "打开",
    "Discard": "放弃", "&Discard": "放弃", "Apply": "应用", "Reset": "重置",
    "Help": "帮助", "Abort": "中止", "Retry": "重试", "Ignore": "忽略",
    "Yes to All": "全部选是", "Yes to &All": "全部选是", "No to All": "全部选否",
    "N&o to All": "全部选否", "Restore Defaults": "恢复默认设置",
}


class TranslationCatalog:
    """Load the reviewed message dictionary and compile anchored templates."""

    def __init__(self, path=None):
        path = Path(path) if path is not None else resource_root() / "app/leap_desktop/locales/en.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("language") != "en":
            raise ValueError("Invalid English translation catalog")
        self.messages = value["messages"]
        if not isinstance(self.messages, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in self.messages.items()):
            raise ValueError("Translation messages must be nonempty strings")
        self.templates = []
        formatter = Formatter()
        for entry in value.get("templates", []):
            source, target = entry["source"], entry["target"]
            pattern, fields, literal_length = [], [], 0
            for literal, name, spec, conversion in formatter.parse(source):
                pattern.append(re.escape(literal))
                literal_length += len(literal)
                if name is not None:
                    if spec or conversion or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or name in fields:
                        raise ValueError("Unsupported translation template field")
                    fields.append(name)
                    pattern.append("(?P<" + name + ">[\\s\\S]+?)")
            target_fields = {name for _, name, _, _ in formatter.parse(target) if name is not None}
            translated_fields = set(entry.get("translate_fields", []))
            if target_fields != set(fields) or not translated_fields.issubset(fields) or not literal_length:
                raise ValueError("Translation template fields do not match")
            self.templates.append((literal_length, re.compile("\\A" + "".join(pattern) + "\\Z"), target, translated_fields))
        self.templates.sort(key=lambda item: item[0], reverse=True)

    def translate(self, text):
        if not isinstance(text, str):
            return text
        exact = self.messages.get(text)
        if exact is not None:
            return exact
        for _, pattern, target, translated_fields in self.templates:
            match = pattern.fullmatch(text)
            if match is not None:
                values = match.groupdict()
                for field in translated_fields:
                    # Only explicitly named application-owned fields recurse.
                    values[field] = self.translate(values[field])
                return target.format_map(values)
        return text


class _EnglishTranslator(QTranslator):
    def __init__(self, catalog, parent):
        super().__init__(parent)
        self.catalog = catalog

    def isEmpty(self):
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        return self.catalog.translate(sourceText)


class _ChineseButtonsTranslator(QTranslator):
    def isEmpty(self):
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if context in _QT_BUTTON_CONTEXTS:
            return _QT_ZH_BUTTONS.get(sourceText, sourceText)
        return sourceText


class LocaleManager(QObject):
    """Persist the user's display language and refresh Qt plus dynamic getters."""

    changed = Signal(str)

    def __init__(self, apphome, parent=None):
        super().__init__(parent)
        self.apphome = ensure_app_home(apphome)
        self.settings_path = self.apphome / "settings.json"
        self.catalog = TranslationCatalog()
        self._language = DEFAULT_LANGUAGE
        self._settings = {"schema_version": 1, "language": DEFAULT_LANGUAGE}
        self.load_error = ""
        self._app = None
        self._engine = None
        self._installed_translator = None
        self._english = _EnglishTranslator(self.catalog, self)
        self._chinese_buttons = _ChineseButtonsTranslator(self)
        if self.settings_path.is_symlink():
            raise ValueError("语言设置文件不能是符号链接。")
        if self.settings_path.exists():
            try:
                stored = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if not isinstance(stored, dict) or stored.get("schema_version") != 1 or stored.get("language") not in SUPPORTED_LANGUAGES:
                    raise ValueError("Unsupported language settings")
                self._settings = stored
                self._language = stored["language"]
            except (OSError, ValueError) as exc:
                # Language settings cannot prevent the scientific application
                # from opening. Preserve the damaged file until an explicit
                # language selection replaces it.
                self.load_error = str(exc)
        else:
            self._save_settings(self._settings)

    language = Property(str, lambda self: self._language, notify=changed)

    def _save_settings(self, settings):
        if self.settings_path.is_symlink():
            raise ValueError("语言设置文件不能是符号链接。")
        temporary = self.settings_path.with_name(".settings-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(settings, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.settings_path)
        finally:
            temporary.unlink(missing_ok=True)

    def preference(self, key, default=None):
        return self._settings.get(key, default)

    def set_preference(self, key, value):
        """All UI preferences share the same atomic settings writer."""
        settings = {**self._settings, key: value}
        self._save_settings(settings)
        self._settings = settings

    def install_on(self, app, engine=None):
        if self._app is not None and self._installed_translator is not None:
            self._app.removeTranslator(self._installed_translator)
        self._app, self._engine = app, engine
        self._installed_translator = None
        self._apply_translator()

    def _apply_translator(self):
        if self._app is not None:
            if self._installed_translator is not None:
                self._app.removeTranslator(self._installed_translator)
            self._installed_translator = self._english if self._language == "en" else self._chinese_buttons
            self._app.installTranslator(self._installed_translator)
        if self._engine is not None:
            self._engine.retranslate()

    @Slot(str)
    def set_language(self, language):
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("界面语言仅支持英文（en）和中文（zh）。")
        if language == self._language and not self.load_error:
            return
        settings = {**self._settings, "schema_version": 1, "language": language}
        self._save_settings(settings)
        self._settings, self._language, self.load_error = settings, language, ""
        self._apply_translator()
        self.changed.emit(language)

    def translate(self, text):
        return self.catalog.translate(text) if self._language == "en" else text
