"""Native counting review: immutable image inputs, editable points, versioned results.

Only Qt and the standard library run in the UI interpreter. Grayscale display
planes are composed with QImage/QPainter; the scientific worker owns TIFF reads.
Coordinates remain in original image pixels. Review label size uses logical screen
pixels; export labels scale with output dimensions independently of review zoom.
"""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

from PySide6.QtCore import (QObject, Property, QProcess, QProcessEnvironment, QRectF,
                            QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal, Slot)
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPainterPath, QPen, QTransform, qRgb


FONT_FAMILY = "Arial"
OVERLAY_COLOR = "#56C987"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _relative(path: Path, base: Path) -> str:
    return os.path.relpath(path, base)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def _fingerprint(state: dict) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _font(size: float) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPixelSize(max(1, round(size)))
    font.setBold(True)
    return font


@lru_cache(maxsize=4096)
def _glyph_bounds(text: str) -> tuple[float, float, float, float]:
    # One normalized font gives fractional geometry at every review/export scale.
    path = QPainterPath()
    path.addText(0, 0, _font(128), text)
    bounds = path.boundingRect()
    return bounds.left(), bounds.top(), bounds.width(), bounds.height()


def reindex_display_numbers(points: list[dict]) -> list[dict]:
    """Number counted cells globally using representative original-image coordinates.

    Candidate/deleted rows follow counted cells to retain unique editor numbers.
    Identity, observation ownership and per-plane anchors are never altered.
    """
    ordered = sorted(points, key=lambda p: (
        not (p.get("status", "accepted") == "accepted" and not p.get("deleted", False)),
        float(p["y"]), float(p["x"]), str(p["id"])))
    numbers = {p["id"]: index + 1 for index, p in enumerate(ordered)}
    return [p if p.get("display_no") == numbers[p["id"]] else
            {**p, "display_no": numbers[p["id"]]} for p in points]


def export_label_size(width: int, height: int) -> float:
    """32 original-image pixels at a 4096-pixel output edge."""
    return max(width, height) / 128.0


def layout_labels(points: list[dict], width: int, height: int,
                  label_size: float, view_scale: float | None = None,
                  cancel: threading.Event | None = None) -> list[dict]:
    """Place every numbered circle at its own anchor; overlap never moves a cell."""
    if width <= 0 or height <= 0:
        return []
    scale = max(0.001, float(view_scale)) if view_scale is not None else 1.0
    size = float(label_size) / scale
    stroke = max(1.5 / scale, size * 0.08)
    padding = stroke / 2 + 2 / scale
    output = []
    for index, point in enumerate(points):
        if cancel is not None and index % 32 == 0:
            if cancel.is_set():
                raise InterruptedError("Label layout cancelled")
        x, y = float(point["x"]), float(point["y"])
        text = str(point["display_no"])
        bx, by, bw, bh = (value * size / 128 for value in _glyph_bounds(text))
        diameter = max(bw, bh) + padding * 2
        left, top = x - diameter / 2, y - diameter / 2
        output.append({"id": point["id"], "text": text, "x": x, "y": y,
                       "left": left, "top": top, "width": diameter, "height": diameter,
                       "baselineX": x - bw / 2 - bx, "baselineY": y - bh / 2 - by,
                       "fontSize": size, "fontFamily": FONT_FAMILY,
                       "strokeWidth": stroke, "status": point.get("status", "accepted")})
    return output


def make_merged_point(points: list[dict], display_no: int) -> dict:
    """Explicit user merge with complete evidence and native-plane anchor provenance."""
    if len(points) < 2 or len({point["id"] for point in points}) != len(points):
        raise ValueError("Merge requires at least two distinct selected cells")
    center_x = math.fsum(point["x"] for point in points) / len(points)
    center_y = math.fsum(point["y"] for point in points) / len(points)
    anchor = min(points, key=lambda point: (point["x"] - center_x) ** 2 + (point["y"] - center_y) ** 2)
    modern = any("source_observations" in point or "cell_id" in point for point in points)
    lineage, legacy_labels, reference_z, parents = [], [], [], []
    observations = {}
    for point in points:
        lineage.extend(point.get("lineage", []))
        lineage.append(point["id"])
        labels = list(point.get("label_ids", []))
        if point.get("label_id") is not None:
            labels.append(int(point["label_id"]))
        legacy_labels.extend(labels)
        refs = point.get("reference_z", [point.get("z")])
        reference_z.extend(refs)
        parent = copy.deepcopy(point)
        # Keep a single ancestry tree, rather than duplicating it at every nested merge.
        parent["parents"] = parent.pop("merge_lineage", parent.get("parents", []))
        parent["reference_z"] = list(refs)
        if not modern:
            parent["label_ids"] = list(dict.fromkeys(labels))
        parents.append(parent)
        for observation in point.get("source_observations", []):
            key = (observation["artifact_id"], int(observation["label_id"]))
            if key in observations and observations[key] != observation:
                raise ValueError("The same label observation has conflicting evidence")
            observations.setdefault(key, copy.deepcopy(observation))
    z = points[0].get("z")
    if any(point.get("z") != z for point in points):
        z = None
    anchors, anchor_sources = {}, {}
    for plane in sorted({plane for point in points for plane in point.get("plane_anchors", {})}, key=int):
        available = [point for point in points if plane in point.get("plane_anchors", {})]
        cx = math.fsum(point["plane_anchors"][plane][0] for point in available) / len(available)
        cy = math.fsum(point["plane_anchors"][plane][1] for point in available) / len(available)
        chosen = min(available, key=lambda point: (point["plane_anchors"][plane][0] - cx) ** 2 +
                     (point["plane_anchors"][plane][1] - cy) ** 2)
        anchors[plane] = copy.deepcopy(chosen["plane_anchors"][plane])
        if modern:
            anchor_sources[plane] = copy.deepcopy(chosen.get("plane_anchor_sources", {}).get(
                plane, {"kind": "manual", "point_id": chosen["id"]}))
    ident = "merged-" + uuid.uuid4().hex
    merged = {"id": ident, "source": "merged", "label_id": None,
              "status": "accepted" if all(p.get("status", "accepted") == "accepted" for p in points) else "review",
              "plane_anchors": anchors, "x": float(anchor["x"]), "y": float(anchor["y"]), "z": z,
              "display_no": display_no, "parent_ids": [point["id"] for point in points],
              "label_ids": [] if modern else list(dict.fromkeys(legacy_labels)),
              "lineage": list(dict.fromkeys(lineage)), "reference_z": list(dict.fromkeys(reference_z)),
              "anchor_parent_id": anchor["id"], "merge_lineage": parents}
    if modern:
        merged.update(cell_id=ident, label_artifact_id=None, source_observations=list(observations.values()),
                      plane_anchor_sources=anchor_sources)
    return merged


def composite_image(base: Path, channels: list[dict], width: int, height: int,
                    *, cancel: threading.Event | None = None) -> QImage:
    """Tint 8-bit display planes with an indexed palette; sum natively in Qt."""
    result = QImage(width, height, QImage.Format.Format_RGB32)
    if result.isNull():
        raise ValueError("Cannot allocate the full-resolution counting image")
    result.fill(Qt.GlobalColor.black)
    painter = QPainter(result)
    try:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for channel in channels:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Image request cancelled")
            if not channel.get("visible", True):
                continue
            path = _resolve(base, channel["path"])
            image = QImage(str(path))
            if image.isNull() or image.width() != width or image.height() != height:
                raise ValueError(f"Invalid or missing display plane: {path.name}")
            gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
            tinted = QImage(gray.constBits(), gray.width(), gray.height(), gray.bytesPerLine(),
                            QImage.Format.Format_Indexed8).copy()
            color = QColor(channel.get("color", "#FFFFFF"))
            if not color.isValid():
                raise ValueError("Invalid channel display color")
            tinted.setColorTable([qRgb(round(color.red() * value / 255),
                                      round(color.green() * value / 255),
                                      round(color.blue() * value / 255)) for value in range(256)])
            painter.drawImage(0, 0, tinted)
    finally:
        painter.end()
    return result


def render_overlay(image: QImage, points: list[dict], label_size: float,
                   cancel: threading.Event | None = None) -> QImage:
    """Render full-resolution green numbered circles for counted Astrocytes."""
    result = image.copy()
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        rows = layout_labels(points, image.width(), image.height(), label_size, cancel=cancel)
        for index, row in enumerate(rows):
            if cancel is not None and index % 8 == 0:
                if cancel.is_set():
                    raise InterruptedError("Overlay rendering cancelled")
                time.sleep(0.001)
            cx, cy = row["left"] + row["width"] / 2, row["top"] + row["height"] / 2
            radius = (max(row["width"], row["height"]) - row["strokeWidth"]) / 2
            painter.setPen(QPen(QColor(OVERLAY_COLOR), max(1.2, row["strokeWidth"] / 2)))
            painter.setBrush(QColor(0, 0, 0, 100))
            painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))
            path = QPainterPath()
            path.addText(0, 0, _font(128), row["text"])
            transform = QTransform()
            transform.translate(row["baselineX"], row["baselineY"])
            transform.scale(row["fontSize"] / 128, row["fontSize"] / 128)
            path = transform.map(path)
            pen = QPen(QColor("black"), row["strokeWidth"])
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(path, pen)
            painter.fillPath(path, QColor(OVERLAY_COLOR))
    finally:
        painter.end()
    return result


def publish_result(analysis_path: Path, analysis: dict, state: dict,
                   cancel: threading.Event | None = None) -> dict:
    """Write one immutable revision, then atomically change the latest pointer.

    The pointer is the publication boundary. A failed pointer update may leave an
    unreferenced complete revision, but never replaces the previous confirmation.
    """
    base = analysis_path.parent
    results = base / "results"
    results.mkdir(exist_ok=True)
    numbers = [int(path.name.split("-")[1]) for path in results.glob("revision-*")
               if path.is_dir() and path.name.split("-")[-1].isdigit()]
    revision = max(numbers, default=0) + 1
    target = results / f"revision-{revision:03d}"
    stage = results / (".staging-" + uuid.uuid4().hex)
    points = reindex_display_numbers(copy.deepcopy(state["points"]))
    accepted = [point for point in points if point.get("status", "accepted") == "accepted" and not point.get("deleted", False)]
    accepted.sort(key=lambda point: point["display_no"])
    count = len(accepted)
    pending_count = sum(point.get("status") == "review" and not point.get("deleted", False) for point in points)
    confirmed_state = {**state, "points": points}
    stamp = datetime.now(timezone.utc).isoformat()
    stage.mkdir()
    try:
        image = composite_image(base, state["channels"], int(analysis["width"]),
                                int(analysis["height"]), cancel=cancel)
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Confirmation cancelled")
        png_label_size = export_label_size(image.width(), image.height())
        image = render_overlay(image, accepted, png_label_size, cancel=cancel)
        image.setText("run_id", str(analysis["run_id"]))
        image.setText("revision", str(revision))
        image.setText("count", str(count))
        image.setText("projection", "MIP")
        image.setText("label_size", str(png_label_size))
        if not image.save(str(stage / "Cell_Count_Overlay.png"), "PNG"):
            raise OSError("Cannot save the numbered PNG")
        with (stage / "Cell_Counts.csv").open("w", newline="", encoding="utf-8") as stream:
            fields = ["run_id", "revision", "count", "display_no", "id", "label_id", "x", "y", "z", "source",
                      "parent_ids", "label_ids", "lineage", "reference_z", "anchor_parent_id",
                      "status", "plane_anchors", "support", "cell_id", "label_artifact_id",
                      "source_observations", "plane_anchor_sources", "association", "point_metadata"]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for point in accepted:
                row = {key: point.get(key) for key in fields}
                for key in ("parent_ids", "label_ids", "lineage", "reference_z", "plane_anchors", "support",
                            "source_observations", "plane_anchor_sources", "association"):
                    row[key] = json.dumps(point.get(key, []), ensure_ascii=False, separators=(",", ":"))
                row["point_metadata"] = json.dumps(point, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                writer.writerow({**row, "run_id": analysis["run_id"], "revision": revision, "count": count})
            stream.flush()
            os.fsync(stream.fileno())
        session_channels = [{**channel, "path": _relative(_resolve(base, channel["path"]), target)}
                            for channel in state["channels"]]
        session = {"schema_version": int(analysis.get("schema_version", 1)), "run_id": analysis["run_id"], "revision": revision,
                   "count": count, "accepted_count": count, "pending_count": pending_count,
                   "excluded_count": sum(p.get("status") == "excluded" and not p.get("deleted", False) for p in points),
                   "deleted_count": sum(p.get("deleted", False) for p in points),
                   "support_channel": analysis.get("support_channel"),
                   "support_marker": analysis.get("support_marker"),
                   "display_channels": analysis.get("display_channels", [c["index"] for c in state["channels"]]),
                   "labels_path": _relative(_resolve(base, analysis["labels_path"]), target) if analysis.get("labels_path") else "",
                   "auto_count": analysis.get("auto_count", len(analysis["points"])),
                   "confirmed_at": stamp, "width": analysis["width"], "height": analysis["height"],
                   "z_count": analysis.get("z_count", 1), "coordinate_system": "original_image_pixels",
                   "reference_z_indexing": "zero_based", "projection": "MIP",
                   "analysis_path": _relative(analysis_path, target),
                   "source_path": _relative(_resolve(base, analysis["source_path"]), target)
                       if analysis.get("source_path") else "",
                   "points": points, "channels": session_channels,
                   "identity_model": analysis.get("identity_model"),
                   "label_artifacts": {key: {**copy.deepcopy(artifact),
                        "path": _relative(_resolve(base, artifact["path"]), target)}
                        for key, artifact in analysis.get("label_artifacts", {}).items()},
                   "export_label_size": png_label_size,
                   "label_size": state["label_size"], "label_size_units": {"review": "logical_screen_pixels", "export": "original_image_pixels"}, "show_labels": True,
                   "editor_state": {**confirmed_state, "channels": session_channels},
                   "state_fingerprint": _fingerprint(confirmed_state),
                   "overlay_path": "Cell_Count_Overlay.png", "csv_path": "Cell_Counts.csv",
                   "physical_size_um": analysis.get("physical_size_um", {}),
                   "series_index": analysis.get("series_index", 0),
                   "time_index": analysis.get("time_index", 0), "model": analysis.get("model", {})}
        _atomic_json(stage / "Cell_Count_Session.json", session)
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Confirmation cancelled")
        # No cancellation between the two publication operations.
        os.replace(stage, target)
        pointer = {"schema_version": int(analysis.get("schema_version", 1)), "run_id": analysis["run_id"], "revision": revision,
                   "count": count, "accepted_count": count, "pending_count": pending_count,
                   "excluded_count": session["excluded_count"], "deleted_count": session["deleted_count"], "confirmed_at": stamp,
                   "session_path": f"{target.name}/Cell_Count_Session.json",
                   "results_dir": target.name}
        _atomic_json(results / "latest.json", pointer)
        return {**pointer, "auto_count": session["auto_count"], "results_dir": str(target),
                "analysis_path": str(analysis_path), "session_path": str(target / "Cell_Count_Session.json"),
                "overlay_path": str(target / "Cell_Count_Overlay.png"),
                "csv_path": str(target / "Cell_Counts.csv"),
                "state_fingerprint": session["state_fingerprint"],
                "source_fingerprint": _fingerprint(state),
                "point_numbers": {point["id"]: point["display_no"] for point in points}}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


class _DraftWriter:
    """One ordered writer, with at most the newest waiting snapshot retained."""
    def __init__(self, completed):
        self._condition = threading.Condition()
        self._pending = None
        self._running = False
        self._submitted = self._finished = 0
        self._error = ""
        self._completed = completed

    def submit(self, path: Path, payload: dict) -> int:
        with self._condition:
            self._submitted += 1
            self._pending = (self._submitted, path, payload)
            if not self._running:
                self._running = True
                threading.Thread(target=self._run, name="counting-draft", daemon=True).start()
            return self._submitted

    def _run(self):
        while True:
            with self._condition:
                if self._pending is None:
                    self._running = False
                    self._condition.notify_all()
                    return
                ticket, path, payload = self._pending
                self._pending = None
            error = ""
            try:
                _atomic_json(path, payload)
            except (OSError, ValueError, TypeError) as exc:
                error = str(exc)
            with self._condition:
                self._finished, self._error = ticket, error
                self._condition.notify_all()
            self._completed((ticket, error))

    def flush(self) -> str:
        with self._condition:
            ticket = self._submitted
            self._condition.wait_for(lambda: self._finished >= ticket)
            return self._error


class _Signals(QObject):
    done = Signal(object)


class _ImageTask(QRunnable):
    """A bounded, one-shot job. No QObjects or widgets are touched in run()."""
    def __init__(self, kind, generation, sequence, function):
        super().__init__()
        self.kind, self.generation, self.sequence = kind, generation, sequence
        self.function = function
        self.cancel = threading.Event()
        self.signals = _Signals()

    def run(self):
        packet = {"kind": self.kind, "generation": self.generation, "sequence": self.sequence}
        try:
            packet["result"] = self.function(self.cancel)
        except InterruptedError:
            packet["cancelled"] = True
        except Exception as exc:
            packet["error"] = str(exc)
        self.signals.done.emit(packet)


class CountingEditor(QObject):
    changed = Signal()
    drainedChanged = Signal()
    overlayChanged = Signal()
    _draftCompleted = Signal(object)
    viewChanged = Signal()
    confirmed = Signal(str, object)

    def __init__(self, parent=None, locale=None):
        super().__init__(parent)
        self._locale = locale
        self._draft_writer = _DraftWriter(self._draftCompleted.emit)
        self._draft_ticket = 0
        self._draftCompleted.connect(self._draft_completed, Qt.ConnectionType.QueuedConnection)
        self._generation = 0
        self._display_active = True
        self._display_owner = 0
        self._sequence = 0
        self._active_task = None
        self._pending_render = None
        self._pending_confirm = None
        self._pending_layout = None
        self._processes = set()
        self._retired_caches = {}
        self._relocation_process = None
        self._slice_process = None
        self._slice_timer = QTimer(self)
        self._slice_timer.setSingleShot(True)
        self._slice_timer.setInterval(40)
        self._slice_timer.timeout.connect(self._request_slice)
        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(200)
        self._view_timer.timeout.connect(self._save_draft)
        self._prefetch_timer = QTimer(self)
        self._prefetch_timer.setSingleShot(True)
        self._prefetch_timer.setInterval(180)
        self._prefetch_timer.timeout.connect(self._start_prefetch)
        self._reset()
        if locale is not None:
            locale.changed.connect(self._locale_changed)

    @Slot()
    def _locale_changed(self, *args):
        self.changed.emit()

    def _reset(self):
        self._analysis_path = None
        self._analysis = {}
        self._points = []
        self._channels = []
        self._selected = set()
        self._undo = []
        self._redo = []
        self._next_display_no = 1
        self._mode = "select"
        self._label_size = 24
        self._show_labels = True
        self._view_zoom, self._view_pan_x, self._view_pan_y = 1.0, 0.0, 0.0
        self._scrubbing = False
        self._last_z_direction = 1
        self._applied_frame_key = None
        self._applied_quality = "native"
        self._applied_frame_width = self._applied_frame_height = 0
        self._frame_pending = None
        self._frame_ready = False
        self._frame_failed = False
        self._slice_buffer = b""
        self._slice_inflight = None
        self._slice_cache = {}
        self._slice_cache_order = []
        self._slice_request_id = 0
        self._z_index = -1
        self._image_url = ""
        self._displayed_image_url = ""
        self._retired_previews = set()
        self._displayed_z_index = None
        self._message = ""
        self._confirmed_fingerprint = ""
        self._confirmed_state = None
        self._confirmation_snapshot = None
        self._confirmed_summary = {}
        self._confirming = False
        self._runtime = None
        self._environment = None
        self._slice_path = None
        self._layout_cache = {}
        self._visible_cache = None
        self._layout_epoch = 0
        self._last_layout = []
        self._content_fingerprint = ""
        self._draft_enabled = True
        self._source_relocation_needed = False
        self._source_relocation_message = ""
        self.viewChanged.emit()
        self.overlayChanged.emit()

    def _state(self):
        return {"points": copy.deepcopy(self._points), "channels": copy.deepcopy(self._channels),
                "label_size": self._label_size, "show_labels": self._show_labels}

    @property
    def _content_fingerprint(self):
        if self._content_fingerprint_cache is None:
            self._content_fingerprint_cache = _fingerprint(self._snapshot_state())
        return self._content_fingerprint_cache

    @_content_fingerprint.setter
    def _content_fingerprint(self, value):
        self._content_fingerprint_cache = value

    def _snapshot_state(self):
        # Editor mutations replace point/channel dictionaries. Draft workers may
        # safely retain these immutable versions without copying their evidence.
        return {"points": self._points, "channels": self._channels,
                "label_size": self._label_size, "show_labels": self._show_labels}

    def _matches_confirmed(self):
        saved = self._confirmed_state
        return saved is not None and self._label_size == saved["label_size"] and \
            self._show_labels == saved["show_labels"] and self._channels == saved["channels"] and \
            self._points == saved["points"]

    def _notify(self, *, content=False, save=False):
        if content:
            if save:
                self._draft_enabled = True
            self._points = reindex_display_numbers(self._points)
            self._content_fingerprint_cache = None
            self._invalidate_layout()
        if save and self.available:
            self._save_draft()
        self.changed.emit()

    @Slot(object)
    def _draft_completed(self, result):
        ticket, error = result
        if ticket == self._draft_ticket and error and self.available:
            self._message = "无法保存计数草稿：" + error
            self.changed.emit()

    def _save_draft(self):
        if not self._analysis_path or not self._draft_enabled:
            return
        self._draft_ticket = self._draft_writer.submit(self._analysis_path.parent / "draft.json", {
            "schema_version": self.schemaVersion, "run_id": self.runId, "analysis_path": self._analysis_path.name,
            "state": self._snapshot_state(), "next_display_no": self._next_display_no,
            "confirmed_fingerprint": self._confirmed_fingerprint,
            "undo": list(self._undo), "redo": list(self._redo),
            "view": {"z_index": self._z_index, "mode": self._mode, "zoom": self._view_zoom,
                     "pan_x": self._view_pan_x, "pan_y": self._view_pan_y},
            "updated_at": datetime.now(timezone.utc).isoformat()})

    def _flush_draft(self):
        error = self._draft_writer.flush()
        if error:
            self._message = "无法保存计数草稿：" + error
            self.changed.emit()
            return False
        return True

    def open_analysis(self, path, *, runtime=None, environment=None):
        if self._relocation_process is not None:
            self._message = "正在验证原图，请等待验证完成后确认或切换会话。"
            self.changed.emit()
            raise RuntimeError(self._message)
        if not self.close():
            raise RuntimeError(self._message)
        try:
            self._load_analysis(path, runtime=runtime, environment=environment)
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            self._reset()
            self.changed.emit()
            raise

    def _load_analysis(self, path, *, runtime=None, environment=None):
        path = Path(path).resolve()
        raw = json.loads(path.read_text(encoding="utf-8"))
        # An explicitly opened revision must not be replaced by a newer draft.
        requested_session = path if path.name == "Cell_Count_Session.json" else None
        self._draft_enabled = requested_session is None
        # A saved result can reopen itself even when the module is uninstalled.
        if path.name == "Cell_Count_Session.json":
            path = _resolve(path.parent, raw["analysis_path"]).resolve()
            raw = json.loads(path.read_text(encoding="utf-8"))
        if int(raw.get("schema_version", 0)) not in (1, 2):
            raise ValueError("Unsupported counting analysis schema")
        if int(raw.get("width", 0)) <= 0 or int(raw.get("height", 0)) <= 0:
            raise ValueError("Counting analysis has invalid image dimensions")
        if not raw.get("run_id") or not isinstance(raw.get("points"), list):
            raise ValueError("Counting analysis is missing its run or points")
        self._analysis_path, self._analysis = path, raw
        self._refresh_source_status()
        self._validate_label_artifacts()
        self._runtime, self._environment = runtime, environment
        display = set(raw.get("display_channels", [item["index"] for item in raw.get("channels", [])]))
        self._channels = [{**item, "index": int(item["index"]), "visible": True,
                           "path": _relative(_resolve(path.parent, item["path"]), path.parent)}
                          for item in raw.get("channels", []) if item["index"] in display]
        self._points = reindex_display_numbers(self._validate_points(raw["points"]))
        # Review uses an explicit screen-pixel size; image dimensions do not
        # silently inflate it. Saved sessions and drafts retain their exact value.
        initial_size = int(raw.get("default_label_size", 24))
        self._label_size = min(256, max(6, initial_size))
        self._next_display_no = max((p["display_no"] for p in self._points), default=0) + 1
        warning = ""
        latest = path.parent / "results" / "latest.json"
        if requested_session is not None or latest.exists():
            try:
                if requested_session is not None:
                    session_path = requested_session
                    session = json.loads(session_path.read_text(encoding="utf-8"))
                    pointer = {key: session[key] for key in ("run_id", "revision", "count", "confirmed_at")}
                else:
                    pointer = json.loads(latest.read_text(encoding="utf-8"))
                    session_path = _resolve(latest.parent, pointer["session_path"])
                    session = json.loads(session_path.read_text(encoding="utf-8"))
                if session["run_id"] != self.runId or session["revision"] != pointer["revision"]:
                    raise ValueError("Saved confirmation does not match this run")
                saved = copy.deepcopy(session["editor_state"])
                for channel in saved["channels"]:
                    channel["path"] = _relative(_resolve(session_path.parent, channel["path"]), path.parent)
                self._apply_state(saved)
                self._confirmed_state = self._snapshot_state()
                self._confirmed_fingerprint = _fingerprint(self._confirmed_state)
                self._confirmed_summary = {**pointer, "results_dir": str(session_path.parent),
                                           "session_path": str(session_path), "analysis_path": str(path)}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                warning = "无法恢复已确认版本：" + str(exc)
        draft_path = path.parent / "draft.json"
        if requested_session is None and draft_path.exists():
            try:
                draft = json.loads(draft_path.read_text(encoding="utf-8"))
                if draft.get("schema_version") not in (1, 2) or draft.get("run_id") != self.runId:
                    raise ValueError("Draft does not match this run")
                self._apply_state(draft["state"])
                self._undo = [self._validate_action(action) for action in draft.get("undo", [])]
                self._redo = [self._validate_action(action) for action in draft.get("redo", [])]
                self._next_display_no = max(self._next_display_no, int(draft.get("next_display_no", 1)))
                mode = draft.get("view", {}).get("mode", "select")
                self._mode = mode if mode in ("select", "add") else "select"
                view = draft.get("view", {})
                self._view_zoom = min(24.0, max(0.25, float(view.get("zoom", 1))))
                self._view_pan_x = float(view.get("pan_x", 0))
                self._view_pan_y = float(view.get("pan_y", 0))
                # A historical MIP opens offline. Navigation preserves zoom/pan.
                if runtime and 0 <= int(view.get("z_index", -1)) < self.zCount:
                    self._z_index = int(view["z_index"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                warning = "无法恢复草稿：" + str(exc)
        if self._matches_confirmed():
            # A matching restored draft reuses immutable confirmation evidence.
            self._points = self._confirmed_state["points"]
        self._content_fingerprint = _fingerprint(self._snapshot_state())
        self._message = warning
        self.viewChanged.emit()
        if self._display_active:
            if self._z_index >= 0:
                self._slice_timer.start()
            else:
                self._schedule_render(keep_message=True)
        self.changed.emit()
        self.overlayChanged.emit()

    def _validate_points(self, points):
        clean, ids, numbers, cell_ids = [], set(), set(), set()
        observation_owners, observation_ids = {}, {}
        for index, original in enumerate(points):
            point = copy.deepcopy(original)
            ident = str(point["id"])
            x, y = float(point["x"]), float(point["y"])
            z = point.get("z")
            number = int(point.get("display_no", index + 1))
            if ident in ids or number in numbers or number < 1 or not ident:
                raise ValueError("Counting points require distinct IDs and display numbers")
            if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < self.width and 0 <= y < self.height):
                raise ValueError("Counting point is outside the original image")
            if z is not None and not (isinstance(z, int) and 0 <= z < self.zCount):
                raise ValueError("Invalid reference Z for counting point")
            status = point.get("status", "accepted" if self.schemaVersion == 1 else None)
            if status not in ("accepted", "review", "excluded"):
                raise ValueError("Counting object requires accepted, review or excluded status")
            if "deleted" in point and type(point["deleted"]) is not bool:
                raise ValueError("Manual deletion must be an explicit boolean edit state")
            anchors = {}
            for plane, anchor in point.get("plane_anchors", {}).items():
                zi = int(plane)
                if str(zi) != str(plane) or not 0 <= zi < self.zCount or len(anchor) != 2:
                    raise ValueError("Invalid object plane anchor")
                ax, ay = map(float, anchor)
                if not (math.isfinite(ax) and math.isfinite(ay) and 0 <= ax < self.width and 0 <= ay < self.height):
                    raise ValueError("Object plane anchor lies outside the image")
                anchors[str(zi)] = [ax, ay]
            if self.schemaVersion == 2 and point.get("source") in ("auto", "support_body", "dapi_support") and not anchors:
                raise ValueError("A 3D automatic object requires real plane intersections")
            point.update(id=ident, x=x, y=y, z=z, display_no=number, status=status, plane_anchors=anchors)
            if self.identityModel == "multi_observation_cell_v1":
                self._validate_cell_observations(point)
                if point["cell_id"] in cell_ids:
                    raise ValueError("Counting cells require distinct logical cell IDs")
                cell_ids.add(point["cell_id"])
                for observation in point["source_observations"]:
                    key = (observation["artifact_id"], observation["label_id"])
                    if key in observation_owners:
                        raise ValueError("A source observation cannot belong to multiple counting cells")
                    if observation["id"] in observation_ids and observation_ids[observation["id"]] != key:
                        raise ValueError("A stable observation ID cannot refer to different label artifacts")
                    observation_owners[key] = ident
                    observation_ids[observation["id"]] = key
            elif point.get("source") in ("support_body", "dapi_support"):
                raise ValueError("Support-body cells require their multi-observation artifact identity")
            ids.add(ident)
            numbers.add(number)
            clean.append(point)
        return clean

    def _validate_label_artifacts(self):
        if self.identityModel != "multi_observation_cell_v1":
            return
        artifacts = self._analysis.get("label_artifacts")
        if not isinstance(artifacts, dict) or not artifacts:
            raise ValueError("Multi-observation cells require a label-artifact registry")
        paths = set()
        expected_kinds = {"dapi_nuclei": "nucleus", "support_bodies": "body"}
        for ident, artifact in artifacts.items():
            if ident not in expected_kinds or artifact.get("kind") != expected_kinds[ident]:
                raise ValueError("Nuclear and body label artifacts must retain distinct kinds")
            if (artifact.get("axes") != "ZYX" or artifact.get("origin_zyx") != [0, 0, 0]
                    or artifact.get("shape_zyx") != [self.zCount, self.height, self.width]):
                raise ValueError("Cell label artifacts must use the original full-volume ZYX coordinates")
            if not isinstance(artifact.get("path"), str) or not artifact["path"]:
                raise ValueError("Cell label artifacts require an explicit path")
            path = _resolve(self._analysis_path.parent, artifact["path"]).resolve()
            if path in paths:
                raise ValueError("Nuclear and body observations cannot share the same label artifact")
            paths.add(path)

    def _validate_cell_observations(self, point):
        if point.get("cell_id") != point["id"]:
            raise ValueError("The logical cell ID must match its stable editor ID")
        source = point.get("source")
        if source not in ("auto", "support_body", "dapi_support", "manual", "merged"):
            raise ValueError("Unknown multi-observation cell source")
        observations = point.get("source_observations")
        if not isinstance(observations, list):
            raise ValueError("Cells require an explicit source-observation list")
        registry = self._analysis["label_artifacts"]
        by_id, references, kinds = {}, set(), set()
        for observation in observations:
            ident = observation.get("id")
            artifact_id, label_id = observation.get("artifact_id"), observation.get("label_id")
            if not isinstance(ident, str) or not ident or ident in by_id:
                raise ValueError("A cell's source observations require distinct stable IDs")
            if (artifact_id not in registry or observation.get("kind") != registry[artifact_id]["kind"]
                    or type(label_id) is not int or label_id < 1):
                raise ValueError("Observation labels must retain their nuclear or body artifact namespace")
            key = (artifact_id, label_id)
            if key in references:
                raise ValueError("A source observation may occur only once per cell")
            references.add(key)
            if observation.get("decision") not in ("accepted", "review", "excluded"):
                raise ValueError("Observation decision is missing or invalid")
            bounds = observation.get("bounds_zyx")
            if (not isinstance(bounds, list) or len(bounds) != 3
                    or any(not isinstance(pair, list) or len(pair) != 2
                           or any(type(v) is not int for v in pair) or not 0 <= pair[0] < pair[1] <= limit
                           for pair, limit in zip(bounds, (self.zCount, self.height, self.width)))):
                raise ValueError("Observation bounds must be native half-open ZYX coordinates")
            anchors = observation.get("plane_anchors")
            if not isinstance(anchors, dict) or not anchors:
                raise ValueError("A 3D observation requires real plane intersections")
            for plane, anchor in anchors.items():
                zi = int(plane)
                if str(zi) != str(plane) or len(anchor) != 2:
                    raise ValueError("Invalid observation plane anchor")
                ax, ay = map(float, anchor)
                if not (math.isfinite(ax) and math.isfinite(ay) and bounds[0][0] <= zi < bounds[0][1]
                        and bounds[1][0] <= ay < bounds[1][1] and bounds[2][0] <= ax < bounds[2][1]):
                    raise ValueError("Observation anchor lies outside its native mask bounds")
            by_id[ident] = observation
            kinds.add(observation["kind"])
        expected = {"auto": {"nucleus"}, "support_body": {"body"}, "dapi_support": {"nucleus", "body"}}
        if source in expected and kinds != expected[source]:
            raise ValueError("The cell source does not match its actual observation kinds")
        alias = point.get("label_id")
        artifact_id = point.get("label_artifact_id")
        if alias is not None and (type(alias) is not int or (artifact_id, alias) not in references):
            raise ValueError("The representative label must belong to a source observation's artifact")
        if source in expected and alias is None:
            raise ValueError("An automatic cell requires a namespaced representative observation")
        anchor_sources = point.get("plane_anchor_sources")
        if not isinstance(anchor_sources, dict) or set(anchor_sources) != set(point["plane_anchors"]):
            raise ValueError("Every displayed plane anchor requires an explicit source")
        for plane, anchor_source in anchor_sources.items():
            if anchor_source.get("kind") == "observation":
                observation = by_id.get(anchor_source.get("observation_id"))
                if observation is None or observation["plane_anchors"].get(plane) != point["plane_anchors"][plane]:
                    raise ValueError("A displayed cell anchor must be an actual source observation's same-Z anchor")
            elif anchor_source.get("kind") == "manual" and source in ("manual", "merged"):
                if not isinstance(anchor_source.get("point_id"), str) or not anchor_source["point_id"]:
                    raise ValueError("A manual anchor requires its original manual point identity")
            else:
                raise ValueError("Unsupported displayed anchor source")

    def _apply_state(self, state):
        points = self._validate_points(state["points"])
        channels = copy.deepcopy(state["channels"])
        if {int(c["index"]) for c in channels} != set(self._analysis.get("display_channels", [int(c["index"]) for c in self._analysis["channels"]])):
            raise ValueError("Saved channels differ from the analyzed image")
        # Paths and colors come from the analysis; drafts can only toggle display.
        by_index = {int(c["index"]): c for c in self._analysis["channels"]}
        channels = [{**by_index[int(c["index"])], "visible": bool(c.get("visible", True)),
                     "path": _relative(_resolve(self._analysis_path.parent, by_index[int(c["index"])]["path"]),
                                       self._analysis_path.parent)} for c in channels]
        self._points, self._channels = reindex_display_numbers(points), channels
        self._label_size = min(256, max(6, int(state["label_size"])))
        self._show_labels = bool(state.get("show_labels", True))
        self._next_display_no = max(self._next_display_no,
                                    max((p["display_no"] for p in points), default=0) + 1)

    def close(self):
        if self.available:
            self._save_draft()
            if not self._flush_draft():
                return False
        if self._analysis_path is not None:
            cache = self._analysis_path.parent / ".editor"
            paths = set(cache.glob("view-*.png")) | set((cache / "slices").glob("frame-*.png"))
            if self._active_task is not None and getattr(self._active_task, "output_path", None):
                paths.add(self._active_task.output_path)
            if self._slice_inflight is not None:
                paths.add(self._slice_inflight["output"])
            self._retired_caches[self._generation] = (cache, paths)
        self._generation += 1
        self._slice_timer.stop()
        self._view_timer.stop()
        self._prefetch_timer.stop()
        self._pending_render = self._pending_confirm = self._pending_layout = None
        if self._active_task is not None:
            self._active_task.cancel.set()
        self._cancel_slice()
        if self._relocation_process is not None:
            self._relocation_process.kill()
            self._relocation_process = None
        self._reset()
        self._cleanup_retired_caches()
        self.changed.emit()
        return True

    def _cleanup_retired_caches(self):
        """Delete only retired display files, after every owning writer drained."""
        for generation, (cache, paths) in tuple(self._retired_caches.items()):
            if ((self._active_task is not None and self._active_task.generation == generation)
                    or any(p.property("generation") == generation for p in self._processes)):
                continue
            try:
                if cache.is_symlink() or (cache / "slices").is_symlink():
                    raise OSError("Counting display cache must not link to an external directory")
                for path in paths:
                    path.unlink(missing_ok=True)
                for directory in (cache / "slices", cache):
                    if directory.is_dir() and not directory.is_symlink() and not any(directory.iterdir()):
                        directory.rmdir()
            except OSError as exc:
                self._message = "无法清理计数显示缓存：" + str(exc)
                continue
            del self._retired_caches[generation]
        self.drainedChanged.emit()

    def _edit(self, points, *, kind="replace"):
        if not self.available or self._confirming:
            return
        if points == self._points:
            return
        before = {point["id"]: point for point in self._points}
        after = {point["id"]: point for point in points}
        changed = {ident for ident in before.keys() & after.keys() if before[ident] != after[ident]}
        self._undo.append({
            "kind": kind,
            "removed": [{"index": index, "point": copy.deepcopy(point)}
                        for index, point in enumerate(self._points) if point["id"] not in after or point["id"] in changed],
            "added": [{"index": index, "point": copy.deepcopy(point)}
                      for index, point in enumerate(points) if point["id"] not in before or point["id"] in changed]})
        # Store changed points rather than 100 complete copies of a dense field.
        self._undo = self._undo[-100:]
        self._redo.clear()
        self._points = points
        self._selected.intersection_update(point["id"] for point in points)
        self._notify(content=True, save=True)

    @Slot(float, float)
    def addPoint(self, x, y):
        if (not self.available or self._confirming or not self._frame_ready
                or self._applied_frame_key != self._slice_key(self._z_index)):
            return
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < self.width and 0 <= y < self.height):
            return
        point = {"id": "manual-" + uuid.uuid4().hex, "label_id": None,
                 "x": float(x), "y": float(y), "z": self._z_index if self._z_index >= 0 else None,
                 "source": "manual", "display_no": self._next_display_no, "status": "accepted",
                 "plane_anchors": {str(self._z_index): [float(x), float(y)]} if self._z_index >= 0 else {}}
        if self.identityModel == "multi_observation_cell_v1":
            point.update(cell_id=point["id"], label_artifact_id=None, source_observations=[],
                         plane_anchor_sources={plane: {"kind": "manual", "point_id": point["id"]}
                                               for plane in point["plane_anchors"]})
        self._next_display_no += 1
        self._selected = {point["id"]}
        self._edit(self._points + [point])

    @Slot(str, bool)
    def selectPoint(self, ident, additive=False):
        if self._confirming:
            return
        valid = {point["id"] for point in self._points if not point.get("deleted", False)}
        if not additive:
            self._selected.clear()
        if ident in valid:
            if additive and ident in self._selected:
                self._selected.remove(ident)
            else:
                self._selected.add(ident)
        self._visible_cache = None
        self.changed.emit()
        self.overlayChanged.emit()

    @Slot(str, bool)
    def activatePoint(self, ident, additive=False):
        """Only a likely-Astrocyte hint accepts on plain click; Command selects."""
        if self.busy:
            return
        point = next((point for point in self._visible_points() if point["id"] == ident), None)
        self.selectPoint(point["id"] if point else "", additive)
        if (point is not None and not additive and point.get("status") == "review"
                and point.get("review_hint") == "likely_astrocyte"):
            self.acceptSelected()

    @Slot()
    def deleteSelected(self):
        if self._confirming or not self._selected:
            return
        if self.schemaVersion == 1:
            self._edit([point for point in self._points if point["id"] not in self._selected])
        else:
            selected = set(self._selected)
            self._selected.clear()
            self._edit([{**point, "deleted": True} if point["id"] in selected else point
                        for point in self._points])

    @Slot()
    def acceptSelected(self):
        if self._selected:
            self._edit([{**point, "status": "accepted"}
                        if point["id"] in self._selected and point.get("status") in ("review", "excluded")
                        and not point.get("deleted", False) else point
                        for point in self._points])

    def _visible_points(self):
        if not self._frame_ready:
            return []
        if self._visible_cache is not None:
            return self._visible_cache
        visible = []
        for point in self._points:
            if point.get("deleted", False):
                continue
            if self._displayed_z_index == -1:
                x, y = point["x"], point["y"]
            elif self._displayed_z_index is not None:
                anchor = point.get("plane_anchors", {}).get(str(self._displayed_z_index))
                if anchor is None:
                    continue
                x, y = anchor
            else:
                continue
            visible.append({"id": point["id"], "x": x, "y": y,
                            "status": point["status"], "review_hint": point.get("review_hint", ""),
                            "display_no": point["display_no"], "selected": point["id"] in self._selected})
        self._visible_cache = visible
        return visible

    @Slot(result="QVariantList")
    def renderingPoints(self):
        return self._visible_points()

    def _invalidate_layout(self):
        self._visible_cache = None
        self._layout_cache.clear()
        self._last_layout = []
        self._layout_epoch += 1
        self._pending_layout = None
        if self._active_task is not None and self._active_task.kind == "layout":
            self._active_task.cancel.set()
        self.overlayChanged.emit()

    @Slot(float, float, float)
    def setView(self, zoom, pan_x, pan_y):
        if not all(math.isfinite(v) for v in (zoom, pan_x, pan_y)):
            return
        previous_edge = self._display_edge()
        values = (min(24.0, max(0.25, float(zoom))), float(pan_x), float(pan_y))
        if values == (self._view_zoom, self._view_pan_x, self._view_pan_y):
            return
        self._view_zoom, self._view_pan_x, self._view_pan_y = values
        self._view_timer.start()
        self.viewChanged.emit()
        if self.available and self._z_index >= 0 and previous_edge != self._display_edge():
            self._request_current_frame()

    @Slot()
    def mergeSelected(self):
        if not self.canMerge:
            return
        selected = [point for point in self._points if point["id"] in self._selected]
        try:
            merged = make_merged_point(selected, self._next_display_no)
            self._validate_points([merged])
        except ValueError as exc:
            self._message = "无法合并细胞：" + str(exc)
            self.changed.emit()
            return
        first_index = next(index for index, point in enumerate(self._points)
                           if point["id"] in self._selected)
        points = [point for point in self._points if point["id"] not in self._selected]
        points.insert(first_index, merged)
        self._next_display_no += 1
        self._selected = {merged["id"]}
        self._edit(points, kind="merge")

    @Slot()
    def undo(self):
        if not self._undo or self._confirming:
            return
        action = self._undo.pop()
        self._redo.append(action)
        self._apply_action(action, reverse=True)
        self._selected.clear()
        self._notify(content=True, save=True)

    @Slot()
    def redo(self):
        if not self._redo or self._confirming:
            return
        action = self._redo.pop()
        self._undo.append(action)
        self._apply_action(action, reverse=False)
        self._selected.clear()
        self._notify(content=True, save=True)

    def _validate_action(self, action):
        kind = action.get("kind", "replace")
        if kind not in ("replace", "merge"):
            raise ValueError("Unknown counting edit action")
        clean = {"kind": kind}
        for kind in ("added", "removed"):
            clean[kind] = []
            for entry in action[kind]:
                index = int(entry["index"])
                if index < 0:
                    raise ValueError("Invalid counting edit position")
                clean[kind].append({"index": index, "point": self._validate_points([entry["point"]])[0]})
        return clean

    def _apply_action(self, action, *, reverse):
        remove = action["added" if reverse else "removed"]
        insert = action["removed" if reverse else "added"]
        removed_ids = {entry["point"]["id"] for entry in remove}
        points = [point for point in self._points if point["id"] not in removed_ids]
        for entry in sorted(insert, key=lambda row: row["index"]):
            points.insert(entry["index"], copy.deepcopy(entry["point"]))
        self._points = points

    @Slot(str)
    def setMode(self, mode):
        if mode in ("select", "add") and not (self._confirming):
            self._mode = mode
            self.changed.emit()

    @Slot(result=int)
    def registerDisplay(self):
        self._display_owner += 1
        return self._display_owner

    @Slot(bool)
    @Slot(bool, int)
    def setDisplayActive(self, active, owner=0):
        if owner and owner != self._display_owner:
            return  # An old page's deferred destruction must not deactivate a newer page.
        active = bool(active)
        if active == self._display_active:
            return
        self._display_active = active
        if not active:
            self._scrubbing = False
            self._sequence += 1
            self._slice_timer.stop()
            self._prefetch_timer.stop()
            self._pending_render = self._pending_layout = None
            self._frame_pending = None
            if self._image_url and self._image_url != self._displayed_image_url and self._analysis_path is not None:
                pending_path = Path(QUrl(self._image_url).toLocalFile())
                if pending_path.parent == self._analysis_path.parent / ".editor" and pending_path.name.startswith("view-"):
                    self._retired_previews.add(pending_path)
            self._image_url = self._displayed_image_url
            if self._active_task is not None and self._active_task.kind in ("render", "layout"):
                self._active_task.cancel.set()
            self.changed.emit()
            self._prune_frame_cache()
        elif self.available and (not self._frame_ready or self._applied_frame_key != self._slice_key(self._z_index)):
            self._request_current_frame(delay_ms=0)
        else:
            self.changed.emit()

    def _display_edge(self):
        if (self._scrubbing and self._view_zoom <= 1.0
                and max(self.width, self.height) > 1536):
            return 1536
        return 0

    def _expected_frame_size(self, max_edge):
        if not max_edge or max(self.width, self.height) <= max_edge:
            return self.width, self.height
        ratio = max_edge / max(self.width, self.height)
        return max(1, round(self.width * ratio)), max(1, round(self.height * ratio))

    def _request_current_frame(self, delay_ms=40):
        self._sequence += 1
        self._pending_render = None
        self._slice_path = None
        self._frame_pending = None
        self._frame_failed = False
        self._prefetch_timer.stop()
        self._slice_timer.stop()
        if self._active_task is not None and self._active_task.kind == "render":
            self._active_task.cancel.set()
        if not self._display_active:
            self.changed.emit()
            return
        if self._z_index < 0:
            self._schedule_render()
        else:
            self._message = "正在读取预选通道的 Z 切片…"
            key = self._slice_key(self._z_index)
            cached = self._slice_cache.get(key)
            if cached and cached.is_file():
                self._request_slice()
            else:
                self._slice_timer.start(delay_ms)
        # Applied pixels and the layout remain valid until a matching candidate commits.
        self.changed.emit()

    @Slot(int)
    def setZ(self, index):
        if not self.available or self._confirming or index < -1 or index >= self.zCount or index == self._z_index:
            return
        if index >= 0 and self._z_index >= 0:
            self._last_z_direction = 1 if index > self._z_index else -1
        self._z_index = index
        self._request_current_frame()

    @Slot(bool)
    def setScrubbing(self, active):
        active = bool(active)
        if active == self._scrubbing or self._confirming:
            return
        previous = self._display_edge()
        self._scrubbing = active
        self._prefetch_timer.stop()
        if self.available and self._z_index >= 0 and previous != self._display_edge():
            self._request_current_frame(delay_ms=40 if active else 180)
        else:
            self.changed.emit()

    @Slot(int, bool)
    def setChannelVisible(self, index, visible):
        if self._confirming:
            return
        for channel in self._channels:
            if int(channel["index"]) == index and channel["visible"] != visible:
                self._channels = [{**item, "visible": bool(visible)} if int(item["index"]) == index else item
                                  for item in self._channels]
                self._notify(content=True, save=True)
                self._request_current_frame()
                break

    @Slot(int)
    def setLabelSize(self, size):
        size = min(256, max(6, int(size)))
        if self._confirming or size == self._label_size:
            return
        self._label_size = size
        self._notify(content=True, save=True)

    @Slot(bool)
    def setShowLabels(self, show):
        if self._confirming or bool(show) == self._show_labels:
            return
        self._show_labels = bool(show)
        self._notify(content=True, save=True)

    @Slot(float, result="QVariantList")
    def labelsForScale(self, scale):
        if not self.available or not self._display_active:
            return []
        # Font size, rather than every fractional wheel delta, controls layout.
        key = round(self._label_size / max(0.001, scale), 6)
        if key in self._layout_cache:
            return self._layout_cache[key]
        visible = [point for point in self._visible_points() if point.get("status", "accepted") == "accepted"]
        if len(visible) <= 200:
            self._cache_layout(key, layout_labels(visible, self.width, self.height, key))
            return self._layout_cache[key]
        signature = (self._layout_epoch, key)
        already_running = (self._active_task is not None and self._active_task.kind == "layout"
                           and self._active_task.generation == self._generation
                           and self._active_task.sequence == signature)
        if not already_running and (self._pending_layout is None or
                                    self._pending_layout["sequence"] != signature):
            self._pending_layout = {"points": visible, "width": self.width,
                                    "height": self.height, "size": key, "generation": self._generation,
                                    "sequence": signature}
            if self._active_task is not None and self._active_task.kind == "layout":
                self._active_task.cancel.set()
            self._start_pending()
        valid = {point["id"] for point in visible}
        return [row for row in self._last_layout if row["id"] in valid]

    def _cache_layout(self, key, rows):
        if len(self._layout_cache) >= 4:
            self._layout_cache.clear()
        self._layout_cache[key] = rows
        self._last_layout = rows

    def _schedule_render(self, *, keep_message=False):
        if not self.available or not self._display_active or (self._z_index >= 0 and self._slice_path is None):
            return
        self._frame_failed = False
        self._sequence += 1
        if self._slice_path is not None:
            self._pending_render = None
            if self._active_task is not None and self._active_task.kind in ("render", "layout"):
                self._active_task.cancel.set()
            # The worker has already composed and encoded this immutable full-resolution
            # frame. Read only its header; QML owns the one necessary pixel decode.
            reader = QImageReader(str(self._slice_path))
            size = reader.size()
            expected = self._expected_frame_size(self._slice_key(self._z_index)[2])
            if not size.isValid() or (size.width(), size.height()) != expected:
                self._frame_failed = True
                self._frame_pending = None
                self._message = "无法显示计数图像：Invalid slice frame dimensions"
            else:
                self._present_frame(self._slice_path, self._z_index, self._message if keep_message else "",
                                    width=size.width(), height=size.height())
            self.changed.emit()
            return
        self._pending_render = {"base": self._analysis_path.parent, "channels": copy.deepcopy(self._channels),
                                "width": self.width, "height": self.height,
                                "generation": self._generation, "sequence": self._sequence,
                                "z_index": self._z_index, "message": self._message if keep_message else ""}
        if self._active_task is not None and self._active_task.kind in ("render", "layout"):
            self._active_task.cancel.set()
        self._start_pending()

    def _start_pending(self):
        if self._active_task is not None:
            return
        if self._pending_confirm is not None:
            request = self._pending_confirm
            self._pending_confirm = None
            task = _ImageTask("confirm", request["generation"], request["sequence"],
                              lambda cancel: publish_result(request["path"], request["analysis"],
                                                            request["state"], cancel))
        elif self._pending_render is not None:
            request = self._pending_render
            self._pending_render = None
            cache = request["base"] / ".editor"
            output = cache / ("view-" + uuid.uuid4().hex + ".png")
            def draw(cancel):
                image = composite_image(request["base"], request["channels"],
                                        request["width"], request["height"], cancel=cancel)
                if image.isNull() or image.width() != request["width"] or image.height() != request["height"]:
                    raise ValueError("Invalid slice frame dimensions")
                if cancel.is_set():
                    raise InterruptedError()
                cache.mkdir(exist_ok=True)
                if not image.save(str(output), "PNG"):
                    raise OSError("Cannot save counting preview")
                return {"path": str(output), "z_index": request["z_index"], "message": request["message"]}
            task = _ImageTask("render", request["generation"], request["sequence"], draw)
            task.output_path = output
        elif self._pending_layout is not None:
            request = self._pending_layout
            self._pending_layout = None
            task = _ImageTask("layout", request["generation"], request["sequence"],
                              lambda cancel: layout_labels(request["points"], request["width"],
                                                           request["height"], request["size"], cancel=cancel))
        else:
            return
        self._active_task = task
        task.signals.done.connect(self._task_done, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(task)
        self.drainedChanged.emit()
        self.changed.emit()

    @Slot(object)
    def _task_done(self, packet):
        self._active_task = None
        current = packet["generation"] == self._generation
        if packet["kind"] == "confirm" and "result" in packet:
            published = packet["result"]
            summary = {key: value for key, value in published.items()
                       if key not in ("source_fingerprint", "point_numbers")}
            if current:
                matches_snapshot = self._snapshot_state() == self._confirmation_snapshot
                if matches_snapshot:
                    self._content_fingerprint = published["source_fingerprint"]
                previous_fingerprint = self._confirmed_fingerprint
                previous_state = self._confirmed_state
                self._confirmed_state = self._confirmation_snapshot
                self._confirmed_fingerprint = summary["state_fingerprint"]
                self._confirmed_summary = summary
                self._confirming = False
                self._message = "计数结果已确认并保存。" if matches_snapshot else "计数版本已保存；后续修改仍在草稿中。"
                self._draft_enabled = True
                self._save_draft()
                if not self._flush_draft():
                    self._confirmed_fingerprint = previous_fingerprint
                    self._confirmed_state = previous_state
                    self._start_pending()
                    self.changed.emit()
                    return
            # A committed old run must still update its job after navigation.
            self.confirmed.emit(summary["run_id"], summary)
        elif current and packet["kind"] == "confirm":
            self._confirming = False
            self._message = "确认计数失败：" + packet.get("error", "操作已取消")
        elif packet["kind"] == "render" and "result" in packet:
            new_path = Path(packet["result"]["path"])
            if current and packet["sequence"] == self._sequence:
                self._present_frame(new_path, packet["result"]["z_index"], packet["result"]["message"])
            else:
                new_path.unlink(missing_ok=True)
        elif packet["kind"] == "layout":
            if current and packet["sequence"][0] == self._layout_epoch and "result" in packet:
                self._cache_layout(packet["sequence"][1], packet["result"])
                self.overlayChanged.emit()
        elif current and packet.get("error") and packet["sequence"] == self._sequence:
            self._frame_failed = True
            self._frame_pending = None
            self._message = "无法显示计数图像：" + packet["error"]
        self._cleanup_retired_caches()
        self._start_pending()
        self.changed.emit()

    def _pinned_frame_paths(self):
        urls = {self._image_url, self._displayed_image_url}
        if self._frame_pending:
            urls.add(self._frame_pending["url"])
        return {Path(QUrl(url).toLocalFile()) for url in urls if url}

    def _prune_frame_cache(self):
        pinned = self._pinned_frame_paths()
        for path in tuple(self._retired_previews):
            if path not in pinned:
                path.unlink(missing_ok=True)
                self._retired_previews.discard(path)
        # The cache belongs to one immutable analysis generation. Colors/ranges and
        # source identity cannot change during it; only Z and visible channels vary.
        for key in tuple(self._slice_cache_order):
            if len(self._slice_cache) <= 24:
                break
            path = self._slice_cache.get(key)
            if path in pinned:
                continue
            self._slice_cache_order.remove(key)
            self._slice_cache.pop(key, None)
            if path is not None:
                path.unlink(missing_ok=True)

    def _present_frame(self, path, z_index, message, *, width=None, height=None):
        url = QUrl.fromLocalFile(str(path)).toString()
        previous = Path(QUrl(self._image_url).toLocalFile()) if self._image_url else None
        key = self._slice_key(z_index)
        if previous and previous != Path(path) and self._analysis_path is not None:
            if previous.parent == self._analysis_path.parent / ".editor" and previous.name.startswith("view-"):
                self._retired_previews.add(previous)
        self._image_url = url
        self._frame_pending = {"url": url, "z": z_index, "key": key, "generation": self._generation,
                               "token": f"{self._generation}:{self._sequence}", "message": message,
                               "width": width or self.width, "height": height or self.height}
        self._prune_frame_cache()
        if url == self._displayed_image_url and key == self._applied_frame_key:
            self.commitFrame(url, self._frame_pending["token"])

    @Slot(str, str, result=bool)
    def commitFrame(self, url, token):
        pending = self._frame_pending
        if (not pending or str(url) != pending["url"] or str(token) != pending["token"]
                or pending["generation"] != self._generation or pending["key"] != self._slice_key(self._z_index)):
            return False
        layer_changed = self._displayed_z_index != pending["z"] or not self._frame_ready
        self._displayed_z_index = pending["z"]
        self._displayed_image_url = str(url)
        self._applied_frame_key = pending["key"]
        self._applied_quality = "proxy" if pending["key"][2] else "native"
        self._applied_frame_width, self._applied_frame_height = pending["width"], pending["height"]
        self._message = pending["message"]
        self._frame_pending = None
        self._frame_ready = True
        self._frame_failed = False
        if layer_changed:
            self._invalidate_layout()
        self._prune_frame_cache()
        if not self._scrubbing and self._z_index >= 0:
            self._prefetch_timer.start()
        self.changed.emit()
        return True

    @Slot(str)
    def imageReady(self, url):
        # Existing non-QML consumers acknowledge the currently offered immutable URL.
        # QML uses commitFrame(url, token) so each buffer also names its generation.
        pending = self._frame_pending
        if pending and str(url) == pending["url"]:
            self.commitFrame(url, pending["token"])

    @Slot(str)
    @Slot(str, str)
    def imageFailed(self, url, token=""):
        pending = self._frame_pending
        if (pending and str(url) == pending["url"] and (not token or str(token) == pending["token"])
                and pending["key"] == self._slice_key(self._z_index)):
            self._frame_pending = None
            self._frame_failed = True
            self._message = "无法加载计数预览图像。"
            self._prune_frame_cache()
            self.changed.emit()

    def _cancel_slice(self):
        process = self._slice_process
        self._slice_process = None
        self._slice_inflight = None
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _slice_key(self, z, max_edge=None):
        edge = self._display_edge() if max_edge is None else int(max_edge)
        if z < 0 or max(self.width, self.height) <= edge:
            edge = 0
        return (int(z), tuple(int(c["index"]) for c in self._channels if c.get("visible", True)), edge)

    @Slot()
    def _request_slice(self):
        if not self.available or not self._display_active or self._z_index < 0:
            return
        key = self._slice_key(self._z_index)
        cached = self._slice_cache.get(key)
        if cached and cached.is_file():
            self._slice_cache_order = [entry for entry in self._slice_cache_order if entry != key]
            self._slice_cache_order.append(key)
            self._slice_path = cached
            self._schedule_render()
            return
        if self._slice_inflight is not None:
            return  # Its completion will service the newest target, not a slider backlog.
        if not self._ensure_slice_server():
            return
        self._send_slice(key)

    def _ensure_slice_server(self):
        if self._slice_process is not None:
            return True
        runtime = self._runtime
        python = runtime.get("python") if isinstance(runtime, dict) else runtime
        if not python:
            self._slice_unavailable("此任务的 MIP 可离线编辑；查看 Z 切片需要已安装的计数运行环境。")
            return False
        self._refresh_source_status()
        if self._source_relocation_needed:
            self._slice_unavailable(self._source_relocation_message)
            return False
        process = QProcess(self)
        self._configure_worker_environment(process)
        process.setWorkingDirectory(str(self._analysis_path.parent))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.setProperty("generation", self._generation)
        process.finished.connect(self._slice_finished)
        process.errorOccurred.connect(self._slice_error)
        process.readyReadStandardOutput.connect(self._drain_slice_output)
        self._processes.add(process)
        self._slice_process = process
        self._slice_buffer = b""
        process.start(str(python), ["-B", "-u", "-m", "leap_cell_counting", "--slice-server", str(self._analysis_path)])
        self.drainedChanged.emit()
        return True

    def _configure_worker_environment(self, process):
        runtime = self._runtime
        env = self._environment
        if isinstance(env, QProcessEnvironment):
            process.setProcessEnvironment(env)
        else:
            target = QProcessEnvironment.systemEnvironment()
            for name in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                         "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH"):
                target.remove(name)
            if isinstance(runtime, dict):
                paths = [str(runtime[key]) for key in ("engine", "site_packages") if runtime.get(key)]
                if paths:
                    target.insert("PYTHONPATH", os.pathsep.join(paths))
            for name, value in (env or {}).items():
                target.insert(name, str(value))
            target.insert("PYTHONDONTWRITEBYTECODE", "1")
            target.insert("PYTHONNOUSERSITE", "1")
            process.setProcessEnvironment(target)

    def _refresh_source_status(self):
        if self._analysis_path is None:
            return
        source = _resolve(self._analysis_path.parent, self._analysis.get("source_path", ""))
        try:
            stat = source.stat()
            if not source.is_file() or not os.access(source, os.R_OK):
                raise PermissionError(str(source))
            signature = self._analysis.get("source_signature")
            changed = signature and (stat.st_size, stat.st_mtime_ns) != (signature["size_bytes"], signature["mtime_ns"])
        except OSError:
            self._source_relocation_needed = True
            self._source_relocation_message = "原始 TIFF 不可用；可重新定位原图，已保存的 MIP 和计数结果仍可编辑。"
        else:
            self._source_relocation_needed = bool(changed)
            self._source_relocation_message = ("原图自分析后已改变；请重新定位并验证原图。" if changed else "")

    @Slot(str)
    def relocateSource(self, path):
        if (not self.available or self._confirming or not self._source_relocation_needed
                or self._relocation_process is not None):
            return
        runtime = self._runtime
        python = runtime.get("python") if isinstance(runtime, dict) else runtime
        if not python:
            self._source_relocation_message = "验证原图需要已安装的计数运行环境。"
            self.changed.emit()
            return
        candidate = QUrl(path).toLocalFile() if QUrl(path).isLocalFile() else path
        self._cancel_slice()
        process = QProcess(self)
        self._configure_worker_environment(process)
        process.setProperty("generation", self._generation)
        process.setWorkingDirectory(str(self._analysis_path.parent))
        process.finished.connect(self._relocation_finished)
        process.errorOccurred.connect(self._relocation_error)
        self._processes.add(process)
        self._relocation_process = process
        self._source_relocation_message = "正在核对原图内容、维度、通道和标定…"
        script = ("import json,sys\n"
                  "from leap_cell_counting.worker import component_usage_lock\n"
                  "from leap_cell_counting.imaging import relocate_source\n"
                  "try:\n"
                  " with component_usage_lock(): result=relocate_source(sys.argv[1],sys.argv[2])\n"
                  " print(json.dumps(result),flush=True)\n"
                  "except Exception as exc:\n"
                  " print(json.dumps({'error':str(exc),'code':getattr(exc,'code','source_unavailable')}),flush=True)\n"
                  " sys.exit(1)\n")
        process.start(str(python), ["-B", "-u", "-c", script, str(self._analysis_path), str(candidate)])
        self.drainedChanged.emit()
        self.changed.emit()

    @Slot(int, QProcess.ExitStatus)
    def _relocation_finished(self, code, status):
        process = self.sender()
        if not isinstance(process, QProcess):
            return
        if process is self._relocation_process and process.property("generation") == self._generation:
            self._relocation_process = None
            try:
                result = json.loads(bytes(process.readAllStandardOutput()))
                if code != 0 or "error" in result:
                    raise ValueError(result.get("error", process.errorString()))
                self._analysis["source_path"] = result["source_path"]
                self._analysis["source_signature"] = result["source_signature"]
                self._source_relocation_needed = False
                self._source_relocation_message = ""
                self._message = "原图已验证并重新定位。"
            except (ValueError, KeyError, TypeError) as exc:
                self._source_relocation_message = "无法重新定位原图：" + str(exc)
        self._processes.discard(process)
        process.deleteLater()
        self._cleanup_retired_caches()
        self.changed.emit()

    @Slot(QProcess.ProcessError)
    def _relocation_error(self, error):
        if error != QProcess.ProcessError.FailedToStart:
            return
        process = self.sender()
        if process is self._relocation_process:
            self._relocation_process = None
            self._source_relocation_message = "无法重新定位原图：" + process.errorString()
        self._processes.discard(process)
        process.deleteLater()
        self._cleanup_retired_caches()
        self.changed.emit()

    def _send_slice(self, key):
        if self._slice_process is None or self._slice_inflight is not None:
            return
        cache = self._analysis_path.parent / ".editor" / "slices"
        cache.mkdir(parents=True, exist_ok=True)
        self._slice_request_id += 1
        output = cache / ("frame-" + uuid.uuid4().hex + ".png")
        request = {"id": self._slice_request_id, "z": key[0], "channels": list(key[1]), "max_edge": key[2], "output": str(output)}
        self._slice_inflight = {"id": request["id"], "key": key, "output": output}
        self._slice_process.write((json.dumps(request) + "\n").encode())

    @Slot()
    def _start_prefetch(self):
        if (not self.available or not self._display_active or self._scrubbing or self._frame_pending is not None or self._slice_inflight is not None
                or self._slice_process is None or self._z_index < 0 or self._view_zoom > 1.0
                or self._applied_frame_key != self._slice_key(self._z_index)):
            return
        z = self._z_index + self._last_z_direction
        if 0 <= z < self.zCount:
            edge = 1536
            key = self._slice_key(z, edge)
            if key not in self._slice_cache:
                self._send_slice(key)

    def _slice_unavailable(self, message):
        self._z_index = -1
        self._slice_path = None
        self._message = message
        self._schedule_render(keep_message=True)
        self.changed.emit()

    @Slot()
    def _drain_slice_output(self):
        process = self.sender()
        if process is not self._slice_process or process.property("generation") != self._generation:
            return
        self._slice_buffer += bytes(process.readAllStandardOutput())
        while b"\n" in self._slice_buffer:
            line, self._slice_buffer = self._slice_buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                response = json.loads(line)
                request = self._slice_inflight
                if request is None or response.get("id") != request["id"]:
                    raise ValueError("Slice response does not match the in-flight request")
                self._slice_inflight = None
                if response.get("error"):
                    self._refresh_source_status()
                    error = response["error"]
                    if isinstance(error, dict) and error.get("code") in ("source_changed", "source_missing", "source_inaccessible"):
                        self._source_relocation_needed = True
                        self._source_relocation_message = "原图自分析后已改变；请重新定位并验证原图。"
                    if self._z_index >= 0 and request["key"] != self._slice_key(self._z_index):
                        request["output"].unlink(missing_ok=True)
                        if not self._slice_timer.isActive():
                            self._request_slice()
                        self.changed.emit()
                        continue
                    raise ValueError(str(response["error"]))
                output = Path(response["path"])
                if output != request["output"] or not output.is_file() or int(response["z"]) != request["key"][0]:
                    raise ValueError("Slice response has invalid output or plane")
                key = request["key"]
                expected = self._expected_frame_size(key[2])
                if ((response.get("native_width", self.width), response.get("native_height", self.height)) != (self.width, self.height)
                        or (response.get("width", expected[0]), response.get("height", expected[1])) != expected
                        or response.get("max_edge", key[2]) != key[2]
                        or response.get("quality", "proxy" if key[2] else "native") != ("proxy" if key[2] else "native")):
                    raise ValueError("Slice response geometry does not match the requested display level")
                previous = self._slice_cache.get(key)
                if previous and previous != output:
                    self._retired_previews.add(previous)
                self._slice_cache[key] = output
                self._slice_cache_order = [entry for entry in self._slice_cache_order if entry != key]
                self._slice_cache_order.append(key)
                self._prune_frame_cache()
                if self._display_active and self._z_index >= 0:
                    desired = self._slice_key(self._z_index)
                    if desired == key:
                        self._slice_path = output
                        self._schedule_render()
                    elif desired not in self._slice_cache and not self._slice_timer.isActive():
                        self._send_slice(desired)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self._slice_inflight = None
                self._slice_unavailable("无法读取 Z 切片：" + str(exc))
            self.changed.emit()

    @Slot(int, QProcess.ExitStatus)
    def _slice_finished(self, code, status):
        process = self.sender()
        if not isinstance(process, QProcess):
            return
        if process is self._slice_process:
            self._slice_process = None
            self._slice_inflight = None
            self._refresh_source_status()
            if self._z_index >= 0:
                detail = bytes(process.readAllStandardError()).decode("utf-8", "replace")[-1000:]
                self._slice_unavailable("切片读取服务已退出：" + (detail or str(code)))
        self._processes.discard(process)
        process.deleteLater()
        self._cleanup_retired_caches()
        self.changed.emit()

    @Slot(QProcess.ProcessError)
    def _slice_error(self, error):
        if error != QProcess.ProcessError.FailedToStart:
            return
        process = self.sender()
        if not isinstance(process, QProcess):
            return
        if process is self._slice_process:
            self._slice_process = None
            self._slice_inflight = None
            self._slice_unavailable("无法启动切片读取：" + process.errorString())
        self._processes.discard(process)
        process.deleteLater()
        self._cleanup_retired_caches()
        self.changed.emit()

    @Slot()
    def confirm(self):
        if self._relocation_process is not None:
            self._message = "正在验证原图，请等待验证完成后确认或切换会话。"
            self.changed.emit()
            return
        if not self.available or self._confirming:
            return
        self._confirming = True
        self._save_draft()
        self._confirmation_snapshot = self._snapshot_state()
        if self._active_task is not None and self._active_task.kind == "layout":
            self._active_task.cancel.set()
        self._message = "正在保存完整分辨率的计数结果…"
        self._pending_confirm = {"generation": self._generation, "sequence": self._sequence,
                                 "path": self._analysis_path, "analysis": self._analysis,
                                 "state": self._confirmation_snapshot}
        self._start_pending()
        self.changed.emit()

    available = Property(bool, lambda self: self._analysis_path is not None, notify=changed)
    runId = Property(str, lambda self: str(self._analysis.get("run_id", "")), notify=changed)
    identityModel = Property(str, lambda self: str(self._analysis.get("identity_model", "")), notify=changed)
    labelArtifacts = Property("QVariantMap", lambda self: copy.deepcopy(self._analysis.get("label_artifacts", {})), notify=changed)
    schemaVersion = Property(int, lambda self: int(self._analysis.get("schema_version", 1)), notify=changed)
    runSettings = Property("QVariantMap", lambda self: {key: copy.deepcopy(self._analysis.get(key)) for key in (
        "source_path", "width", "height", "z_count", "dapi_channel", "support_channel", "support_marker",
        "display_channels", "physical_size_um")}, notify=changed)
    visiblePoints = Property("QVariantList", _visible_points, notify=changed)
    pendingCount = Property(int, lambda self: sum(p.get("status") == "review" and not p.get("deleted", False)
                                                for p in self._points), notify=changed)
    neuronCount = Property(int, lambda self: sum(p.get("status") == "excluded" and not p.get("deleted", False)
                                               for p in self._points), notify=changed)
    likelyUnrecognizedCount = Property(int, lambda self: sum(p.get("status") == "review" and not p.get("deleted", False)
        and p.get("review_hint") == "likely_astrocyte" for p in self._points), notify=changed)
    ordinaryUnrecognizedCount = Property(int, lambda self: self.pendingCount - self.likelyUnrecognizedCount, notify=changed)
    viewZoom = Property(float, lambda self: self._view_zoom, notify=viewChanged)
    viewPanX = Property(float, lambda self: self._view_pan_x, notify=viewChanged)
    viewPanY = Property(float, lambda self: self._view_pan_y, notify=viewChanged)
    displayedZIndex = Property(int, lambda self: self._displayed_z_index if self._displayed_z_index is not None else -1, notify=changed)
    frameReady = Property(bool, lambda self: self._frame_ready, notify=changed)
    points = Property("QVariantList", lambda self: copy.deepcopy(self._points), notify=changed)
    selectedIds = Property("QStringList", lambda self: sorted(self._selected), notify=changed)
    total = Property(int, lambda self: sum(p.get("status", "accepted") == "accepted" and not p.get("deleted", False)
                                         for p in self._points), notify=changed)
    autoCount = Property(int, lambda self: int(self._analysis.get("auto_count", 0)), notify=changed)
    mode = Property(str, lambda self: self._mode, notify=changed)
    canMerge = Property(bool, lambda self: self.available and not self._confirming and
                        sum(point["id"] in self._selected for point in self._points) >= 2 and
                        not any(p["id"] in self._selected and p.get("status") == "excluded" for p in self._points), notify=changed)
    canAcceptSelected = Property(bool, lambda self: self.available and not self._confirming and
        any(p["id"] in self._selected and p.get("status") in ("review", "excluded")
            and not p.get("deleted", False) for p in self._points), notify=changed)
    canUndo = Property(bool, lambda self: bool(self._undo) and not self._confirming, notify=changed)
    canRedo = Property(bool, lambda self: bool(self._redo) and not self._confirming, notify=changed)
    dirty = Property(bool, lambda self: self.available and not self._matches_confirmed(), notify=changed)
    confirmationInProgress = Property(bool, lambda self: self._confirming or self._pending_confirm is not None or
        (self._active_task is not None and self._active_task.kind == "confirm"), notify=changed)
    confirmedState = Property(bool, lambda self: self.available and bool(self._confirmed_fingerprint)
                              and self._matches_confirmed(), notify=changed)
    imageUrl = Property(str, lambda self: self._image_url, notify=changed)
    displayActive = Property(bool, lambda self: self._display_active, notify=changed)
    appliedImageUrl = Property(str, lambda self: self._displayed_image_url, notify=changed)
    pendingFrameToken = Property(str, lambda self: self._frame_pending["token"] if self._frame_pending else "", notify=changed)
    appliedPreviewQuality = Property(str, lambda self: self._applied_quality, notify=changed)
    appliedFrameWidth = Property(int, lambda self: self._applied_frame_width, notify=changed)
    appliedFrameHeight = Property(int, lambda self: self._applied_frame_height, notify=changed)
    scrubbing = Property(bool, lambda self: self._scrubbing, notify=changed)
    width = Property(int, lambda self: int(self._analysis.get("width", 0)), notify=changed)
    height = Property(int, lambda self: int(self._analysis.get("height", 0)), notify=changed)
    zCount = Property(int, lambda self: int(self._analysis.get("z_count", 1)), notify=changed)
    zIndex = Property(int, lambda self: self._z_index, notify=changed)
    channels = Property("QVariantList", lambda self: copy.deepcopy(self._channels), notify=changed)
    labelSize = Property(int, lambda self: self._label_size, notify=changed)
    showLabels = Property(bool, lambda self: self._show_labels, notify=changed)
    labelLayout = Property("QVariantList", lambda self: self.labelsForScale(1.0), notify=changed)
    busy = Property(bool, lambda self: self._confirming or self._relocation_process is not None or self._scrubbing or
                    (self._active_task is not None and self._active_task.kind == "render"
                     and self._active_task.generation == self._generation and self._active_task.sequence == self._sequence) or
                    self._pending_render is not None or (self.available and self._display_active and self._applied_frame_key != self._slice_key(self._z_index) and not self._frame_failed) or self._slice_timer.isActive(), notify=changed)
    drained = Property(bool, lambda self: self._active_task is None and not self._processes and not self._retired_caches,
                       notify=drainedChanged)
    sourceRelocationNeeded = Property(bool, lambda self: self._source_relocation_needed, notify=changed)
    sourceRelocationInProgress = Property(bool, lambda self: self._relocation_process is not None, notify=changed)
    sourceRelocationMessage = Property(str, lambda self: self._locale.translate(self._source_relocation_message)
                                      if self._locale else self._source_relocation_message, notify=changed)
    message = Property(str, lambda self: self._locale.translate(self._message) if self._locale else self._message, notify=changed)
