"""Superseded update cleanup cannot reach installed components or external paths."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from leap_desktop.paths import ensure_app_home
from leap_desktop.update_manager import discard_staged_updates


class UpdateStagingCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="leap-staging-cleanup-")
        self.root = Path(self.temporary.name).resolve()
        self.home = ensure_app_home(self.root / "home")
        self.plan = {"schema_version": 1, "id": "a" * 32}
        self.staged = self.home / "updates/staged" / self.plan["id"]
        self.staged.parent.mkdir(parents=True)
        self.active = self.home / "components/science-runtime/generations/active"
        self.active.mkdir(parents=True)
        (self.active / "keep").write_text("unchanged")

    def tearDown(self):
        self.temporary.cleanup()

    def test_only_selected_staging_is_removed_and_child_symlinks_are_not_followed(self):
        self.staged.mkdir()
        (self.staged / "package.tar.gz").write_bytes(b"staged")
        (self.staged / "external-link").symlink_to(self.active, target_is_directory=True)
        other = self.staged.with_name("b" * 32)
        other.mkdir()
        result = discard_staged_updates(self.home, self.plan)
        self.assertEqual(result["status"], "discarded", result)
        self.assertFalse(self.staged.exists())
        self.assertTrue(other.exists())
        self.assertEqual((self.active / "keep").read_text(), "unchanged")
        self.assertEqual(discard_staged_updates(self.home, self.plan)["status"], "discarded")

    def test_traversal_redirect_and_symlink_root_are_rejected(self):
        self.assertEqual(discard_staged_updates(self.home, dict(self.plan, id="../../components"))["status"], "failed")
        self.assertEqual(discard_staged_updates(self.home, dict(self.plan, staging_dir=str(self.active)))["status"], "failed")
        self.staged.symlink_to(self.active, target_is_directory=True)
        self.assertEqual(discard_staged_updates(self.home, self.plan)["status"], "failed")
        self.assertEqual((self.active / "keep").read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
