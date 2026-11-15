"""Bounded, observational run details; never changes scientific task state.

Live data is fed from the controller's existing incremental readers. Historical
reads are capped and run off the GUI thread; original logs are never rewritten.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import math
from pathlib import Path
import re
import threading
import time

from PySide6.QtCore import QObject, QRunnable, Signal

LOG_LIMIT = 120_000
TEXT_READ_LIMIT = 2 * 1024 * 1024
JSON_READ_LIMIT = 16 * 1024 * 1024
MAX_ROWS = 128
MAX_TIMINGS = 96
MAX_MESSAGES = 64
MAX_LINE = 16_384
PROGRESS_READ_LIMIT = 64 * 1024
PROGRESS_LINE_LIMIT = 256 * 1024
SECTION_TITLES = ("任务与软件", "样本与通道", "分析步骤与参数", "审核与结果", "耗时与重要事件")
TIMING_LABELS = {
    "source_scan_seconds": "原始数据扫描",
    "localization_seconds": "细胞核定位",
    "model_load_seconds": "模型加载（与扫描并行）",
    "model_wait_seconds": "等待模型就绪",
    "source_and_model_preparation_seconds": "图像与模型准备：实际经过时间",
    "body_localization_seconds": "胞体定位",
    "observation_routes_wall_seconds": "并行观察路线：实际经过时间",
    "nuclear_route_seconds": "细胞核路线（与胞体并行）",
    "body_route_seconds": "胞体路线（与细胞核并行）",
    "identity_association_seconds": "细胞身份关联",
    "display_artifacts_seconds": "生成显示图像",
    "total_seconds": "自动分析总耗时",
}


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def empty_details(job_id="", module_id="", *, history=False, loading=False):
    return {"jobId": job_id, "runId": job_id, "moduleId": module_id, "status": "idle",
            "stage": "", "elapsedSeconds": None, "elapsedIncludesReview": False,
            "available": False, "loading": loading, "history": history, "rows": [],
            "timings": [], "warnings": [], "errors": [], "resources": {},
            "logText": "", "logTruncated": False, "detailsTruncated": False, "summarySections": []}


class RunDetails:
    def __init__(self, job, *, live=False):
        self.job_id = str(job.get("id", ""))
        self.run_id = str(job.get("run_id", self.job_id))
        self.module_id = job.get("module_id", "project-leap-2d")
        self.status, self.stage = job.get("status", "idle"), job.get("stage", "")
        self.rows, self.timings = OrderedDict(), OrderedDict()
        self.warnings, self.errors = [], []
        self.started = time.monotonic() if live else None
        self.elapsed = None
        self.partial_line = ""
        self.line_discarded = False
        self.available = live
        self.truncated = False
        self.current_resources = {}
        self.sections = OrderedDict((title, OrderedDict()) for title in SECTION_TITLES)
        self.live = live
        self.pending_updates = []
        self._observing_console = False
        saved = job.get("run_details") or {}
        if saved.get("runId") == self.run_id and saved.get("moduleId") == self.module_id:
            for item in saved.get("rows", [])[:MAX_ROWS]:
                if isinstance(item, dict):
                    label = item.get("label", "")
                    if label == "模型版本" and self.module_id == "cell-counting":
                        label = "Cellpose 软件包版本"
                    self._row(label, item.get("value", ""))
            for item in saved.get("timings", [])[:MAX_TIMINGS]:
                if isinstance(item, dict):
                    self._timing(item.get("stage", ""), item.get("seconds"), item.get("label"))
            for kind in ("warnings", "errors"):
                for message in saved.get(kind, [])[-MAX_MESSAGES:]:
                    self._message(kind, str(message))
            elapsed = saved.get("elapsedSeconds")
            if finite_number(elapsed):
                self.elapsed = elapsed
            self.available |= bool(saved.get("available"))
            self.truncated |= bool(saved.get("detailsTruncated"))
            for section in saved.get("summarySections", []):
                if isinstance(section, dict) and section.get("title") in self.sections:
                    for row in section.get("rows", [])[:MAX_ROWS]:
                        if isinstance(row, dict):
                            self._summary_row(section["title"], row.get("label"), row.get("value"))
        self.pending_updates.clear()
        self.job(job)

    def _summary_row(self, section, label, value):
        if not label or value is None or value == "":
            return
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        target = self.sections[section]
        target[str(label)[:160]] = value[:2048]
        if len(target) > MAX_ROWS:
            target.popitem(last=False)
            self.truncated = True

    def job(self, job):
        for key, label in (("id", "任务编号"), ("app_version", "应用版本"),
                           ("module_version", "分析组件版本"), ("created_at", "开始时间")):
            self._summary_row(SECTION_TITLES[0], label, job.get(key))
        self._summary_row(SECTION_TITLES[0], "分析模块", job.get("module_id", self.module_id))
        self._summary_row(SECTION_TITLES[1], "样本", job.get("name"))
        states = {"completed": "分析完成", "waiting_review": "等待审核", "failed": "任务未完成",
                  "rejected": "分析已停止", "cancelled": "已取消", "interrupted": "上次任务已中断",
                  "running": "分析处理中", "importing": "拆分与准备图像"}
        self._summary_row(SECTION_TITLES[3], "任务状态", job.get("stage") or states.get(job.get("status"), job.get("status")))
        for key, label in (("auto_count", "自动接受数量"), ("count", "已确认数量"),
                           ("revision", "确认版本"), ("confirmed_at", "确认时间")):
            if key == "count" and job.get("status") != "completed":
                continue
            self._summary_row(SECTION_TITLES[3], label, job.get(key))

    def take_live_updates(self):
        updates, self.pending_updates = self.pending_updates, []
        return updates

    def _row(self, label, value):
        if not label:
            return
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        self.truncated |= len(value) > 2048
        label, value = str(label)[:160], value[:2048]
        if self.live and not self._observing_console and self.rows.get(label) != value:
            self.pending_updates.append((label, value))
        self.rows[label] = value
        section = SECTION_TITLES[1] if label in {
            "图像组", "时间点", "源通道角色", "DAPI 源通道", "辅助源通道", "辅助标记物", "显示源通道",
            "标定（µm）", "标定来源", "源数据类型", "源图 Z 层数", "图像尺寸", "源图轴顺序"} else SECTION_TITLES[2]
        if label in {"模型", "Cellpose 软件包版本", "模型 SHA-256", "细胞核模型", "细胞核模型 SHA-256", "实际推理设备", "掩码动力学设备"}:
            section = SECTION_TITLES[0]
        self._summary_row(section, label, value)
        if len(self.rows) > MAX_ROWS:
            self.rows.popitem(last=False)
            self.truncated = True
        self.available = True

    def _message(self, kind, message):
        target = getattr(self, kind)
        message = message[:4096]
        if message and message not in target:
            target.append(message)
            if len(target) > MAX_MESSAGES:
                del target[0]
                self.truncated = True
            self.available = True

    def _timing(self, stage, seconds, label=None):
        if not finite_number(seconds) or seconds < 0:
            return
        stage = str(stage)[:256]
        if (self.live and not self._observing_console
                and self.timings.get(stage, {}).get("seconds") != seconds):
            self.pending_updates.append((str(label or stage), f"{seconds:.3f} s"))
        self.timings[stage] = {"stage": stage, "seconds": seconds, "label": str(label or stage)[:512]}
        if len(self.timings) > MAX_TIMINGS:
            self.timings.popitem(last=False)
            self.truncated = True
        self.available = True

    def model(self, model):
        if not isinstance(model, dict):
            return
        labels = {"name": "模型", "version": "Cellpose 软件包版本", "sha256": "模型 SHA-256",
                  "device": "实际推理设备", "mask_dynamics_device": "掩码动力学设备",
                  "input": "模型输入", "input_mode": "输入模式", "normalization": "归一化",
                  "tiling_version": "分块方式", "calibration": "标定",
                  "loaded_concurrently_with_source_statistics": "模型加载与源图统计并行"}
        for key, label in labels.items():
            if key in model:
                self._row(label, model[key])
        parameters = model.get("parameters")
        if isinstance(parameters, dict):
            for key, value in list(parameters.items())[:MAX_ROWS]:
                self._row("参数 · " + key, value)

    def timing_data(self, timings):
        if not isinstance(timings, dict):
            return
        for key, value in timings.items():
            if key.endswith("_seconds") and finite_number(value):
                self._timing(key, value, TIMING_LABELS.get(key, key))
        if timings.get("route_times_overlap_and_must_not_be_added") is True:
            self._row("计时解释", "细胞核与胞体路线并行执行，各路线耗时不能相加。")
        if "model_load_seconds" in timings and "source_scan_seconds" in timings:
            self._row("准备阶段计时", "模型加载与源图统计并行执行；准备阶段总耗时采用实际经过时间。")
        if finite_number(timings.get("total_seconds")):
            self.elapsed = timings["total_seconds"]

    def request(self, value):
        if not isinstance(value, dict) or value.get("run_id", self.run_id) != self.run_id:
            return
        for key, label in (("series_index", "图像组"), ("time_index", "时间点"),
                           ("channel_roles", "源通道角色"), ("dapi_channel", "DAPI 源通道"),
                           ("support_channel", "辅助源通道"), ("support_marker", "辅助标记物"),
                           ("display_channels", "显示源通道"), ("profile", "请求的年龄配置")):
            if key in value and value[key] is not None:
                display = value[key]
                if key in ("series_index", "time_index") and type(display) is int:
                    display += 1
                elif key in ("dapi_channel", "support_channel") and type(display) is int and display >= 0:
                    display = "C" + str(display + 1)
                elif key == "display_channels" and isinstance(display, list):
                    display = ", ".join("C" + str(index + 1) for index in display if type(index) is int and index >= 0)
                elif key == "channel_roles" and isinstance(display, list):
                    display = ", ".join("C" + str(index + 1) + ": " + str(role) for index, role in enumerate(display))
                self._row(label, display)

    def analysis(self, value):
        if not isinstance(value, dict) or value.get("run_id") != self.run_id:
            return
        self.model(value.get("model"))
        self.timing_data(value.get("timings"))
        self.request(value)
        for key, label in (("physical_size_um", "标定（µm）"), ("source_dtype", "源数据类型"),
                           ("z_count", "源图 Z 层数"), ("calibration_sources", "标定来源")):
            if key in value:
                self._row(label, value[key])
        if value.get("width") and value.get("height"):
            self._row("图像尺寸", f"{value['width']} × {value['height']} px")
        for key, label in (("auto_count", "自动接受数量"), ("pending_count", "待审核数量"),
                           ("excluded_count", "自动排除数量")):
            self._summary_row(SECTION_TITLES[3], label, value.get(key))

    def event(self, event):
        if not isinstance(event, dict) or event.get("run_id", self.run_id) != self.run_id:
            return False
        if event.get("module_id", self.module_id) != self.module_id:
            return False
        self.available = True
        kind = event.get("type")
        self.model(event.get("model"))
        self.timing_data(event.get("timings"))
        if kind == "warning":
            self._message("warnings", event.get("message", ""))
        elif kind in ("error", "failed"):
            self._message("errors", event.get("message", ""))
        return True

    def console(self, text, *, final=False):
        if text:
            self.available = True
        text = self.partial_line + text
        lines = text.split("\n")
        self.partial_line = lines.pop()
        if final and self.partial_line:
            lines.append(self.partial_line)
            self.partial_line = ""
        self._observing_console = True
        try:
            for line in lines:
                if self.line_discarded:
                    self.line_discarded = False
                    continue
                self._console_line(line[:MAX_LINE].rstrip("\r"))
        finally:
            self._observing_console = False
        if len(self.partial_line) > MAX_LINE:
            self.partial_line = ""
            self.line_discarded = True
            self.truncated = True

    def _console_line(self, line):
        if re.search(r"\b(warning|warn|retrying|fallback|no seeds|no masks)\b", line, re.I):
            self._message("warnings", line)
        if re.match(r"\s*(?:error|\w+Error|\w+Exception)\s*:", line, re.I):
            self._message("errors", line)
        if self.module_id != "project-leap-2d":
            return
        if "Cellpose-SAM inference" in line:
            self._row("模型", "Cellpose-SAM")
            for key, value in re.findall(r"\b(device|max_side|diameter|cellprob|batch)=([^|\s]+)", line):
                self._row("实际推理设备" if key == "device" else "最近一次推理 · " + key, value)
            match = re.search(r"(Cellpose-SAM inference \d+) completed.*\btime=([\d.]+) s", line)
            if match:
                self._timing(match[1], float(match[2]))
        match = re.search(r"Nucleus Model(?: SHA256)?:\s*(.+)", line)
        if match:
            self._row("细胞核模型 SHA-256" if "SHA256" in line else "细胞核模型", match[1])
        # Preserve the source label's wall/worker/human-wait distinctions.
        match = re.match(r"\s*([^:]+):\s*([\d.]+) s\b(.*)", line)
        if match:
            self._timing(match[1].strip(), float(match[2]), (match[1] + match[3]).strip())
            if match[1].strip() == "Complete pipeline elapsed":
                self.elapsed = float(match[2])

    def freeze(self):
        if self.started is not None:
            if self.elapsed is None:
                self.elapsed = max(0.0, time.monotonic() - self.started)
            self.started = None
        if self.current_resources:
            self.current_resources = {**self.current_resources, "mode": "stopped"}

    def snapshot(self, *, history=False, log_text="", log_truncated=False):
        result = empty_details(self.job_id, self.module_id, history=history)
        elapsed = max(0.0, time.monotonic() - self.started) if self.started is not None else self.elapsed
        sections = OrderedDict((title, OrderedDict(rows)) for title, rows in self.sections.items())
        for timing in self.timings.values():
            sections[SECTION_TITLES[4]][timing["label"]] = f"{timing['seconds']:.3f} s"
        for kind, label in (("warnings", "重要提示"), ("errors", "任务错误")):
            messages = [message for message in getattr(self, kind)
                        if not re.search(r"Traceback|File \"|/[^\s]+\.py\b", message)]
            if messages:
                sections[SECTION_TITLES[4]][label] = "\n".join(messages[-8:])
        summary_sections = [{"title": title, "rows": [{"label": label, "value": value}
                             for label, value in rows.items()]} for title, rows in sections.items() if rows]
        result.update(runId=self.run_id, status=self.status, stage=self.stage,
                      available=self.available or bool(log_text) or (history and bool(summary_sections)),
                      rows=[{"label": key, "value": value} for key, value in self.rows.items()],
                      timings=list(self.timings.values()), warnings=list(self.warnings), errors=list(self.errors),
                      elapsedSeconds=elapsed, elapsedIncludesReview=self.module_id == "project-leap-2d",
                      logText="" if history else log_text[-LOG_LIMIT:], logTruncated=False if history else log_truncated or len(log_text) > LOG_LIMIT,
                      detailsTruncated=self.truncated, resources={} if history else self.current_resources,
                      summarySections=summary_sections)
        return result

    def persisted(self):
        result = self.snapshot(history=True)
        return {key: result[key] for key in ("runId", "moduleId", "available", "rows", "timings", "warnings",
                                            "errors", "elapsedSeconds", "detailsTruncated", "summarySections")}


def _json_file(path, cancel):
    if cancel.is_set():
        raise InterruptedError
    if not path.is_file():
        return None
    if path.stat().st_size > JSON_READ_LIMIT:
        raise ValueError("Run details file exceeds the display limit: " + path.name)
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _text_parts(path, cancel):
    """Read a bounded prefix and tail; do not scan a multi-gigabyte log."""
    if cancel.is_set():
        raise InterruptedError
    if not path.is_file():
        return [], False
    with path.open("rb") as stream:
        size = path.stat().st_size
        if size <= TEXT_READ_LIMIT:
            return [stream.read(TEXT_READ_LIMIT).decode("utf-8", errors="replace")], False
        head = stream.read(TEXT_READ_LIMIT // 2)
        head = head.rsplit(b"\n", 1)[0] + b"\n"
        stream.seek(max(0, size - TEXT_READ_LIMIT // 2))
        tail = stream.read(TEXT_READ_LIMIT // 2)
        # Discard the partial first line using only the already bounded read.
        tail = tail.split(b"\n", 1)[1] if b"\n" in tail else b""
        return [head.decode("utf-8", errors="replace"), tail.decode("utf-8", errors="replace")], True


class ProgressReader:
    """Read only new, complete diagnostic records with a fixed per-tick budget."""
    FILES = ("model-progress.jsonl", "native-recovery-progress.jsonl", "body-progress.jsonl")

    def __init__(self):
        self.offsets = dict.fromkeys(self.FILES, 0)
        self.partial = {name: b"" for name in self.FILES}
        self.discarding = set()

    def read(self, directory):
        records = []
        for name in self.FILES:
            path = Path(directory) / name
            try:
                with path.open("rb") as stream:
                    stream.seek(self.offsets[name])
                    chunk = stream.read(PROGRESS_READ_LIMIT)
                    self.offsets[name] = stream.tell()
            except OSError:
                continue  # Diagnostic observation must never stop the task.
            if not chunk:
                continue
            parts = (self.partial[name] + chunk).split(b"\n")
            self.partial[name] = parts.pop()
            for line in parts:
                if name in self.discarding:
                    self.discarding.remove(name)
                    continue
                if len(line) > PROGRESS_LINE_LIMIT:
                    continue
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(value, dict):
                    records.append((name, value))
            if len(self.partial[name]) > PROGRESS_LINE_LIMIT:
                self.partial[name] = b""
                self.discarding.add(name)
        return records

    def pending(self, directory):
        for name in self.FILES:
            try:
                if (Path(directory) / name).stat().st_size > self.offsets[name]:
                    return True
            except OSError:
                pass
        return False


def progress_description(name, value):
    """Select completed-work evidence; batch times are explicitly not additive."""
    if name == "model-progress.jsonl":
        rows = [("窗口", value.get("window_id")), ("主要候选", value.get("primary_instances")),
                ("网络分块", value.get("actual_network_tiles")),
                ("批次耗时（同批窗口共享）", value.get("model_batch_seconds"))]
        if value.get("permissive_triggered"):
            rows.append(("宽松候选（待审核）", len(value.get("permissive_new_xy_ids", []))))
        return "DAPI 局部推理", rows
    if name == "native-recovery-progress.jsonl":
        metrics = value.get("metrics") or {}
        return "原生三维恢复", [("窗口", value.get("window_id")), ("处理尝试次数", len(value.get("attempts", []))),
            ("恢复对象", metrics.get("nuclear_identities_created", metrics.get("objects"))),
            ("耗时", value.get("seconds"))]
    return "胞体区域分析", [("区域", value.get("region_id")), ("胞体对象", value.get("objects")),
                            ("处理尝试次数", len(value.get("attempts", []))), ("耗时", value.get("seconds"))]


REPORT_FIELDS = {
    "Astrocyte Profile": (2, "实际年龄配置"), "Profile Decision Source": (2, "年龄配置来源"),
    "Supported GFAP-only Age Profile": (2, "实际年龄配置"), "Measurement": (1, "测量通道"),
    "ROI Definition Channels": (1, "ROI 定义通道"), "ROI Definition": (1, "ROI 定义通道"),
    "Measurement Channel Used for ROI Definition": (2, "测量通道参与 ROI 定义"),
    "Image Geometry": (1, "图像几何"), "Pixel Calibration (um)": (1, "标定（µm）"),
    "Best Candidate": (2, "最终候选"), "Final Z Position": (2, "最终 Z 位置"),
    "Z Range": (2, "分析 Z 范围"), "Selected Z (1-based inclusive)": (2, "分析 Z 范围（首尾均包含）"),
    "Actual Z Width": (2, "实际 Z 厚度"), "Actual Surface Guard": (2, "实际表面保护距离"),
    "Projection": (2, "分析投影"), "Cellpose-SAM": (0, "Cellpose-SAM 实际配置"),
    "Cellpose Batch Size": (2, "Cellpose 批大小"), "Segmentation Method": (2, "分割方法"),
    "Nucleus Model": (0, "细胞核模型"), "Nucleus Model SHA256": (0, "细胞核模型 SHA-256"),
    "Automatically Selected Whole Astrocyte ROIs": (3, "自动 ROI 数量"),
    "Astrocyte ROI Count": (3, "自动 ROI 数量"), "Final ROI Count": (3, "最终 ROI 数量"),
    "Final ROI Counts": (3, "最终分区 ROI 数量"), "Manual ROI Review Used": (3, "人工审核记录"),
    "Manual Review Used": (3, "已使用人工审核"), "Fiji Status": (3, "Fiji 审核状态"),
    "Partition QC": (3, "分区一致性检查"), "Measurement Channel QC": (3, "测量通道检查"),
    "Candidate Errors": (4, "候选错误数量"),
    "Cellpose Model Initialization": (4, "Cellpose 模型加载"),
    "Candidate Stage Wall Time": (4, "候选阶段实际经过时间"),
    "Candidate Worker Compute Total": (4, "候选工作进程计算时间之和（并行）"),
    "Candidate Ranking Time": (4, "候选排序"),
    "Soma/Processes and 3D Ownership Time": (4, "分区与三维归属"),
    "Fiji Startup to Review Ready": (4, "Fiji 启动至可审核"),
    "Fiji Native Measurement Time": (4, "Fiji 原生测量"),
    "Automated Inference Elapsed (s)": (4, "自动推理耗时（秒）"),
}


def _report_summary(details, text):
    section = ""
    parameters = False
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line
        if line == "Compartment Parameters:":
            parameters = True
            continue
        if not line.strip():
            parameters = False
        key, separator, value = line.partition(":")
        if separator and parameters and line.startswith("  "):
            details._summary_row(SECTION_TITLES[2], "分区参数 · " + key.strip(), value.strip())
        if separator and section == "[Fiji]" and key.strip() == "Status":
            details._summary_row(SECTION_TITLES[3], "Fiji 审核状态", value.strip())
        if separator and key.strip() in REPORT_FIELDS:
            section, label = REPORT_FIELDS[key.strip()]
            # Report values can include an input filename, never script/stack paths.
            details._summary_row(SECTION_TITLES[section], label, value.strip())
        match = re.match(r"- (Z selection|DAPI nucleus model|GFAP structure and compartments|Measurement preparation|Fiji review and publication): Completed \(([\d.]+) s\)", line)
        if match:
            labels = {"Z selection": "Z 范围选择", "DAPI nucleus model": "细胞核模型推理",
                      "GFAP structure and compartments": "GFAP 分区", "Measurement preparation": "测量准备",
                      "Fiji review and publication": "Fiji 审核与发布（含人工等待）"}
            details._summary_row(SECTION_TITLES[4], labels[match[1]], match[2] + " s")


def _history_sources(details, run_dir, job, cancel):
    def read(name):
        try:
            return _json_file(run_dir / name, cancel) or {}
        except (OSError, ValueError):
            details.truncated = True
            return {}
    provenance = read("runtime_provenance.json")
    if provenance.get("run_id", details.run_id) == details.run_id:
        details._summary_row(SECTION_TITLES[0], "科学引擎版本", (provenance.get("engine") or {}).get("version"))
        requested = provenance.get("request_provenance") or {}
        details._summary_row(SECTION_TITLES[0], "应用版本", requested.get("app_version"))
        details._summary_row(SECTION_TITLES[0], "分析组件版本", requested.get("module_version") or requested.get("component_version"))
        for name, weight_label, hash_label in (
                ("cellpose", "运行环境 Cellpose 模型权重", "运行环境 Cellpose 权重 SHA-256"),
                ("instanseg", "运行环境 InstanSeg 模型权重", "运行环境 InstanSeg 权重 SHA-256")):
            model = (provenance.get("models") or {}).get(name)
            if isinstance(model, dict):
                details._summary_row(SECTION_TITLES[0], hash_label, model.get("sha256"))
                if isinstance(model.get("path"), str):
                    details._summary_row(SECTION_TITLES[0], weight_label, Path(model["path"]).name)
    imported = read("import-result.json")
    if imported.get("run_id", details.run_id) == details.run_id:
        metadata = imported.get("metadata") or {}
        details.request(metadata)
        for key, label in (("shape", "图像尺寸"), ("axes", "源图轴顺序"), ("dtype", "源数据类型"),
                           ("physical_size_um", "标定（µm）"), ("calibration_sources", "标定来源")):
            if key in metadata:
                details._row(label, metadata[key])
        details._timing("import", imported.get("elapsed_seconds"), "图像导入")
    outcome = read("outcome.json") or job.get("outcome") or {}
    if outcome.get("run_id", details.run_id) == details.run_id:
        if outcome.get("status") in ("failed", "rejected", "cancelled"):
            details._summary_row(SECTION_TITLES[4], "结束原因", outcome.get("message"))
            details._summary_row(SECTION_TITLES[4], "错误代码", outcome.get("error_code"))
    # Only known artifacts inside this run are read. No source TIFF or debug tree.
    artifacts = outcome.get("artifacts") or {}
    report = artifacts.get("report") if isinstance(artifacts, dict) else None
    candidates = [run_dir / "workspace/Result/IHC_2D_Analysis_Report.txt"]
    if isinstance(report, str):
        path = Path(report)
        path = path if path.is_absolute() else run_dir / path
        if path.resolve().is_relative_to(run_dir.resolve()):
            candidates.insert(0, path)
    for path in candidates:
        try:
            parts, truncated = _text_parts(path, cancel)
        except OSError:
            continue
        if parts:
            details.truncated |= truncated
            for part in parts:
                _report_summary(details, part)
            break
    if details.module_id == "cell-counting":
        diagnostics = read("counting/diagnostics.json")
        scan = diagnostics.get("source_scan") or {}
        for label, value in (("所有 Z 层参与分析", scan.get("all_z_participated")),
                             ("实际网络分块数量", diagnostics.get("total_actual_network_tiles")),
                             ("已分析局部窗口", len(diagnostics["windows"]) if "windows" in diagnostics else None),
                             ("已分析胞体区域", len((diagnostics.get("body_route") or {}).get("regions", [])) if "body_route" in diagnostics else None)):
            details._summary_row(SECTION_TITLES[2], label, value)
        result_dir = Path(job.get("results_dir") or run_dir / "results")
        if not result_dir.is_absolute():
            result_dir = run_dir / result_dir
        if result_dir.resolve().is_relative_to(run_dir.resolve()):
            candidates = [result_dir / "Cell_Count_Session.json"]
            # Current jobs point to a revision, older jobs may point to its parent.
            pointer_path = result_dir / "latest.json"
            try:
                pointer = _json_file(pointer_path, cancel) or {}
                selected = pointer.get("session_path")
                if selected and (result_dir / selected).resolve().is_relative_to(run_dir.resolve()):
                    candidates.insert(0, result_dir / selected)
                for path in candidates:
                    confirmed = _json_file(path, cancel)
                    if confirmed and confirmed.get("run_id") == details.run_id:
                        for key, label in (("count", "已确认数量"), ("pending_count", "确认时待审核数量"),
                            ("excluded_count", "确认时排除数量"), ("deleted_count", "人工删除数量"),
                            ("revision", "确认版本"), ("confirmed_at", "确认时间")):
                            details._summary_row(SECTION_TITLES[3], label, confirmed.get(key))
                        break
            except (OSError, ValueError):
                details.truncated = True


def load_run_details(run_dir, job, cancel=None):
    cancel = cancel or threading.Event()
    run_dir = Path(run_dir)
    details = RunDetails(job)
    for name in ("import-request.json", "request.json"):
        try:
            details.request(_json_file(run_dir / name, cancel))
        except (OSError, ValueError):
            details.truncated = True
    for name in ("console.log", "events.jsonl"):
        try:
            parts, truncated = _text_parts(run_dir / name, cancel)
            details.truncated |= truncated
            for text in parts:
                if cancel.is_set():
                    raise InterruptedError
                if name == "console.log":
                    details.console(text, final=True)
                else:
                    for line in text.splitlines():
                        if cancel.is_set():
                            raise InterruptedError
                        if len(line) > MAX_LINE:
                            details.truncated = True
                            continue
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        details.event(event)
        except OSError:
            details.truncated = True
    analysis = run_dir / "counting" / "analysis.json"
    if job.get("analysis_path"):
        candidate = Path(job["analysis_path"])
        if candidate.is_absolute() and candidate.resolve().is_relative_to(run_dir.resolve()):
            analysis = candidate
    try:
        details.analysis(_json_file(analysis, cancel))
    except (OSError, ValueError):
        details.truncated = True
    _history_sources(details, run_dir, job, cancel)
    details.job(job)
    return details.snapshot(history=True)


class _DetailsSignals(QObject):
    done = Signal(object)


class DetailsTask(QRunnable):
    def __init__(self, generation, run_dir, job):
        super().__init__()
        self.generation, self.run_dir, self.job = generation, Path(run_dir), dict(job)
        self.cancel = threading.Event()
        self.signals = _DetailsSignals()

    def run(self):
        packet = {"generation": self.generation, "jobId": self.job["id"]}
        try:
            packet["details"] = load_run_details(self.run_dir, self.job, self.cancel)
        except InterruptedError:
            packet["cancelled"] = True
        except (OSError, ValueError, TypeError) as exc:
            packet["details"] = empty_details(self.job["id"], self.job.get("module_id", ""), history=True)
            packet["details"]["errors"] = [str(exc)]
        self.signals.done.emit(packet)
