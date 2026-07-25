from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Callable

from backend.app.schemas.streaming_current import StreamingCurrentTrackingRequest
from backend.app.services.high_rate_csv_recorder import (
    HighRateCsvRecordingManager,
)
from backend.app.services.zurich_stream_buffer import (
    StreamSample,
    ZurichContinuousSampleBuffer,
)


class _StreamingInstrumentAdapter:
    """为既有PID核心提供流式采样接口，同时隔离其运行状态和记录器。"""

    def __init__(
        self,
        *,
        manager: Any,
        buffer: ZurichContinuousSampleBuffer,
        request: StreamingCurrentTrackingRequest,
        channel_index: int,
        runtime_filter_diagnostics: dict[str, Any],
    ) -> None:
        self._manager = manager
        self._buffer = buffer
        self._request = request
        self._channel_index = int(channel_index)
        self._settle_s = manager._measurement_settle_s(
            channel_index,
            request.tracking_settle_ms,
        )
        self._timestamp_margin_s = request.stream_timestamp_margin_ms / 1000.0
        self._read_timeout_s = request.stream_poll_timeout_ms / 1000.0
        self._stable_after_s = time.perf_counter()
        self._last_device_timestamp_by_channel: dict[int, float] = {}
        self._latest_sample: StreamSample | None = None
        self._runtime_filter_diagnostics = dict(runtime_filter_diagnostics)
        self._diagnostics_cache: dict[str, Any] = {}
        self._diagnostics_cache_s = 0.0
        self.measurement_state: dict[str, Any] = {
            "running": True,
            "mode": "streaming_current_tracking",
            "status": "starting",
            "tracking": {},
        }
        self.current_tracking_recordings = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)

    def set_microwave_frequency_fast(self, frequency_hz: float) -> bool:
        ok = self._manager.set_microwave_frequency_fast(frequency_hz)
        if ok:
            self._stable_after_s = (
                time.perf_counter()
                + self._settle_s
                + self._timestamp_margin_s
            )
        return ok

    def read_lockin_filter_runtime_diagnostics(
        self,
        channel_index: int,
    ) -> dict[str, Any]:
        return dict(self._runtime_filter_diagnostics)

    def _read_stream_sample(
        self,
        channel_index: int,
    ) -> tuple[dict[str, Any], dict[str, float]]:
        started_s = time.perf_counter()
        previous_device_timestamp_s = self._last_device_timestamp_by_channel.get(
            int(channel_index)
        )
        sample = self._buffer.wait_for_sample(
            channel_index=int(channel_index),
            not_before_host_s=self._stable_after_s,
            after_device_timestamp_s=previous_device_timestamp_s,
            timeout_s=self._read_timeout_s,
        )
        completed_s = time.perf_counter()
        if sample is None:
            return (
                {
                    "x_v": math.nan,
                    "y_v": math.nan,
                    "r_v": math.nan,
                    "channel_index": int(channel_index),
                },
                {
                    "lock_wait_ms": 0.0,
                    "lockin_read_ms": (completed_s - started_s) * 1000.0,
                },
            )
        self._last_device_timestamp_by_channel[int(channel_index)] = (
            sample.device_timestamp_s
        )
        self._latest_sample = sample
        signal = sample.as_signal()
        signal["stream_sample_age_ms"] = max(
            0.0,
            (completed_s - sample.host_timestamp_s) * 1000.0,
        )
        return signal, {
            "lock_wait_ms": 0.0,
            "lockin_read_ms": (completed_s - started_s) * 1000.0,
        }

    def read_lockin_sample_for_channel_timed(
        self,
        channel_index: int,
    ) -> tuple[dict[str, Any], dict[str, float]]:
        return self._read_stream_sample(channel_index)

    def read_lockin_sample_for_channel(
        self,
        channel_index: int,
    ) -> dict[str, Any]:
        return self._read_stream_sample(channel_index)[0]

    def stream_point_metadata(self) -> dict[str, Any]:
        sample = self._latest_sample
        now_s = time.perf_counter()
        return {
            "stream_sequence": sample.sequence if sample is not None else 0,
            "stream_device_timestamp_s": (
                sample.device_timestamp_s if sample is not None else None
            ),
            "stream_sample_age_ms": (
                max(0.0, (now_s - sample.host_timestamp_s) * 1000.0)
                if sample is not None
                else None
            ),
        }

    def stream_diagnostics(self, maximum_age_s: float = 1.0) -> dict[str, Any]:
        now_s = time.perf_counter()
        if (
            not self._diagnostics_cache
            or now_s - self._diagnostics_cache_s >= maximum_age_s
        ):
            self._diagnostics_cache = self._buffer.diagnostics()
            self._diagnostics_cache_s = now_s
        return dict(self._diagnostics_cache)


class StreamingCurrentTrackingRuntime:
    MODE = "streaming_current_tracking"

    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.recordings = HighRateCsvRecordingManager(
            Path(__file__).resolve().parents[3]
            / "data"
            / "streaming_current"
        )

    def begin(self, request: StreamingCurrentTrackingRequest) -> None:
        channel_index = self.manager._resolve_measurement_channel_index(
            request.channel_index
        )
        self.manager.odmr_stop_event.clear()
        self.manager.measurement_state.update(
            {
                "running": True,
                "mode": self.MODE,
                "status": "准备Zurich连续订阅",
                "cancel_requested": False,
                "progress": 0.0,
                "current_point": 0,
                "current_frequency_hz": 0.0,
                "current_value": 0.0,
                "last_streaming_current_request": {
                    **request.model_dump(),
                    "channel_index": channel_index,
                },
            }
        )

    def finish(
        self,
        request: StreamingCurrentTrackingRequest,
        result: dict[str, Any],
    ) -> None:
        status = str(result.get("status", "completed"))
        last_point = dict(result.get("last_point", {}) or {})
        self.manager.measurement_state.update(
            {
                "running": False,
                "mode": "idle",
                "status": status,
                "cancel_requested": False,
                "progress": 1.0 if status == "completed" else 0.0,
                "current_point": int(last_point.get("cycle_index", 0) or 0),
                "current_frequency_hz": float(
                    0.5
                    * (
                        float(last_point.get("left_frequency_hz", 0.0) or 0.0)
                        + float(
                            last_point.get("right_frequency_hz", 0.0) or 0.0
                        )
                    )
                ),
                "current_value": float(
                    last_point.get("splitting_hz", 0.0) or 0.0
                ),
                "last_streaming_current_request": request.model_dump(),
                "last_streaming_current_result": result,
            }
        )

    def _check_hardware(
        self,
        request: StreamingCurrentTrackingRequest,
    ) -> tuple[int, list[int]]:
        if self.manager.lockin_device is None or self.manager.lockin_session is None:
            raise RuntimeError("锁相未连接，无法运行流式双峰跟踪。")
        if (
            self.manager.microwave_resource is None
            or not self.manager.microwave_state.get("connected")
        ):
            raise RuntimeError("微波源未连接，无法运行流式双峰跟踪。")
        channel_index = self.manager._resolve_measurement_channel_index(
            request.channel_index
        )
        channel_indices = [channel_index]
        if request.independent_dc_channel_index >= 0:
            dc_channel_index = self.manager._resolve_measurement_channel_index(
                request.independent_dc_channel_index
            )
            channel_indices.append(dc_channel_index)
        return channel_index, sorted(set(channel_indices))

    def run(
        self,
        request: StreamingCurrentTrackingRequest,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        channel_index, channel_indices = self._check_hardware(request)
        manager = self.manager
        runtime_filter_diagnostics: dict[str, Any] = {}
        try:
            runtime_filter_diagnostics = (
                manager.read_lockin_filter_runtime_diagnostics(channel_index)
            )
        except Exception:
            pass

        recording_status: dict[str, Any] | None = None
        if request.record_enabled:
            recording_status = self.recordings.start(
                label=request.record_label,
                batch_points=request.record_batch_points,
                flush_interval_s=request.record_flush_interval_s,
                queue_capacity=request.record_queue_capacity,
            )

        buffer = ZurichContinuousSampleBuffer(
            manager=manager,
            channel_indices=channel_indices,
            sample_rate_hz=request.stream_sample_rate_hz,
            poll_window_s=request.stream_poll_window_ms / 1000.0,
            poll_timeout_s=request.stream_poll_timeout_ms / 1000.0,
        )
        last_point: dict[str, Any] = {}
        last_recording_event_s = 0.0
        try:
            buffer.start(warmup_timeout_s=request.stream_warmup_timeout_s)
            adapter = _StreamingInstrumentAdapter(
                manager=manager,
                buffer=buffer,
                request=request,
                channel_index=channel_index,
                runtime_filter_diagnostics=runtime_filter_diagnostics,
            )

            def publish(source_event: dict[str, Any]) -> None:
                nonlocal last_point, last_recording_event_s
                event = dict(source_event)
                event_type = str(event.get("type", ""))
                if event_type.startswith("current_tracking"):
                    event["type"] = event_type.replace(
                        "current_tracking",
                        "streaming_current",
                        1,
                    )
                if event_type == "current_tracking_point":
                    point = dict(event.get("point", {}) or {})
                    point.update(adapter.stream_point_metadata())
                    point["stream"] = adapter.stream_diagnostics()
                    last_point = point
                    event["point"] = point
                    if request.record_enabled:
                        if not self.recordings.enqueue(point):
                            status = self.recordings.status()
                            raise RuntimeError(
                                status.get("writer_error")
                                or "流式CSV记录队列写入失败。"
                            )
                    manager.measurement_state.update(
                        {
                            "mode": self.MODE,
                            "status": (
                                "流式双峰已锁定"
                                if point.get("valid")
                                else f"流式输出无效: {point.get('invalid_reason')}"
                            ),
                            "current_point": int(
                                point.get("cycle_index", 0) or 0
                            ),
                            "current_frequency_hz": 0.5
                            * (
                                float(
                                    point.get("left_frequency_hz", 0.0) or 0.0
                                )
                                + float(
                                    point.get("right_frequency_hz", 0.0) or 0.0
                                )
                            ),
                            "current_value": float(
                                point.get("splitting_hz", 0.0) or 0.0
                            ),
                            "streaming_current": point,
                        }
                    )
                    now_s = time.perf_counter()
                    if (
                        request.record_enabled
                        and now_s - last_recording_event_s >= 1.0
                    ):
                        last_recording_event_s = now_s
                        status = self.recordings.status()
                        if callable(event_callback):
                            event_callback(
                                {
                                    "type": "streaming_current_recording",
                                    "recording": status,
                                }
                            )
                elif event_type == "current_tracking_timing":
                    event["stream"] = adapter.stream_diagnostics()
                if callable(event_callback):
                    event_callback(event)

            pid_request = request.pid_request()
            result = type(manager).run_current_tracking(
                adapter,
                pid_request,
                publish,
            )
            result["last_point"] = last_point
            result["stream"] = buffer.diagnostics()
            result["acquisition_mode"] = "zurich_continuous_subscription"
            terminal_status = str(result.get("status", "completed"))
            if request.record_enabled:
                recording_status = self.recordings.finish(terminal_status)
                if recording_status and recording_status.get("writer_error"):
                    raise RuntimeError(recording_status["writer_error"])
            result["recording"] = recording_status
            return result
        except Exception:
            if request.record_enabled:
                self.recordings.finish("error")
            raise
        finally:
            buffer.close()

    def recording_status(self, session_id: str | None = None) -> dict[str, Any]:
        return self.recordings.status(session_id)

    def export_recording(
        self,
        session_id: str | None = None,
    ) -> tuple[Path, bool]:
        return self.recordings.export(session_id)
