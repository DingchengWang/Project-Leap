"""Quit waits for result publication, not disposable preview work or QML ack."""
from __future__ import annotations

import copy
import json
import threading
import time
import unittest
from unittest.mock import patch

from test_counting_controller import CountingControllerFixture
from PySide6.QtCore import QThreadPool, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from leap_desktop import counting


class CountingQuitControllerTests(CountingControllerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.run, self.analysis = self.review_fixture()
        self.editor = self.controller.counting
        self.editor.addPoint(55, 44)
        self.original_points = copy.deepcopy(self.editor.points)
        self.job_before = (self.run / "job.json").read_bytes()

    def wait_without_frame_ack(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.application.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Controlled preview/publication worker did not reach its test boundary")

    def test_hidden_review_with_native_preview_awaiting_ack_can_quit_and_restore_draft(self):
        self.controller.setPage("settings")
        applied = self.editor.appliedImageUrl
        self.editor.setChannelVisible(1, False)
        self.wait_without_frame_ack(lambda: bool(self.editor.pendingFrameToken) and self.editor._active_task is None)
        self.assertEqual(self.controller.page, "settings")
        self.assertNotEqual(self.editor.imageUrl, applied)
        self.assertFalse(QImage(QUrl(self.editor.imageUrl).toLocalFile()).isNull())
        self.assertTrue(self.editor.busy)
        self.assertFalse(self.editor.confirmationInProgress)
        # This view change is still waiting for its debounce timer. close() must
        # flush it together with the retained manual edit and channel selection.
        self.editor.setView(1.75, 13.0, -8.0)
        with patch.object(QApplication, "quit") as quit_app, \
                patch("leap_desktop.controller.QMessageBox.information") as information, \
                patch.object(self.editor, "close", wraps=self.editor.close) as close:
            self.controller.requestQuit()
        information.assert_not_called()
        close.assert_called_once()
        quit_app.assert_called_once()
        self.assertFalse(self.editor.available)
        self.assertFalse(self.editor.busy)
        self.assertEqual(self.editor.pendingFrameToken, "")
        draft = json.loads((self.analysis.parent / "draft.json").read_text())
        self.assertEqual(draft["state"]["points"], self.original_points)
        self.assertEqual((draft["view"]["zoom"], draft["view"]["pan_x"], draft["view"]["pan_y"]), (1.75, 13.0, -8.0))
        self.assertFalse(next(channel for channel in draft["state"]["channels"] if channel["index"] == 1)["visible"])
        self.assertEqual((self.run / "job.json").read_bytes(), self.job_before)
        restarted = self.make_controller()
        restarted.openCountingJob(self.run.name)
        self.wait_until(lambda: not restarted.counting.busy)
        self.assertEqual(restarted.counting.points, self.original_points)
        self.assertEqual((restarted.counting.viewZoom, restarted.counting.viewPanX, restarted.counting.viewPanY), (1.75, 13.0, -8.0))

    def test_quit_cancels_active_preview_worker_through_existing_close(self):
        entered, cancelled = threading.Event(), threading.Event()
        original_composite = counting.composite_image

        def blocked_preview(*args, **kwargs):
            entered.set()
            cancel = kwargs["cancel"]
            if not cancel.wait(5):
                return original_composite(*args, **kwargs)
            cancelled.set()
            raise InterruptedError()

        with patch("leap_desktop.counting.composite_image", side_effect=blocked_preview), \
                patch.object(QApplication, "quit") as quit_app, \
                patch("leap_desktop.controller.QMessageBox.information") as information:
            self.editor.setChannelVisible(1, False)
            self.wait_without_frame_ack(entered.is_set)
            self.controller.setPage("settings")
            self.assertTrue(self.editor.busy)
            self.controller.requestQuit()
            self.wait_without_frame_ack(cancelled.is_set)
            self.assertTrue(QThreadPool.globalInstance().waitForDone(5000))
            self.application.processEvents()
            self.wait_without_frame_ack(lambda: quit_app.call_count == 1)
        information.assert_not_called()
        quit_app.assert_called_once()
        self.assertFalse(self.editor.available)
        self.assertEqual(json.loads((self.analysis.parent / "draft.json").read_text())["state"]["points"], self.original_points)

    def test_active_publication_cannot_be_interrupted_and_deferred_quit_uses_same_gate(self):
        entered, release = threading.Event(), threading.Event()
        original_publish = counting.publish_result
        cancellation_tokens = []

        def held_publish(path, analysis, state, cancel):
            cancellation_tokens.append(cancel)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release publication transaction")
            return original_publish(path, analysis, state, cancel)

        with patch("leap_desktop.counting.publish_result", side_effect=held_publish), \
                patch.object(QApplication, "quit") as quit_app, \
                patch("leap_desktop.controller.QMessageBox.information") as information, \
                patch.object(self.editor, "close", wraps=self.editor.close) as close, \
                patch("leap_desktop.controller.QTimer.singleShot") as retry:
            self.editor.confirm()
            try:
                self.wait_without_frame_ack(entered.is_set)
                self.assertTrue(self.editor.confirmationInProgress)
                self.controller.requestQuit()
                information.assert_called_once()
                close.assert_not_called()
                quit_app.assert_not_called()
                self.assertFalse(cancellation_tokens[0].is_set())
                self.controller._quit_when_idle()
                retry.assert_called_once_with(300, self.controller._quit_when_idle)
                close.assert_not_called()
                quit_app.assert_not_called()
            finally:
                release.set()
            self.wait_until(lambda: not self.editor.confirmationInProgress)
            self.assertEqual(self.controller.status, "completed")
            self.assertTrue((self.analysis.parent / "results/latest.json").is_file())
            self.controller._quit_when_idle()
            close.assert_called_once()
            quit_app.assert_called_once()

    def test_queued_confirmation_also_blocks_quit_before_publication_worker_starts(self):
        entered, release = threading.Event(), threading.Event()
        original_composite = counting.composite_image

        def held_preview(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release preview worker")
            return original_composite(*args, **kwargs)

        with patch("leap_desktop.counting.composite_image", side_effect=held_preview), \
                patch.object(QApplication, "quit") as quit_app, \
                patch("leap_desktop.controller.QMessageBox.information") as information, \
                patch.object(self.editor, "close", wraps=self.editor.close) as close:
            self.editor.setChannelVisible(1, False)
            try:
                self.wait_without_frame_ack(entered.is_set)
                self.editor.confirm()
                self.assertIsNotNone(self.editor._pending_confirm)
                self.assertTrue(self.editor.confirmationInProgress)
                self.controller.requestQuit()
                information.assert_called_once()
                close.assert_not_called()
                quit_app.assert_not_called()
                self.assertFalse(self.editor._active_task.cancel.is_set())
            finally:
                release.set()
            self.wait_until(lambda: not self.editor.confirmationInProgress)
            self.assertEqual(self.controller.status, "completed")


if __name__ == "__main__":
    unittest.main()
