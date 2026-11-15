"""Targeted adapter contract tests; never execute real models or Fiji."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scientific_freeze_check", ROOT / "tools/check_scientific_core.py")
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "app"))
from project_leap_2d import desktop_paths


class ScientificFreezeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "original"
        self.engine = self.root / "engine"
        self.engine.mkdir()
        self.manifest = []

    def add(self, name, category, text):
        original = self.source / freeze.BASELINE_PREFIX / name
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text(text)
        target = self.engine / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        self.manifest.append({
            "relative_path": freeze.BASELINE_PREFIX + name,
            "category": category,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        })
        return target

    def inspect(self, approvals=None):
        baseline = self.root / "baseline.json"
        baseline.write_text(json.dumps({"source_root": str(self.source), "file_manifest": self.manifest}))
        return freeze.inspect_engine(baseline, self.engine, approvals=approvals or {})

    def test_changed_scientific_constant_cannot_be_approved(self):
        target = self.add("settings.py", "scientific_configuration_or_data_contract", "THRESHOLD = 2\n")
        target.write_text("THRESHOLD = 3\n")
        result = self.inspect({"settings.py": {
            "baseline_sha256": self.manifest[0]["sha256"], "current_sha256": freeze.sha256(target),
            "reason": "An approval must never waive a frozen category.",
        }})
        self.assertFalse(result["scientific_core_unchanged"])
        self.assertFalse(result["ok"])

    def test_modified_interface_requires_exact_review_and_reports_function(self):
        self.add("core.py", "scientific_core", "VALUE = 2\n")
        target = self.add("interface.py", "mixed_scientific_behavior_and_engineering_boundary", "def run():\n    return 1\n")
        target.write_text("def run():\n    return 2\n")
        result = self.inspect()
        self.assertFalse(result["engineering_review_complete"])
        delta = result["engineering_changes"][0]
        self.assertEqual(delta["changed_python_nodes"][0]["node"], "FunctionDef:run#1")
        approval = {"interface.py": {
            "baseline_sha256": self.manifest[-1]["sha256"], "current_sha256": freeze.sha256(target),
            "reason": "Fixture for hash-pinned engineering approval.",
        }}
        self.assertTrue(self.inspect(approval)["ok"])
        target.write_text("def run():\n    return 3\n")
        self.assertFalse(self.inspect(approval)["ok"])

    def test_changed_original_invalidates_baseline_evidence(self):
        self.add("core.py", "scientific_core", "VALUE = 2\n")
        (self.source / freeze.BASELINE_PREFIX / "core.py").write_text("VALUE = 3\n")
        result = self.inspect()
        self.assertTrue(result["scientific_core_unchanged"])
        self.assertFalse(result["original_engine_matches_stage0"])
        self.assertFalse(result["ok"])

    def test_new_adapter_is_not_hidden_by_unchanged_science(self):
        self.add("core.py", "scientific_core", "VALUE = 2\n")
        (self.engine / "adapter.py").write_text("MODEL_BATCH_SIZE = 1\n")
        result = self.inspect()
        self.assertTrue(result["scientific_core_unchanged"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["engineering_changes"][0]["path"], "adapter.py")

    def test_missing_model_or_symlink_fails_frozen_check(self):
        target = self.add("model.pt", "pinned_model_resource", "weights")
        target.unlink()
        self.assertFalse(self.inspect()["scientific_core_unchanged"])
        target.symlink_to(self.source / freeze.BASELINE_PREFIX / "model.pt")
        self.assertFalse(self.inspect()["scientific_core_unchanged"])


class CrossModuleLockContractTests(unittest.TestCase):
    def test_worker_uses_same_lock_as_maintenance(self):
        from leap_desktop import maintenance
        with tempfile.TemporaryDirectory(prefix="leap-component-contract-", dir="/private/tmp") as temporary:
            home = Path(temporary).resolve() / "app-home"
            lock = maintenance.component_lock_path(home)
            with patch.dict(desktop_paths.os.environ, {"PROJECT_LEAP_COMPONENT_LOCK": str(lock)}):
                with desktop_paths.component_usage_lock():
                    with self.assertRaises(maintenance.ComponentBusy):
                        with maintenance._component_lock(home, exclusive=True):
                            self.fail("Maintenance must not acquire the worker-held component")
                with maintenance._component_lock(home, exclusive=True):
                    with self.assertRaisesRegex(RuntimeError, "being installed or repaired"):
                        with desktop_paths.component_usage_lock():
                            self.fail("Worker must not enter during maintenance")
                with desktop_paths.component_usage_lock():
                    pass


if __name__ == "__main__":
    unittest.main()
