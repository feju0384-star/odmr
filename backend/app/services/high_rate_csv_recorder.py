from __future__ import annotations

import csv
import math
import queue
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CSV_COLUMNS: tuple[str, ...] = (
    "timestamp_utc",
    "timestamp_local",
    "elapsed_s",
    "cycle_index",
    "valid",
    "invalid_reason",
    "current_a",
    "current_sigma_a",
    "f_left_hz",
    "f_right_hz",
    "delta_f_hz",
    "delta_f_sigma_hz",
    "common_mode_hz",
    "tracking_target",
    "global_state",
    "left_state",
    "right_state",
    "left_quality",
    "right_quality",
    "dc_independent",
    "left_error_hz",
    "right_error_hz",
    "left_q_hz",
    "right_q_hz",
    "left_setpoint_hz",
    "right_setpoint_hz",
    "relock_count",
    "lost_lock_count",
    "measured_update_rate_hz",
    "single_acquisition_p50_ms",
    "full_cycle_p50_ms",
    "stream_sample_rate_hz",
    "stream_poll_p50_ms",
    "stream_poll_p95_ms",
    "stream_sample_age_ms",
    "stream_sequence",
)


def _safe_filename(value: str, fallback: str) -> str:
    normalized = re.sub(
        r"[^0-9A-Za-z\u4e00-\u9fff._-]+",
        "_",
        str(value).strip(),
    )
    return normalized.strip("._-")[:80] or fallback


def _finite(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


@dataclass
class _FlushCommand:
    completed: threading.Event


_SENTINEL = object()


class HighRateCsvRecorder:
    """每个输出点入队，独立线程按点数/时间批量写CSV。"""

    def __init__(
        self,
        *,
        base_dir: Path,
        label: str,
        batch_points: int,
        flush_interval_s: float,
        queue_capacity: int,
        filename_prefix: str = "streaming_current",
    ) -> None:
        now = datetime.now().astimezone()
        self.started_datetime = now
        self.started_at = now.isoformat(timespec="milliseconds")
        self.session_id = f"{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
        session_name = _safe_filename(
            f"{self.session_id}_{label}" if str(label).strip() else self.session_id,
            self.session_id,
        )
        self.session_dir = Path(base_dir) / session_name
        self.session_dir.mkdir(parents=True, exist_ok=False)
        safe_prefix = _safe_filename(filename_prefix, "current_tracking")
        self.csv_path = self.session_dir / f"{safe_prefix}_{session_name}.csv"
        self.batch_points = max(1, int(batch_points))
        self.flush_interval_s = max(0.05, float(flush_interval_s))
        self.queue_capacity = max(self.batch_points * 2, int(queue_capacity))
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=self.queue_capacity)
        self._state_lock = threading.RLock()
        self._file_lock = threading.RLock()
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="high-rate-current-csv-writer",
            daemon=True,
        )
        self.status = "recording"
        self.ended_at: str | None = None
        self.enqueued_rows = 0
        self.rows_written = 0
        self.valid_rows = 0
        self.dropped_rows = 0
        self.write_batches = 0
        self.maximum_queue_depth = 0
        self.last_write_batch_size = 0
        self.last_write_duration_ms = 0.0
        self.writer_error = ""
        self.closed = False
        self._thread.start()

    def _point_to_row(self, point: dict[str, Any]) -> dict[str, Any]:
        elapsed_s = _finite(point.get("elapsed_s"))
        timestamp = (
            self.started_datetime + timedelta(seconds=elapsed_s)
            if elapsed_s is not None
            else datetime.now().astimezone()
        )
        timing = dict(point.get("timing", {}) or {})
        stream = dict(point.get("stream", {}) or {})
        return {
            "timestamp_utc": timestamp.astimezone(timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "timestamp_local": timestamp.isoformat(timespec="milliseconds"),
            "elapsed_s": elapsed_s,
            "cycle_index": int(point.get("cycle_index", 0) or 0),
            "valid": bool(point.get("valid")),
            "invalid_reason": str(point.get("invalid_reason") or ""),
            "current_a": _finite(point.get("estimated_current_a")),
            "current_sigma_a": _finite(point.get("current_sigma_a")),
            "f_left_hz": _finite(point.get("left_frequency_hz")),
            "f_right_hz": _finite(point.get("right_frequency_hz")),
            "delta_f_hz": _finite(point.get("splitting_hz")),
            "delta_f_sigma_hz": _finite(point.get("delta_f_sigma_hz")),
            "common_mode_hz": _finite(point.get("common_mode_hz")),
            "tracking_target": str(point.get("tracking_target") or ""),
            "global_state": str(point.get("global_state") or ""),
            "left_state": str(point.get("left_state") or ""),
            "right_state": str(point.get("right_state") or ""),
            "left_quality": _finite(point.get("left_quality")),
            "right_quality": _finite(point.get("right_quality")),
            "dc_independent": bool(point.get("dc_independent")),
            "left_error_hz": _finite(point.get("left_error_hz")),
            "right_error_hz": _finite(point.get("right_error_hz")),
            "left_q_hz": _finite(point.get("left_q_hz")),
            "right_q_hz": _finite(point.get("right_q_hz")),
            "left_setpoint_hz": _finite(point.get("left_setpoint_hz")),
            "right_setpoint_hz": _finite(point.get("right_setpoint_hz")),
            "relock_count": int(point.get("relock_count", 0) or 0),
            "lost_lock_count": int(point.get("lost_lock_count", 0) or 0),
            "measured_update_rate_hz": _finite(
                timing.get("measured_update_rate_hz")
            ),
            "single_acquisition_p50_ms": _finite(
                timing.get("acquisition_median_ms")
            ),
            "full_cycle_p50_ms": _finite(
                timing.get("cycle_median_ms")
            ),
            "stream_sample_rate_hz": _finite(
                stream.get("measured_sample_rate_hz")
            ),
            "stream_poll_p50_ms": _finite(stream.get("poll_p50_ms")),
            "stream_poll_p95_ms": _finite(stream.get("poll_p95_ms")),
            "stream_sample_age_ms": _finite(
                point.get("stream_sample_age_ms")
            ),
            "stream_sequence": int(point.get("stream_sequence", 0) or 0),
        }

    def enqueue(self, point: dict[str, Any]) -> bool:
        if self.closed or self.writer_error:
            return False
        row = self._point_to_row(point)
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            with self._state_lock:
                self.dropped_rows += 1
                self.writer_error = (
                    "电流CSV写入队列已满；为保证数据完整性应停止测量。"
                )
            return False
        with self._state_lock:
            self.enqueued_rows += 1
            self.maximum_queue_depth = max(
                self.maximum_queue_depth,
                self._queue.qsize(),
            )
        return True

    @staticmethod
    def _csv_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: (
                "true"
                if row.get(key) is True
                else "false"
                if row.get(key) is False
                else ""
                if row.get(key) is None
                else row.get(key)
            )
            for key in CSV_COLUMNS
        }

    def _write_batch(
        self,
        handle: Any,
        writer: csv.DictWriter,
        rows: list[dict[str, Any]],
        *,
        force_sync: bool = False,
    ) -> None:
        if not rows:
            return
        started_s = time.perf_counter()
        with self._file_lock:
            writer.writerows(self._csv_row(row) for row in rows)
            handle.flush()
            if force_sync:
                try:
                    import os

                    os.fsync(handle.fileno())
                except OSError:
                    pass
        duration_ms = (time.perf_counter() - started_s) * 1000.0
        with self._state_lock:
            self.rows_written += len(rows)
            self.valid_rows += sum(1 for row in rows if row.get("valid"))
            self.write_batches += 1
            self.last_write_batch_size = len(rows)
            self.last_write_duration_ms = duration_ms

    def _writer_loop(self) -> None:
        pending: list[dict[str, Any]] = []
        next_flush_s = time.perf_counter() + self.flush_interval_s
        last_fsync_s = time.perf_counter()
        try:
            with self.csv_path.open(
                "w",
                newline="",
                encoding="utf-8-sig",
                buffering=1024 * 1024,
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                handle.flush()
                stopping = False
                while not stopping:
                    timeout_s = max(0.0, next_flush_s - time.perf_counter())
                    try:
                        item = self._queue.get(timeout=timeout_s)
                    except queue.Empty:
                        item = None
                    if item is _SENTINEL:
                        stopping = True
                        self._queue.task_done()
                    elif isinstance(item, _FlushCommand):
                        self._write_batch(handle, writer, pending)
                        pending.clear()
                        next_flush_s = time.perf_counter() + self.flush_interval_s
                        item.completed.set()
                        self._queue.task_done()
                    elif item is not None:
                        pending.append(item)
                        self._queue.task_done()

                    now_s = time.perf_counter()
                    due = now_s >= next_flush_s
                    full = len(pending) >= self.batch_points
                    if pending and (due or full or stopping):
                        force_sync = stopping or now_s - last_fsync_s >= 10.0
                        self._write_batch(
                            handle,
                            writer,
                            pending,
                            force_sync=force_sync,
                        )
                        pending.clear()
                        next_flush_s = now_s + self.flush_interval_s
                        if force_sync:
                            last_fsync_s = now_s
                    elif due:
                        next_flush_s = now_s + self.flush_interval_s
        except Exception as exc:
            with self._state_lock:
                self.writer_error = str(exc)
        finally:
            with self._state_lock:
                self.closed = True

    def snapshot(self, timeout_s: float = 5.0) -> Path:
        if not self.closed:
            command = _FlushCommand(threading.Event())
            self._queue.put(command, timeout=max(0.1, timeout_s))
            if not command.completed.wait(timeout=max(0.1, timeout_s)):
                raise TimeoutError("等待CSV批量写线程刷新超时。")
        snapshot_path = self.session_dir / f".snapshot_{uuid.uuid4().hex}.csv"
        with self._file_lock:
            shutil.copyfile(self.csv_path, snapshot_path)
        return snapshot_path

    def finalize(self, status: str) -> dict[str, Any]:
        if not self.closed:
            self._queue.put(_SENTINEL)
            self._thread.join(timeout=30.0)
            if self._thread.is_alive():
                with self._state_lock:
                    self.writer_error = "CSV写入线程在30秒内未完成收尾。"
        with self._state_lock:
            self.status = "error" if self.writer_error else str(status)
            self.ended_at = datetime.now().astimezone().isoformat(
                timespec="milliseconds"
            )
        return self.status_dict()

    def status_dict(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "session_id": self.session_id,
                "status": self.status,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "csv_path": str(self.csv_path.resolve()),
                "csv_size_bytes": (
                    self.csv_path.stat().st_size
                    if self.csv_path.exists()
                    else 0
                ),
                "enqueued_rows": self.enqueued_rows,
                "rows_written": self.rows_written,
                "valid_rows": self.valid_rows,
                "dropped_rows": self.dropped_rows,
                "queue_depth": self._queue.qsize(),
                "maximum_queue_depth": self.maximum_queue_depth,
                "queue_capacity": self.queue_capacity,
                "write_batches": self.write_batches,
                "last_write_batch_size": self.last_write_batch_size,
                "average_rows_per_batch": (
                    self.rows_written / self.write_batches
                    if self.write_batches
                    else 0.0
                ),
                "last_write_duration_ms": self.last_write_duration_ms,
                "batch_points": self.batch_points,
                "flush_interval_s": self.flush_interval_s,
                "writer_error": self.writer_error,
                "download_available": self.rows_written > 0,
            }


class HighRateCsvRecordingManager:
    def __init__(
        self,
        base_dir: Path,
        *,
        filename_prefix: str = "streaming_current",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.filename_prefix = str(filename_prefix)
        self.lock = threading.RLock()
        self.active: HighRateCsvRecorder | None = None
        self.sessions: dict[str, HighRateCsvRecorder] = {}

    def start(
        self,
        *,
        label: str,
        batch_points: int,
        flush_interval_s: float,
        queue_capacity: int,
    ) -> dict[str, Any]:
        with self.lock:
            if self.active is not None and not self.active.closed:
                self.active.finalize("superseded")
            recorder = HighRateCsvRecorder(
                base_dir=self.base_dir,
                label=label,
                batch_points=batch_points,
                flush_interval_s=flush_interval_s,
                queue_capacity=queue_capacity,
                filename_prefix=self.filename_prefix,
            )
            self.active = recorder
            self.sessions[recorder.session_id] = recorder
            return recorder.status_dict()

    def enqueue(self, point: dict[str, Any]) -> bool:
        with self.lock:
            recorder = self.active
        return bool(recorder and recorder.enqueue(point))

    def finish(self, status: str) -> dict[str, Any] | None:
        with self.lock:
            recorder = self.active
            self.active = None
        return recorder.finalize(status) if recorder is not None else None

    def status(self, session_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            recorder = (
                self.sessions.get(session_id)
                if session_id
                else self.active
                or (
                    next(reversed(self.sessions.values()))
                    if self.sessions
                    else None
                )
            )
        return recorder.status_dict() if recorder is not None else {
            "status": "idle",
            "session_id": None,
            "rows_written": 0,
            "enqueued_rows": 0,
            "dropped_rows": 0,
            "queue_depth": 0,
            "download_available": False,
        }

    def export(self, session_id: str | None = None) -> tuple[Path, bool]:
        with self.lock:
            recorder = (
                self.sessions.get(session_id)
                if session_id
                else self.active
                or (
                    next(reversed(self.sessions.values()))
                    if self.sessions
                    else None
                )
            )
        if recorder is None:
            raise FileNotFoundError("没有可下载的电流CSV记录。")
        if recorder.closed:
            return recorder.csv_path, False
        return recorder.snapshot(), True
