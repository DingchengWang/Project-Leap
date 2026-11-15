"""Slice handoff avoids redundant decoding while retaining frame-ready and cache ownership."""
from __future__ import annotations

from pathlib import Path
import os
os.environ.setdefault("QT_QUICK_BACKEND", "software")
import copy
import json
import unittest
import threading
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QImage
import test_counting_editor as editor_fixtures


class CountingSliceTransportTests(unittest.TestCase):
    setUpClass = editor_fixtures.CountingEditorTests.__dict__["setUpClass"]
    setUp = editor_fixtures.CountingEditorTests.setUp
    make_editor = editor_fixtures.CountingEditorTests.make_editor
    wait_until = editor_fixtures.CountingEditorTests.wait_until
    cleanup_editors = editor_fixtures.CountingEditorTests.cleanup_editors

    def test_close_waits_for_late_render_then_removes_all_display_cache(self):
        from PySide6.QtCore import QThreadPool, Qt
        from leap_desktop.counting import _ImageTask
        e = self.editor
        late = self.base / '.editor/view-late.png'
        sliced = self.frame('frame-retired.png')
        release = threading.Event()
        self.addCleanup(release.set)
        def finish_after_close(cancel):
            self.assertTrue(release.wait(3))
            late.parent.mkdir(exist_ok=True)
            late.write_bytes(b'late pixels')
            return {'path': str(late), 'z_index': -1, 'message': ''}
        task = _ImageTask('render', e._generation, e._sequence, finish_after_close)
        task.output_path = late
        task.signals.done.connect(e._task_done, Qt.ConnectionType.QueuedConnection)
        e._active_task = task
        QThreadPool.globalInstance().start(task)
        self.assertTrue(e.close())
        self.assertFalse(e.drained)
        self.assertTrue(sliced.exists())
        release.set()
        self.wait_until(lambda: e.drained)
        self.assertFalse((self.base / '.editor').exists())
        self.assertTrue((self.base / 'draft.json').exists())
        self.assertTrue((self.base / 'analysis.json').exists())

    def test_close_drains_slice_process_before_cleanup(self):
        runtime = editor_fixtures.CountingEditorTests.fake_server(self)
        self.editor.close()
        e = self.make_editor(runtime=runtime)
        e.setZ(0)
        self.wait_until(lambda: (self.base / 'slice-calls.txt').exists())
        e.close()
        self.wait_until(lambda: e.drained)
        self.assertFalse((self.base / '.editor').exists())

    def test_source_relocation_is_only_exposed_for_source_anomalies(self):
        e = self.editor
        self.assertTrue(e.sourceRelocationNeeded)
        source = self.root / 'source.tif'
        source.write_bytes(b'present source fixture')
        e._refresh_source_status()
        self.assertFalse(e.sourceRelocationNeeded)
        self.assertEqual(e.sourceRelocationMessage, '')
        stat = source.stat()
        e._analysis['source_signature'] = {'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        e._refresh_source_status()
        self.assertFalse(e.sourceRelocationNeeded)
        source.write_bytes(b'changed source fixture')
        e._refresh_source_status()
        self.assertTrue(e.sourceRelocationNeeded)

    def frame(self, name, width=160, height=120):
        folder = self.base / ".editor/slices"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(QColor(25, 75, 125))
        self.assertTrue(image.save(str(path)))
        return path

    def test_worker_png_is_handed_directly_to_qml_without_ui_decode_or_resave(self):
        e = self.editor
        path = self.frame("frame-direct.png")
        e._slice_cache[e._slice_key(2)] = path
        with patch("leap_desktop.counting.QImage", side_effect=AssertionError("unexpected UI decode/copy")):
            e.setZ(2)
            self.wait_until(lambda: e.imageUrl == QUrl.fromLocalFile(str(path)).toString(), acknowledge=False)
            self.assertTrue(e.frameReady)
            self.assertEqual(e.displayedZIndex, -1)
            self.assertEqual(len(e.visiblePoints), 3)
            e.imageReady(e.imageUrl)
        self.assertEqual(e.displayedZIndex, 2)
        self.assertFalse(e.busy)
        self.assertEqual(QImage(str(path)).pixelColor(0, 0).getRgb(), (25, 75, 125, 255))
        self.assertFalse(list((self.base / ".editor").glob("view-*.png")))

    def test_qml_double_buffer_preserves_old_pixels_and_labels_until_ready_commit(self):
        from PySide6.QtCore import QObject
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        import shiboken6
        engine = QQmlEngine()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(editor_fixtures.ROOT / "app/leap_desktop/qml/components/CountCanvas.qml")))
        item = component.createWithInitialProperties({"editor": self.editor, "width": 640, "height": 480})
        self.assertIsNotNone(item, str(component.errors()))
        try:
            image = item.findChild(QObject, "countReviewImage")
            self.assertIsNotNone(image)
            self.wait_until(lambda: image.property("visible"), acknowledge=False)
            old_source = QUrl(image.property("source"))
            old_points = self.editor.visiblePoints
            path = self.frame("frame-double-buffer.png")
            self.editor._slice_cache[self.editor._slice_key(2)] = path
            self.editor.setZ(2)
            self.assertTrue(image.property("visible"))
            self.assertEqual(QUrl(image.property("source")), old_source)
            self.assertEqual(self.editor.visiblePoints, old_points)
            self.wait_until(lambda: self.editor.frameReady and self.editor.displayedZIndex == 2, acknowledge=False)
            applied = item.findChild(QObject, "countReviewImage")
            self.assertIsNot(applied, image)
            self.assertTrue(applied.property("visible"))
            self.assertFalse(image.property("visible"))
            self.assertEqual(QUrl(applied.property("source")).toLocalFile(), str(path))
        finally:
            shiboken6.delete(item)
            shiboken6.delete(engine)

    def test_header_rejects_wrong_dimensions_without_committing_plane(self):
        e = self.editor
        path = self.frame("frame-wrong.png", width=159)
        e._slice_cache[e._slice_key(2)] = path
        e.setZ(2)
        self.wait_until(lambda: not e.busy, acknowledge=False)
        self.assertTrue(e.frameReady)
        self.assertEqual(e.displayedZIndex, -1)
        self.assertIn("Invalid slice frame dimensions", e.message)
        self.assertEqual(len(e.visiblePoints), 3)

    def test_cache_pins_displayed_and_pending_frames_until_ready_handoff(self):
        e = self.editor
        paths = [self.frame(f"frame-cache-{i}.png") for i in range(26)]
        e._slice_cache = {(i, (0, 1), 0): path for i, path in enumerate(paths)}
        e._slice_cache_order = list(e._slice_cache)
        e._displayed_image_url = QUrl.fromLocalFile(str(paths[0])).toString()
        e._image_url = QUrl.fromLocalFile(str(paths[1])).toString()
        e._z_index = 1
        e._frame_pending = {"url": e.imageUrl, "z": 1, "key": e._slice_key(1),
                            "generation": e._generation, "token": "pin-test", "message": "", "width": 160, "height": 120}
        e._frame_ready = False
        e._prune_frame_cache()
        self.assertEqual(len(e._slice_cache), 24)
        self.assertTrue(paths[0].exists())
        self.assertTrue(paths[1].exists())
        self.assertFalse(paths[2].exists())
        self.assertFalse(paths[3].exists())
        e.imageReady(e.imageUrl)
        extra = self.frame("frame-cache-extra.png")
        e._slice_cache[(26, (0, 1), 0)] = extra
        e._slice_cache_order.append((26, (0, 1), 0))
        e._prune_frame_cache()
        self.assertFalse(paths[0].exists())
        self.assertTrue(paths[1].exists())
        self.assertEqual(len(e._slice_cache), 24)

    def large_editor(self):
        self.editor.close()
        self.base = self.root / "large/counting"
        self.base.mkdir(parents=True)
        channel = QImage(2048, 1024, QImage.Format.Format_Grayscale8)
        channel.fill(80)
        channel.save(str(self.base / "C1.png"))
        analysis = copy.deepcopy(self.analysis)
        analysis.update(run_id="large", width=2048, height=1024, display_channels=[0])
        analysis["channels"] = [{**analysis["channels"][0], "path": "C1.png"}]
        path = self.base / "analysis.json"
        path.write_text(json.dumps(analysis))
        self.editor = self.make_editor(path)
        return self.editor

    def test_proxy_then_native_handoff_keeps_original_coordinates_and_rejects_old_tokens(self):
        e = self.large_editor()
        original = e.points
        proxy = self.frame("frame-proxy1536.png", 1536, 768)
        native = self.frame("frame-native2048.png", 2048, 1024)
        e._slice_cache[e._slice_key(2, 1536)] = proxy
        e._slice_cache[e._slice_key(2, 0)] = native
        e.setScrubbing(True)
        e.setZ(2)
        self.wait_until(lambda: bool(e._frame_pending), acknowledge=False)
        proxy_token = e.pendingFrameToken
        self.assertTrue(e.commitFrame(e.imageUrl, proxy_token))
        self.assertEqual((e.appliedPreviewQuality, e.appliedFrameWidth, e.width), ("proxy", 1536, 2048))
        self.assertEqual(e.visiblePoints[0]["x"], 42)
        applied_proxy_url = e.appliedImageUrl
        applied_points = e.visiblePoints
        e.setScrubbing(False)
        self.wait_until(lambda: bool(e._frame_pending), acknowledge=False)
        self.assertEqual(e.appliedImageUrl, applied_proxy_url)
        self.assertEqual(e.visiblePoints, applied_points)
        self.assertFalse(e.commitFrame(e.imageUrl, proxy_token))
        self.assertTrue(e.commitFrame(e.imageUrl, e.pendingFrameToken))
        self.assertEqual((e.appliedPreviewQuality, e.appliedFrameWidth), ("native", 2048))
        self.assertFalse(e.busy)
        self.assertEqual(e.points, original)

    def test_default_proxy_zoom_upgrade_and_cache_keys_are_separate(self):
        e = self.large_editor()
        proxy = self.frame("frame-proxy1536.png", 1536, 768)
        native = self.frame("frame-zoom-native.png", 2048, 1024)
        e._slice_cache[e._slice_key(1, 1536)] = proxy
        e._slice_cache[e._slice_key(1, 0)] = native
        e.setScrubbing(True)
        e.setZ(1)
        self.wait_until(lambda: e.appliedPreviewQuality == "proxy")
        self.assertEqual(e.appliedFrameWidth, 1536)
        self.assertNotEqual(e._slice_key(1, 1536), e._slice_key(1, 0))
        e.setView(2, 0, 0)
        self.wait_until(lambda: e.appliedPreviewQuality == "native")
        self.assertEqual(e.appliedFrameWidth, 2048)
        e.setScrubbing(False)
        e.close()
        reopened = self.make_editor(self.base / "analysis.json")
        self.assertFalse(reopened.scrubbing)
        self.assertEqual(reopened.viewZoom, 2)
        reopened.setView(1, 0, 0)
        reopened.setScrubbing(True)
        self.assertEqual(reopened._display_edge(), 1536)

    def test_qml_proxy_buffer_uses_native_geometry_then_restores_full_resolution(self):
        from PySide6.QtCore import QObject
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        import shiboken6
        e = self.large_editor()
        proxy = self.frame("frame-qml-proxy.png", 1536, 768)
        native = self.frame("frame-qml-native.png", 2048, 1024)
        e._slice_cache[e._slice_key(2, 1536)] = proxy
        e._slice_cache[e._slice_key(2, 0)] = native
        engine = QQmlEngine()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(editor_fixtures.ROOT / "app/leap_desktop/qml/components/CountCanvas.qml")))
        item = component.createWithInitialProperties({"editor": e, "width": 640, "height": 480})
        self.assertIsNotNone(item, str(component.errors()))
        try:
            e.setScrubbing(True)
            e.setZ(2)
            self.wait_until(lambda: e.appliedPreviewQuality == "proxy" and e.displayedZIndex == 2, acknowledge=False)
            applied = item.findChild(QObject, "countReviewImage")
            self.assertEqual(QUrl(applied.property("source")).toLocalFile(), str(proxy))
            self.assertAlmostEqual(applied.property("width"), e.width * item.property("imageScale"))
            self.assertEqual(e.visiblePoints[0]["x"], 42)
            labels = item.property("labels")
            labels = labels.toVariant() if hasattr(labels, "toVariant") else labels
            self.assertEqual(next(row for row in labels if row["id"] == "auto-2")["x"], 42)
            e.setScrubbing(False)
            self.wait_until(lambda: e.appliedPreviewQuality == "native" and not e.busy, acknowledge=False)
            full = item.findChild(QObject, "countReviewImage")
            self.assertEqual(QUrl(full.property("source")).toLocalFile(), str(native))
            self.assertEqual(e.appliedFrameWidth, 2048)
            self.assertEqual(e.visiblePoints[0]["x"], 42)
        finally:
            shiboken6.delete(item)
            shiboken6.delete(engine)

    def test_latest_desired_z_and_generation_reject_superseded_ready(self):
        e = self.editor
        first = self.frame("frame-z1.png")
        second = self.frame("frame-z2.png")
        e._slice_cache[e._slice_key(1)] = first
        e._slice_cache[e._slice_key(2)] = second
        old_url = e.appliedImageUrl
        e.setZ(1)
        self.wait_until(lambda: bool(e._frame_pending), acknowledge=False)
        stale_url, stale_token = e.imageUrl, e.pendingFrameToken
        e.setZ(2)
        self.wait_until(lambda: bool(e._frame_pending), acknowledge=False)
        self.assertFalse(e.commitFrame(stale_url, stale_token))
        self.assertFalse(e.commitFrame(e.imageUrl, "old-generation:9"))
        self.assertEqual(e.appliedImageUrl, old_url)
        self.assertEqual(e.displayedZIndex, -1)
        self.assertTrue(e.commitFrame(e.imageUrl, e.pendingFrameToken))
        self.assertEqual(e.displayedZIndex, 2)

    def test_hidden_proxy_defers_native_work_and_resumes_without_losing_review(self):
        e = self.large_editor()
        proxy = self.frame("frame-hide-proxy.png", 1536, 768)
        native = self.frame("frame-return-native.png", 2048, 1024)
        e._slice_cache[e._slice_key(2, 1536)] = proxy
        e.setScrubbing(True)
        e.setZ(2)
        self.wait_until(lambda: e.appliedPreviewQuality == "proxy")
        points = e.points
        e.setDisplayActive(False)
        with patch.object(e, "_ensure_slice_server", side_effect=AssertionError("hidden native work")):
            e.setScrubbing(False)
            e._request_slice()
            e._start_prefetch()
            self.application.processEvents()
        self.assertFalse(e.scrubbing)
        self.assertFalse(e.busy)
        self.assertFalse(e._slice_timer.isActive())
        self.assertEqual(e.appliedPreviewQuality, "proxy")
        self.assertEqual(e.zIndex, 2)
        self.assertEqual(e.points, points)
        e._slice_cache[e._slice_key(2, 0)] = native
        e.setDisplayActive(True)
        self.wait_until(lambda: e.appliedPreviewQuality == "native" and not e.busy)
        self.assertEqual(e.points, points)

    def test_old_page_visibility_callback_cannot_deactivate_new_page(self):
        e = self.editor
        old_owner = e.registerDisplay()
        e.setDisplayActive(False, old_owner)
        current_owner = e.registerDisplay()
        e.setDisplayActive(True, current_owner)
        e.setDisplayActive(False, old_owner)
        self.assertTrue(e.displayActive)
        self.assertNotEqual(old_owner, current_owner)

    def test_real_qml_hidden_proxy_does_not_launch_offline_native_fallback(self):
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        from PySide6.QtQuick import QQuickWindow
        import shiboken6
        e = self.large_editor()
        proxy = self.frame("frame-qml-hide-proxy.png", 1536, 768)
        native = self.frame("frame-qml-show-native.png", 2048, 1024)
        e._slice_cache[e._slice_key(2, 1536)] = proxy
        engine = QQmlEngine()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(editor_fixtures.ROOT / "app/leap_desktop/qml/components/CountCanvas.qml")))
        item = component.createWithInitialProperties({"editor": e, "width": 640, "height": 480})
        self.assertIsNotNone(item, str(component.errors()))
        window = QQuickWindow()
        window.resize(640, 480)
        item.setParentItem(window.contentItem())
        window.show()
        self.application.processEvents()
        try:
            e.setScrubbing(True)
            e.setZ(2)
            self.wait_until(lambda: e.appliedPreviewQuality == "proxy", acknowledge=False)
            item.setProperty("visible", False)
            self.wait_until(lambda: not e.displayActive, acknowledge=False)
            self.assertFalse(e.busy)
            self.assertFalse(e._slice_timer.isActive())
            self.assertEqual((e.zIndex, e.displayedZIndex, e.appliedPreviewQuality), (2, 2, "proxy"))
            e._slice_cache[e._slice_key(2, 0)] = native
            item.setProperty("visible", True)
            self.wait_until(lambda: e.displayActive and e.appliedPreviewQuality == "native", acknowledge=False)
            self.assertEqual(e.displayedZIndex, 2)
        finally:
            shiboken6.delete(item)
            self.application.processEvents()
            window.close()
            shiboken6.delete(window)
            shiboken6.delete(engine)

    def test_error_from_superseded_request_does_not_replace_latest_z_with_mip(self):
        runtime = editor_fixtures.CountingEditorTests.fake_server(self)
        script = self.root / "fake-engine/leap_cell_counting/__main__.py"
        script.write_text(script.read_text().replace(
            " shutil.copyfile(base/'rgb.png',r['output'])",
            " if r['z']==0:\n  print(json.dumps({'id':r['id'],'z':r['z'],'error':'old request failed'}),flush=True); continue\n shutil.copyfile(base/'rgb.png',r['output'])"))
        self.editor.close()
        e = self.make_editor(runtime=runtime)
        original = e.points
        e.setZ(0)
        self.wait_until(lambda: (self.base / "slice-calls.txt").exists())
        e.setZ(2)
        self.wait_until(lambda: e.displayedZIndex == 2 and not e.busy)
        self.assertEqual(e.zIndex, 2)
        self.assertEqual(e.points, original)
        self.assertNotIn("old request failed", e.message)

    def test_return_to_loaded_cached_frame_needs_no_missing_ready_signal(self):
        e = self.editor
        path = self.frame("frame-return.png")
        e._slice_cache[e._slice_key(2)] = path
        e.setZ(2)
        self.wait_until(lambda: not e.busy)
        e.setZ(1)
        e.setZ(2)
        self.wait_until(lambda: not e.busy, acknowledge=False)
        self.assertEqual(e.imageUrl, QUrl.fromLocalFile(str(path)).toString())
        self.assertTrue(e.frameReady)
        self.assertEqual(e.displayedZIndex, 2)
        self.assertEqual([p["id"] for p in e.visiblePoints], ["auto-2", "auto-9"])


if __name__ == "__main__":
    unittest.main()
