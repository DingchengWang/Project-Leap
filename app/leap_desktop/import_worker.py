"""Independent TIFF worker: progress and terminal JSON objects on stdout.

``--inspect PATH`` prints an inspect_image dict on success. Optional
``--preview-path ABSOLUTE.png`` adds a single-plane cache thumbnail; preview
failure adds preview_error while retaining the successful metadata response.
``--request PATH``
accepts source_path, destination_dir, channel_roles and result_path, plus optional
profile, series_index, time_index, cancel_path and run_id. A split result file and
stdout contain schema_version/status plus the importer result or error details.
Exit codes: 0 completed, 1 failed, 130 cancelled. This worker never imports Qt.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import tempfile


class ImportWorkerError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@contextmanager
def _component_usage_lock():
    """Own the shared component lock even if the launching GUI has exited."""
    value = os.environ.get("PROJECT_LEAP_COMPONENT_LOCK")
    if not value:
        yield
        return
    path = Path(value)
    if not path.is_absolute():
        raise ImportWorkerError("invalid_component_lock", "组件锁必须为绝对路径。")
    try:
        # The application creates this persistent lock file. Opening read-only
        # neither creates a replacement inode nor writes to any source directory.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ImportWorkerError("component_lock_unavailable", f"无法打开应用组件锁：{exc}") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ImportWorkerError("component_busy", "组件正在安装、修复或更新；请在维护完成后重新导入。") from exc
        yield
    finally:
        os.close(descriptor)


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".import-result-",
                                         suffix=".json", dir=str(path.parent), delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _parse_arguments(argv):
    parser = argparse.ArgumentParser(description="Project Leap TIFF import worker")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect")
    mode.add_argument("--request")
    parser.add_argument("--preview-path")
    return parser.parse_args(argv)


def _register_worker(request_path):
    """Persist ownership before loading scientific dependencies or decoding TIFF."""
    path = Path(request_path)
    if not path.is_absolute():
        raise ImportWorkerError("invalid_request", "request 文件必须为绝对路径。")
    try:
        path = path.resolve(strict=True)
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImportWorkerError("invalid_request", f"无法读取导入请求：{exc}") from exc
    source_value = request.get("source_path") if isinstance(request, dict) else None
    if not isinstance(source_value, str) or not Path(source_value).is_absolute():
        raise ImportWorkerError("invalid_request", "source_path 必须为绝对路径。")
    source = Path(source_value).resolve()
    target = path.parent / "worker.json"
    if target.parent == source.parent or source.parent in target.parents:
        raise ImportWorkerError("source_directory_read_only", "导入任务及进程登记必须位于原图目录之外。")
    try:
        _write_json(target, {"pid": os.getpid(), "kind": "import", "request_path": str(path)})
    except OSError as exc:
        raise ImportWorkerError("worker_registration_failed", f"无法保存导入进程登记：{exc}") from exc


def _run(args):
    # Component files are imported only after the worker owns its shared lock.
    from .importer import ImportCancelled, ImportImageError, create_preview, inspect_image, split_image

    request = {}
    result_path = None
    cancelled = False
    sequence = 0
    def cancel_signal(signum, frame):
        nonlocal cancelled
        cancelled = True
    signal.signal(signal.SIGINT, cancel_signal)
    signal.signal(signal.SIGTERM, cancel_signal)
    try:
        if args.inspect:
            result = inspect_image(args.inspect)
            if args.preview_path:
                try:
                    result.update(create_preview(args.inspect, args.preview_path))
                except Exception as exc:
                    result["preview_error"] = {"code": getattr(exc, "code", "preview_failed"), "message": str(exc)}
        else:
            if args.preview_path:
                raise ImportImageError("invalid_request", "--preview-path 只能与 --inspect 一起使用。")
            request = json.loads(Path(args.request).read_text(encoding="utf-8"))
            if not isinstance(request, dict):
                raise ImportImageError("invalid_request", "请求必须是 JSON 对象。")
            for field in ("source_path", "destination_dir", "channel_roles", "result_path"):
                if field not in request:
                    raise ImportImageError("invalid_request", "请求缺少字段：" + field)
            for field in ("source_path", "destination_dir", "result_path"):
                if not isinstance(request[field], str) or not Path(request[field]).is_absolute():
                    raise ImportImageError("invalid_request", field + " 必须为绝对路径。")
            source = Path(request["source_path"]).expanduser().resolve(strict=True)
            candidate = Path(request["result_path"]).expanduser().resolve()
            if candidate == source or source.parent == candidate.parent or source.parent in candidate.parents:
                raise ImportImageError("source_directory_read_only", "result_path 必须位于原图目录之外。")
            result_path = candidate
            cancel_path = Path(request.get("cancel_path", Path(args.request).parent / "cancel.request"))
            def progress(event):
                nonlocal sequence
                sequence += 1
                payload = {"schema_version": 1, "run_id": request.get("run_id", ""),
                           "sequence": sequence, "type": "progress", "timestamp": datetime.now(timezone.utc).isoformat(), **event}
                print(json.dumps(payload, ensure_ascii=False, allow_nan=False), flush=True)
            imported = split_image(request["source_path"], request["destination_dir"], request["channel_roles"],
                                   profile=request.get("profile"), series_index=request.get("series_index"),
                                   time_index=request.get("time_index"), progress=progress,
                                   cancelled=lambda: cancelled or cancel_path.exists())
            result = {"schema_version": 1, "type": "completed", "status": "completed", **imported}
        exit_code = 0
    except (ImportCancelled, KeyboardInterrupt):
        result = {"schema_version": 1, "type": "cancelled", "status": "cancelled", "error_code": "cancelled", "message": "图像导入已取消。"}
        exit_code = 130
    except Exception as exc:
        result = {"schema_version": 1, "type": "failed", "status": "failed", "error_code": getattr(exc, "code", "import_failed"),
                  "message": str(exc)}
        exit_code = 1
    if args.request:
        result.update({"run_id": request.get("run_id", "") if isinstance(request, dict) else "",
                       "sequence": sequence + 1, "timestamp": datetime.now(timezone.utc).isoformat()})
    if result_path is not None:
        try:
            _write_json(result_path, result)
        except OSError as exc:
            result = {"schema_version": 1, "type": "failed", "status": "failed", "error_code": "result_write_failed",
                      "message": str(exc), "run_id": request.get("run_id", ""), "sequence": sequence + 1,
                      "timestamp": datetime.now(timezone.utc).isoformat()}
            exit_code = 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    return exit_code


def main(argv=None):
    args = _parse_arguments(argv)
    try:
        with _component_usage_lock():
            if args.request:
                _register_worker(args.request)
            return _run(args)
    except ImportWorkerError as exc:
        result = {"schema_version": 1, "type": "failed", "status": "failed", "error_code": exc.code,
                  "message": str(exc), "timestamp": datetime.now(timezone.utc).isoformat()}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
