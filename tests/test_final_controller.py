"""Final storage/lifecycle regressions using only isolated synthetic task data."""
from __future__ import annotations

from datetime import datetime as RealDatetime, timedelta, timezone
import threading
import unittest
from unittest.mock import patch

from test_counting_controller import CountingControllerFixture
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from leap_desktop import paths
from leap_desktop.i18n import LocaleManager


def frozen_datetime(value):
    class FrozenDatetime(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)
    return FrozenDatetime


class FinalStorageLifecycleTests(CountingControllerFixture, unittest.TestCase):
    def stored_task(self, identifier="stored-task"):
        run = self.paths.runs / identifier
        run.mkdir()
        record = {"id": identifier, "name": "synthetic task", "status": "completed",
                  "module_id": "cell-counting", "created_at": "2026-01-01T00:00:00Z"}
        paths.write_json(run / "job.json", record)
        (run / "retained-result.bin").write_bytes(b"saved result")
        self.controller._load_jobs()
        return run

    def test_one_calendar_month_handles_month_end_and_preserves_other_preferences(self):
        run = self.stored_task()
        controller = self.controller
        controller.locale.set_language("zh")
        controller.setAppearanceMode("dark")
        cases = [(RealDatetime(2026, 1, 31, 12, 34), (2026, 2, 28)),
                 (RealDatetime(2028, 1, 31, 12, 34), (2028, 2, 29)),
                 (RealDatetime(2026, 12, 31, 12, 34), (2027, 1, 31))]
        for now, expected_date in cases:
            with self.subTest(now=now), \
                    patch("leap_desktop.controller.datetime", frozen_datetime(now)), \
                    patch.object(controller, "_run_maintenance") as operation:
                controller._pending_delete_ids = [run.name]
                controller.confirmDeleteJobs(True)
                operation.assert_called_once()
            restored = LocaleManager(self.paths.home)
            expiry = RealDatetime.fromisoformat(restored.preference("delete_confirmation_until"))
            self.assertEqual((expiry.year, expiry.month, expiry.day), expected_date)
            self.assertEqual((expiry.hour, expiry.minute), (12, 34))
            self.assertIsNotNone(expiry.tzinfo)
            self.assertEqual(restored.language, "zh")
            self.assertEqual(restored.preference("appearance"), "dark")
        controller.locale.set_language("en")
        restored = LocaleManager(self.paths.home)
        self.assertEqual(restored.language, "en")
        self.assertEqual(restored.preference("appearance"), "dark")
        self.assertEqual(restored.preference("delete_confirmation_until"), expiry.isoformat())

    def test_restarted_controller_skips_confirmation_only_until_exact_expiry(self):
        run = self.stored_task()
        expiry = RealDatetime(2026, 3, 31, 16, 0, tzinfo=timezone.utc)
        self.controller.locale.set_preference("delete_confirmation_until", expiry.isoformat())
        restarted = self.make_controller()
        confirmations = []
        restarted.deleteConfirmationRequested.connect(confirmations.append)
        with patch.object(restarted, "_run_maintenance") as operation:
            with patch("leap_desktop.controller.datetime", frozen_datetime(expiry - timedelta(microseconds=1))):
                restarted.requestDeleteJobs([run.name])
            operation.assert_called_once()
            self.assertEqual(confirmations, [])
            with patch("leap_desktop.controller.datetime", frozen_datetime(expiry)):
                restarted.requestDeleteJobs([run.name])
            self.assertEqual(confirmations, [1])
            operation.assert_called_once()
            restarted.cancelDeleteJobs()

    def test_busy_storage_refresh_runs_after_worker_finishes(self):
        run = self.stored_task()
        controller = self.controller
        controller._busy = True
        controller.refreshStorageUsage()
        self.assertFalse(controller.storageUsageBusy)
        self.assertTrue(controller._storage_refresh_pending)
        controller._commit_finish("failed", "Synthetic worker has exited")
        self.wait_until(lambda: run.name in controller._storage_usage and not controller._maintenance_busy)
        self.assertGreater(controller._storage_usage[run.name], 0)
        self.assertFalse(controller._storage_refresh_pending)
        self.assertIn("storageLabel", controller.jobs[0])

    def test_refresh_deferred_during_maintenance_resumes_after_completion(self):
        run = self.stored_task()
        controller = self.controller
        controller._maintenance_busy = True
        controller._maintenance_operation = "task_cleanup"
        controller.refreshStorageUsage()
        self.assertTrue(controller._storage_refresh_pending)
        controller._maintenance_done({"status": "cleared", "message": "Synthetic cleanup complete"})
        self.wait_until(lambda: run.name in controller._storage_usage and not controller._maintenance_busy)
        self.assertFalse(controller._storage_refresh_pending)

    def test_quit_waits_for_late_preview_write_and_removes_cache_before_quitting(self):
        _, analysis = self.review_fixture()
        controller, editor = self.controller, self.controller.counting
        entered, release = threading.Event(), threading.Event()
        base = analysis.parent
        original_save = QImage.save

        def save_after_cancel(image, filename, *args, **kwargs):
            # draw() has already passed its cancellation check before save().
            # Hold this actual write past close(), then let it finish normally.
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release the synthetic render")
            return original_save(image, filename, *args, **kwargs)

        with patch.object(QImage, "save", new=save_after_cancel), \
                patch.object(QApplication, "quit") as quit_app:
            editor.setChannelVisible(1, False)
            self.wait_until(entered.is_set)
            try:
                controller.requestQuit()
                quit_app.assert_not_called()
                self.assertFalse(editor.drained)
            finally:
                release.set()
            self.wait_until(lambda: quit_app.call_count == 1)
            self.assertTrue(editor.drained)
            self.assertFalse((base / ".editor").exists())
            self.assertTrue((base / "channels/C1.png").is_file())
            self.assertEqual(self.source_fingerprint(), self.source_before)

    def test_quit_does_not_start_pending_cleanup_during_synchronous_close_signal(self):
        run = self.stored_task()
        controller = self.controller
        controller._pending_cleanup_ids.add(run.name)
        try:
            with patch.object(controller, "_run_maintenance") as maintenance, \
                    patch.object(QApplication, "quit") as quit_app:
                controller.requestQuit()
                maintenance.assert_not_called()
                quit_app.assert_called_once()
        finally:
            controller._pending_cleanup_ids.clear()


if __name__ == "__main__":
    unittest.main()
