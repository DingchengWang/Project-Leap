"""Native cell identity tests: independent body and nuclear label namespaces.

Synthetic display data and immutable evidence placeholders exercise only the Qt
editor. Scientific tests separately verify that the declared anchors hit masks.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import test_counting_editor as fixture
from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from leap_desktop.counting import CountingEditor, make_merged_point, publish_result


def observation(kind, label, anchors, *, decision="accepted"):
    return {
        "id": f"{'nucleus' if kind == 'nucleus' else 'support-body'}-{label}",
        "kind": kind, "artifact_id": "dapi_nuclei" if kind == "nucleus" else "support_bodies",
        "label_id": label, "bounds_zyx": [[0, 4], [0, 120], [0, 160]],
        "plane_anchors": anchors, "decision": decision, "reasons": [f"{kind}_evidence"],
        "metrics": {"volume_um3": 23.75, "distinctive_extension": {"retained": True}},
    }


def cell(ident, source, observations, anchor_observations, *, status="accepted"):
    by_id = {item["id"]: item for item in observations}
    anchors = {z: copy.deepcopy(by_id[obs_id]["plane_anchors"][z])
               for z, obs_id in anchor_observations.items()}
    z = int(next(iter(anchors)))
    representative = observations[0]
    return {
        "id": ident, "cell_id": ident, "source": source, "status": status,
        "label_artifact_id": representative["artifact_id"], "label_id": representative["label_id"],
        "x": anchors[str(z)][0], "y": anchors[str(z)][1], "z": z,
        "source_observations": observations, "plane_anchors": anchors,
        "plane_anchor_sources": {plane: {"kind": "observation", "observation_id": obs_id}
                                 for plane, obs_id in anchor_observations.items()},
        "association": {"status": "one_to_one" if source == "dapi_support" else "unmatched",
                        "method": "native_3d_enclosure", "evidence": {"not_iou": True}},
        "future_scientific_metadata": {"original": [1, 2, 3]},
    }


class MultiObservationCellEditorTests(unittest.TestCase):
    # Reuse the isolated asynchronous test harness without inheriting its tests.
    make_editor = fixture.CountingEditorTests.make_editor
    cleanup_editors = fixture.CountingEditorTests.cleanup_editors
    wait_until = fixture.CountingEditorTests.wait_until
    cached_slice = fixture.CountingEditorTests.cached_slice

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        fixture.CountingEditorTests.setUp(self)
        self.editor.close()
        (self.base / "draft.json").unlink(missing_ok=True)
        # Numeric label 2 deliberately exists in both artifact namespaces.
        nucleus = observation("nucleus", 2, {"0": [40, 30], "1": [41, 31]})
        body = observation("body", 2, {"2": [80, 65], "3": [82, 66]})
        paired_nucleus = observation("nucleus", 9, {"1": [110, 70], "2": [111, 71]})
        paired_body = observation("body", 8, {"0": [108, 70], "1": [109, 71], "2": [110, 72], "3": [111, 73]})
        excluded = observation("body", 99, {"3": [130, 90]}, decision="excluded")
        self.analysis.update(identity_model="multi_observation_cell_v1", auto_count=2,
            label_artifacts={
                "dapi_nuclei": {"path": "labels.npy", "kind": "nucleus", "axes": "ZYX",
                                "origin_zyx": [0, 0, 0], "shape_zyx": [4, 120, 160]},
                "support_bodies": {"path": "body-labels.npy", "kind": "body", "axes": "ZYX",
                                   "origin_zyx": [0, 0, 0], "shape_zyx": [4, 120, 160]}},
            points=[cell("auto-2", "auto", [nucleus], {"0": "nucleus-2", "1": "nucleus-2"}),
                    cell("body-2", "support_body", [body], {"2": "support-body-2", "3": "support-body-2"}),
                    cell("auto-9", "dapi_support", [paired_nucleus, paired_body],
                         {"0": "support-body-8", "1": "nucleus-9", "2": "nucleus-9", "3": "support-body-8"},
                         status="review"),
                    cell("body-99", "support_body", [excluded], {"3": "support-body-99"}, status="excluded")])
        self.analysis["channels"][2]["path"] = None
        # These are read-only evidence sentinels, not fabricated scientific masks.
        for name in ("labels.npy", "body-labels.npy"):
            (self.base / name).write_bytes(f"immutable-{name}".encode())
        self.path.write_text(json.dumps(self.analysis))
        self.editor = self.make_editor()

    def select(self, *ids):
        self.editor.selectPoint("", False)
        for ident in ids:
            self.editor.selectPoint(ident, True)

    def test_body_and_paired_cells_count_once_with_actual_same_plane_anchors(self):
        e = self.editor
        original = e.points
        self.assertEqual((e.total, e.pendingCount, sum(p.get('status') == 'excluded' for p in e.points)), (2, 1, 1))
        self.assertEqual(len(e.points[2]["source_observations"]), 2)
        self.assertEqual([p["id"] for p in e.visiblePoints], ["auto-2", "body-2", "auto-9", "body-99"])
        expected = {
            0: [("auto-2", 40, 30, 1), ("auto-9", 108, 70, 3)],
            1: [("auto-2", 41, 31, 1), ("auto-9", 110, 70, 3)],
            2: [("body-2", 80, 65, 2), ("auto-9", 111, 71, 3)],
            3: [("body-2", 82, 66, 2), ("auto-9", 111, 73, 3), ("body-99", 130, 90, 4)],
        }
        for z, rows in expected.items():
            self.cached_slice(z)
            self.assertEqual([(p["id"], p["x"], p["y"], p["display_no"]) for p in e.visiblePoints], rows)
            for point in e.visiblePoints:
                evidence = next(item for item in e.points if item["id"] == point["id"])
                anchor_source = evidence["plane_anchor_sources"][str(z)]
                obs = next(obs for obs in evidence["source_observations"] if obs["id"] == anchor_source["observation_id"])
                self.assertEqual(obs["plane_anchors"][str(z)], [point["x"], point["y"]])
        self.assertEqual(e.points, original)
        self.assertIn("body-99", [p["id"] for p in e.visiblePoints])
        e.activatePoint("body-99", False)
        self.assertEqual(e.selectedIds, ["body-99"])
        self.assertTrue(e.canAcceptSelected)

    def test_batch_deletion_and_undo_reopen_preserve_every_observation(self):
        e = self.editor
        original = e.points
        self.select("body-2", "auto-9")
        e.deleteSelected()
        self.assertEqual((e.total, e.pendingCount, sum(p.get('status') == 'excluded' for p in e.points)), (1, 0, 1))
        for before, after in zip(original, e.points):
            self.assertEqual({k: v for k, v in before.items() if k != "deleted"},
                             {k: v for k, v in after.items() if k != "deleted"})
        e.undo()
        self.assertEqual(e.points, original)
        e.redo()
        e.close()
        reopened = self.make_editor()
        reopened.undo()
        self.assertEqual(reopened.points, original)
        reopened.selectPoint("auto-9", False)
        reopened.acceptSelected()
        self.assertEqual((reopened.total, reopened.pendingCount, sum(p.get('status') == 'excluded' for p in reopened.points)), (3, 0, 1))
        self.assertEqual(reopened.points[2]["source_observations"], original[2]["source_observations"])

    def test_ordinary_pending_activation_selects_and_explicit_accept_preserves_identity(self):
        e = self.editor
        original = e.points
        numbers = [p["display_no"] for p in original]
        self.assertEqual({row["id"] for row in e.labelsForScale(1)}, {"auto-2", "body-2"})
        e.activatePoint("auto-9", False)
        self.assertEqual((e.total, e.pendingCount, len(e.points)), (2, 1, 4))
        self.assertEqual(e.selectedIds, ["auto-9"])
        self.assertTrue(e.canAcceptSelected)
        e.acceptSelected()
        self.assertEqual((e.total, e.pendingCount, len(e.points)), (3, 0, 4))
        self.assertEqual([p["id"] for p in e.points], [p["id"] for p in original])
        self.assertEqual([p["display_no"] for p in e.points], numbers)
        self.assertEqual(e.points[2]["source_observations"], original[2]["source_observations"])
        self.assertEqual({row["id"] for row in e.labelsForScale(1)}, {"auto-2", "body-2", "auto-9"})
        e.activatePoint("auto-9", False)
        self.assertEqual(len(e._undo), 1)
        e.undo()
        self.assertEqual(e.points, original)
        self.assertEqual((e.total, e.pendingCount), (2, 1))
        self.assertNotIn("auto-9", {row["id"] for row in e.labelsForScale(1)})
        e.activatePoint("body-99", False)
        self.assertEqual(e.points, original)
        self.assertEqual(e.selectedIds, ["body-99"])
        self.assertTrue(e.canAcceptSelected)

    def test_astrocyte_hint_is_explicit_preserved_metadata_and_does_not_change_count(self):
        self.editor.close()
        (self.base / "draft.json").unlink(missing_ok=True)
        self.analysis["points"][2]["review_hint"] = "likely_astrocyte"
        self.analysis["points"][1]["status"] = "review"
        self.path.write_text(json.dumps(self.analysis))
        self.editor = self.make_editor()
        e = self.editor
        original = e.points
        self.assertEqual((e.total, e.pendingCount, e.neuronCount), (1, 2, 1))
        self.assertEqual((e.likelyUnrecognizedCount, e.ordinaryUnrecognizedCount), (1, 1))
        self.assertEqual(len(e.visiblePoints), 4)
        self.assertEqual([p["id"] for p in e.visiblePoints if p.get("review_hint") == "likely_astrocyte"], ["auto-9"])
        self.assertNotIn("auto-9", {row["id"] for row in e.labelsForScale(1)})
        result = publish_result(self.path, self.analysis, e._state())
        reopened = self.make_editor(Path(result["session_path"]))
        self.assertTrue(reopened.confirmedState)
        self.assertEqual(reopened.points, original)
        reopened.activatePoint("auto-9", False)
        self.assertEqual((reopened.total, reopened.pendingCount, reopened.neuronCount), (2, 1, 1))
        self.assertEqual((reopened.likelyUnrecognizedCount, reopened.ordinaryUnrecognizedCount), (0, 1))
        self.assertEqual(reopened.points[2]["review_hint"], "likely_astrocyte")
        reopened.undo()
        self.assertEqual(reopened.points, original)

    def test_command_selection_batches_candidate_acceptance_without_duplicate_cells(self):
        self.editor.close()
        (self.base / "draft.json").unlink(missing_ok=True)
        self.analysis["points"][1]["status"] = "review"
        self.path.write_text(json.dumps(self.analysis))
        self.editor = self.make_editor()
        e = self.editor
        original = e.points
        e.activatePoint("body-2", True)
        e.activatePoint("auto-9", True)
        self.assertEqual((e.total, e.pendingCount), (1, 2))
        self.assertEqual(set(e.selectedIds), {"body-2", "auto-9"})
        e.acceptSelected()
        self.assertEqual((e.total, e.pendingCount, len(e.points)), (3, 0, 4))
        self.assertEqual(len(e._undo), 1)
        e.undo()
        self.assertEqual(e.points, original)
        e.redo()
        for ident in ("body-2", "auto-9"):
            e.activatePoint(ident, True)
        e.deleteSelected()
        self.assertEqual((e.total, e.pendingCount), (1, 0))
        self.assertEqual([p["id"] for p in e.visiblePoints if p["status"] == "accepted"], ["auto-2"])
        self.assertEqual(len(e.visiblePoints), 2)
        self.assertEqual(e.neuronCount, 1)
        self.assertEqual(sum(p.get("deleted", False) for p in e.points), 2)
        e.undo()
        self.assertEqual((e.total, e.pendingCount), (3, 0))
        self.assertEqual([p["source_observations"] for p in e.points], [p["source_observations"] for p in original])

    def test_pending_only_session_confirms_zero_and_reopens_with_all_evidence(self):
        state = self.editor._state()
        for point in state["points"]:
            if point["status"] == "accepted":
                point["status"] = "review"
        result = publish_result(self.path, self.analysis, state)
        self.assertEqual((result["count"], result["pending_count"]), (0, 3))
        with Path(result["csv_path"]).open() as stream:
            self.assertEqual(list(csv.DictReader(stream)), [])
        self.assertEqual(QImage(result["overlay_path"]).text("count"), "0")
        saved = json.loads(Path(result["session_path"]).read_text())
        self.assertEqual(saved["points"], state["points"])
        self.assertEqual(saved["pending_count"], 3)
        reopened = self.make_editor(Path(result["session_path"]))
        self.assertEqual((reopened.total, reopened.pendingCount), (0, 3))
        self.assertEqual(reopened.points, state["points"])
        self.assertEqual(reopened.labelsForScale(1), [])
        self.assertTrue(reopened.confirmedState)
        self.assertFalse(reopened.dirty)

    def test_neuron_explicit_correction_and_manual_deletion_are_distinct_saved_edits(self):
        e = self.editor
        original = e.points
        e.activatePoint("body-99", False)
        self.assertEqual((e.total, e.neuronCount), (2, 1))
        self.assertTrue(e.canAcceptSelected)
        e.acceptSelected()
        self.assertEqual((e.total, e.neuronCount), (3, 0))
        self.assertEqual(e.points[-1]["source_observations"], original[-1]["source_observations"])
        e.undo()
        self.assertEqual(e.points, original)
        e.selectPoint("auto-2", False)
        e.deleteSelected()
        self.assertEqual(e.points[0]["status"], "accepted")
        self.assertTrue(e.points[0]["deleted"])
        self.assertEqual((e.total, e.pendingCount, e.neuronCount), (1, 1, 1))
        self.assertEqual({p["id"] for p in e.visiblePoints}, {"body-2", "auto-9", "body-99"})
        e.selectPoint("auto-2", False)
        self.assertEqual(e.selectedIds, [])
        result = publish_result(self.path, self.analysis, e._state())
        saved = json.loads(Path(result["session_path"]).read_text())
        self.assertEqual((saved["count"], saved["pending_count"], saved["excluded_count"], saved["deleted_count"]), (1, 1, 1, 1))
        with Path(result["csv_path"]).open() as stream:
            self.assertEqual([row["id"] for row in csv.DictReader(stream)], ["body-2"])
        reopened = self.make_editor(Path(result["session_path"]))
        self.assertEqual(reopened.points, e.points)
        self.assertEqual((reopened.total, reopened.pendingCount, reopened.neuronCount), (1, 1, 1))
        e.close()
        draft = self.make_editor()
        draft.undo()
        self.assertEqual(draft.points, original)
        self.assertEqual((draft.total, draft.pendingCount, draft.neuronCount), (2, 1, 1))

    def test_merge_keeps_namespaced_labels_anchor_sources_and_nested_parent_evidence(self):
        e = self.editor
        original = e.points
        self.select("auto-2", "body-2")
        e.mergeSelected()
        first = e.points[0]
        self.assertEqual(first["id"], first["cell_id"])
        self.assertEqual(first["label_ids"], [])
        self.assertIsNone(first["label_artifact_id"])
        self.assertEqual({(o["artifact_id"], o["label_id"]) for o in first["source_observations"]},
                         {("dapi_nuclei", 2), ("support_bodies", 2)})
        self.assertEqual(first["source_observations"], original[0]["source_observations"] + original[1]["source_observations"])
        self.assertEqual(first["plane_anchor_sources"]["3"]["observation_id"], "support-body-2")
        self.assertEqual(first["merge_lineage"][1]["association"], original[1]["association"])
        self.assertEqual((e.total, e.pendingCount), (1, 1))
        self.select(first["id"], "auto-9")
        e.mergeSelected()
        nested = e.points[0]
        self.assertEqual(len(nested["source_observations"]), 4)
        self.assertEqual(nested["status"], "review")
        self.assertEqual(nested["merge_lineage"][0]["parents"][0]["future_scientific_metadata"],
                         original[0]["future_scientific_metadata"])
        self.assertEqual(nested["merge_lineage"][1]["source"], "dapi_support")
        e.close()
        reopened = self.make_editor()
        self.assertEqual(reopened.points[0], nested)
        reopened.undo()
        self.assertEqual(reopened.points[0], first)
        reopened.undo()
        self.assertEqual(reopened.points, original)
        reopened.redo()
        reopened.redo()
        self.assertEqual(reopened.points[0], nested)

    def test_manual_add_and_merge_keep_manual_identity_and_no_invented_mip_plane(self):
        e = self.editor
        e.addPoint(14, 15)
        mip = e.points[-1]
        self.assertEqual(mip["cell_id"], mip["id"])
        self.assertEqual(mip["source_observations"], [])
        self.assertEqual(mip["plane_anchor_sources"], {})
        self.cached_slice(0)
        self.assertNotIn(mip["id"], [p["id"] for p in e.visiblePoints])
        e.addPoint(16, 17)
        plane = e.points[-1]
        self.assertEqual(plane["plane_anchor_sources"], {"0": {"kind": "manual", "point_id": plane["id"]}})
        self.select("body-2", plane["id"])
        e.mergeSelected()
        merged = next(p for p in e.points if p["source"] == "merged")
        self.assertEqual(merged["plane_anchor_sources"]["0"], plane["plane_anchor_sources"]["0"])
        self.assertEqual(merged["plane_anchor_sources"]["2"]["observation_id"], "support-body-2")
        self.assertEqual(len(merged["source_observations"]), 1)
        self.assertEqual(e._validate_points(e.points), e.points)

    def test_publication_rebases_both_artifacts_and_reopens_after_moving_run(self):
        e = self.editor
        self.assertEqual(e.pendingCount, 1)
        self.select("auto-9")
        e.acceptSelected()
        original = e.points
        evidence = {name: (self.base / name).read_bytes() for name in ("labels.npy", "body-labels.npy")}
        result = publish_result(self.path, self.analysis, e._state())
        saved_path = Path(result["session_path"])
        session = json.loads(saved_path.read_text())
        self.assertEqual((session["count"], session["excluded_count"]), (3, 1))
        self.assertEqual(session["identity_model"], "multi_observation_cell_v1")
        self.assertEqual(session["points"], original)
        for key, artifact in session["label_artifacts"].items():
            self.assertEqual((saved_path.parent / artifact["path"]).resolve(),
                             (self.base / self.analysis["label_artifacts"][key]["path"]).resolve())
            self.assertFalse(Path(artifact["path"]).is_absolute())
        with Path(result["csv_path"]).open() as stream:
            rows = list(csv.DictReader(stream))
        accepted = [p for p in original if p["status"] == "accepted"]
        self.assertEqual([json.loads(row["point_metadata"]) for row in rows], accepted)
        self.assertEqual([json.loads(row["source_observations"]) for row in rows], [p["source_observations"] for p in accepted])
        self.assertEqual(rows[1]["label_artifact_id"], "support_bodies")
        self.assertEqual(QImage(result["overlay_path"]).text("count"), "3")
        e.close()
        moved = self.root / "relocated" / "run-a"
        moved.parent.mkdir()
        shutil.move(str(self.base.parent), str(moved))
        saved_path = moved / "counting" / saved_path.relative_to(self.base)
        reopened = self.make_editor(saved_path)
        self.assertTrue(reopened.confirmedState, reopened.message)
        self.assertEqual(reopened.points, original)
        self.assertEqual(reopened.labelArtifacts, self.analysis["label_artifacts"])
        self.assertEqual(reopened.total, 3)
        for name, content in evidence.items():
            self.assertEqual((moved / "counting" / name).read_bytes(), content)
        for artifact in json.loads(saved_path.read_text())["label_artifacts"].values():
            self.assertTrue((saved_path.parent / artifact["path"]).resolve().is_file())

    def test_rejects_misidentified_artifacts_fabricated_anchors_and_duplicate_observations(self):
        original = self.editor.points
        cases = []
        points = copy.deepcopy(original)
        points[1]["label_artifact_id"] = "dapi_nuclei"
        cases.append(points)
        points = copy.deepcopy(original)
        points[1]["source_observations"][0]["kind"] = "nucleus"
        cases.append(points)
        points = copy.deepcopy(original)
        points[2]["plane_anchors"]["0"] = [109, 70]
        cases.append(points)
        points = copy.deepcopy(original)
        points[1]["cell_id"] = "auto-2"
        cases.append(points)
        points = copy.deepcopy(original)
        duplicate = copy.deepcopy(points[1])
        duplicate.update(id="body-duplicate", cell_id="body-duplicate", display_no=5)
        points.append(duplicate)
        cases.append(points)
        for points in cases:
            with self.subTest(points=points[1]["id"]), self.assertRaises(ValueError):
                self.editor._validate_points(points)
        self.assertEqual(self.editor.points, original)
        self.editor._analysis["label_artifacts"]["support_bodies"]["path"] = "labels.npy"
        with self.assertRaisesRegex(ValueError, "same label artifact"):
            self.editor._validate_label_artifacts()
        self.editor._analysis["label_artifacts"]["support_bodies"]["path"] = "body-labels.npy"
        conflicting = copy.deepcopy(original[1])
        conflicting["id"] = conflicting["cell_id"] = "body-other"
        conflicting["source_observations"][0]["metrics"]["volume_um3"] = 99
        with self.assertRaisesRegex(ValueError, "conflicting evidence"):
            make_merged_point([original[1], conflicting], 5)

    def test_unselected_channel_without_preview_and_unknown_experimental_fields_are_ignored(self):
        self.assertEqual([c["index"] for c in self.editor.channels], [0, 1])
        self.editor.close()
        (self.base / "draft.json").unlink(missing_ok=True)
        self.analysis.update(display_channels=[], support_review_hints=[{"id": "obsolete", "x": 12, "y": 13, "z": 1}])
        for channel in self.analysis["channels"]:
            channel["path"] = None
        self.path.write_text(json.dumps(self.analysis))
        self.editor = self.make_editor()
        self.assertEqual(self.editor.channels, [])
        self.assertEqual((self.editor.total, self.editor.pendingCount, len(self.editor.points)), (2, 1, 4))
        self.assertEqual(QImage(QUrl(self.editor.imageUrl).toLocalFile()).pixelColor(0, 0).getRgb(), (0, 0, 0, 255))
        self.select("auto-9")
        self.editor.acceptSelected()
        result = publish_result(self.path, self.analysis, self.editor._state())
        session = json.loads(Path(result["session_path"]).read_text())
        self.assertEqual(session["count"], 3)
        self.assertNotIn("support_review_hints", session)

    def test_engine_identity_output_with_native_masks_opens_without_source_loss(self):
        python = fixture.ROOT / "runtime/seed/python/bin/python3.9"
        packages = fixture.ROOT / "runtime/seed/site-packages"
        if not python.is_file() or not packages.is_dir():
            self.skipTest("Scientific runtime seed is unavailable")
        self.editor.close()
        (self.base / "draft.json").unlink(missing_ok=True)
        # Real engine identity assembly, with explicit synthetic match decisions.
        # This verifies the interface and exact mask-anchor references, not the
        # biological quality of the detector or its association decisions.
        code = """import json, pathlib, sys
import numpy as np
from leap_cell_counting.identity import unify_cells
path=pathlib.Path(sys.argv[1]); analysis=json.loads(path.read_text())
observations=[o for point in analysis['points'] for o in point['source_observations']]
labels={name:np.zeros((4,120,160),dtype=np.uint16) for name in analysis['label_artifacts']}
for observation in observations:
 first=next(iter(observation['plane_anchors']))
 observation['representative_xyz']=[*observation['plane_anchors'][first],int(first)]
 for plane,(x,y) in observation['plane_anchors'].items():
  labels[observation['artifact_id']][int(plane),int(y),int(x)]=observation['label_id']
relations=[{'nucleus_id':'nucleus-9','body_id':'support-body-8','decision':'match','evidence':{'synthetic_fixture':True}}]
analysis['points']=unify_cells(observations,relations)
for name,array in labels.items(): np.save(path.parent/analysis['label_artifacts'][name]['path'],array)
checked=0
for point in analysis['points']:
 by_id={o['id']:o for o in point['source_observations']}
 for plane,(x,y) in point['plane_anchors'].items():
  observation=by_id[point['plane_anchor_sources'][plane]['observation_id']]
  assert int(labels[observation['artifact_id']][int(plane),int(y),int(x)])==observation['label_id']
  checked+=1
path.write_text(json.dumps(analysis))
print(json.dumps({'cells':len(analysis['points']),'actual_mask_anchor_checks':checked}))
"""
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(fixture.ROOT / "engine"), str(packages)]),
                   PYTHONDONTWRITEBYTECODE="1")
        completed = subprocess.run([str(python), "-B", "-c", code, str(self.path)],
                                   env=env, check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(completed.stdout), {"cells": 4, "actual_mask_anchor_checks": 9})
        self.analysis = json.loads(self.path.read_text())
        hashes = {name: hashlib.sha256((self.base / name).read_bytes()).hexdigest()
                  for name in ("labels.npy", "body-labels.npy")}
        self.editor = self.make_editor()
        e = self.editor
        self.assertEqual((e.total, e.pendingCount, sum(p.get('status') == 'excluded' for p in e.points)), (3, 0, 1))
        self.assertTrue(all(p["id"] == p["cell_id"] and p["id"].startswith("cell-") for p in e.points))
        self.assertEqual([p["source_observations"] for p in e.points],
                         [p["source_observations"] for p in self.analysis["points"]])
        for z in range(4):
            self.cached_slice(z)
            self.assertEqual({p["id"] for p in e.visiblePoints},
                             {p["id"] for p in self.analysis["points"]
                              if str(z) in p["plane_anchors"]})
        result = publish_result(self.path, self.analysis, e._state())
        e.close()
        reopened = self.make_editor(Path(result["session_path"]))
        self.assertEqual(reopened.total, 3)
        self.assertEqual([p["source_observations"] for p in reopened.points],
                         [p["source_observations"] for p in self.analysis["points"]])
        self.assertEqual(hashes, {name: hashlib.sha256((self.base / name).read_bytes()).hexdigest()
                                 for name in hashes})


if __name__ == "__main__":
    unittest.main()
