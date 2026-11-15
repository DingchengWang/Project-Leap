"""Lightweight localization checks; no GUI windows or scientific runtime."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox
from leap_desktop import i18n

APP = None
CATALOG_PATH = ROOT / "app/leap_desktop/locales/en.json"


class LocaleManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use the suite's QApplication when present; never install a core-only
        # singleton during test discovery that would block later widget tests.
        global APP
        APP = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Leap-language-")
        self.home = Path(self.temp.name).resolve()
        self.manager = i18n.LocaleManager(self.home)

    def tearDown(self):
        if self.manager._installed_translator is not None:
            APP.removeTranslator(self.manager._installed_translator)
        self.temp.cleanup()

    def test_new_user_defaults_to_persisted_english(self):
        self.assertEqual(self.manager.language, "en")
        self.assertEqual(json.loads((self.home / "settings.json").read_text())["language"], "en")
        self.assertEqual(self.manager.translate("准备分析"), "Preparing analysis")

    def test_choice_survives_recreation_and_preserves_unrelated_settings(self):
        settings = self.home / "settings.json"
        settings.write_text(json.dumps({"schema_version": 1, "language": "en", "future_setting": "preserved"}))
        self.manager = i18n.LocaleManager(self.home)
        self.manager.set_language("zh")
        self.assertEqual(i18n.LocaleManager(self.home).language, "zh")
        self.assertEqual(json.loads(settings.read_text())["future_setting"], "preserved")
        self.assertEqual(self.manager.translate("准备分析"), "准备分析")

    def test_qt_switch_refreshes_static_text_dynamic_text_and_buttons(self):
        class Engine:
            calls = 0
            def retranslate(self):
                self.calls += 1
        engine = Engine()
        signals = []
        self.manager.changed.connect(signals.append)
        self.manager.install_on(APP, engine)
        self.assertEqual(QCoreApplication.translate("Main", "设置"), "Settings")
        self.assertEqual(QCoreApplication.translate("Main", "Unknown source"), "Unknown source")
        self.manager.set_language("zh")
        self.assertEqual(QCoreApplication.translate("Main", "设置"), "设置")
        self.assertEqual(QCoreApplication.translate("QPlatformTheme", "&Yes"), "是")
        self.assertEqual(QCoreApplication.translate("QMessageBox", "Cancel"), "取消")
        self.assertEqual(QCoreApplication.translate("Main", "Cancel"), "Cancel")
        buttons = QMessageBox.StandardButton
        dialog = QMessageBox()
        dialog.setStandardButtons(buttons.Yes | buttons.No | buttons.Ok | buttons.Cancel)
        for button, expected in ((buttons.Yes, "是"), (buttons.No, "否"), (buttons.Ok, "确定"), (buttons.Cancel, "取消")):
            self.assertEqual(dialog.button(button).text(), expected)
        dialog.deleteLater()
        self.assertEqual(self.manager.translate("正在拆分 GFAP：3/9"), "正在拆分 GFAP：3/9")
        self.manager.set_language("en")
        self.assertEqual(QCoreApplication.translate("Main", "设置"), "Settings")
        self.assertEqual(QCoreApplication.translate("QPlatformTheme", "&Yes"), "&Yes")
        self.assertEqual(self.manager.translate("正在拆分 GFAP：3/9"), "Splitting GFAP: 3/9")
        self.assertEqual(signals, ["zh", "en"])
        self.assertEqual(engine.calls, 3)

    def test_failed_save_does_not_change_language_or_existing_file(self):
        before = (self.home / "settings.json").read_bytes()
        changed = []
        self.manager.changed.connect(changed.append)
        with mock.patch.object(i18n.os, "replace", side_effect=PermissionError("write blocked")):
            with self.assertRaises(PermissionError):
                self.manager.set_language("zh")
        self.assertEqual(self.manager.language, "en")
        self.assertEqual((self.home / "settings.json").read_bytes(), before)
        self.assertEqual(changed, [])
        self.assertFalse(list(self.home.glob(".settings-*.tmp")))

    def test_invalid_language_is_rejected_without_changing_settings(self):
        before = (self.home / "settings.json").read_bytes()
        with self.assertRaises(ValueError):
            self.manager.set_language("fr")
        self.assertEqual((self.home / "settings.json").read_bytes(), before)

    def test_damaged_settings_preserved_until_explicit_selection(self):
        path = self.home / "settings.json"
        path.write_text("{broken")
        self.manager = i18n.LocaleManager(self.home)
        self.assertEqual(self.manager.language, "en")
        self.assertTrue(self.manager.load_error)
        self.assertEqual(path.read_text(), "{broken")
        self.manager.set_language("en")
        self.assertEqual(json.loads(path.read_text())["language"], "en")
        self.assertFalse(self.manager.load_error)

    def test_settings_symlink_is_never_followed(self):
        target = self.home / "original.txt"
        target.write_text("keep")
        path = self.home / "settings.json"
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.manager.set_language("zh")
        with self.assertRaises(ValueError):
            i18n.LocaleManager(self.home)
        self.assertEqual(target.read_text(), "keep")

    def test_templates_preserve_paths_biomarkers_dimensions_and_diagnostics(self):
        path = "/tmp/设置/脑切片 GFAP 01.tif"
        detail = "[Errno 13] permission denied: " + path
        self.assertEqual(self.manager.translate("组件含失效符号链接：" + path),
                         "The component contains a broken symbolic link: " + path)
        self.assertEqual(self.manager.translate("无法打开应用组件锁：" + detail),
                         "The application component lock could not be opened: " + detail)
        self.assertEqual(self.manager.translate(detail + "；未提交组件清理失败：" + path),
                         detail + "\nCleanup of uncommitted components failed: " + path)
        self.assertEqual(self.manager.translate("组件内容校验失败；安装源可能损坏。；本次新增 Fiji 回滚失败：" + detail),
                         "Component verification failed. The installation source may be damaged.\n"
                         "Rollback of the newly added Fiji component failed: " + detail)
        self.assertEqual(self.manager.translate(detail + "；未提交组件清理失败：" + path + "；本次新增 Fiji 回滚失败：" + path),
                         detail + "\nCleanup of uncommitted components failed: " + path +
                         "\nRollback of the newly added Fiji component failed: " + path)
        self.assertEqual(self.manager.translate("计算与比较 ROI 候选：已完成 90/90"),
                         "Computing and comparing ROI candidates: 90/90 complete")
        self.assertEqual(self.manager.translate("需要明确的 Z、Y、X 轴及可选 C/T 轴；实际 axes=TCZYX, shape=(2, 4, 8, 512, 512)。"),
                         "Explicit Z, Y, X axes with optional C/T axes are required; actual axes=TCZYX, shape=(2, 4, 8, 512, 512).")
        for value in (path, "DAPI", "eGFP", "GFAP", "KCNN2", "KCNJ10", "Ignore", "RAW LOG: 准备分析", "用户自定义中文诊断"):
            self.assertEqual(self.manager.translate(value), value)


class CatalogCoverageTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(CATALOG_PATH.read_text())
        self.catalog = i18n.TranslationCatalog()

    def test_catalog_is_loaded_from_resource_root_and_missing_catalog_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(i18n, "resource_root", return_value=root):
                with self.assertRaises(FileNotFoundError):
                    i18n.TranslationCatalog()
                destination = root / "app/leap_desktop/locales/en.json"
                destination.parent.mkdir(parents=True)
                destination.write_bytes(CATALOG_PATH.read_bytes())
                self.assertEqual(i18n.TranslationCatalog().translate("设置"), "Settings")

    def test_qml_and_native_navigation_sources_have_english_and_preserve_placeholders(self):
        found = []
        for path in (ROOT / "app/leap_desktop/qml").rglob("*.qml"):
            for match in re.finditer(r'qsTr\(\s*("(?:[^"\\]|\\.)*")\s*\)', path.read_text()):
                source = json.loads(match.group(1))
                found.append(source)
                self.assertIn(source, self.catalog.messages, (path, source))
                target = self.catalog.messages[source]
                self.assertEqual(sorted(re.findall(r"%[1-9n]", source)), sorted(re.findall(r"%[1-9n]", target)), source)
                self.assertFalse(re.search(r"[\u4e00-\u9fff]", target), source)
        native_path = ROOT / "app/leap_desktop/native_style.py"
        native_tree = ast.parse(native_path.read_text())
        navigation = next(node for node in native_tree.body
                          if isinstance(node, ast.FunctionDef) and node.name == "navigation_state")
        native_sources = {node.value for node in ast.walk(navigation)
                          if isinstance(node, ast.Constant) and isinstance(node.value, str)
                          and re.search(r"[\u4e00-\u9fff]", node.value)}
        for source in native_sources:
            self.assertIn(source, self.catalog.messages, (native_path, source))
            self.assertFalse(re.search(r"[\u4e00-\u9fff]", self.catalog.messages[source]), source)
        self.assertTrue({"设置", "功能与维护", "分析工作台", "任务与结果"}.issubset(set(found) | native_sources))

    def test_qml_chinese_text_is_wrapped_except_language_self_name(self):
        for path in (ROOT / "app/leap_desktop/qml").rglob("*.qml"):
            source = path.read_text()
            for match in re.finditer(r'"(?:[^"\\]|\\.)*"', source):
                value = json.loads(match.group())
                if re.search(r"[\u4e00-\u9fff]", value) and value != "中文":
                    prefix = source[max(0, match.start() - 80):match.start()]
                    self.assertRegex(prefix, r"qsTr\(\s*$", (path, value))

    def test_owned_python_messages_and_fstrings_have_catalog_coverage(self):
        templates = [item["source"] for item in self.raw["templates"]]
        for path in sorted((ROOT / "app/leap_desktop").glob("*.py")):
            name = path.name
            tree = ast.parse(path.read_text())
            parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(r"[\u4e00-\u9fff]", node.value):
                    if name == "i18n.py" and node.value in i18n._QT_ZH_BUTTONS.values():
                        continue
                    ancestor = parents.get(node)
                    raw_log = False
                    while ancestor is not None:
                        if isinstance(ancestor, ast.Call) and isinstance(ancestor.func, ast.Attribute) and ancestor.func.attr == "_append_log":
                            raw_log = True
                            break
                        ancestor = parents.get(ancestor)
                    if isinstance(parents.get(node), ast.JoinedStr) or raw_log:
                        continue
                    with self.subTest(file=name, line=node.lineno, text=node.value):
                        self.assertTrue(node.value in self.catalog.messages or any(node.value in source for source in templates))
                elif isinstance(node, ast.JoinedStr):
                    sample = "".join(str(value.value) if isinstance(value, ast.Constant) else "VALUE" for value in node.values)
                    if re.search(r"[\u4e00-\u9fff]", sample):
                        with self.subTest(file=name, line=node.lineno, text=sample):
                            self.assertFalse(re.search(r"[\u4e00-\u9fff]", self.catalog.translate(sample)))


if __name__ == "__main__":
    unittest.main()
