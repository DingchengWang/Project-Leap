"""Qt task controller; computation and TIFF I/O run outside the GUI process."""
from __future__ import annotations

import ctypes
import calendar
import codecs
import json
import math
import os
import signal
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from PySide6.QtCore import QObject, Property, QProcess, QProcessEnvironment, QThread, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .version import application_version, application_build
from .i18n import LocaleManager
from .paths import write_json
from . import maintenance
from .run_details import DetailsTask, ProgressReader, RunDetails, empty_details, progress_description


MEASUREMENT_MARKERS = ("KCNN1", "KCNN2", "KCNN3", "KCNJ10")
MARKER_KEYS = ("DAPI", "eGFP", "GFAP", "Target")
SUPPORT_MARKERS = ("eGFP", "Biocytin", "ALDH1L1")


@dataclass
class WorkbenchSession:
    """Inputs, task state and selection belonging to one scientific module."""
    module_id: str
    status: str = "idle"
    stage: str = "准备分析"
    message: str = "选择或拖入多通道 TIFF 开始。"
    progress: float = -1.0
    image_info: dict = field(default_factory=dict)
    preview_file: str = ""
    channel_roles: list = field(default_factory=list)
    age_profile: str = "auto"
    pending_measurement_marker: str = ""
    series_index: int | None = None
    time_index: int | None = None
    selected_job_id: str = ""
    result_info: dict = field(default_factory=dict)
    log_text: str = ""
    run_dir: Path | None = None
    job: dict | None = None
    run_settings: dict = field(default_factory=dict)
    event_offset: int = 0
    console_offset: int = 0
    fiji_pid: int | None = None
    owned_processes: dict = field(default_factory=dict)
    recovered_task: bool = False
    run_details: RunDetails | None = None
    log_truncated: bool = False
    console_decoder: object = field(default_factory=lambda: codecs.getincrementaldecoder("utf-8")("replace"))
    progress_reader: ProgressReader = field(default_factory=ProgressReader)
    details_finalized: bool = False


@dataclass(frozen=True)
class WorkerContext:
    """Identity fixed when the single subprocess is launched."""
    module_id: str
    run_id: str | None


class SessionField:
    """Keep controller helpers bound to their explicit UI or worker session."""
    def __init__(self, name):
        self.name = name

    def __get__(self, instance, owner=None):
        return self if instance is None else getattr(instance._session, self.name)

    def __set__(self, instance, value):
        setattr(instance._session, self.name, value)


def worker_session(function):
    @wraps(function)
    def bound(self, *args, **kwargs):
        context = self._worker_context
        with self._in_session(context.module_id if context else self._session.module_id):
            if context and context.run_id and (self._job or {}).get("id") != context.run_id:
                raise RuntimeError("后台任务与工作区会话不一致。")
            try:
                return function(self, *args, **kwargs)
            finally:
                if not self._busy and not self._process_kind and not self._cleanup_pending:
                    self._worker_context = None
    return bound


STAGES = {
    "preflight": "检查任务与组件", "loading_engine": "加载分析引擎",
    "input": "读取图像", "input_validation": "检查输入",
    "model": "加载模型", "segmentation": "计算 ROI", "candidates": "计算与比较候选",
    "candidate_generation": "生成候选 ROI", "candidate_selection": "选择候选 ROI",
    "compartments": "划分 Soma 与 Processes", "fiji": "启动 Fiji",
    "fiji_startup": "启动 Fiji", "review": "等待 Fiji 审核",
    "measurement": "测量荧光强度", "publishing": "保存结果与最终 ROI",
    "cleanup": "结束本次任务", "complete": "分析完成",
    "loading_stacks": "载入通道图像", "cellpose_inference": "计算细胞分割",
    "candidate_morphology": "计算与比较 ROI 候选", "measurement_projection": "准备测量图像",
    "preparing_fiji": "准备 Fiji 审核", "timing_summary": "整理运行记录",
    "measurement_complete": "测量完成", "gfap_analysis": "计算 GFAP ROI",
    "z_selection": "选择分析 Z 范围", "dapi_nucleus_model": "计算细胞核候选",
    "gfap_compartments": "构建 GFAP 分区", "analysis": "分析处理中",
    "count_projection": "准备显示投影", "count_model": "加载细胞计数模型",
    "count_inference": "识别 DAPI 细胞核", "count_prepare_review": "准备计数审核",
}


class MaintenanceTask(QThread):
    result = Signal(object)
    problem = Signal(str)
    progress = Signal(float, str)

    def __init__(self, function, *args, **kwargs):
        super().__init__()
        self.function, self.args = function, args
        self.kwargs = kwargs

    def run(self):
        try:
            self.result.emit(self.function(*self.args, progress=self.progress.emit, **self.kwargs))
        except Exception as exc:
            self.problem.emit(str(exc))


class Controller(QObject):
    changed = Signal()
    jobsChanged = Signal()
    deleteConfirmationRequested = Signal(int)
    appearanceModeChanged = Signal(str)
    logChanged = Signal()
    logAppended = Signal(str)
    logTrimmed = Signal(int)
    logReset = Signal(str)
    restartRequested = Signal(str)
    runDetailsChanged = Signal()
    jobRunDetailsChanged = Signal()

    _status = SessionField("status")
    _stage = SessionField("stage")
    _message = SessionField("message")
    _progress = SessionField("progress")
    _image_info = SessionField("image_info")
    _channel_roles = SessionField("channel_roles")
    _age_profile = SessionField("age_profile")
    _pending_measurement_marker = SessionField("pending_measurement_marker")
    _series_index = SessionField("series_index")
    _time_index = SessionField("time_index")
    _selected_job_id = SessionField("selected_job_id")
    _result_info = SessionField("result_info")
    _log_text = SessionField("log_text")
    _run_dir = SessionField("run_dir")
    _job = SessionField("job")
    _event_offset = SessionField("event_offset")
    _console_offset = SessionField("console_offset")
    _fiji_pid = SessionField("fiji_pid")
    _owned_processes = SessionField("owned_processes")
    _recovered_task = SessionField("recovered_task")

    def __init__(self, paths, *, locale=None):
        super().__init__()
        self._active_module_id = "project-leap-2d"
        self._sessions = {}
        self._callback_module_id = None
        self._worker_context = None
        self.paths = paths
        self.locale = locale or LocaleManager(paths.home)
        self.locale.changed.connect(lambda _language: self.changed.emit())
        self._app_version = application_version()
        self._app_build = application_build()
        self._update_lock = None
        self._detached_pid = None
        self._recovered_task = False
        self._page = "analysis"
        self._busy = False
        self._status = "idle"
        self._stage = "准备分析"
        self._message = "选择或拖入多通道 TIFF 开始。"
        self._progress = -1.0
        self._image_info = {}
        self._channel_roles = []
        self._age_profile = "auto"
        self._pending_measurement_marker = ""
        self._jobs = []
        self._storage_usage = {}
        self._storage_refresh_pending = False
        self._pending_delete_ids = []
        self._pending_cleanup_ids = set()
        self._closing = False
        self._component_info = {"status": "checking", "message": "正在读取组件状态。"}
        self._module_states = {}
        self._module_success_messages = {}
        self._maintenance_module_id = None
        self._maintenance_operation = None
        self._selected_job_id = ""
        self._module_faults = self._read_module_faults()
        self._result_info = {}
        self._log_text = ""
        self._maintenance_busy = False
        self._maintenance_progress = -1.0
        self._maintenance_message = ""
        self._maintenance_message_kind = "info"
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(5000)
        self._notice_timer.timeout.connect(self._clear_success_notice)
        self._process = None
        self._process_kind = None
        self._process_buffer = ""
        self._inspect_buffer = ""
        self._pending_image = None
        self._pending_image_module_id = None
        self._run_dir = None
        self._job = None
        self._event_offset = 0
        self._console_offset = 0
        self._fiji_pid = None
        self._owned_processes = {}
        self._cleanup_pending = None
        self._use_lock = None
        self._maintenance_thread = None
        self._maintenance_result = None
        self._series_index = None
        self._time_index = None
        self._updater = None
        self._updates_phase = ""
        self._update_status = ""
        self._update_components = None
        self._update_candidate = None
        self._update_plan = None
        self._update_check_thread = None
        self._update_check_result = None
        from .counting import CountingEditor
        self._counting = CountingEditor(parent=self, locale=self.locale)
        self._counting.confirmed.connect(self._counting_confirmed)
        self._counting.changed.connect(self.changed.emit)
        self._counting.drainedChanged.connect(self._try_task_cleanup)
        self._run_details_visible = False
        self._run_details_state_key = None
        self._monitor_identity = None
        from .resource_monitor import ResourceMonitor
        self._resource_monitor = ResourceMonitor(self)
        self._resource_monitor.snapshotReady.connect(self._resource_snapshot)
        self._history_generation = 0
        self._history_task = None
        self._history_pending = None
        self._history_restore = None
        self._history_pool = QThreadPool(self)
        self._history_pool.setMaxThreadCount(1)
        self._job_run_details = empty_details(history=True)
        self.changed.connect(self._details_state_changed)
        self.locale.changed.connect(lambda _language: self.jobRunDetailsChanged.emit())
        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._load_jobs()
        QTimer.singleShot(0, self._initial_status)

    page = Property(str, lambda self: self._page, notify=changed)
    busy = Property(bool, lambda self: self._busy, notify=changed)
    scientificTaskBusy = Property(bool, lambda self: self._busy, notify=changed)
    runningTask = Property("QVariantMap", lambda self: self._running_task(), notify=changed)
    activeModuleBusy = Property(bool, lambda self: self._active_module_busy(), notify=changed)
    parametersLocked = Property(bool, lambda self: self._parameters_locked(), notify=changed)
    status = Property(str, lambda self: self._active_session.status, notify=changed)
    stage = Property(str, lambda self: self.locale.translate(self._active_session.stage), notify=changed)
    message = Property(str, lambda self: self.locale.translate(self._active_session.message), notify=changed)
    progress = Property(float, lambda self: self._active_session.progress, notify=changed)
    imageInfo = Property("QVariantMap", lambda self: self._active_session.image_info, notify=changed)
    channelRoles = Property("QStringList", lambda self: self._active_session.channel_roles, notify=changed)
    ageProfile = Property(str, lambda self: self._active_session.age_profile, notify=changed)
    counting = Property(QObject, lambda self: self._counting, constant=True)
    countDapiChannel = Property(int, lambda self: self.channelRoles.index("DAPI")
                               if "DAPI" in self.channelRoles else -1, notify=changed)
    countSupportChannel = Property(int, lambda self: self.imageInfo.get("support_channel", -1), notify=changed)
    countSupportMarker = Property(str, lambda self: self.imageInfo.get("support_marker", ""), notify=changed)
    countDisplayChannels = Property("QVariantList", lambda self: list(self.imageInfo.get("display_channels", [])), notify=changed)
    countMappingMode = Property(str, lambda self: self.imageInfo.get("mapping_mode", "auto"), notify=changed)
    countRunSettings = Property("QVariantMap", lambda self: dict(self._active_session.run_settings), notify=changed)
    countCalibrationReady = Property(bool, lambda self: bool(self._active_session.image_info.get("path"))
                                     and self._active_session.series_index is not None
                                     and not self._count_input_error(), notify=changed)
    countInputError = Property(str, lambda self: self._count_input_error(), notify=changed)
    markerSourceIndices = Property("QVariantList", lambda self: self._marker_source_indices(self._active_session), notify=changed)
    measurementMarker = Property(str, lambda self: self._measurement_marker(self._active_session), notify=changed)
    jobs = Property("QVariantList", lambda self: [self._localized_item(item) for item in self._jobs], notify=changed)
    jobGroups = Property("QVariantList", lambda self: self._job_groups(), notify=changed)
    selectedJobId = Property(str, lambda self: self._active_session.selected_job_id, notify=changed)
    installedModules = Property("QVariantList", lambda self: self._module_items(True), notify=changed)
    availableModules = Property("QVariantList", lambda self: self._module_items(False), notify=changed)
    activeModuleId = Property(str, lambda self: self._active_module_id, notify=changed)
    activeModuleName = Property(str, lambda self: self._module_name(self._active_module_id), notify=changed)
    activeModuleInputPage = Property(str, lambda self: self._module_definition(self._active_module_id).get("input_page", ""), notify=changed)
    fijiAvailable = Property(bool, lambda self: bool(self._active_session.fiji_pid), notify=changed)
    componentInfo = Property("QVariantMap", lambda self: self._localized_item(self._component_info), notify=changed)
    resultInfo = Property("QVariantMap", lambda self: self._localized_item(self._active_session.result_info), notify=changed)
    logText = Property(str, lambda self: self._active_session.log_text, notify=logChanged)
    activeRunDetails = Property("QVariantMap", lambda self: self._active_run_details(), notify=runDetailsChanged)
    jobRunDetails = Property("QVariantMap", lambda self: self._localized_details(self._job_run_details), notify=jobRunDetailsChanged)
    appVersion = Property(str, lambda self: self._app_version, constant=True)
    dataLocation = Property(str, lambda self: str(self.paths.home), constant=True)
    maintenanceBusy = Property(bool, lambda self: self._maintenance_busy, notify=changed)
    maintenanceProgress = Property(float, lambda self: self._maintenance_progress, notify=changed)
    maintenanceMessage = Property(str, lambda self: self.locale.translate(self._maintenance_message), notify=changed)
    maintenanceMessageKind = Property(str, lambda self: self._maintenance_message_kind, notify=changed)
    updatesBusy = Property(bool, lambda self: bool(self._updates_phase), notify=changed)
    updateStatusMessage = Property(str, lambda self: self.locale.translate(self._update_status), notify=changed)
    appearanceMode = Property(str, lambda self: self.locale.preference("appearance", "system"), notify=changed)
    storageUsageBusy = Property(bool, lambda self: self._maintenance_busy and self._maintenance_operation == "storage", notify=changed)
    canDeleteJobs = Property(bool, lambda self: not (self._busy or self._maintenance_busy or self._counting.busy), notify=changed)

    @Slot(str)
    def setAppearanceMode(self, mode):
        if mode not in ("system", "light", "dark") or not self.languageEditable:
            return
        try:
            self.locale.set_preference("appearance", mode)
        except (OSError, ValueError) as exc:
            self._notice(str(exc), "error")
            return
        self.appearanceModeChanged.emit(mode)
        self.changed.emit()

    @Slot(str)
    def relocateCountingSource(self, value):
        if self._maintenance_busy:
            return
        url = QUrl(value)
        self._counting.relocateSource(url.toLocalFile() if url.isLocalFile() else value)

    language = Property(str, lambda self: self.locale.language, notify=changed)
    languageEditable = Property(bool, lambda self: not (
        self._maintenance_busy and self._maintenance_operation == "migration"), notify=changed)

    def _session_for(self, module_id):
        if module_id not in self._sessions:
            self._sessions[module_id] = WorkbenchSession(module_id)
        return self._sessions[module_id]

    @property
    def _active_session(self):
        return self._session_for(self._active_module_id)

    @property
    def _session(self):
        return self._session_for(self._callback_module_id or self._active_module_id)

    @contextmanager
    def _in_session(self, module_id):
        previous = self._callback_module_id
        self._callback_module_id = module_id
        try:
            yield self._session
        finally:
            self._callback_module_id = previous

    def _active_module_busy(self):
        return bool(self._busy and (not self._worker_context
                    or self._worker_context.module_id == self._active_module_id))

    def _ensure_run_details(self, *, live=False):
        session = self._session
        if not session.job:
            return session.run_details
        if (session.run_details is None or session.run_details.job_id != session.job["id"]
                or session.run_details.module_id != session.module_id):
            session.run_details = RunDetails(session.job, live=live)
            session.progress_reader = ProgressReader()
            session.details_finalized = False
        return session.run_details

    def _localized_details(self, details):
        value = dict(details)
        value["stage"] = self.locale.translate(value.get("stage", ""))
        value["rows"] = [{**row, "label": self.locale.translate(row["label"]),
                          "value": (self.locale.translate(row["value"])
                                    if row["label"] in ("计时解释", "准备阶段计时") else row["value"])}
                         for row in value.get("rows", [])]
        value["timings"] = [{**item, "label": self.locale.translate(item["label"])}
                            for item in value.get("timings", [])]
        value["summarySections"] = [{"title": self.locale.translate(section["title"]),
            "rows": [{"label": self._detail_label(row["label"]), "value": self.locale.translate(row["value"])}
                     for row in section["rows"]]} for section in value.get("summarySections", [])]
        return value

    def _active_run_details(self):
        session = self._active_session
        if session.run_details is None:
            return empty_details(module_id=session.module_id)
        details = session.run_details.snapshot(history=not session.run_details.live, log_text=session.log_text,
                                               log_truncated=session.log_truncated)
        return self._localized_details(details)

    @Slot()
    def _details_state_changed(self):
        for session in self._sessions.values():
            if session.run_details and session.job and session.run_details.job_id == session.job["id"]:
                session.run_details.status, session.run_details.stage = session.status, session.stage
        monitor_changed = self._sync_resource_monitor()
        session = self._active_session
        key = (self._active_module_id, id(session.run_details), session.status, session.stage,
               self.locale.language)
        if key != self._run_details_state_key or monitor_changed:
            self._run_details_state_key = key
            self.runDetailsChanged.emit()

    @Slot(bool)
    def setRunDetailsVisible(self, visible):
        self._run_details_visible = bool(visible)
        if self._sync_resource_monitor():
            self.runDetailsChanged.emit()

    def _sync_resource_monitor(self):
        context = self._worker_context
        session = self._session_for(context.module_id) if context else None
        active = bool(not self._closing and self._run_details_visible and self._page == "analysis"
                      and self._busy and context and context.run_id
                      and context.module_id == self._active_module_id and session
                      and session.status in ("running", "importing")
                      and session.job and session.job["id"] == context.run_id)
        identity = (context.module_id, context.run_id) if active else None
        if identity != self._monitor_identity:
            if self._monitor_identity:
                previous = self._session_for(self._monitor_identity[0]).run_details
                if previous and previous.run_id == self._monitor_identity[1]:
                    if previous.current_resources:
                        previous.current_resources = {**previous.current_resources, "mode": "stopped"}
            self._resource_monitor.setActive(False)
            self._monitor_identity = identity
            if active:
                self._resource_monitor.setActive(True)
            return True
        return False

    @Slot(dict)
    def _resource_snapshot(self, snapshot):
        identity = self._monitor_identity
        context = self._worker_context
        if (not identity or not context or identity != (context.module_id, context.run_id)
                or self._closing):
            return
        session = self._session_for(identity[0])
        details = session.run_details
        if not details or details.run_id != identity[1]:
            return
        details.current_resources = {**snapshot, "mode": "current"}
        if session.module_id == self._active_module_id:
            self.runDetailsChanged.emit()

    @Slot(str)
    def loadJobRunDetails(self, identifier):
        if self._closing:
            return
        job = next((item for item in self._jobs if item["id"] == identifier), None)
        self._history_generation += 1
        if self._history_task:
            self._history_task.cancel.set()
        self._history_pending = None
        if not job:
            self._job_run_details = empty_details(identifier, history=True)
        else:
            self._job_run_details = empty_details(identifier, job.get("module_id", "project-leap-2d"),
                                                 history=True, loading=True)
            self._history_pending = (self._history_generation, dict(job))
            self._start_history_details()
        self.jobRunDetailsChanged.emit()

    def _start_history_details(self):
        if self._history_task or not self._history_pending or self._closing:
            return
        generation, job = self._history_pending
        self._history_pending = None
        self._history_task = DetailsTask(generation, self.paths.runs / job["id"], job)
        self._history_task.signals.done.connect(self._history_details_ready)
        self._history_pool.start(self._history_task)

    @Slot(object)
    def _history_details_ready(self, packet):
        self._history_task = None
        if (not self._closing and packet["generation"] == self._history_generation
                and packet.get("details") is not None):
            self._job_run_details = packet["details"]
            if self._history_restore == packet["generation"]:
                session = self._session_for(packet["details"]["moduleId"])
                if session.job and session.job["id"] == packet["jobId"]:
                    with self._in_session(session.module_id):
                        # A reopened review gets a bounded historical view. A
                        # later live run or module switch cannot receive it.
                        session.run_details = RunDetails({**session.job, "run_details": packet["details"]})
                        self._reset_log(packet["details"].get("logText", ""))
                        session.log_truncated = packet["details"].get("logTruncated", False)
                        self.runDetailsChanged.emit()
                self._history_restore = None
            self.jobRunDetailsChanged.emit()
        self._start_history_details()

    def _running_task(self):
        if not self._busy or not self._worker_context:
            return {}
        context = self._worker_context
        session = self._session_for(context.module_id)
        return {"moduleId": context.module_id, "moduleName": self._module_name(context.module_id),
                "runId": context.run_id or "", "status": session.status,
                "stage": self.locale.translate(session.stage), "message": self.locale.translate(session.message),
                "progress": session.progress, "fijiAvailable": bool(session.fiji_pid)}

    def _parameters_locked(self):
        return bool(self._maintenance_busy or self._active_module_busy()
                    or self._active_session.status == "waiting_review"
                    or (self._active_module_id == "cell-counting"
                        and (self._counting.busy or self._counting.dirty)))

    def _count_input_error(self):
        session = self._active_session
        if not session.image_info.get("path") or session.series_index is None:
            return ""
        z_count = session.image_info.get("z_count")
        if type(z_count) is not int or z_count < 1:
            return self.locale.translate("图像层数缺失，无法确认计数维度。")
        sizes = {key.lower(): value for key, value in (session.image_info.get("physical_size_um") or {}).items()}
        axes = "xy" if z_count == 1 else "xyz"
        missing = [axis.upper() for axis in axes if isinstance(sizes.get(axis), bool)
                   or not isinstance(sizes.get(axis), (int, float))
                   or not math.isfinite(sizes[axis]) or sizes[axis] <= 0]
        if missing:
            return self.locale.translate("缺少有效的微米标定：") + ", ".join(missing)
        if z_count > 1 and not math.isclose(sizes["x"], sizes["y"], rel_tol=1e-3):
            return self.locale.translate("当前三维计数需要方形 XY 像素；原始 X/Y 标定不一致。")
        return ""

    def _localized_item(self, item):
        value = dict(item)
        if "id" in value and value["id"] in self._storage_usage:
            size = self._storage_usage[value["id"]]
            unit = "B"
            amount = float(size)
            for candidate in ("KiB", "MiB", "GiB", "TiB"):
                if amount < 1024:
                    break
                amount /= 1024
                unit = candidate
            value["storageLabel"] = f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        for key in ("stage", "message", "durability_warning"):
            if key in value and isinstance(value[key], str):
                value[key] = self.locale.translate(value[key])
        return value

    def _read_module_faults(self):
        path = self.paths.home / "module-health.json"
        try:
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _mark_module_fault(self, message, module_id=None):
        identifier = module_id or (self._job or {}).get("module_id") or self._session.module_id
        self._module_faults[identifier] = message
        write_json(self.paths.home / "module-health.json", self._module_faults)

    def _clear_module_fault(self, identifier):
        if identifier in self._module_faults:
            del self._module_faults[identifier]
            write_json(self.paths.home / "module-health.json", self._module_faults)

    @staticmethod
    def _module_definition(identifier):
        from . import modules
        return modules.get_module(identifier)

    def _module_name(self, identifier):
        try:
            definition = self._module_definition(identifier)
        except (KeyError, ValueError):
            return identifier
        return self.locale.translate(definition.get("name", identifier))

    def _refresh_modules(self):
        statuses = maintenance.list_statuses(self.paths.home)
        self._module_states = {item["id"]: item for item in statuses}
        self._module_success_messages.clear()
        if self._active_module_id in self._module_states:
            self._component_info = self._module_states[self._active_module_id]

    def _module_items(self, installed):
        from .modules import list_modules
        items = []
        for definition in list_modules():
            identifier = definition["id"]
            state = self._module_states.get(identifier)
            if state is None:
                continue
            if bool(state.get("installed")) != installed:
                continue
            item = self._localized_item(state)
            item.update(id=identifier, name=self._module_name(identifier),
                        version=state.get("component_version") or definition.get("version", ""),
                        githubUrl=definition.get("github_url", ""),
                        inputPage=definition.get("input_page", ""),
                        hasError=bool(self._module_faults.get(identifier)) or state.get("status") in ("damaged", "failed"))
            item["faultMessage"] = self.locale.translate(self._module_faults.get(identifier, ""))
            items.append(item)
        return items

    def _job_groups(self):
        groups = {item["id"]: {"moduleId": item["id"], "moduleName": item["name"],
                  "installed": True, "jobs": []} for item in self._module_items(True)}
        for job in self._jobs:
            identifier = job["module_id"]
            group = groups.setdefault(identifier, {"moduleId": identifier,
                     "moduleName": self._module_name(identifier), "installed": False, "jobs": []})
            group["jobs"].append(self._localized_item(job))
        return list(groups.values())

    @Slot(str)
    def selectModule(self, identifier):
        if identifier not in self._module_states or not self._module_states[identifier].get("installed"):
            return
        self._activate_module(identifier)
        self._page = "analysis"
        self.changed.emit()

    def _activate_module(self, identifier):
        self._active_module_id = identifier
        self._component_info = self._module_states.get(identifier, {})
        self.logReset.emit(self._active_session.log_text)
        self.logChanged.emit()
        self._details_state_changed()

    @Slot(str)
    def openModuleGitHub(self, identifier):
        try:
            url = self._module_definition(identifier).get("github_url", "")
        except (KeyError, ValueError):
            return
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @Slot()
    def openLicenses(self):
        self._open_about_document("licenses.html")

    @Slot()
    def openReferences(self):
        self._open_about_document("references.html")

    def _open_about_document(self, name):
        path = self.paths.resources / "app/leap_desktop/docs" / name
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot()
    def clearImportedImage(self):
        """Discard only the current module's unstarted preparation."""
        if self._parameters_locked() or self._status not in ("idle", "ready", "failed", "cancelled") or self._job:
            return
        self._remove_input_preview(self._session)
        self._image_info, self._channel_roles = {}, []
        self._series_index = self._time_index = None
        self._age_profile, self._pending_measurement_marker = "auto", ""
        self._status, self._stage = "idle", "准备分析"
        self._message, self._progress = "选择或拖入多通道 TIFF 开始。", -1.0
        self._session.run_settings = {}
        if self._pending_image_module_id == self._active_module_id:
            self._pending_image = self._pending_image_module_id = None
        self.changed.emit()

    @Slot(str)
    def setLanguage(self, language):
        if not self.languageEditable:
            return
        try:
            self.locale.set_language(language)
        except (OSError, ValueError) as exc:
            self._message = str(exc)
            self.changed.emit()

    @Slot()
    def _initial_status(self):
        self._refresh_modules()
        try:
            from .updater import SparkleUpdater
            self._updater = SparkleUpdater()
            self._updater.start()
        except Exception as exc:
            self._append_log("更新组件：" + str(exc))
        self._recover_live_job()
        self._resume_pending_update()
        self.changed.emit()

    def _append_log(self, text, *, observe=True):
        if not text:
            return
        appended = text[-120000:]
        trim = max(0, len(self._log_text) + len(appended) - 120000)
        removed = self._log_text[:trim]
        self._log_text = self._log_text[trim:] + appended
        self._session.log_truncated |= bool(trim or len(text) > 120000)
        details = self._ensure_run_details()
        if details and observe:
            details.console(text)
        if self._session.module_id != self._active_module_id:
            return
        if removed:
            # QML TextEdit positions count UTF-16 units, including surrogate pairs.
            self.logTrimmed.emit(len(removed.encode("utf-16-le")) // 2)
        self.logAppended.emit(appended)
        self.logChanged.emit()
        self.runDetailsChanged.emit()

    def _detail_line(self, stage, message, *, timestamp=None):
        stamp = str(timestamp or datetime.now(timezone.utc).isoformat())
        return "[" + stamp + "] " + self.locale.translate(stage) + " | " + message + "\n"

    def _detail_label(self, label):
        for prefix, template in (("参数 · ", "参数 · %1"), ("分区参数 · ", "分区参数 · %1")):
            if label.startswith(prefix):
                return self.locale.translate(template).replace("%1", label[len(prefix):])
        return self.locale.translate(label)

    def _append_detail_updates(self, *, timestamp=None):
        details = self._ensure_run_details()
        if details:
            text = "".join(self._detail_line("运行配置", self._detail_label(label) + ": " + value,
                                           timestamp=timestamp) for label, value in details.take_live_updates())
            self._append_log(text, observe=False)

    def _read_progress_details(self):
        if not self._run_dir or self._session.module_id != "cell-counting":
            return
        lines = []
        for name, record in self._session.progress_reader.read(self._run_dir / "counting"):
            stage, rows = progress_description(name, record)
            fields = []
            for label, value in rows:
                if value is None:
                    continue
                if "耗时" in label and isinstance(value, (int, float)):
                    value = f"{value:.3f} s"
                fields.append(self.locale.translate(label) + "=" + str(value))
            lines.append(self._detail_line(stage, "; ".join(fields)))
        self._append_log("".join(lines), observe=False)

    def _finish_detail_observation(self, module_id, run_id):
        """Drain a finished run in bounded turns; never drive its task state."""
        if self._closing:
            return
        session = self._session_for(module_id)
        if (not session.job or session.job["id"] != run_id or session.details_finalized
                or not session.run_details or not session.run_details.live):
            return
        with self._in_session(module_id):
            console_error = False
            try:
                self._read_console(observe_only=True)
            except OSError:
                console_error = True
                if session.run_details:
                    session.run_details.truncated = True
            self._read_progress_details()
            try:
                console_pending = not console_error and (session.run_dir / "console.log").stat().st_size > session.console_offset
            except OSError:
                console_pending = False
            if console_pending or session.progress_reader.pending(session.run_dir / "counting"):
                QTimer.singleShot(10, lambda: self._finish_detail_observation(module_id, run_id))
                return
            details = session.run_details
            if details:
                details.console(session.console_decoder.decode(b"", final=True), final=True)
                self._append_detail_updates()
                self._persist_detail_summary(session)
                if module_id == self._active_module_id:
                    self.runDetailsChanged.emit()

    def _persist_detail_summary(self, session):
        details = session.run_details
        if not session.job or not details or not details.live or session.details_finalized:
            return
        details.job(session.job)
        session.job["run_details"] = details.persisted()
        try:
            write_json(session.run_dir / "job.json", session.job)
            session.details_finalized = True
            for index, job in enumerate(self._jobs):
                if job["id"] == session.job["id"] and job.get("module_id") == session.module_id:
                    self._jobs[index] = dict(session.job)
                    break
        except OSError as exc:
            with self._in_session(session.module_id):
                self._append_log(self._detail_line("运行记录", self.locale.translate("运行详情摘要未能保存：") + str(exc)), observe=False)

    def _reset_log(self, text=""):
        self._log_text = text[-120000:]
        self._session.log_truncated = len(text) > 120000
        if self._session.module_id != self._active_module_id:
            return
        self.logReset.emit(self._log_text)
        self.logChanged.emit()

    def _load_jobs(self):
        jobs = []
        for path in self.paths.runs.glob("*/job.json"):
            try:
                item = json.loads(path.read_text())
                # All jobs before the module catalog were produced by this sole engine.
                item.setdefault("module_id", "project-leap-2d")
                item.setdefault("module_version", (item.get("outcome", {}).get("provenance") or {}).get("component_version", ""))
                count_review = item.get("module_id") == "cell-counting" and item.get("status") == "waiting_review"
                if item.get("status") in ("running", "importing", "waiting_review", "cancelling") and not count_review:
                    if not any(session.job and item["id"] == session.job["id"]
                               for session in self._sessions.values()):
                        item = {**item, "status": "interrupted", "message": "上次任务未完整结束。原输入与已保存结果仍保留。"}
                jobs.append(item)
            except (OSError, ValueError):
                continue
        self._jobs = sorted(jobs, key=lambda item: item.get("created_at", ""), reverse=True)
        self.jobsChanged.emit()

    def _save_job(self):
        if self._job and self._run_dir:
            self._job.update(status=self._status, stage=self._stage, message=self._message)
            self._job["owned_processes"] = list(self._owned_processes.values())
            details = self._ensure_run_details(live=bool(self._busy and self._worker_context
                                                       and self._worker_context.run_id == self._job["id"]))
            details.status, details.stage = self._status, self._stage
            write_json(self._run_dir / "job.json", self._job)
            self._load_jobs()

    @Slot(str)
    def setPage(self, page):
        if page in ("analysis", "jobs", "maintenance", "settings"):
            if page == "maintenance" and self._page != page and not self._maintenance_busy:
                for identifier, (message, previous_message) in self._module_success_messages.items():
                    state = self._module_states.get(identifier, {})
                    if state.get("status") == "ready" and state.get("message") == message:
                        self._module_states[identifier] = {**state, "message": previous_message}
                        if identifier == self._active_module_id:
                            self._component_info = self._module_states[identifier]
                self._module_success_messages.clear()
            self._page = page
            self.changed.emit()

    @Slot()
    def chooseImage(self):
        if self._busy or self._parameters_locked():
            return
        if not self._supports_image_input():
            return
        path, _ = QFileDialog.getOpenFileName(None, self.locale.translate("选择多通道 TIFF"), "", self.locale.translate("TIFF 图像 (*.tif *.tiff)"))
        if path:
            self.inspectImage(path)

    def _runtime(self):
        state = (self._component_info if self._session.module_id == self._active_module_id
                 else self._module_states.get(self._session.module_id, {}))
        descriptor = state.get("runtime_descriptor")
        if state.get("status") != "ready" or not descriptor:
            raise RuntimeError("请先在功能与维护中安装或修复当前模块。")
        return descriptor

    def _environment(self, descriptor, module_id=None):
        module_id = module_id or self._session.module_id
        env = QProcessEnvironment.systemEnvironment()
        for name in ("PYTHONHOME", "PYTHONPATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH",
                     "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH",
                     "PROJECT_LEAP_FIJI_ROOT", "PROJECT_LEAP_MODEL_ROOT", "PROJECT_LEAP_SCIENCE_ROOT",
                     "CELLPOSE_LOCAL_MODELS_PATH"):
            env.remove(name)
        env.insert("PYTHONDONTWRITEBYTECODE", "1")
        env.insert("PYTHONNOUSERSITE", "1")
        env.insert("PYTHONPATH", os.pathsep.join((str(descriptor["engine"]), str(self.paths.app), str(descriptor["site_packages"]))))
        if descriptor.get("schema_version") == 4:
            env.insert("PROJECT_LEAP_SCIENCE_ROOT", str(descriptor["science_root"]))
        if descriptor.get("models"):
            env.insert("CELLPOSE_LOCAL_MODELS_PATH", str(descriptor["models"]))
        if descriptor.get("model_root"):
            env.insert("PROJECT_LEAP_MODEL_ROOT", str(descriptor["model_root"]))
        if descriptor.get("fiji_root"):
            env.insert("PROJECT_LEAP_FIJI_ROOT", str(descriptor["fiji_root"]))
        env.insert("PROJECT_LEAP_COMPONENT_ROOT", str(descriptor["root"]))
        env.insert("PROJECT_LEAP_COMPONENT_LOCK", str(maintenance.component_lock_path(self.paths.home)))
        env.insert("PROJECT_LEAP_HOME", str(self.paths.home))
        env.insert("PROJECT_LEAP_MODULE_ID", module_id)
        if module_id == "cell-counting":
            cache = self.paths.cache / "cell-counting"
            cache.mkdir(parents=True, exist_ok=True)
            env.insert("MPLCONFIGDIR", str(cache / "matplotlib"))
            env.insert("TORCH_HOME", str(cache / "torch"))
            env.insert("OMP_NUM_THREADS", "1")
            env.insert("OPENBLAS_NUM_THREADS", "1")
            env.insert("VECLIB_MAXIMUM_THREADS", "1")
            env.insert("NUMEXPR_NUM_THREADS", "1")
        return env

    def _launch(self, kind, arguments):
        self._worker_context = WorkerContext(self._session.module_id, (self._job or {}).get("id"))
        descriptor = self._runtime()
        process = QProcess(self)
        process.setProcessEnvironment(self._environment(descriptor))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if kind != "inspect":
            from PySide6.QtCore import QIODevice
            log_path = self._run_dir / "console.log"
            self._console_offset = log_path.stat().st_size if log_path.exists() else 0
            self._session.console_decoder = codecs.getincrementaldecoder("utf-8")("replace")
            process.setStandardOutputFile(str(log_path), QIODevice.OpenModeFlag.Append)
        process.setWorkingDirectory(str(self.paths.home))
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._process_finished)
        process.started.connect(self._worker_started)
        process.errorOccurred.connect(self._process_error)
        self._process = process
        self._process_kind = kind
        self._process_buffer = ""
        process.start(str(descriptor["python"]), ["-B", *arguments])

    @Slot()
    @worker_session
    def _worker_started(self):
        if self._job and self._process_kind in ("import", "analysis"):
            self._job["worker"] = {"pid": int(self._process.processId()), "kind": self._process_kind}
            self._save_job()

    @staticmethod
    def _matches_process(pid, identity):
        if not isinstance(pid, int) or pid < 2:
            return False
        try:
            command = subprocess.run(["/bin/ps", "-ww", "-p", str(pid), "-o", "command="],
                                     capture_output=True, text=True, timeout=2)
            return command.returncode == 0 and str(identity) in command.stdout
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _recover_live_job(self):
        for saved in self._jobs:
            run = self.paths.runs / saved["id"]
            record = json.loads((run / "job.json").read_text())
            if record.get("status") not in ("running", "importing", "waiting_review", "cancelling"):
                continue
            if record.get("module_id") == "cell-counting" and record.get("status") == "waiting_review":
                # A review-ready event can precede the terminal outcome and host
                # callback. Only skip fully persisted native review records.
                saved_analysis = record.get("analysis_path")
                if saved_analysis and self.paths.resolve_managed_path(saved_analysis).is_file():
                    continue
            worker_path = run / "worker.json"
            worker = json.loads(worker_path.read_text()) if worker_path.is_file() else record.get("worker", {})
            request = run / ("request.json" if worker.get("kind") == "analysis" else "import-request.json")
            pid = worker.get("pid")
            outcome = run / "outcome.json"
            live = self._matches_process(pid, request)
            owned = {item["pid"]: item for item in record.get("owned_processes", [])
                     if item.get("process_status") != "exited" and self._matches_process(item.get("pid"), run)}
            if not live and not outcome.exists() and not owned:
                continue
            record.setdefault("module_id", "project-leap-2d")
            record.setdefault("module_version", "")
            self._active_module_id = record["module_id"]
            if self._active_module_id in self._module_states:
                self._component_info = self._module_states[self._active_module_id]
            self._job, self._run_dir = record, run
            self._worker_context = WorkerContext(record["module_id"], record["id"])
            self._recovered_task = True
            self._image_info = {"path": record["source"], "name": record["name"]}
            self._status, self._stage = record.get("status", "running"), record.get("stage", "恢复任务状态")
            self._event_offset, self._console_offset = 0, 0
            self._owned_processes = owned
            self._read_events()
            self._owned_processes = {pid: {**item, "recovered": True} for pid, item in self._owned_processes.items()
                                     if item.get("process_status") != "exited" and self._matches_process(pid, run)}
            self._fiji_pid = next((pid for pid, item in self._owned_processes.items()
                                   if item.get("process_type") == "fiji"), None)
            self._read_console()
            if live:
                self._detached_pid, self._process_kind = pid, worker["kind"]
                self._use_lock = maintenance.component_use_lock(self.paths.home)
                self._use_lock.__enter__()
                self._busy = True
                self._status = "waiting_review" if self._fiji_pid else "running"
                self._message = "已重新连接仍在运行的任务。"
            elif outcome.exists():
                self._process_kind = "analysis"
                self._process_finished(0, QProcess.ExitStatus.NormalExit)
            else:
                self._owned_processes = {pid: {**item, "recovered": True} for pid, item in owned.items()}
                self._use_lock = maintenance.component_use_lock(self.paths.home)
                self._use_lock.__enter__()
                self._finish("interrupted", "上次分析进程已退出，已保留输入与恢复记录。")
            break

    @Slot(str)
    def inspectImage(self, value):
        if self._busy or self._parameters_locked() or self._update_in_progress():
            return
        if not self._supports_image_input():
            return
        url = QUrl(value)
        path = url.toLocalFile() if url.isLocalFile() else value
        source = Path(path).expanduser()
        if not source.is_file():
            self._message = "所选文件不存在或不可读取。"
            self.changed.emit()
            return
        if self._active_module_id == "cell-counting":
            if not self._counting.close():
                self._message = self._counting.message
                self.changed.emit()
                return
        self._remove_input_preview(self._session)
        self._job, self._run_dir = None, None
        self._session.run_settings = {}
        self._session.run_details = None
        self._reset_log()
        self._recovered_task = False
        self._event_offset, self._fiji_pid = 0, None
        self._owned_processes = {}
        if self._component_info.get("status") != "ready":
            self._pending_image = str(source)
            self._pending_image_module_id = self._active_module_id
            self._page = "maintenance"
            self._message = "请先安装当前功能模块，随后将自动读取已选择的图像。"
            self.changed.emit()
            return
        self._page = "analysis"
        self._busy = True
        self._status = "inspecting"
        self._stage = "读取图像信息"
        self._message = "正在读取维度、通道与标定。"
        self._image_info = {"path": str(source), "name": source.name}
        self._channel_roles = []
        self._age_profile = "auto"
        self._pending_measurement_marker = ""
        self._result_info = {}
        self._inspect_buffer = ""
        self._series_index = self._time_index = None
        self._worker_context = WorkerContext(self._active_module_id, None)
        try:
            preview_root = self.paths.cache / "modules" / self._active_module_id
            self.paths.check_owned_directory(preview_root)
            preview_root.mkdir(parents=True, exist_ok=True)
            preview = preview_root / (uuid.uuid4().hex + ".png")
            self._session.preview_file = str(preview)
            self._launch("inspect", ["-m", self._module_definition(self._active_module_id)["import_module"], "--inspect", str(source), "--preview-path", str(preview)])
        except Exception as exc:
            self._failed(str(exc))
        self.changed.emit()

    def _apply_series(self, index):
        series = self._image_info.get("series", [])
        if not 0 <= index < len(series):
            return
        item = series[index]
        self._image_info.update({key: value for key, value in item.items() if key != "name"})
        self._image_info["series_name"] = item.get("name", "")
        self._image_info["selected_series"] = index
        self._series_index = index
        sizes = item.get("physical_size_um") or {}
        self._image_info["physical_size_um"] = {key.lower(): value for key, value in sizes.items()}
        axes, shape = item.get("axes", ""), item.get("shape", [])
        dimensions = dict(zip(axes, shape))
        self._image_info.update(width=dimensions.get("X", 0), height=dimensions.get("Y", 0), z_count=dimensions.get("Z", 1))
        count = int(item.get("channel_count") or dimensions.get("C", 1))
        supplied_hints = item.get("channel_hints") or []
        hints = [supplied_hints[i] if i < len(supplied_hints) else "Channel " + str(i + 1)
                 for i in range(count)]
        self._channel_roles = ["Ignore"] * count
        if self._session.module_id == "cell-counting":
            suggested = item.get("channel_roles") or ["Other"] * count
            self._channel_roles = list(suggested) if len(suggested) == count else ["Other"] * count
            dapi_index = item.get("dapi_channel", -1)
            self._channel_roles = ["Other" if role == "DAPI" else role for role in self._channel_roles]
            if isinstance(dapi_index, int) and 0 <= dapi_index < count:
                self._channel_roles[dapi_index] = "DAPI"
            self._image_info["channel_colors"] = list(item.get("channel_colors") or [])
            support_index = item.get("support_channel", -1)
            if (type(support_index) is not int or not 0 <= support_index < count
                    or support_index == dapi_index):
                candidates = [i for i, role in enumerate(self._channel_roles)
                              if i != dapi_index and role in SUPPORT_MARKERS]
                support_index = candidates[0] if len(candidates) == 1 else -1
            marker = item.get("support_marker") or (self._channel_roles[support_index] if support_index >= 0 else "")
            self._image_info.update(support_channel=support_index, support_marker=marker,
                                    display_channels=sorted({i for i in (dapi_index, support_index)
                                                             if type(i) is int and 0 <= i < count}), mapping_mode="auto",
                                    detected_dapi_channel=dapi_index,
                                    detected_support_channel=support_index,
                                    detected_support_marker=marker,
                                    detected_channel_roles=list(self._channel_roles))
        else:
            for marker in MARKER_KEYS:
                roles = MEASUREMENT_MARKERS if marker == "Target" else (marker,)
                sources = [i for i, hint in enumerate(hints) if hint in roles]
                if len(sources) == 1:
                    self._channel_roles[sources[0]] = hints[sources[0]]
        self._pending_measurement_marker = next((role for role in self._channel_roles
                                                 if role in MEASUREMENT_MARKERS), "")
        self._image_info["channel_count"] = count
        self._image_info["channel_hints"] = hints
        self._time_index = 0 if int(item.get("time_count") or 1) == 1 else None
        self._image_info["selected_time"] = self._time_index if self._time_index is not None else -1

    @Slot(int)
    def selectSeries(self, index):
        if not self._parameters_locked():
            self._apply_series(index)
            self.changed.emit()

    @Slot(int)
    def selectTime(self, index):
        if not self._parameters_locked() and 0 <= index < int(self._image_info.get("time_count") or 1):
            self._time_index = index
            self._image_info["selected_time"] = index
            self.changed.emit()

    @Slot(int, str)
    def setChannelRole(self, index, role):
        if (self._parameters_locked() or type(index) is not int
                or not 0 <= index < len(self._channel_roles)
                or role not in ("Ignore", *MARKER_KEYS[:3], *MEASUREMENT_MARKERS)):
            return
        self._pending_measurement_marker = self._measurement_marker()
        self._channel_roles[index] = role
        if role in MEASUREMENT_MARKERS:
            self._pending_measurement_marker = role
        self.changed.emit()

    def _marker_source_indices(self, session=None):
        """Expose four logical rows without changing the physical source array."""
        session = session or self._session
        return [next((index for index, role in enumerate(session.channel_roles)
                      if role in (MEASUREMENT_MARKERS if marker == "Target" else (marker,))), -1)
                for marker in MARKER_KEYS]

    def _measurement_marker(self, session=None):
        session = session or self._session
        return next((role for role in session.channel_roles if role in MEASUREMENT_MARKERS),
                    session.pending_measurement_marker)

    @Slot(str, int)
    def assignMarkerSource(self, marker, source_index):
        if (self._parameters_locked() or marker not in MARKER_KEYS
                or type(source_index) is not int or not -1 <= source_index < len(self._channel_roles)
                or source_index >= int(self._image_info.get("channel_count") or 0)):
            return
        measurement = self._measurement_marker()
        if marker == "Target" and source_index >= 0 and not measurement:
            return
        assigned_roles = MEASUREMENT_MARKERS if marker == "Target" else (marker,)
        roles = ["Ignore" if role in assigned_roles else role for role in self._channel_roles]
        if source_index >= 0:
            roles[source_index] = measurement if marker == "Target" else marker
        if roles == self._channel_roles:
            return
        self._pending_measurement_marker = measurement
        self._channel_roles = roles
        self.changed.emit()

    @Slot(str)
    def setMeasurementMarker(self, marker):
        if self._parameters_locked() or marker not in MEASUREMENT_MARKERS:
            return
        source_index = self._marker_source_indices()[3]
        roles = ["Ignore" if role in MEASUREMENT_MARKERS else role for role in self._channel_roles]
        if source_index >= 0:
            roles[source_index] = marker
        if roles == self._channel_roles and marker == self._pending_measurement_marker:
            return
        self._pending_measurement_marker = marker
        self._channel_roles = roles
        self.changed.emit()

    def _update_in_progress(self):
        return bool(self._updates_phase or self._updater and self._updater.status().get("session_in_progress"))

    def _supports_fluorescence_input(self):
        definition = self._module_definition(self._active_module_id)
        if definition.get("input_page") == "fluorescence":
            return True
        self._message = "请使用当前模块声明的输入页面。"
        self.changed.emit()
        return False

    def _supports_image_input(self):
        if self._module_definition(self._active_module_id).get("input_page") in ("fluorescence", "counting"):
            return True
        self._message = "请使用当前模块声明的输入页面。"
        self.changed.emit()
        return False

    @Slot(int)
    def setCountDapiChannel(self, index):
        if self._parameters_locked() or self._active_module_id != "cell-counting":
            return
        if index < -1 or index >= len(self._channel_roles):
            return
        roles = ["Other" if role == "DAPI" else role for role in self._channel_roles]
        if index >= 0:
            roles[index] = "DAPI"
        self._channel_roles = roles
        self._image_info["mapping_mode"] = "manual"
        if index >= 0 and self._image_info.get("support_channel") == index:
            self._image_info["support_channel"] = -1
            self._image_info["support_marker"] = ""
        self.changed.emit()

    @Slot(int, str)
    def setCountChannelColor(self, index, color):
        if (self._parameters_locked() or self._active_module_id != "cell-counting"
                or not 0 <= index < len(self._channel_roles) or (color and not QColor(color).isValid())):
            return
        colors = list(self._image_info.get("channel_colors") or [""] * len(self._channel_roles))
        if len(colors) != len(self._channel_roles):
            colors = [""] * len(self._channel_roles)
        colors[index] = QColor(color).name() if color else ""
        self._image_info["channel_colors"] = colors
        self.changed.emit()

    @Slot(int)
    def setCountSupportChannel(self, index):
        if (self._parameters_locked() or self._active_module_id != "cell-counting"
                or type(index) is not int or not -1 <= index < len(self._channel_roles)
                or (index >= 0 and self._channel_roles[index] == "DAPI")):
            return
        self._image_info["support_channel"] = index
        if index == -1:
            self._image_info["support_marker"] = ""
        elif self._channel_roles[index] in SUPPORT_MARKERS:
            self._image_info["support_marker"] = self._channel_roles[index]
        else:
            self._image_info["support_marker"] = ""
        self._image_info["mapping_mode"] = "manual"
        self.changed.emit()

    @Slot(str)
    def setCountSupportMarker(self, marker):
        if self._parameters_locked() or self._active_module_id != "cell-counting":
            return
        marker = marker.strip()
        if len(marker) > 120 or any(ord(character) < 32 for character in marker):
            return
        self._image_info["support_marker"] = marker
        self._image_info["mapping_mode"] = "manual"
        self.changed.emit()

    @Slot(int, bool)
    def setCountDisplayChannel(self, index, visible):
        if (self._parameters_locked() or self._active_module_id != "cell-counting"
                or type(index) is not int or not 0 <= index < len(self._channel_roles)):
            return
        channels = set(self._image_info.get("display_channels", range(len(self._channel_roles))))
        if visible:
            channels.add(index)
        else:
            channels.discard(index)
        self._image_info["display_channels"] = sorted(channels)
        self.changed.emit()

    @Slot(str)
    def setCountMappingMode(self, mode):
        if (self._parameters_locked() or self._active_module_id != "cell-counting"
                or mode not in ("auto", "manual")):
            return
        if mode == "auto":
            roles = list(self._image_info.get("detected_channel_roles", self._channel_roles))
            roles = ["Other" if role == "DAPI" else role for role in roles]
            index = self._image_info.get("detected_dapi_channel", -1)
            if type(index) is int and 0 <= index < len(roles):
                roles[index] = "DAPI"
            self._channel_roles = roles
            self._image_info["support_channel"] = self._image_info.get("detected_support_channel", -1)
            self._image_info["support_marker"] = self._image_info.get("detected_support_marker", "")
        self._image_info["mapping_mode"] = mode
        self.changed.emit()

    @Slot(str)
    def setAgeProfile(self, profile):
        if (self._parameters_locked() or self._active_module_id != "project-leap-2d"
                or profile not in ("auto", "mature", "neonatal") or profile == self._age_profile):
            return
        if profile == "neonatal" and "GFAP" in self._channel_roles and "eGFP" not in self._channel_roles:
            self._message = "仅含 GFAP 的星形胶质细胞荧光强度分析目前支持 Mature；请选择自动或 Mature。"
            self.changed.emit()
            return
        self._age_profile = profile
        self.changed.emit()

    @Slot()
    def startAnalysis(self):
        if self._busy or self._parameters_locked():
            return
        if self._active_module_id == "cell-counting":
            self._start_counting()
            return
        if not self._supports_fluorescence_input():
            return
        if self._update_in_progress():
            self._message = "应用更新正在进行，请完成更新后再开始分析。"
            self.changed.emit()
            return
        selected = [role for role in self._channel_roles if role != "Ignore"]
        measurement = [role for role in selected if role in ("KCNN1", "KCNN2", "KCNN3", "KCNJ10")]
        if not self._image_info.get("path") or self._series_index is None or self._time_index is None:
            self._message = "请先选择图像，并明确图像组与时间点。"
            self.changed.emit()
            return
        if len(set(selected)) != len(selected) or "DAPI" not in selected or not any(role in selected for role in ("eGFP", "GFAP")) or len(measurement) != 1:
            self._message = "请选择一个 DAPI、至少一个 eGFP/GFAP，以及一个目标测量通道；角色不能重复。"
            self.changed.emit()
            return
        if self._age_profile == "neonatal" and "eGFP" not in selected:
            self._message = "仅含 GFAP 的星形胶质细胞荧光强度分析目前支持 Mature；请选择自动或 Mature。"
            self.changed.emit()
            return
        # This launch attempt must never overwrite the prior task on preflight failure.
        self._job, self._run_dir = None, None
        self._recovered_task = False
        try:
            descriptor = self._runtime()
            self._use_lock = maintenance.component_use_lock(self.paths.home)
            self._use_lock.__enter__()
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
            self._run_dir = self.paths.runs / run_id
            for relative in ("workspace/Sample Image", "workspace/Result", "workspace/Analysis Package/Run State", "cache"):
                (self._run_dir / relative).mkdir(parents=True, exist_ok=True)
            self._job = {"id": run_id, "run_id": run_id, "name": self._image_info["name"],
                         "source": self._image_info["path"], "created_at": datetime.now(timezone.utc).isoformat(),
                         "results_dir": "", "app_version": self._app_version,
                         "age_profile": self._age_profile,
                         "module_id": self._active_module_id, "module_version": descriptor.get("component_version", "")}
            self._worker_context = WorkerContext(self._active_module_id, run_id)
            self._busy, self._status, self._stage = True, "importing", "拆分与准备图像"
            self._message, self._progress = "正在创建本次任务的单通道工作副本。", 0.0
            self._reset_log()
            self._event_offset, self._fiji_pid = 0, None
            self._owned_processes = {}
            request = {"source_path": self._image_info["path"], "destination_dir": str(self._run_dir / "workspace/Sample Image"),
                       "channel_roles": self._channel_roles, "series_index": self._series_index, "time_index": self._time_index,
                       "profile": None if self._age_profile == "auto" else self._age_profile,
                       "result_path": str(self._run_dir / "import-result.json"),
                       "cancel_path": str(self._run_dir / "cancel.request"), "run_id": run_id}
            write_json(self._run_dir / "import-request.json", request)
            self._save_job()
            self._session.run_details.request(request)
            self._append_detail_updates()
            self._launch("import", ["-m", self._module_definition(self._active_module_id)["import_module"], "--request", str(self._run_dir / "import-request.json")])
        except Exception as exc:
            self._failed(str(exc))
        self.changed.emit()

    def _start_counting(self):
        if self._counting.busy:
            self._message = "正在保存或载入计数结果，请稍候。"
            self.changed.emit()
            return
        if self._update_in_progress():
            self._message = "应用更新正在进行，请完成更新后再开始分析。"
            self.changed.emit()
            return
        if not self._image_info.get("path") or self._series_index is None or self._time_index is None:
            self._message = "请先选择图像，并明确图像组与时间点。"
            self.changed.emit()
            return
        input_error = self._count_input_error()
        if input_error:
            self._message = input_error
            self.changed.emit()
            return
        if self._channel_roles.count("DAPI") != 1:
            self._message = "请自动识别或手动选择一个 DAPI 通道。"
            self.changed.emit()
            return
        support_index = self._image_info.get("support_channel", -1)
        if (type(support_index) is not int or not 0 <= support_index < len(self._channel_roles)
                or support_index == self._channel_roles.index("DAPI") or not self._image_info.get("support_marker", "").strip()):
            self._message = "请选择与 DAPI 不同的 Support 通道，并填写其标记。"
            self.changed.emit()
            return
        display_channels = self._image_info.get("display_channels", [])
        if (not display_channels or any(type(index) is not int or not 0 <= index < len(self._channel_roles)
                                        for index in display_channels)):
            self._message = "请至少选择一个用于审核显示的源通道。"
            self.changed.emit()
            return
        if not self._counting.close():
            self._message = self._counting.message
            self.changed.emit()
            return
        self._job, self._run_dir = None, None
        self._recovered_task = False
        try:
            descriptor = self._runtime()
            self._use_lock = maintenance.component_use_lock(self.paths.home)
            self._use_lock.__enter__()
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
            self._run_dir = self.paths.runs / run_id
            self._run_dir.mkdir(parents=True)
            (self._run_dir / "cache").mkdir()
            self._job = {"id": run_id, "run_id": run_id, "name": self._image_info["name"],
                         "source": self._image_info["path"], "created_at": datetime.now(timezone.utc).isoformat(),
                         "results_dir": "", "app_version": self._app_version,
                         "module_id": "cell-counting", "module_version": descriptor.get("component_version", "")}
            self._worker_context = WorkerContext("cell-counting", run_id)
            request = {"schema_version": 2, "run_id": run_id, "source_path": self._image_info["path"],
                       "series_index": self._series_index, "time_index": self._time_index,
                       "dapi_channel": self._channel_roles.index("DAPI"), "channel_roles": list(self._channel_roles),
                       "support_channel": support_index, "support_marker": self._image_info["support_marker"],
                       "display_channels": list(display_channels),
                       "channel_colors": self._image_info.get("channel_colors") or [],
                       "run_dir": str(self._run_dir), "events_path": str(self._run_dir / "events.jsonl"),
                       "outcome_path": str(self._run_dir / "outcome.json"),
                       "cancel_path": str(self._run_dir / "cancel.request")}
            write_json(self._run_dir / "request.json", request)
            self._session.run_settings = json.loads(json.dumps(request))
            self._busy, self._status, self._stage = True, "running", "读取图像"
            self._message, self._progress = "正在按原始图像标定准备 DAPI 与辅助通道计数。", 0.0
            self._reset_log()
            self._event_offset, self._fiji_pid = 0, None
            self._owned_processes = {}
            self._save_job()
            self._session.run_details.request(request)
            self._append_detail_updates()
            self._launch("analysis", ["-m", self._module_definition("cell-counting")["entrypoint"],
                                       "--request", str(self._run_dir / "request.json")])
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            self._failed(str(exc))
        self.changed.emit()

    @worker_session
    def _start_engine(self, imported):
        descriptor = self._runtime()
        request = {"schema_version": 1, "run_id": self._job["id"], "workspace": str(self._run_dir / "workspace"),
                   "cache_root": str(self._run_dir / "cache"), "events_path": str(self._run_dir / "events.jsonl"),
                   "outcome_path": str(self._run_dir / "outcome.json"), "fiji_launcher": descriptor["fiji_launcher"],
                   "engine_args": [],
                   "provenance": {"app_version": self._app_version, "component_version": descriptor.get("component_version"),
                                  "module_id": self._job["module_id"], "module_version": self._job.get("module_version", ""),
                                  "environment_contract": descriptor.get("environment_contract"),
                                  "source": self._image_info["path"], "import_result_path": str(self._run_dir / "import-result.json")},
                   "input_provenance": imported}
        write_json(self._run_dir / "request.json", request)
        self._status, self._stage = "running", "加载分析引擎"
        self._message, self._progress = "图像准备完成，正在启动分析。", -1.0
        self._save_job()
        self._launch("analysis", ["-m", self._module_definition(self._job["module_id"])["entrypoint"], "--request", str(self._run_dir / "request.json")])

    @Slot()
    @worker_session
    def _read_output(self):
        process = self.sender()
        if not isinstance(process, QProcess) or process is not self._process:
            return
        self._consume_output(process)

    def _consume_output(self, process):
        text = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._consume_text(text)

    def _read_console(self, *, observe_only=False):
        if not self._run_dir:
            return
        path = self._run_dir / "console.log"
        if not path.exists():
            return
        with path.open("rb") as stream:
            stream.seek(self._console_offset)
            text = self._session.console_decoder.decode(stream.read(64 * 1024))
            self._console_offset = stream.tell()
        if text:
            if observe_only:
                self._append_log(text)
            else:
                self._consume_text(text)

    def _consume_text(self, text):
        state_changed = False
        if self._process_kind == "inspect":
            self._inspect_buffer += text
        else:
            self._append_log(text)
            if self._process_kind == "import":
                self._process_buffer += text
                while "\n" in self._process_buffer:
                    line, self._process_buffer = self._process_buffer.split("\n", 1)
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "progress":
                        state_changed = True
                        total = event.get("total") or 0
                        self._progress = float(event.get("current", 0)) / total if total else -1.0
                        self._message = event.get("message", "正在拆分图像。")
        if state_changed:
            self.changed.emit()

    @Slot(int, QProcess.ExitStatus)
    @worker_session
    def _process_finished(self, exit_code, exit_status):
        sender = self.sender()
        if isinstance(sender, QProcess) and sender is not self._process:
            return
        kind, process = self._process_kind, self._process
        was_cancelling = self._status == "cancelling"
        inspection_reported_failure = False
        if process and kind == "inspect":
            self._consume_output(process)
        if kind != "inspect":
            self._read_console()
        self._detached_pid = None
        self._process, self._process_kind = None, None
        if process:
            process.deleteLater()
        try:
            if was_cancelling and kind in ("import", "analysis"):
                terminal = self._run_dir / ("outcome.json" if kind == "analysis" else "import-result.json")
                if not terminal.is_file():
                    if kind == "analysis":
                        self._read_events()
                    self._finish("cancelled", "本次分析已取消，输入副本保留。")
                    if self._job:
                        self._show_job(self._job)
                    self.changed.emit()
                    return
            if exit_status == QProcess.ExitStatus.CrashExit and not was_cancelling:
                self._mark_module_fault("后台进程异常退出，请修复当前模块后重试。")
            if kind == "inspect":
                if self._status == "cancelling":
                    self._finish("cancelled", "图像读取已取消。")
                    self.changed.emit()
                    return
                try:
                    result = json.loads(self._inspect_buffer.strip())
                except ValueError as exc:
                    if self._inspect_buffer.strip():
                        self._append_log(self._inspect_buffer.strip() + "\n")
                    raise RuntimeError("图像读取进程未返回有效结果。") from exc
                if not isinstance(result, dict):
                    raise RuntimeError("图像读取进程未返回有效结果。")
                inspection_reported_failure = result.get("status") == "failed"
                if result.get("status") == "cancelled":
                    self._finish("cancelled", result.get("message", "图像读取已取消。"))
                    self.changed.emit()
                    return
                if exit_code or result.get("status") == "failed":
                    raise RuntimeError(result.get("message", "图像信息读取失败。"))
                self._image_info.update(result)
                if result.get("preview_path"):
                    self._image_info["thumbnail_url"] = QUrl.fromLocalFile(result["preview_path"]).toString()
                self._image_info.setdefault("name", Path(result["path"]).name)
                if int(result.get("series_count", 1)) == 1:
                    self._apply_series(0)
                self._status, self._stage, self._busy = "ready", "确认通道", False
                if self._session.module_id == "cell-counting":
                    if "DAPI" in self._channel_roles and self._image_info.get("support_channel", -1) >= 0:
                        self._message = "已自动找到 DAPI 与 Support，请确认分析通道和显示通道。"
                    else:
                        self._message = "请确认 DAPI、Support 及其标记，再选择审核显示通道。"
                else:
                    self._message = "请确认每个通道的用途，然后开始分析。"
            elif kind == "import":
                result = json.loads((self._run_dir / "import-result.json").read_text())
                if was_cancelling or result.get("status") == "cancelled":
                    self._finish("cancelled", "本次分析已取消，输入副本保留。")
                elif result.get("status") != "completed" or exit_code:
                    self._failed(result.get("message", "图像导入失败。"))
                else:
                    self._start_engine(result)
            elif kind == "analysis":
                self._read_events()
                path = self._run_dir / "outcome.json"
                if not path.is_file():
                    raise RuntimeError("分析进程已结束，但没有完整的任务结束记录。输入与已保存结果保持保留。")
                outcome = json.loads(path.read_text())
                self._job["outcome"] = outcome
                self._job["results_dir"] = outcome.get("results_dir") or ""
                status = outcome["status"]
                if self._job.get("module_id") == "cell-counting" and status == "review_ready" and not was_cancelling:
                    analysis = self.paths.resolve_managed_path(outcome["analysis_path"])
                    if not analysis.is_file():
                        raise RuntimeError("计数审核记录不存在。")
                    self._job.update(analysis_path=str(analysis), auto_count=int(outcome.get("auto_count", 0)),
                                     count=int(outcome.get("auto_count", 0)))
                    self._clear_module_fault("cell-counting")
                    self._finish("waiting_review", "自动计数完成，可增删编号并确认保存。")
                    self._open_counting_analysis(analysis)
                    self._append_detail_updates()
                    self._show_job(self._job)
                    self.changed.emit()
                    return
                if outcome.get("error_code") == "engine_check_stopped":
                    status = "rejected"
                if was_cancelling and status != "completed":
                    status = "cancelled"
                if outcome.get("message"):
                    self._append_log(outcome["message"] + "\n")
                messages = {"completed": "结果与最终 ROI 已保存。", "cancelled": "本次分析已取消，输入副本保留。",
                            "rejected": "现有分析引擎按自身检查停止，未发布正式结果。",
                            "failed": "分析遇到技术错误，详细原因见诊断记录。"}
                if outcome.get("error_code") == "analysis_already_running":
                    messages["failed"] = "另一个分析任务仍在运行，请等待它结束后重试。"
                if status == "failed" and outcome.get("error_code") in (
                        "technical_failure", "engine_failed", "engine_not_published"):
                    self._mark_module_fault(outcome.get("message", messages["failed"]))
                elif status == "completed":
                    self._clear_module_fault(self._job.get("module_id", self._active_module_id))
                self._finish(status, messages.get(status, outcome.get("message", "分析未完成。")))
                self._show_job(self._job)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            if not was_cancelling and (kind == "analysis" or (kind == "inspect" and not inspection_reported_failure)):
                self._mark_module_fault(str(exc))
            if was_cancelling:
                self._finish("cancelled", "本次分析已取消，输入副本保留。")
            else:
                self._failed(str(exc))
        self.changed.emit()

    @Slot(QProcess.ProcessError)
    @worker_session
    def _process_error(self, error):
        sender = self.sender()
        if isinstance(sender, QProcess) and sender is not self._process:
            return
        if error == QProcess.ProcessError.FailedToStart:
            process = self._process
            message = process.errorString() if process else "无法启动后台进程。"
            self._mark_module_fault(message)
            self._process_kind = None
            self._process = None
            if process:
                process.deleteLater()
            self._failed(message)

    def _read_events(self):
        if not self._run_dir:
            return False
        path = self._run_dir / "events.jsonl"
        if not path.exists():
            return False
        previous_offset = self._event_offset
        with path.open(encoding="utf-8") as stream:
            stream.seek(self._event_offset)
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    stream.seek(offset)
                    break
                event = json.loads(line)
                details = self._ensure_run_details()
                accepted_details = details.event(event) if details else False
                kind = event.get("type")
                if (accepted_details and kind in ("stage", "progress", "warning", "failed", "completed", "cancelled", "review_ready")
                        and event.get("message")):
                    message = self.locale.translate(str(event["message"]))
                    if event.get("total"):
                        message += " (" + str(event.get("current", 0)) + "/" + str(event["total"]) + ")"
                    for key, label in (("auto_count", "自动接受数量"), ("pending_count", "待审核数量"),
                                       ("excluded_count", "自动排除数量")):
                        if key in event:
                            message += "; " + self.locale.translate(label) + "=" + str(event[key])
                    self._append_log(self._detail_line(STAGES.get(event.get("stage"), event.get("stage") or "运行记录"),
                        message, timestamp=event.get("timestamp")), observe=False)
                if accepted_details:
                    self._append_detail_updates(timestamp=event.get("timestamp"))
                if kind == "process":
                    pid, pgid = event.get("pid"), event.get("pgid")
                    if isinstance(pid, int) and pid > 1 and pgid == pid:
                        record = {key: event.get(key) for key in ("pid", "pgid", "process_type", "process_status")}
                        if self._recovered_task:
                            record["recovered"] = True
                        self._owned_processes[pid] = record
                        if event.get("process_type") == "fiji":
                            self._fiji_pid = pid if event.get("process_status") != "exited" else None
                elif kind == "review_ready":
                    if self._status != "cancelling":
                        counting = (self._job or {}).get("module_id") == "cell-counting"
                        self._status, self._stage = "waiting_review", "等待计数确认" if counting else "等待 Fiji 审核"
                        self._message = ("自动计数完成，可增删编号并确认保存。" if counting else
                                         "请在 Fiji 中复核 ROI 或确认测量。完成后会自动继续。")
                        self._progress = -1.0
                elif kind in ("stage", "progress"):
                    if self._status != "cancelling":
                        self._status = "running"
                        stage = event.get("stage", "")
                        self._stage = STAGES.get(stage, "分析处理中")
                        self._message = self._stage + "，请稍候。"
                        total = event.get("total") or 0
                        self._progress = float(event.get("current", 0)) / total if total else -1.0
                        if total:
                            self._message = self._stage + "：已完成 " + str(event.get("current", 0)) + "/" + str(total)
                elif kind == "warning":
                    self._message = "当前任务有提示，详情见日志。"
            self._event_offset = stream.tell()
        if self._event_offset != previous_offset:
            self._save_job()
            return True
        return False

    @Slot()
    @worker_session
    def _poll(self):
        if self._cleanup_pending:
            self._poll_cleanup()
        if self._process_kind in ("import", "analysis"):
            self._read_console()
        if self._process_kind == "analysis" and self._session.status == "running":
            self._read_progress_details()
        request_path = (self._run_dir / ("request.json" if self._process_kind == "analysis" else "import-request.json")
                        if self._run_dir else None)
        if self._detached_pid and not self._matches_process(self._detached_pid, request_path):
            self._process_finished(0, QProcess.ExitStatus.NormalExit)
        if self._process_kind == "analysis":
            try:
                if self._read_events():
                    self.changed.emit()
            except (OSError, ValueError) as exc:
                self._append_log("状态读取：" + str(exc) + "\n")
        self._poll_updates()

    def _release_lock(self):
        if self._use_lock:
            self._use_lock.__exit__(None, None, None)
            self._use_lock = None

    def _finish(self, status, message):
        remaining = self._alive_owned_processes()
        if remaining:
            self._cleanup_pending = (status, message, time.monotonic())
            self._busy, self._status, self._stage = True, "cancelling", "结束本次后台会话"
            self._message = "正在关闭本任务的后台会话，组件维护将在退出后恢复。"
            self._signal_owned(remaining, signal.SIGTERM)
            self._save_job()
            return
        self._commit_finish(status, message)

    def _alive_owned_processes(self):
        remaining = []
        for record in self._owned_processes.values():
            if record.get("recovered") and not self._matches_process(record["pid"], self._run_dir):
                record["process_status"] = "exited"
            if record.get("process_status") == "exited":
                continue
            try:
                if os.getpgid(record["pid"]) == record["pgid"]:
                    remaining.append(record)
            except ProcessLookupError:
                record["process_status"] = "exited"
            except PermissionError:
                remaining.append(record)
        return remaining

    def _signal_owned(self, records, sig):
        for record in records:
            if record.get("recovered") and not self._matches_process(record["pid"], self._run_dir):
                record["process_status"] = "exited"
                continue
            try:
                if os.getpgid(record["pid"]) == record["pgid"] == record["pid"]:
                    os.killpg(record["pgid"], sig)
            except ProcessLookupError:
                record["process_status"] = "exited"
            except PermissionError as exc:
                self._append_log("本次会话尚未退出：" + str(exc) + "\n")

    def _poll_cleanup(self):
        status, message, started = self._cleanup_pending
        remaining = self._alive_owned_processes()
        if not remaining:
            self._cleanup_pending = None
            self._commit_finish(status, message)
            if self._job:
                self._show_job(self._job)
        elif time.monotonic() - started >= 5.0:
            self._signal_owned(remaining, signal.SIGKILL)
        self.changed.emit()

    def _commit_finish(self, status, message):
        self._busy, self._status, self._message = False, status, message
        self._stage = {"completed": "分析完成", "cancelled": "已取消", "failed": "任务未完成", "rejected": "分析已停止", "interrupted": "上次任务已中断", "waiting_review": "等待计数确认"}.get(status, status)
        self._progress = 1.0 if status == "completed" else -1.0
        details = self._ensure_run_details()
        if details:
            details.freeze()
        self._sync_resource_monitor()
        self._release_lock()
        self._save_job()
        if self._job:
            module_id, run_id = self._session.module_id, self._job["id"]
            QTimer.singleShot(0, lambda: self._finish_detail_observation(module_id, run_id))
        QTimer.singleShot(0, self._resume_storage_refresh)
        if status == "completed" and self._job:
            self._pending_cleanup_ids.add(self._job["id"])
            QTimer.singleShot(0, self._try_task_cleanup)

    def _failed(self, message):
        self._append_log(message + "\n")
        self._finish("failed", message)
        self.changed.emit()

    @Slot()
    @worker_session
    def cancelAnalysis(self):
        if (self._process or self._detached_pid) and self._busy:
            if self._run_dir and self._process_kind != "inspect":
                (self._run_dir / "cancel.request").touch()
            self._status, self._stage = "cancelling", "正在取消"
            self._message = ("正在取消细胞计数，请稍候。" if self._session.module_id == "cell-counting" else
                             "正在结束本次计算和 Fiji 会话，请稍候。")
            pid = self._detached_pid or int(self._process.processId())
            if pid:
                try:
                    os.kill(pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
            self._save_job()
            self.changed.emit()

    def _result_for_job(self, job):
        directory = self.paths.resolve_managed_path(job.get("results_dir") or self.paths.runs / job["id"] / "workspace/Result")
        artifacts = [{"name": p.name, "path": str(p), "url": QUrl.fromLocalFile(str(p)).toString(),
                      "kind": p.suffix.lstrip(".").lower()}
                     for p in sorted(directory.glob("*")) if p.is_file() and not p.is_symlink()]
        result = {"jobId": job["id"], "name": job.get("name", job["id"]), "status": job["status"],
                "moduleId": job.get("module_id", "project-leap-2d"), "results_dir": str(directory),
                "artifacts": artifacts, "images": [item for item in artifacts if item["kind"] == "png"],
                "message": job.get("message", "")}
        if job.get("module_id") == "cell-counting":
            result.update(count=job.get("count", job.get("auto_count", 0)), auto_count=job.get("auto_count", 0),
                          analysis_path=job.get("analysis_path", ""), revision=job.get("revision", 0),
                          confirmed=job.get("status") == "completed")
            if not result["images"] and job.get("analysis_path"):
                analysis_path = self.paths.resolve_managed_path(job["analysis_path"])
                if analysis_path.is_file():
                    analysis = json.loads(analysis_path.read_text())
                    preview = analysis_path.parent / analysis.get("preview_path", "composite.png")
                    if preview.is_file():
                        result["images"] = [{"name": "自动计数预览", "path": str(preview),
                                             "url": QUrl.fromLocalFile(str(preview)).toString(), "kind": "png"}]
        return result

    def _open_counting_analysis(self, path):
        path = Path(path)
        session = self._session_for("cell-counting")
        analysis = json.loads(path.read_text())
        details = self._ensure_run_details()
        if details:
            details.analysis(analysis)
        request_path = session.run_dir / "request.json" if session.run_dir else None
        settings = json.loads(request_path.read_text()) if request_path and request_path.is_file() else analysis
        session.run_settings = {key: value for key, value in settings.items()
                                if key in ("schema_version", "run_id", "source_path", "series_index", "time_index",
                                           "dapi_channel", "support_channel", "support_marker", "display_channels",
                                           "channel_roles", "channel_colors")}
        session.image_info.update({key: value for key, value in analysis.items()
                                   if key in ("width", "height", "z_count", "physical_size_um", "support_channel",
                                              "support_marker", "display_channels", "channel_colors")})
        session.channel_roles = list(settings.get("channel_roles", analysis.get("channel_roles", [])))
        session.image_info["channel_count"] = len(session.channel_roles)
        session.image_info["channel_hints"] = [item.get("name", "") for item in analysis.get("channels", [])]
        runtime, environment = None, None
        state = self._module_states.get("cell-counting", {})
        if state.get("status") == "ready" and state.get("runtime_descriptor"):
            runtime = state["runtime_descriptor"]
            environment = self._environment(runtime, "cell-counting")
        self._counting.open_analysis(str(path), runtime=runtime, environment=environment)

    @Slot(str)
    def openCountingJob(self, identifier):
        session = self._session_for("cell-counting")
        if self._maintenance_busy or self._counting.busy or self._update_in_progress():
            return
        if (self._busy and (not self._worker_context or self._worker_context.module_id == "cell-counting")):
            return
        if session.job and session.job["id"] == identifier and self._counting.available:
            self._activate_module("cell-counting")
            self._page = "analysis"
            self.changed.emit()
            return
        if session.status == "waiting_review" or self._counting.dirty:
            session.message = "请先确认当前计数审核，再打开另一项计数任务。"
            self.changed.emit()
            return
        job = next((item for item in self._jobs if item["id"] == identifier and item.get("module_id") == "cell-counting"), None)
        if not job or not job.get("analysis_path"):
            return
        try:
            path = self.paths.resolve_managed_path(job["analysis_path"])
            self._sessions["cell-counting"] = WorkbenchSession("cell-counting")
            self._activate_module("cell-counting")
            self._job, self._run_dir = dict(job), self.paths.runs / identifier
            self._status = job["status"]
            self._stage = "已确认" if self._status == "completed" else "等待计数确认"
            self._image_info = {"path": job["source"], "name": job["name"]}
            self._open_counting_analysis(path)
            self.loadJobRunDetails(identifier)
            self._history_restore = self._history_generation
            self._page = "analysis"
            self._show_job(job)
            self._message = "已打开保存的计数结果。"
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            self._sessions["cell-counting"] = session
            self._activate_module("cell-counting")
            self._message = str(exc)
        self.changed.emit()

    @Slot(str, object)
    def _counting_confirmed(self, identifier, summary):
        with self._in_session("cell-counting"):
            self._apply_counting_confirmation(identifier, summary)

    def _apply_counting_confirmation(self, identifier, summary):
        path = self.paths.runs / identifier / "job.json"
        try:
            job = json.loads(path.read_text())
            if job.get("module_id") != "cell-counting":
                raise ValueError("计数结果与任务不一致。")
            results = Path(summary["results_dir"])
            if not results.is_relative_to(path.parent) or not results.is_dir():
                raise ValueError("计数结果目录不属于当前任务。")
            job.update(status="completed", stage="已确认", message="细胞计数结果已确认并保存。",
                       results_dir=str(results), count=int(summary["count"]),
                       revision=int(summary["revision"]), confirmed_at=datetime.now(timezone.utc).isoformat())
            # A rapid confirmation can precede the bounded console drain. The
            # already-loaded analysis still supplies every final model/timing
            # field, and this existing durable write preserves that summary.
            details = self._session.run_details
            if (not job.get("run_details") and details and details.run_id == identifier):
                details.job(job)
                job["run_details"] = details.persisted()
            write_json(path, job)
            if self._job and self._job["id"] == identifier:
                self._job = job
                self._session.details_finalized = True
                self._status, self._stage, self._message = "completed", "已确认", job["message"]
            self._load_jobs()
            # The job is durable before quit becomes available; detach the editor
            # only after its publication callback has returned to the event loop.
            QTimer.singleShot(0, lambda: self._reset_confirmed_counting(identifier))
        except (OSError, ValueError, KeyError) as exc:
            self._message = str(exc)
        self.changed.emit()

    def _reset_confirmed_counting(self, identifier):
        with self._in_session("cell-counting"):
            if (not self._job or self._job["id"] != identifier or self._status != "completed"
                    or self._counting.runId != identifier or self._counting.dirty
                    or self._counting.confirmationInProgress):
                return
            if self._counting.close():
                self._pending_cleanup_ids.add(identifier)
                self._remove_input_preview(self._session)
                self._sessions["cell-counting"] = WorkbenchSession("cell-counting")
                if self._pending_image_module_id == "cell-counting":
                    self._pending_image = self._pending_image_module_id = None
                self._reset_log()
            else:
                self._message = self._counting.message
            self.changed.emit()
            self._try_task_cleanup()

    def _show_job(self, job):
        self._result_info = self._result_for_job(job)
        self._selected_job_id = job["id"]

    @Slot(str)
    def selectJob(self, identifier):
        if self._selected_job_id == identifier:
            self._selected_job_id, self._result_info = "", {}
            self.changed.emit()
            return
        for job in self._jobs:
            if job["id"] == identifier:
                self._show_job(job)
                self.changed.emit()
                break

    @Slot()
    def openResults(self):
        self.openResultsForJob(self._selected_job_id)

    @Slot(str)
    def openResultsForJob(self, identifier):
        job = next((item for item in self._jobs if item["id"] == identifier), None)
        if not job:
            return
        path = self._result_for_job(job).get("results_dir")
        if path and Path(path).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    @Slot(str, str)
    def openArtifactForJob(self, identifier, path):
        job = next((item for item in self._jobs if item["id"] == identifier), None)
        if job and any(item["path"] == path for item in self._result_for_job(job)["artifacts"]):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    @Slot()
    def openDataFolder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths.home)))

    @Slot()
    @worker_session
    def showFiji(self):
        if not self._fiji_pid:
            return
        ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        lookup = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)(("objc_msgSend", objc))
        activate = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(("objc_msgSend", objc))
        app = lookup(objc.objc_getClass(b"NSRunningApplication"), objc.sel_registerName(b"runningApplicationWithProcessIdentifier:"), int(self._fiji_pid))
        if app:
            activate(app, objc.sel_registerName(b"activateWithOptions:"), 2)

    def _maintenance_allowed(self):
        if self._busy or self._maintenance_busy or self._counting.busy or self._update_in_progress():
            self._notice("请等待当前任务或更新结束，再维护组件。", "info")
            return False
        return True

    def _current_sources(self):
        values = []
        for session in self._sessions.values():
            if session.image_info.get("path"):
                values.append(session.image_info["path"])
            if session.preview_file:
                values.append(session.preview_file)
            url = QUrl(session.image_info.get("thumbnail_url", ""))
            if url.isLocalFile():
                values.append(url.toLocalFile())
        return values

    def _remove_input_preview(self, session):
        value = session.preview_file
        if not value:
            url = QUrl(session.image_info.get("thumbnail_url", ""))
            value = url.toLocalFile() if url.isLocalFile() else ""
        session.preview_file = ""
        session.image_info.pop("thumbnail_url", None)
        if value:
            path = Path(value)
            if path.parent.resolve().is_relative_to((self.paths.cache / "modules").resolve()):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    self._notice("无法清理图像预览：" + str(exc), "error")

    @Slot()
    def refreshStorageUsage(self):
        if self._closing:
            return
        if self._busy or self._maintenance_busy:
            self._storage_refresh_pending = True
            return
        self._storage_refresh_pending = False
        from .task_storage import task_storage_usage
        self._run_maintenance(task_storage_usage, self.paths, operation="storage")

    def _resume_storage_refresh(self):
        if self._storage_refresh_pending and not self._closing and not self._busy and not self._maintenance_busy:
            self.refreshStorageUsage()

    def _deletion_allowed(self, identifiers):
        if not self.canDeleteJobs or not self._counting.drained:
            self._notice("请等待当前图像读写结束后删除分析。", "info")
            return False
        if self._counting.available and self._counting.runId in identifiers:
            self._notice("请先完成当前计数审核，再删除该分析。", "info")
            return False
        known = {job["id"] for job in self._jobs}
        if not identifiers or any(not isinstance(value, str) or value not in known for value in identifiers):
            self._notice("所选分析已不存在，请刷新列表。", "error")
            return False
        for identifier in identifiers:
            root = self.paths.runs / identifier
            worker = root / "worker.json"
            if worker.is_file():
                try:
                    record = json.loads(worker.read_text())
                    if self._matches_process(record.get("pid"), record.get("request_path", root / "request.json")):
                        self._notice("所选分析仍在运行，请等待结束后删除。", "info")
                        return False
                except (OSError, ValueError):
                    self._notice("无法读取所选分析的运行状态。", "error")
                    return False
        return True

    @Slot("QVariantList")
    def requestDeleteJobs(self, identifiers):
        identifiers = list(dict.fromkeys(identifiers))
        if not self._deletion_allowed(identifiers):
            return
        self._pending_delete_ids = identifiers
        until = self.locale.preference("delete_confirmation_until", "")
        try:
            snoozed = datetime.fromisoformat(until) > datetime.now(timezone.utc) if until else False
        except (ValueError, TypeError):
            snoozed = False
        if snoozed:
            self.confirmDeleteJobs(False)
        else:
            self.deleteConfirmationRequested.emit(len(identifiers))

    @Slot()
    def cancelDeleteJobs(self):
        self._pending_delete_ids = []

    @Slot(bool)
    def confirmDeleteJobs(self, skip_one_month):
        identifiers = self._pending_delete_ids
        if not self._deletion_allowed(identifiers):
            self._pending_delete_ids = []
            return
        if skip_one_month:
            now = datetime.now()
            year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
            until = now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))
            try:
                self.locale.set_preference("delete_confirmation_until", until.astimezone().isoformat())
            except (OSError, ValueError) as exc:
                self._notice(str(exc), "error")
                return
        self._pending_delete_ids = []
        from .task_storage import delete_tasks
        self._run_maintenance(delete_tasks, self.paths, identifiers, current_sources=self._current_sources(), operation="delete_tasks")

    @Slot()
    def _try_task_cleanup(self):
        if (self._closing or not self._pending_cleanup_ids or self._busy or self._maintenance_busy
                or not self._counting.drained):
            return
        available = sorted(value for value in self._pending_cleanup_ids
                           if not self._counting.available or self._counting.runId != value)
        if not available:
            return
        identifier = available[0]
        self._pending_cleanup_ids.discard(identifier)
        from .task_storage import cleanup_completed_task
        self._run_maintenance(cleanup_completed_task, self.paths, identifier,
                              current_sources=self._current_sources(), operation="task_cleanup")

    def _notice(self, message, kind="info"):
        self._notice_timer.stop()
        self._maintenance_message, self._maintenance_message_kind = message, kind
        self._message = message
        if kind == "success":
            self._notice_timer.start()
        self.changed.emit()

    @Slot()
    def _clear_success_notice(self):
        if self._maintenance_message_kind == "success":
            if self._message == self._maintenance_message:
                self._message = ""
            self._maintenance_message = ""
            self.changed.emit()

    def _run_maintenance(self, function, *args, operation, target_module=None, **kwargs):
        if operation == "migration":
            # Flush the latest draft before taking the storage snapshot.
            if not self._counting.close():
                self._notice(self._counting.message, "error")
                return
        if operation not in ("storage", "task_cleanup"):
            self._notice_timer.stop()
        self._maintenance_busy, self._maintenance_progress = True, 0.0
        self._maintenance_operation, self._maintenance_module_id = operation, target_module
        self._module_success_messages.pop(target_module, None)
        self._maintenance_result = None
        if operation not in ("storage", "task_cleanup"):
            message = {"delete_tasks": "正在删除所选分析…", "clear_cache": "正在清理缓存…"}.get(operation, "正在准备组件。")
            self._maintenance_message, self._maintenance_message_kind = message, "info"
        task = MaintenanceTask(function, *args, **kwargs)
        task.setParent(self)
        task.progress.connect(self._maintenance_progressed)
        task.result.connect(self._maintenance_result_received)
        task.problem.connect(self._maintenance_problem_received)
        task.finished.connect(self._maintenance_finished)
        task.finished.connect(task.deleteLater)
        self._maintenance_thread = task
        task.start()
        self.changed.emit()

    @Slot(object)
    def _maintenance_result_received(self, result):
        self._maintenance_result = result

    @Slot(str)
    def _maintenance_problem_received(self, message):
        self._maintenance_result = {"status": "failed", "message": message}

    @Slot()
    def _maintenance_finished(self):
        # Only release the UI gate or restart after the maintenance worker exits.
        result = self._maintenance_result or {"status": "failed", "message": "组件操作未返回结果。"}
        self._maintenance_result = None
        self._maintenance_thread = None
        self._maintenance_done(result)

    def _maintain(self, repair, identifier=None):
        if not self._maintenance_allowed():
            return
        identifier = identifier or self._active_module_id
        try:
            self._module_definition(identifier)
        except (KeyError, ValueError) as exc:
            self._notice(str(exc), "error")
            return
        state = self._module_states.get(identifier, {})
        configuration = self.paths.release_configuration
        source = None
        if not configuration.catalog_url:
            source = self.paths.package_source(state.get("source"))
        self._module_states[identifier] = {**state, "id": identifier,
            "status": "repairing" if repair else "installing", "message": "正在准备组件。"}
        if identifier == self._active_module_id:
            self._component_info = self._module_states[identifier]
        function = maintenance.repair_component if repair else maintenance.install_component
        self._run_maintenance(function, self.paths.home, source,
                              operation="repair" if repair else "install", target_module=identifier,
                              module_id=identifier, configuration=configuration)

    @Slot()
    @Slot(str)
    def installModule(self, identifier=""):
        self._maintain(False, identifier)

    @Slot()
    @Slot(str)
    def repairModule(self, identifier=""):
        self._maintain(True, identifier)

    @Slot(str)
    def removeModule(self, identifier):
        if not self._maintenance_allowed() or not self._module_states.get(identifier, {}).get("installed"):
            return
        answer = QMessageBox.question(None, self.locale.translate("删除功能模块") + " · " + self._module_name(identifier),
            self.locale.translate("将删除此模块的运行组件；历史任务与结果保留。是否继续？"))
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_maintenance(maintenance.remove_component, self.paths.home,
                              operation="remove", target_module=identifier, module_id=identifier)

    @Slot(float, str)
    def _maintenance_progressed(self, fraction, message):
        self._maintenance_progress = fraction
        if self._maintenance_operation in ("updates_stage", "updates_apply"):
            self._set_update_status(message)
        if self._maintenance_operation not in ("storage", "task_cleanup"):
            self._maintenance_message = message
        identifier = self._maintenance_module_id
        if identifier:
            self._module_states[identifier] = {**self._module_states.get(identifier, {}), "message": message}
            if identifier == self._active_module_id:
                self._component_info = self._module_states[identifier]
        self.changed.emit()

    @Slot(object)
    def _maintenance_done(self, result):
        operation, identifier = self._maintenance_operation, self._maintenance_module_id
        self._maintenance_busy = False
        if operation in ("updates_stage", "updates_apply"):
            self._update_operation_done(operation, result)
            return
        success = result.get("status") in ("ready", "intact", "removed", "migrated", "scheduled", "deleted", "cleared", "inspected")
        self._maintenance_progress = 1.0 if success else -1.0
        message = result.get("message", "组件操作结束。")
        if operation in ("storage", "delete_tasks", "clear_cache", "task_cleanup"):
            if operation == "storage" and success:
                self._storage_usage = {item["id"]: item["allocated_bytes"] for item in result["tasks"]}
                self.jobsChanged.emit()
            elif operation == "delete_tasks":
                removed = set(result.get("deleted_ids", []))
                for key, session in list(self._sessions.items()):
                    if session.job and session.job["id"] in removed:
                        self._remove_input_preview(session)
                        self._sessions[key] = WorkbenchSession(key)
                    elif session.selected_job_id in removed:
                        session.selected_job_id, session.result_info = "", {}
                self._pending_cleanup_ids.difference_update(removed)
                self._load_jobs()
            if operation != "storage" and (operation != "task_cleanup" or not success):
                self._notice(message, "success" if success else "error")
            self.changed.emit()
            QTimer.singleShot(0, self._try_task_cleanup)
            QTimer.singleShot(0, self._resume_storage_refresh)
            if operation in ("delete_tasks", "clear_cache"):
                QTimer.singleShot(0, self.refreshStorageUsage)
            return
        if operation == "remove" and result.get("model_retained_for_users"):
            message += "\n" + self.locale.translate("Cellpose 模型仍由其他已安装模块使用，已保留。")
        if operation == "migration":
            self._notice(message, "success" if success else "error")
            if success and result.get("restart_required"):
                self.restartRequested.emit(str(result["data_dir"]))
            return
        self._refresh_modules()
        QTimer.singleShot(0, self._resume_storage_refresh)
        if success and identifier and result.get("status") != "intact":
            self._clear_module_fault(identifier)
        elif identifier:
            state = self._module_states.get(identifier, {})
            if operation == "repair" and result.get("status") == "intact":
                self._module_success_messages[identifier] = (message, state.get("message", ""))
            self._module_states[identifier] = {**state, "message": message}
            if identifier == self._active_module_id:
                self._component_info = self._module_states[identifier]
        self._notice(message, "success" if success else "error")
        if (result.get("status") == "ready" and identifier == self._active_module_id
                and self._pending_image_module_id == identifier and self._pending_image):
            path, self._pending_image = self._pending_image, None
            self._pending_image_module_id = None
            QTimer.singleShot(0, lambda: self.inspectImage(path))
        if operation == "remove" and success:
            if identifier == self._active_module_id:
                installed = self._module_items(True)
                if installed:
                    self._activate_module(installed[0]["id"])
                else:
                    self._image_info, self._channel_roles = {}, []
                    self._pending_measurement_marker = ""
                    self._page = "maintenance"
            remove_fiji = bool(result.get("offer_remove_fiji"))
            remove_model = bool(result.get("offer_remove_model"))
            if remove_fiji or remove_model:
                if remove_fiji and remove_model:
                    title, question = "删除共享组件", "已没有模块使用 Cellpose 模型和 Fiji。是否同时删除它们？历史任务与结果保留。"
                elif remove_model:
                    title, question = "删除共享 Cellpose 模型", "已没有模块使用 Cellpose 模型。是否同时删除模型？选择否可供以后重装使用。"
                else:
                    title, question = "删除共享 Fiji", "已没有安装的模块需要 Fiji。是否同时删除 Fiji？历史结果仍会保留。"
                answer = QMessageBox.question(None, self.locale.translate(title), self.locale.translate(question),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if answer == QMessageBox.StandardButton.Yes:
                    self._run_maintenance(self._remove_unused_shared, self.paths.home, remove_fiji, remove_model,
                                          operation="remove_shared")
        self.changed.emit()

    @staticmethod
    def _remove_unused_shared(home, remove_fiji, remove_model, *, progress=None):
        messages = []
        for enabled, function in ((remove_fiji, maintenance.remove_fiji),
                                  (remove_model, getattr(maintenance, "remove_model", None))):
            if enabled:
                result = function(home, progress=progress)
                if result.get("status") != "removed":
                    return result
                messages.append(result.get("message", ""))
        return {"status": "removed", "message": "\n".join(messages)}

    @Slot()
    def chooseDataDirectory(self):
        if not self._maintenance_allowed():
            return
        destination = QFileDialog.getExistingDirectory(None, self.locale.translate("选择新的应用数据目录"))
        if not destination:
            return
        from . import lifecycle
        self._run_maintenance(lifecycle.migrate_app_data, self.paths, Path(destination), operation="migration")

    @Slot(bool)
    def requestUninstall(self, remove_data):
        if not self._maintenance_allowed():
            return
        if not self._counting.close():
            self._notice(self._counting.message, "error")
            return
        from . import lifecycle
        result = lifecycle.schedule_uninstall(self.paths, keep_results=not remove_data)
        if result.get("status") == "scheduled":
            self._notice(result.get("message", "已安排卸载，应用即将退出。"))
            QApplication.quit()
        else:
            self._notice(result.get("message", "无法安排卸载。"), "error")

    @Slot()
    def checkUpdates(self):
        if self._updates_phase or self._maintenance_busy:
            return
        configuration = self.paths.release_configuration
        state = self._updater.status() if self._updater else {}
        app_unconfigured = (not self._updater
                            or state.get("status") == "unconfigured" and not state.get("feed_url")
                            or state.get("status") == "unavailable" and self._updater.bundle_path is None)
        if not configuration.catalog_url and not configuration.public_key and app_unconfigured:
            return
        self._updates_phase = "checking"
        self._update_status = "正在检查更新…"
        self._update_components = self._update_candidate = self._update_plan = None
        if self._updater:
            self._updater.check_for_updates()
        task = MaintenanceTask(self._check_component_releases, self.paths.home,
                               configuration=configuration)
        task.setParent(self)
        self._update_check_result = None
        task.progress.connect(lambda _fraction, message: self._set_update_status(message))
        task.result.connect(self._update_check_received)
        task.problem.connect(self._update_check_failed)
        task.finished.connect(self._update_check_finished)
        task.finished.connect(task.deleteLater)
        self._update_check_thread = task
        task.start()
        self.changed.emit()

    @Slot(object)
    def _update_check_received(self, result):
        self._update_check_result = result

    @Slot(str)
    def _update_check_failed(self, message):
        self._update_check_result = {"status": "failed", "message": message}

    @Slot()
    def _update_check_finished(self):
        self._update_check_thread = None
        self._update_components = self._update_check_result or {"status": "failed", "message": "更新检查未完成。"}
        self._update_check_result = None
        self._poll_updates()

    @staticmethod
    def _check_component_releases(home, configuration=None, progress=None):
        from .update_manager import check_component_updates, discard_staged_updates
        pending = Path(home) / "updates" / "pending.json"
        if pending.is_file():
            if pending.is_symlink():
                raise ValueError("更新记录不能是符号链接。")
            previous = json.loads(pending.read_text())
            if not isinstance(previous, dict):
                raise ValueError("更新记录格式无效。")
            if previous.get("plan"):
                result = discard_staged_updates(home, previous["plan"])
                if result["status"] != "discarded":
                    return result
            pending.unlink()
        return check_component_updates(home, configuration=configuration, progress=progress)

    def _installation_idle(self):
        return not (self._busy or self._cleanup_pending or self._maintenance_busy
                    or self._counting.available or self._counting.busy or not self._counting.drained)

    def _set_update_status(self, message):
        if self._update_status != message:
            self._update_status = message
            self.changed.emit()

    def _finish_updates(self, message):
        self._updates_phase = ""
        if self._update_lock:
            self._update_lock.__exit__(None, None, None)
            self._update_lock = None
        self._set_update_status(message)
        self.changed.emit()

    def _pending_update_file(self):
        folder = self.paths.home / "updates"
        self.paths.check_owned_directory(folder)
        return folder / "pending.json"

    def _save_pending_update(self, expected_build):
        path = self._pending_update_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, {"schema_version": 1, "expected_host_build": expected_build,
                          "plan": self._update_plan})

    def _resume_pending_update(self):
        path = self._pending_update_file()
        if not path.is_file():
            return
        try:
            if path.is_symlink():
                raise ValueError("更新记录不能是符号链接。")
            pending = json.loads(path.read_text())
            if not isinstance(pending, dict) or pending.get("schema_version") != 1:
                raise ValueError("更新记录格式无效。")
            if pending.get("expected_host_build") != self._app_build:
                self._set_update_status("应用更新尚未完成，请重新检查更新。")
                return
            self._update_plan = pending.get("plan")
            if self._update_plan:
                self._updates_phase = "waiting_apply"
                self._set_update_status("正在完成模块更新…")
            else:
                path.unlink()
                self._finish_updates("应用更新完成。")
        except (OSError, ValueError, TypeError) as exc:
            self._finish_updates("无法继续更新：" + str(exc))

    def _poll_updates(self):
        if self._closing:
            return
        state = self._updater.status() if self._updater else {"status": "unavailable"}
        if self._updates_phase == "checking" and self._update_components is not None:
            if state.get("session_in_progress"):
                return
            result = self._update_components
            if result["status"] not in ("updates_available", "up_to_date", "unconfigured"):
                self._finish_updates(result.get("message", "更新检查未完成。"))
                return
            if state.get("status") == "failed":
                self._finish_updates(state.get("message", "应用更新检查未完成。"))
                return
            self._update_plan = result.get("plan") if result.get("updates") else None
            self._update_candidate = dict(state) if state.get("status") == "available" else None
            required_build = max((int(item.get("minimum_host_build", 0)) for item in result.get("updates", [])), default=0)
            target_build = self._update_candidate.get("available_build") if self._update_candidate else self._app_build
            if not str(target_build).isdigit() or int(target_build) < required_build:
                self._finish_updates("模块更新需要更新的主程序版本。")
                return
            if not self._update_plan and not self._update_candidate:
                if state.get("status") == "unavailable":
                    self._finish_updates(state.get("message", "应用更新信息不可用。"))
                elif result["status"] == "unconfigured":
                    self._finish_updates(result.get("message", "更新检查未完成。"))
                elif state.get("status") == "unconfigured":
                    self._finish_updates(state.get("message", "应用更新信息不可用。"))
                else:
                    self._finish_updates("当前应用和已安装模块均为最新可用版本。")
                return
            self._updates_phase = "waiting_stage"
        if self._updates_phase in ("waiting_stage", "waiting_apply"):
            if not self._installation_idle():
                self._set_update_status("等待当前任务结束。")
                return
            from .update_manager import stage_component_updates, apply_staged_updates
            if self._updates_phase == "waiting_apply":
                self._updates_phase = "applying"
                self._run_maintenance(apply_staged_updates, self.paths.home, self._update_plan,
                                      configuration=self.paths.release_configuration, operation="updates_apply")
            elif self._update_plan:
                self._updates_phase = "staging"
                self._run_maintenance(stage_component_updates, self.paths.home, self._update_plan,
                                      configuration=self.paths.release_configuration, operation="updates_stage")
            else:
                self._updates_phase = "waiting_app"
        elif self._updates_phase == "waiting_app":
            if state.get("status") == "failed":
                self._finish_updates(state.get("message", "应用更新检查未完成。"))
            elif self._installation_idle() and not state.get("session_in_progress") and state.get("can_check"):
                self._start_app_update()
        elif self._updates_phase == "app_install":
            if state.get("status") in ("failed", "unconfigured", "unavailable"):
                self._finish_updates(state.get("message", "应用更新未完成。"))
                return
            if state.get("status") == "completed":
                self._finish_updates(state.get("message", "应用更新完成。"))
                return
            idle = self._installation_idle()
            if idle and not self._update_lock:
                try:
                    self._update_lock = maintenance.component_maintenance_lock(self.paths.home)
                    self._update_lock.__enter__()
                except maintenance.ComponentBusy:
                    self._update_lock = None
                    idle = False
            self._updater.set_installation_busy(not idle)
            self._set_update_status(state.get("message", "正在更新应用…"))

    def _start_app_update(self):
        candidate = self._update_candidate
        if not candidate or not self._updater:
            self._finish_updates("应用更新信息不可用。")
            return
        try:
            self._save_pending_update(candidate["available_build"])
            state = self._updater.install_update(candidate["available_build"], candidate["feed_url"],
                                                 candidate["host_build"], installation_busy=True)
        except (OSError, ValueError, RuntimeError) as exc:
            self._finish_updates("无法开始应用更新：" + str(exc))
            return
        if state.get("status") in ("failed", "unconfigured", "unavailable"):
            self._finish_updates(state.get("message", "应用更新未完成。"))
            return
        self._updates_phase = "app_install"
        self._set_update_status(state.get("message", "正在下载应用更新…"))

    def _update_operation_done(self, operation, result):
        if operation == "updates_stage":
            if result.get("status") != "staged":
                self._finish_updates(result.get("message", "组件下载未完成。"))
            else:
                self._update_plan = result["plan"]
                if self._update_candidate:
                    self._updates_phase = "waiting_app"
                else:
                    try:
                        self._save_pending_update(self._app_build)
                    except (OSError, ValueError) as exc:
                        self._finish_updates("无法保存更新记录：" + str(exc))
                        return
                    self._updates_phase = "waiting_apply"
        elif operation == "updates_apply":
            self._refresh_modules()
            if result.get("status") == "ready":
                self._pending_update_file().unlink(missing_ok=True)
                self._update_plan = None
            self._finish_updates(result.get("message", "组件更新未完成。"))
        self.changed.emit()

    @Slot()
    def clearDisposableCache(self):
        if not self._maintenance_allowed():
            return
        if self._counting.available or not self._counting.drained:
            self._notice("请先完成当前计数审核，再清理缓存。", "info")
            return
        from .task_storage import clear_disposable_cache
        self._run_maintenance(clear_disposable_cache, self.paths,
                              current_sources=self._current_sources(), operation="clear_cache")

    @Slot()
    def requestQuit(self):
        if self._counting.confirmationInProgress:
            QMessageBox.information(None, self.locale.translate("正在保存计数结果"),
                                    self.locale.translate("请等待计数结果保存完成后退出。"))
            return
        if self._maintenance_busy:
            QMessageBox.information(None, self.locale.translate("正在维护"), self.locale.translate("请等待组件操作结束后退出。"))
            return
        if self._busy:
            answer = QMessageBox.question(None, self.locale.translate("退出 Project Leap"), self.locale.translate("退出将取消当前任务。是否取消任务并退出？"))
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._closing = True
            self.cancelAnalysis()
            self._quit_after_cancel = True
            QTimer.singleShot(300, self._quit_when_idle)
            return
        self._closing = True
        if not self._counting.close():
            self._closing = False
            self._message = self._counting.message
            self.changed.emit()
            return
        self._finish_quit()

    def _finish_quit(self):
        self._resource_monitor.shutdown()
        self._history_pending = None
        if self._history_task:
            self._history_task.cancel.set()
        if not self._counting.drained or self._maintenance_busy or self._update_check_thread is not None:
            QTimer.singleShot(50, self._finish_quit)
            return
        if not self._resource_monitor.isDrained() or self._history_pool.activeThreadCount():
            QTimer.singleShot(50, self._finish_quit)
            return
        for session in self._sessions.values():
            if session.status in ("waiting_review", "completed", "failed", "cancelled", "rejected", "interrupted"):
                # Preserve already-observed final metadata without synchronously
                # scanning a potentially large console when the user quits.
                self._persist_detail_summary(session)
            self._remove_input_preview(session)
        self._updates_phase = ""
        self._closing = False
        QApplication.quit()

    def _quit_when_idle(self):
        if self._busy or self._counting.confirmationInProgress:
            QTimer.singleShot(300, self._quit_when_idle)
        else:
            self.requestQuit()
