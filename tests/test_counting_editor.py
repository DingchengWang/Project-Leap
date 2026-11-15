"""Isolated Qt editor checks; synthetic display PNGs never invoke a scientific model."""
from __future__ import annotations

import copy
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import hashlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from PySide6.QtCore import QThreadPool, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from leap_desktop.counting import CountingEditor, composite_image, layout_labels, publish_result, render_overlay, export_label_size, _atomic_json, _fingerprint


class CountingEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="leap-count-editor-d-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "run-a/counting"
        (self.base / "channels").mkdir(parents=True)
        for number, value in ((1, 100), (2, 70), (3, 40)):
            image = QImage(160, 120, QImage.Format.Format_Grayscale8)
            image.fill(QColor(value, value, value))
            self.assertTrue(image.save(str(self.base / f"channels/C{number}.png")))
        self.analysis = {
            "schema_version": 2, "run_id": "run-a", "source_path": str(self.root / "source.tif"),
            "width": 160, "height": 120, "z_count": 4, "dapi_channel": 0,
            "support_channel": 1, "support_marker": "eGFP", "display_channels": [0, 1],
            "physical_size_um": {"x": 0.4, "y": 0.4, "z": 1.2}, "labels_path": "labels.npy",
            "auto_count": 1, "default_label_size": 16,
            "channels": [
                {"index": 0, "name": "DAPI", "role": "DAPI", "color": "#0000FF", "path": "channels/C1.png"},
                {"index": 1, "name": "eGFP", "role": "eGFP", "color": "#00FF00", "path": "channels/C2.png"},
                {"index": 2, "name": "unused", "role": "other", "color": "#FF0000", "path": "channels/C3.png"}],
            "points": [
                {"id": "auto-2", "label_id": 2, "x": 40, "y": 30, "source": "auto", "z": 1,
                 "status": "accepted", "plane_anchors": {"0": [38, 29], "1": [40, 30], "2": [42, 31]}},
                {"id": "auto-9", "label_id": 9, "x": 80, "y": 65, "source": "auto", "z": 2,
                 "status": "review", "plane_anchors": {"1": [79, 64], "2": [80, 65]}, "support": {"decision": "review"}},
                {"id": "auto-99", "label_id": 99, "x": 120, "y": 90, "source": "auto", "z": 3,
                 "status": "excluded", "plane_anchors": {"3": [120, 90]}}]}
        self.path = self.base / "analysis.json"
        self.path.write_text(json.dumps(self.analysis))
        self.editors = []
        self.addCleanup(self.cleanup_editors)
        self.editor = self.make_editor()

    def make_editor(self, path=None, **kwargs):
        editor = CountingEditor()
        self.editors.append(editor)
        editor.open_analysis(path or self.path, **kwargs)
        self.wait_until(lambda: bool(editor.imageUrl) and not editor.busy)
        return editor

    def cleanup_editors(self):
        for editor in self.editors:
            editor.close()
        self.wait_until(lambda: all(not editor._processes for editor in self.editors))
        QThreadPool.globalInstance().waitForDone(5000)
        self.application.processEvents()

    def wait_until(self, predicate, timeout=6, acknowledge=True):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.application.processEvents()
            if acknowledge:
                for editor in self.editors:
                    if editor._frame_pending:
                        editor.imageReady(editor.imageUrl)
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for asynchronous counting editor")

    def cached_slice(self, z):
        image = composite_image(self.base, self.editor.channels, 160, 120)
        path = self.root / f"slice-{z}.png"
        image.save(str(path))
        self.editor._slice_cache[self.editor._slice_key(z)] = path
        self.editor.setZ(z)
        self.wait_until(lambda: not self.editor.busy)

    def accept_review(self):
        self.editor.selectPoint("auto-9", False)
        self.editor.acceptSelected()

    def test_status_counts_and_real_plane_intersections_preserve_identity(self):
        e = self.editor
        original = e.points
        self.assertEqual((e.total, e.pendingCount, sum(p.get('status') == 'excluded' for p in e.points)), (1, 1, 1))
        self.assertEqual([p["id"] for p in e.visiblePoints], ["auto-2", "auto-9", "auto-99"])
        self.cached_slice(0)
        self.assertEqual([(p["id"], p["x"], p["y"]) for p in e.visiblePoints], [("auto-2", 38, 29)])
        self.cached_slice(2)
        self.assertEqual([(p["id"], p["x"], p["display_no"]) for p in e.visiblePoints], [("auto-2", 42, 1), ("auto-9", 80, 2)])
        self.assertEqual(e.points, original)
        self.cached_slice(3)
        self.assertEqual([p["id"] for p in e.visiblePoints], ["auto-99"])
        e.activatePoint("auto-99", False)
        self.assertEqual(e.selectedIds, ["auto-99"])
        self.assertTrue(e.canAcceptSelected)
        self.assertEqual(e.neuronCount, 1)
        self.assertEqual(e.points, original)

    def test_frame_ready_is_atomic_and_stale_ack_cannot_relabel_pixels(self):
        e = self.editor
        old_url = e.imageUrl
        old_z = e.displayedZIndex
        old_visible = e.visiblePoints
        path = self.root / "z2.png"
        composite_image(self.base, e.channels, 160, 120).save(str(path))
        e._slice_cache[e._slice_key(2)] = path
        e.setZ(2)
        self.wait_until(lambda: e.imageUrl != old_url, acknowledge=False)
        self.assertEqual(e.displayedZIndex, old_z)
        self.assertEqual(e.visiblePoints, old_visible)
        self.assertEqual(e.appliedImageUrl, old_url)
        self.assertTrue(e.busy)
        e.imageReady(old_url)
        self.assertEqual(e.displayedZIndex, old_z)
        e.imageReady(e.imageUrl)
        self.assertEqual(e.displayedZIndex, 2)
        self.assertEqual([p["id"] for p in e.visiblePoints], ["auto-2", "auto-9"])
        self.assertFalse(e.busy)

    def test_preselected_channels_only_and_native_composite(self):
        self.assertEqual([c["index"] for c in self.editor.channels], [0, 1])
        image = QImage(QUrl(self.editor.imageUrl).toLocalFile())
        self.assertEqual(image.pixelColor(0, 0).getRgb(), (0, 70, 100, 255))
        self.editor.setChannelVisible(2, True)
        self.assertEqual(len(self.editor.channels), 2)
        self.editor.setChannelVisible(1, False)
        self.wait_until(lambda: not self.editor.busy)
        self.assertEqual(QImage(QUrl(self.editor.imageUrl).toLocalFile()).pixelColor(0, 0).getRgb(), (0, 0, 100, 255))
        overlay = render_overlay(image, self.editor.points[:1], 16)
        self.assertIn("#56c987", {overlay.pixelColor(x, y).name() for x in range(160) for y in range(120)})

    def test_pending_can_be_saved_without_counting_or_silently_accepting(self):
        e = self.editor
        original = e.points
        summaries = []
        e.confirmed.connect(lambda _, summary: summaries.append(summary))
        e.confirm()
        self.wait_until(lambda: len(summaries) == 1 and not e.busy)
        self.assertEqual((e.total, e.pendingCount), (1, 1))
        self.assertEqual(e.points, original)
        self.assertTrue(e.confirmedState)
        summary = summaries[0]
        self.assertEqual(summary["pending_count"], 1)
        session = json.loads(Path(summary["session_path"]).read_text())
        self.assertEqual((session["count"], session["pending_count"], session["excluded_count"]), (1, 1, 1))
        self.assertEqual(session["points"], original)
        with Path(summary["csv_path"]).open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row["id"] for row in rows], ["auto-2"])
        reopened = self.make_editor(Path(summary["session_path"]))
        self.assertEqual((reopened.total, reopened.pendingCount), (1, 1))
        self.assertEqual(reopened.points, original)
        self.assertTrue(reopened.confirmedState)
        self.assertFalse(reopened.dirty)

    def test_accept_and_delete_whole_objects_have_persistent_undo(self):
        e = self.editor
        original = e.points
        e.selectPoint("auto-2", False)
        e.selectPoint("auto-9", True)
        e.deleteSelected()
        self.assertEqual((e.total, e.pendingCount, sum(p.get('status') == 'excluded' for p in e.points)), (0, 0, 1))
        self.assertEqual(len(e.points), 3)
        self.assertEqual(sum(p.get("deleted", False) for p in e.points), 2)
        self.assertEqual([p["status"] for p in e.points], [p["status"] for p in original])
        self.assertEqual([p["id"] for p in e.visiblePoints], ["auto-99"])
        self.assertEqual(e.selectedIds, [])
        self.assertEqual(e.points[0]["plane_anchors"], original[0]["plane_anchors"])
        self.assertEqual(e.points[0]["source"], "auto")
        e.undo()
        self.assertEqual(e.points, original)
        e.redo()
        e.close()
        reopened = self.make_editor()
        reopened.undo()
        self.assertEqual(reopened.points, original)
        reopened.selectPoint("auto-9", False)
        reopened.acceptSelected()
        self.assertEqual((reopened.total, reopened.pendingCount, sum(p.get('status') == 'excluded' for p in reopened.points)), (2, 0, 1))
        reopened.undo()
        self.assertEqual(reopened.points, original)

    def test_manual_mip_point_has_no_invented_z_and_plane_add_is_local(self):
        e = self.editor
        e.addPoint(10, 20)
        mip = e.points[-1]
        self.assertIsNone(mip["z"])
        self.assertEqual(mip["plane_anchors"], {})
        self.cached_slice(2)
        self.assertNotIn(mip["id"], [p["id"] for p in e.visiblePoints])
        e.addPoint(11, 21)
        plane = e.points[-1]
        self.assertEqual(plane["z"], 2)
        self.assertEqual(plane["plane_anchors"], {"2": [11.0, 21.0]})
        self.cached_slice(1)
        self.assertNotIn(plane["id"], [p["id"] for p in e.visiblePoints])
        e.setZ(-1)
        self.wait_until(lambda: not e.busy)
        self.assertTrue({mip["id"], plane["id"]} <= {p["id"] for p in e.visiblePoints})
        before = e.points
        for x, y in [(float("nan"), 0), (-1, 0), (160, 0)]:
            e.addPoint(x, y)
        self.assertEqual(e.points, before)

    def test_merge_preserves_3d_plane_union_ancestry_and_review_status(self):
        e = self.editor
        original = e.points
        e.selectPoint("auto-2", False)
        e.selectPoint("auto-9", True)
        e.mergeSelected()
        merged = e.points[0]
        self.assertEqual(merged["status"], "review")
        self.assertEqual(merged["label_ids"], [2, 9])
        self.assertEqual(set(merged["plane_anchors"]), {"0", "1", "2"})
        self.assertEqual(merged["plane_anchors"]["0"], [38.0, 29.0])
        self.assertEqual(merged["merge_lineage"][1]["support"], {"decision": "review"})
        self.assertEqual(merged["display_no"], 1)
        e.undo()
        self.assertEqual(e.points, original)
        e.redo()
        self.assertEqual(e.points[0], merged)
        e.selectPoint("auto-99", True)
        self.assertFalse(e.canMerge)
        e.close()
        reopened = self.make_editor()
        self.assertEqual(reopened.points[0], merged)
        reopened.undo()
        self.assertEqual(reopened.points, original)

    def test_confirmation_keeps_numbers_all_statuses_and_revision_consistency(self):
        self.accept_review()
        e = self.editor
        e.addPoint(12, 13)
        original = e.points
        summaries = []
        e.confirmed.connect(lambda _, summary: summaries.append(summary))
        e.confirm()
        e.addPoint(1, 1)
        self.wait_until(lambda: len(summaries) == 1 and not e.busy)
        self.assertEqual(e.points, original)
        self.assertTrue(e.confirmedState)
        first = Path(summaries[0]["results_dir"])
        first_bytes = (first / "Cell_Count_Session.json").read_bytes()
        session = json.loads(first_bytes)
        self.assertEqual((session["schema_version"], session["count"], session["excluded_count"]), (2, 3, 1))
        self.assertEqual(session["points"], original)
        self.assertEqual(session["editor_state"]["points"], original)
        self.assertEqual(session["label_size_units"], {"review": "logical_screen_pixels", "export": "original_image_pixels"})
        image = QImage(str(first / "Cell_Count_Overlay.png"))
        self.assertEqual(int(image.text("count")), 3)
        with (first / "Cell_Counts.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 3)
        self.assertEqual({int(row["count"]) for row in rows}, {3})
        by_id = {point["id"]: point for point in original}
        self.assertEqual([int(row["display_no"]) for row in rows], [1, 2, 3])
        for row in rows:
            self.assertEqual(json.loads(row["plane_anchors"]), by_id[row["id"]]["plane_anchors"])
        e.selectPoint("auto-2", False)
        e.deleteSelected()
        e.confirm()
        self.wait_until(lambda: len(summaries) == 2 and not e.busy)
        self.assertEqual(summaries[-1]["count"], 2)
        self.assertEqual((first / "Cell_Count_Session.json").read_bytes(), first_bytes)
        historical = self.make_editor(first / "Cell_Count_Session.json")
        self.assertEqual(historical.points, original)
        self.assertTrue(historical.confirmedState)
        e.undo()
        self.assertEqual(e.points, original)
        self.assertEqual([p["display_no"] for p in e.points], [2, 3, 4, 1])

    def test_failed_publication_preserves_pointer_and_zero_accept_is_valid(self):
        for point in self.editor.points:
            self.editor.selectPoint(point["id"], True)
        self.editor.deleteSelected()
        result = publish_result(self.path, self.analysis, self.editor._state())
        self.assertEqual(result["count"], 0)
        pointer_bytes = (self.base / "results/latest.json").read_bytes()
        with patch("leap_desktop.counting.render_overlay", side_effect=OSError("synthetic save error")):
            with self.assertRaises(OSError):
                publish_result(self.path, self.analysis, self.editor._state())
        self.assertEqual((self.base / "results/latest.json").read_bytes(), pointer_bytes)
        self.assertFalse(list((self.base / "results").glob(".staging-*")))

    def test_legacy_schema_one_remains_two_dimensional(self):
        self.editor.close()
        legacy = copy.deepcopy(self.analysis)
        legacy["schema_version"] = 1
        legacy.pop("display_channels")
        for point in legacy["points"]:
            point.pop("status")
            point.pop("plane_anchors")
            point["z"] = None
        self.path.write_text(json.dumps(legacy))
        (self.base / "draft.json").unlink()
        self.editor = self.make_editor()
        self.assertEqual(self.editor.total, 3)
        self.assertEqual(len(self.editor.visiblePoints), 3)
        self.cached_slice(2)
        self.assertEqual(self.editor.visiblePoints, [])
        self.assertTrue(all(p["z"] is None for p in self.editor.points))
        self.editor.selectPoint("auto-9", False)
        self.editor.deleteSelected()
        self.assertEqual(len(self.editor.points), 2)
        self.editor.undo()
        self.assertEqual(len(self.editor.points), 3)

    def test_view_persists_on_recreation_without_dirtying_count(self):
        before = self.editor._content_fingerprint
        self.editor.setView(2.5, 18, -21)
        self.assertEqual(before, self.editor._content_fingerprint)
        self.editor.close()
        reopened = self.make_editor()
        self.assertEqual((reopened.viewZoom, reopened.viewPanX, reopened.viewPanY), (2.5, 18, -21))
        reopened.setView(1, 0, 0)
        self.assertEqual((reopened.viewZoom, reopened.viewPanX, reopened.viewPanY), (1, 0, 0))

    def test_label_size_has_no_small_scale_flat_zone(self):
        e = self.editor
        e.setLabelSize(10)
        first = e.labelsForScale(0.1)
        e.setLabelSize(11)
        second = e.labelsForScale(0.1)
        self.assertEqual(first[0]["fontSize"] * 0.1, 10)
        self.assertEqual(second[0]["fontSize"] * 0.1, 11)
        self.assertEqual(layout_labels(e.points[:1], 160, 120, 11)[0]["fontSize"], 11)
        self.assertEqual(layout_labels(e.points[:1], 160, 120, 11, view_scale=0.1)[0]["fontSize"], 110)

    def test_large_layout_is_async_and_draft_history_is_compact(self):
        points = [{"id": str(i), "x": (i % 20) * 7 + 3, "y": (i // 20) * 5 + 3,
                   "display_no": i + 1, "z": 0, "source": "auto", "status": "accepted",
                   "plane_anchors": {"0": [(i % 20) * 7 + 3, (i // 20) * 5 + 3]}} for i in range(400)]
        self.editor._points = points
        self.editor._next_display_no = 401
        self.editor._notify(content=True)
        started = time.monotonic()
        self.assertEqual(self.editor.labelsForScale(1), [])
        self.assertLess(time.monotonic() - started, 0.08)
        self.wait_until(lambda: len(self.editor.labelsForScale(1)) == 400)
        self.assertEqual(self.editor.labelsForScale(1), layout_labels(points, 160, 120, 16))
        for _ in range(10):
            self.editor.addPoint(10, 10)
        self.assertTrue(self.editor._flush_draft())
        draft = json.loads((self.base / "draft.json").read_text())
        self.assertEqual(sum(len(a["added"]) + len(a["removed"]) for a in draft["undo"]), 10)
        self.assertLess((self.base / "draft.json").stat().st_size, 180000)

    def test_numbering_is_global_spatial_and_reindexes_after_every_edit(self):
        e = self.editor
        self.accept_review()
        original = e.points
        e.addPoint(12, 13)
        manual_id = e.points[-1]["id"]
        self.assertEqual({p["id"]: p["display_no"] for p in e.points},
                         {"auto-2": 2, "auto-9": 3, "auto-99": 4, manual_id: 1})
        self.cached_slice(2)
        self.assertEqual([(p["id"], p["display_no"]) for p in e.visiblePoints],
                         [("auto-2", 2), ("auto-9", 3)])
        e.selectPoint("auto-2", False)
        e.deleteSelected()
        self.assertEqual({p["id"]: p["display_no"] for p in e.points
                          if p["status"] == "accepted" and not p.get("deleted")},
                         {"auto-9": 2, manual_id: 1})
        e.undo()
        e.undo()
        self.assertEqual(e.points, original)
        e.selectPoint("auto-2", False)
        e.selectPoint("auto-9", True)
        e.mergeSelected()
        self.assertEqual((e.total, e.points[0]["display_no"]), (1, 1))
        e.undo()
        self.assertEqual(e.points, original)
        self.assertEqual(_fingerprint(e._state()), e._content_fingerprint)

    def test_historical_gapped_numbers_map_without_rewriting_saved_evidence(self):
        result = publish_result(self.path, self.analysis, self.editor._state())
        saved_path = Path(result["session_path"])
        saved = json.loads(saved_path.read_text())
        for index, point in enumerate(saved["points"]):
            point["display_no"] = 100 + index * 17
        saved["editor_state"]["points"] = copy.deepcopy(saved["points"])
        saved_path.write_text(json.dumps(saved))
        before = {path: path.read_bytes() for path in (self.path, saved_path)}
        reopened = self.make_editor(saved_path)
        self.assertEqual([p["display_no"] for p in reopened.points], [1, 2, 3])
        self.assertTrue(reopened.confirmedState)
        self.assertTrue(reopened.close())
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_local_label_geometry_and_compact_cached_rendering(self):
        points = [{"id": str(i), "x": 50, "y": 50, "display_no": i + 1}
                  for i in range(20)]
        rows = layout_labels(points, 160, 120, 16)
        for row in rows:
            self.assertAlmostEqual(row["left"] + row["width"] / 2, 50)
            self.assertAlmostEqual(row["top"] + row["height"] / 2, 50)
            self.assertFalse({"leaderX", "leaderY", "displaced", "collision"} & row.keys())
        larger = layout_labels(points, 160, 120, 16.1)
        self.assertGreater(larger[0]["width"], rows[0]["width"])
        e = self.editor
        with patch("leap_desktop.counting.copy.deepcopy", side_effect=AssertionError("evidence copy")):
            first = e.renderingPoints()
            self.assertIs(first, e._visible_points())
            self.assertEqual(set(first[0]), {"id", "x", "y", "status", "review_hint", "display_no", "selected"})
            cached = e.labelsForScale(1)
            with patch.object(e, "_visible_points", side_effect=AssertionError("cache miss")):
                self.assertIs(e.labelsForScale(1), cached)
        e.selectPoint("auto-2", False)
        self.assertTrue(next(p for p in e.renderingPoints() if p["id"] == "auto-2")["selected"])

    def test_export_size_is_dimension_based_and_independent_of_review_size(self):
        self.assertEqual(export_label_size(4096, 3000), 32)
        self.assertEqual(export_label_size(8192, 4096), 64)
        state = self.editor._state()
        first = publish_result(self.path, self.analysis, state)
        state["label_size"] = 120
        second = publish_result(self.path, self.analysis, state)
        a, b = QImage(first["overlay_path"]), QImage(second["overlay_path"])
        self.assertEqual((a.width(), a.height()), (160, 120))
        self.assertEqual(bytes(a.constBits()), bytes(b.constBits()))
        self.assertEqual(float(a.text("label_size")), 160 / 128)
        session = json.loads(Path(second["session_path"]).read_text())
        self.assertEqual(session["label_size"], 120)
        self.assertEqual(session["export_label_size"], 160 / 128)

    def test_drafts_coalesce_off_thread_and_finalize_latest_undo_state(self):
        e = self.editor
        entered, release = threading.Event(), threading.Event()
        writes, threads = [], []
        def delayed(path, payload):
            if path.name == "draft.json":
                writes.append(payload)
                threads.append(threading.get_ident())
                if len(writes) == 1:
                    entered.set()
                    self.assertTrue(release.wait(3))
            return _atomic_json(path, payload)
        with patch("leap_desktop.counting._atomic_json", side_effect=delayed):
            e.addPoint(5, 5)
            self.assertTrue(entered.wait(2))
            try:
                for i in range(8):
                    e.addPoint(10 + i, 10 + i)
                e.setLabelSize(30)
                e.setView(2, 18, 24)
                e._save_draft()
                self.assertEqual(len(writes), 1)
                self.assertEqual(len(writes[0]["state"]["points"]), 4)
            finally:
                release.set()
            self.assertTrue(e._flush_draft())
        self.assertEqual(len(writes), 2)
        self.assertTrue(all(ident != threading.get_ident() for ident in threads))
        self.assertTrue(e.close())
        draft = json.loads((self.base / "draft.json").read_text())
        self.assertEqual((len(draft["state"]["points"]), draft["state"]["label_size"]), (12, 30))
        reopened = self.make_editor()
        self.assertEqual((reopened.viewZoom, reopened.viewPanX, reopened.viewPanY), (2, 18, 24))
        for _ in range(9):
            reopened.undo()
        self.assertEqual((reopened.total, len(reopened.points)), (1, 3))

    def test_draft_write_failure_preserves_session_and_blocks_switch(self):
        e = self.editor
        e.addPoint(5, 5)
        original = e.points
        self.assertTrue(e._flush_draft())
        with patch("leap_desktop.counting._atomic_json", side_effect=OSError("disk full")):
            self.assertFalse(e.close())
            self.assertTrue(e.available)
            self.assertEqual(e.points, original)
            self.assertTrue(e.canUndo)
            with self.assertRaises(RuntimeError):
                e.open_analysis(self.path)
        self.assertTrue(e.close())
        reopened = self.make_editor()
        self.assertEqual(reopened.points, original)

    def test_confirmation_signal_waits_for_final_draft_and_undo_restores_clean_state(self):
        e = self.editor
        observations = []
        e.confirmed.connect(lambda run, summary: observations.append(
            json.loads((self.base / "draft.json").read_text())))
        e.confirm()
        self.wait_until(lambda: len(observations) == 1)
        self.assertEqual(observations[0]["confirmed_fingerprint"], e._confirmed_fingerprint)
        self.assertTrue(e.confirmedState)
        e.addPoint(6, 7)
        self.assertTrue(e.dirty)
        e.undo()
        self.assertFalse(e.dirty)
        self.assertTrue(e.confirmedState)
        self.assertTrue(e.close())
        reopened = self.make_editor()
        self.assertTrue(reopened.confirmedState)
        self.assertFalse(reopened.dirty)

    def test_failed_confirmation_draft_barrier_retains_review_and_suppresses_success(self):
        e = self.editor
        e.addPoint(7, 8)
        self.assertTrue(e._flush_draft())
        emitted = []
        e.confirmed.connect(lambda *args: emitted.append(args))
        def fail_draft(path, payload):
            if path.name == "draft.json":
                raise OSError("disk full")
            return _atomic_json(path, payload)
        with patch("leap_desktop.counting._atomic_json", side_effect=fail_draft):
            e.confirm()
            self.wait_until(lambda: not e.confirmationInProgress)
        self.assertEqual(emitted, [])
        self.assertTrue(e.available)
        self.assertTrue(e.canUndo)
        self.assertTrue(e.dirty)
        self.assertIn("disk full", e.message)
        self.assertTrue((self.base / "results/latest.json").is_file())
        self.assertTrue(e.close())

    def fake_server(self):
        module = self.root / "fake-engine/leap_cell_counting"
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("")
        (module / "__main__.py").write_text(
            "import argparse,json,pathlib,shutil,time,sys,os\n"
            "p=argparse.ArgumentParser(); p.add_argument('--slice-server'); a=p.parse_args(); base=pathlib.Path(a.slice_server).parent\n"
            "with (base/'server-starts.txt').open('a') as f: f.write(str(os.getpid())+'\\n')\n"
            "for line in sys.stdin:\n"
            " r=json.loads(line)\n"
            " with (base/'slice-calls.txt').open('a') as f: f.write(json.dumps(r)+'\\n')\n"
            " time.sleep(0.18 if r['z']==0 else 0.015)\n"
            " shutil.copyfile(base/'rgb.png',r['output'])\n"
            " print(json.dumps({'id':r['id'],'z':r['z'],'path':r['output'],'plane_anchors':{}}),flush=True)\n")
        composite_image(self.base, self.editor.channels, 160, 120).save(str(self.base / "rgb.png"))
        (self.root / "source.tif").write_bytes(b"unused fixture")
        return {"python": sys.executable, "engine": str(module.parent)}

    def test_persistent_server_latest_target_cache_prefetch_and_close(self):
        runtime = self.fake_server()
        self.editor.close()
        e = self.make_editor(runtime=runtime)
        original = e.points
        e.setZ(0)
        self.wait_until(lambda: (self.base / "slice-calls.txt").exists())
        process = e._slice_process
        e.setZ(1)
        e.setZ(2)
        e.addPoint(11, 11)
        self.assertEqual(e.points, original)
        self.wait_until(lambda: not e.busy)
        self.assertEqual(e.displayedZIndex, 2)
        self.assertIs(e._slice_process, process)
        calls = [json.loads(line) for line in (self.base / "slice-calls.txt").read_text().splitlines()]
        self.assertEqual([call["z"] for call in calls[:2]], [0, 2])
        self.assertTrue(all(call["channels"] == [0, 1] for call in calls))
        self.assertEqual(QImage(QUrl(e.imageUrl).toLocalFile()).pixelColor(0, 0).getRgb(), (0, 70, 100, 255))
        self.wait_until(lambda: e._slice_inflight is None)
        call_count = len((self.base / "slice-calls.txt").read_text().splitlines())
        e.setZ(-1)
        self.wait_until(lambda: not e.busy)
        e.setZ(2)
        self.wait_until(lambda: not e.busy)
        self.assertEqual(len((self.base / "slice-calls.txt").read_text().splitlines()), call_count)
        self.assertEqual(len((self.base / "server-starts.txt").read_text().splitlines()), 1)
        started = time.monotonic()
        e.close()
        self.assertLess(time.monotonic() - started, 0.04)
        self.wait_until(lambda: not e._processes)

    def test_real_tiff_server_composes_all_preselected_channels_without_source_mutation(self):
        python = ROOT / "runtime/seed/python/bin/python3.9"
        packages = ROOT / "runtime/seed/site-packages"
        if not python.is_file() or not packages.is_dir():
            self.skipTest("Scientific runtime seed is unavailable")
        env = dict(os.environ, PYTHONPATH=str(packages), PYTHONDONTWRITEBYTECODE="1")
        source = self.root / "inputs/source.tif"
        source.parent.mkdir()
        code = """import numpy as np, tifffile, sys
array=np.zeros((4,3,120,160),dtype=np.uint16)
for z in range(4):
 array[z,0]=30+20*z
 array[z,1]=70+10*z
 array[z,2]=200
metadata={'axes':'ZCYX','PhysicalSizeX':0.4,'PhysicalSizeY':0.4,'PhysicalSizeZ':1.2}
tifffile.imwrite(sys.argv[1],array,ome=True,metadata=metadata,photometric='minisblack')
"""
        subprocess.run([str(python), "-B", "-c", code, str(source)], env=env, check=True, capture_output=True)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        analysis = copy.deepcopy(self.analysis)
        analysis.update(series_index=0, time_index=0, source_path=str(source))
        for channel in analysis["channels"]:
            channel["display_range"] = [0, 255]
        self.editor.close()
        self.path.write_text(json.dumps(analysis))
        e = self.make_editor(runtime={"python": str(python), "engine": str(ROOT / "engine"), "site_packages": str(packages)})
        e.setZ(0)
        self.wait_until(lambda: not e.busy)
        first_process = e._slice_process
        self.assertEqual(QImage(QUrl(e.imageUrl).toLocalFile()).pixelColor(0, 0).getRgb(), (0, 70, 30, 255), e.message)
        e.setZ(2)
        self.wait_until(lambda: not e.busy)
        self.assertIs(e._slice_process, first_process)
        self.assertEqual(QImage(QUrl(e.imageUrl).toLocalFile()).pixelColor(0, 0).getRgb(), (0, 90, 70, 255))
        e.setChannelVisible(1, False)
        self.wait_until(lambda: not e.busy)
        self.assertEqual(QImage(QUrl(e.imageUrl).toLocalFile()).pixelColor(0, 0).getRgb(), (0, 0, 70, 255))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_offline_slice_failure_returns_to_actual_mip(self):
        self.editor.setZ(3)
        self.wait_until(lambda: not self.editor.busy)
        self.assertEqual(self.editor.displayedZIndex, -1)
        self.assertIn("MIP", self.editor.message)
        self.assertEqual(len(self.editor.visiblePoints), 3)

    def test_failed_slice_launch_cleans_process_references(self):
        (self.root / "source.tif").write_bytes(b"unused fixture")
        self.editor.close()
        e = self.make_editor(runtime={"python": str(self.root / "missing-python")})
        e.setZ(1)
        self.wait_until(lambda: not e.busy)
        self.assertIn("无法启动", e.message)
        self.assertEqual(e.displayedZIndex, -1)
        self.assertFalse(e._processes)

    def test_render_and_qml_image_errors_end_loading_without_false_frame_commit(self):
        e = self.editor
        old_z = e.displayedZIndex
        (self.base / "channels/C1.png").unlink()
        e.setChannelVisible(1, False)
        self.wait_until(lambda: not e.busy)
        self.assertTrue(e.frameReady)
        self.assertEqual(len(e.visiblePoints), 3)
        self.assertIn("无法显示", e.message)
        self.assertEqual(e.displayedZIndex, old_z)
        before = e.points
        e.addPoint(12, 14)
        self.assertEqual(e.points, before)
        e._image_url = "file:///missing-frame.png"
        e._z_index = 2
        e._frame_pending = {"url": e.imageUrl, "z": 2, "key": e._slice_key(2),
                            "generation": e._generation, "token": "test-error"}
        e._frame_failed = False
        e.imageFailed("file:///stale.png")
        self.assertTrue(e.busy)
        e.imageFailed(e.imageUrl)
        self.assertFalse(e.busy)
        self.assertTrue(e.frameReady)
        self.assertEqual(e.displayedZIndex, old_z)
        self.assertIn("无法加载", e.message)

    def test_bad_3d_anchor_is_rejected(self):
        self.editor.close()
        invalid = copy.deepcopy(self.analysis)
        invalid["points"][0]["plane_anchors"] = {"4": [40, 30]}
        self.path.write_text(json.dumps(invalid))
        with self.assertRaisesRegex(ValueError, "plane anchor"):
            self.editor.open_analysis(self.path)
        self.assertFalse(self.editor.available)


if __name__ == "__main__":
    unittest.main()
