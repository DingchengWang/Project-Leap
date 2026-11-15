"""Fixed marker rows preserve source-channel identity; no image analysis runs.

Run: runtime/ui-env/bin/python -B -m unittest discover -s tests -p test_fixed_marker_mapping.py -v
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtWidgets import QApplication
from leap_desktop.controller import Controller
from leap_desktop.paths import AppPaths


class FixedMarkerMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="leap-marker-mapping-", dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initial_status = patch.object(Controller, "_initial_status", return_value=None)
        initial_status.start()
        self.addCleanup(initial_status.stop)
        self.backend = Controller(AppPaths(self.root / "AppData"))
        self.backend._timer.stop()
        self.addCleanup(self.backend.deleteLater)
        self.addCleanup(self.backend._release_lock)
        self.backend._component_info = {"status": "ready", "runtime_descriptor": {"component_version": "fixture"}}
        self.changes = []
        self.backend.changed.connect(lambda: self.changes.append(self.snapshot()))

    def snapshot(self):
        return (list(self.backend.channelRoles), list(self.backend.markerSourceIndices), self.backend.measurementMarker)

    def select_metadata(self, hints, *, count=None):
        count = len(hints) if count is None else count
        self.backend._image_info = {"path": str(self.root / "metadata-only.tif"), "name": "metadata-only.tif",
            "series": [{"name": "Fixture series", "axes": "CYX", "shape": [count, 12, 16],
                        "channel_count": count, "time_count": 1, "channel_hints": hints}]}
        self.backend.selectSeries(0)
        self.changes.clear()

    def test_three_sources_have_four_rows_without_fabricating_gfap(self):
        self.assertEqual(self.snapshot(), ([], [-1, -1, -1, -1], ""))
        self.select_metadata(["DAPI", "eGFP", "KCNN2"])
        self.assertEqual(self.snapshot(), (["DAPI", "eGFP", "KCNN2"], [0, 1, -1, 2], "KCNN2"))
        self.assertEqual(self.backend.imageInfo["channel_count"], 3)
        self.backend.assignMarkerSource("GFAP", 3)
        self.assertEqual(self.snapshot(), (["DAPI", "eGFP", "KCNN2"], [0, 1, -1, 2], "KCNN2"))
        self.assertEqual(self.changes, [])

    def test_reassignment_displaces_one_row_without_swapping_and_emits_atomic_state(self):
        self.select_metadata(["DAPI", "eGFP", "KCNN2"])
        self.backend.assignMarkerSource("GFAP", 1)
        self.assertEqual(self.changes, [(["DAPI", "GFAP", "KCNN2"], [0, -1, 1, 2], "KCNN2")])
        self.backend.assignMarkerSource("DAPI", 2)
        self.assertEqual(self.changes[-1], (["Ignore", "GFAP", "DAPI"], [2, -1, 1, -1], "KCNN2"))
        self.assertEqual(len(self.changes), 2)
        self.backend.assignMarkerSource("Target", 0)
        self.assertEqual(self.changes[-1], (["KCNN2", "GFAP", "DAPI"], [2, -1, 1, 0], "KCNN2"))
        self.backend.assignMarkerSource("GFAP", -1)
        self.assertEqual(self.snapshot(), (["KCNN2", "Ignore", "DAPI"], [2, -1, -1, 0], "KCNN2"))
        self.assertEqual(len(self.changes), 4)
        self.backend.assignMarkerSource("GFAP", -1)
        self.assertEqual(len(self.changes), 4)

    def test_target_requires_explicit_marker_and_keeps_choice_while_unassigned(self):
        self.select_metadata(["DAPI", "GFAP", "Channel 3"])
        original = self.snapshot()
        self.backend.assignMarkerSource("Target", 2)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.changes, [])
        self.backend.setMeasurementMarker("KCNJ10")
        self.assertEqual(self.snapshot(), (["DAPI", "GFAP", "Ignore"], [0, -1, 1, -1], "KCNJ10"))
        self.backend.assignMarkerSource("Target", 2)
        self.backend.setMeasurementMarker("KCNN3")
        self.assertEqual(self.snapshot(), (["DAPI", "GFAP", "KCNN3"], [0, -1, 1, 2], "KCNN3"))
        self.backend.assignMarkerSource("Target", -1)
        self.assertEqual(self.snapshot(), (["DAPI", "GFAP", "Ignore"], [0, -1, 1, -1], "KCNN3"))
        self.backend.setMeasurementMarker("KCNN1")
        self.backend.assignMarkerSource("Target", 2)
        self.assertEqual(self.snapshot(), (["DAPI", "GFAP", "KCNN1"], [0, -1, 1, 2], "KCNN1"))
        self.assertEqual(len(self.changes), 6)

    def test_more_than_four_sources_retain_original_positions_in_import_request(self):
        self.select_metadata(["unused", "eGFP", "unused", "unused", "DAPI", "KCNN2"])
        self.assertEqual(self.backend.markerSourceIndices, [4, 1, -1, 5])
        self.backend.assignMarkerSource("GFAP", 3)
        self.backend.assignMarkerSource("Target", 0)
        expected = ["KCNN2", "eGFP", "Ignore", "GFAP", "DAPI", "Ignore"]
        self.assertEqual(self.backend.channelRoles, expected)
        with patch.object(self.backend, "_launch") as launch:
            self.backend.startAnalysis()
            self.assertEqual(launch.call_args.args[0], "import")
        request_path = self.backend._run_dir / "import-request.json"
        request = json.loads(request_path.read_text())
        self.assertEqual(request["channel_roles"], expected)
        self.assertEqual(request["source_path"], str(self.root / "metadata-only.tif"))
        self.assertEqual(request["series_index"], 0)
        self.assertEqual(request["time_index"], 0)
        self.assertIsNone(request["profile"])
        self.assertEqual(request["destination_dir"], str(self.backend._run_dir / "workspace/Sample Image"))

    def test_source_count_is_authoritative_when_optional_hints_are_incomplete(self):
        self.select_metadata(["DAPI", "GFAP"], count=5)
        self.assertEqual(self.backend.channelRoles, ["DAPI", "GFAP", "Ignore", "Ignore", "Ignore"])
        self.assertEqual(self.backend.imageInfo["channel_hints"], ["DAPI", "GFAP", "Channel 3", "Channel 4", "Channel 5"])
        self.backend.setMeasurementMarker("KCNN2")
        self.backend.assignMarkerSource("Target", 4)
        self.assertEqual(self.backend.markerSourceIndices, [0, -1, 1, 4])
        self.select_metadata(["DAPI", "GFAP", "KCNN2", "KCNN3"], count=3)
        self.assertEqual(self.snapshot(), (["DAPI", "GFAP", "KCNN2"], [0, -1, 1, 2], "KCNN2"))

    def test_invalid_indices_keys_and_markers_leave_state_and_notifications_unchanged(self):
        self.select_metadata(["DAPI", "eGFP", "KCNN2"])
        original = self.snapshot()
        for index in (-2, 3, 100, True, 1.0, "1", None, []):
            with self.subTest(index=index):
                self.backend.assignMarkerSource("DAPI", index)
                self.backend.setChannelRole(index, "DAPI")
                self.assertEqual(self.snapshot(), original)
        for marker in ("", "Ignore", "KCNN2", None, [], {}):
            with self.subTest(marker_key=marker):
                self.backend.assignMarkerSource(marker, 1)
                self.assertEqual(self.snapshot(), original)
        for marker in ("", "Ignore", "DAPI", "Target", "KCNN4", None, [], {}):
            with self.subTest(target_marker=marker):
                self.backend.setMeasurementMarker(marker)
                self.assertEqual(self.snapshot(), original)
        for role in ("Target", "KCNN4", "", None, [], {}):
            with self.subTest(legacy_role=role):
                self.backend.setChannelRole(1, role)
                self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.changes, [])

    def test_ambiguous_metadata_requires_explicit_choice_and_never_hides_extra_roles(self):
        hints = ["DAPI", "eGFP", "GFAP", "KCNN1", "KCNN2"]
        self.select_metadata(hints)
        self.assertEqual(self.snapshot(), (["DAPI", "eGFP", "GFAP", "Ignore", "Ignore"], [0, 1, 2, -1], ""))
        self.assertEqual(self.backend.imageInfo["channel_hints"], hints)
        self.assertEqual(self.backend.imageInfo["channel_count"], 5)
        self.backend.assignMarkerSource("Target", 4)
        self.assertEqual(self.backend.markerSourceIndices[3], -1)
        self.backend.setMeasurementMarker("KCNN2")
        self.backend.assignMarkerSource("Target", 4)
        self.assertEqual(self.snapshot(), (["DAPI", "eGFP", "GFAP", "Ignore", "KCNN2"], [0, 1, 2, 4], "KCNN2"))
        self.select_metadata(["DAPI", "DAPI", "eGFP", "KCNN2"])
        self.assertEqual(self.snapshot(), (["Ignore", "Ignore", "eGFP", "KCNN2"], [-1, 2, -1, 3], "KCNN2"))
        self.backend.assignMarkerSource("DAPI", 1)
        self.assertEqual(self.snapshot(), (["Ignore", "DAPI", "eGFP", "KCNN2"], [1, 2, -1, 3], "KCNN2"))

    def test_metadata_bounds_and_busy_states_prevent_edits(self):
        self.select_metadata(["DAPI", "eGFP", "KCNN2"])
        original = self.snapshot()
        self.backend._image_info["channel_count"] = 2
        self.backend.assignMarkerSource("GFAP", 2)
        self.assertEqual(self.snapshot(), original)
        self.backend._image_info["channel_count"] = 3
        for busy_attribute in ("_busy", "_maintenance_busy"):
            with self.subTest(gate=busy_attribute):
                setattr(self.backend, busy_attribute, True)
                self.backend.assignMarkerSource("GFAP", 1)
                self.backend.assignMarkerSource("Target", -1)
                self.backend.setMeasurementMarker("KCNN1")
                self.backend.setChannelRole(1, "GFAP")
                self.assertEqual(self.snapshot(), original)
                setattr(self.backend, busy_attribute, False)
        self.assertEqual(self.changes, [])

    def test_legacy_source_consumer_still_updates_real_target_role(self):
        self.select_metadata(["DAPI", "eGFP", "KCNN2"])
        self.backend.setChannelRole(1, "GFAP")
        self.backend.setChannelRole(2, "KCNJ10")
        self.assertEqual(self.snapshot(), (["DAPI", "GFAP", "KCNJ10"], [0, -1, 1, 2], "KCNJ10"))
        self.backend.setChannelRole(2, "Ignore")
        self.assertEqual(self.snapshot(), (["DAPI", "GFAP", "Ignore"], [0, -1, 1, -1], "KCNJ10"))

    def test_new_series_module_and_inspection_reset_pending_target_choice(self):
        self.select_metadata(["DAPI", "eGFP", "KCNN2"])
        self.backend.assignMarkerSource("Target", -1)
        self.backend.setMeasurementMarker("KCNJ10")
        self.backend._image_info["series"].append({"name": "Unlabelled", "axes": "CYX", "shape": [3, 12, 16],
                                                   "channel_count": 3, "time_count": 1})
        self.backend.selectSeries(1)
        self.assertEqual(self.snapshot(), (["Ignore"] * 3, [-1] * 4, ""))
        self.backend.selectSeries(0)
        self.assertEqual(self.backend.measurementMarker, "KCNN2")
        self.backend._module_states["fixture-module"] = {"installed": True}
        self.backend._activate_module("fixture-module")
        self.assertEqual(self.snapshot(), ([], [-1] * 4, ""))
        self.backend._active_module_id = "project-leap-2d"
        self.select_metadata(["DAPI", "GFAP", "KCNN3"])
        fixture = self.root / "never-decoded.tif"
        fixture.write_bytes(b"Inspection launch fixture, not TIFF content")
        self.backend._component_info = {"status": "ready"}
        with patch.object(self.backend, "_launch") as launch:
            self.backend.inspectImage(str(fixture))
            self.assertEqual(launch.call_args.args[0], "inspect")
        self.assertEqual(self.snapshot(), ([], [-1] * 4, ""))


if __name__ == "__main__":
    unittest.main()
