from __future__ import annotations

import math
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamSample:
    channel_index: int
    device_timestamp_s: float
    host_timestamp_s: float
    x_v: float
    y_v: float
    sequence: int

    @property
    def r_v(self) -> float:
        return math.hypot(self.x_v, self.y_v)

    def as_signal(self) -> dict[str, float | int]:
        return {
            "x_v": self.x_v,
            "y_v": self.y_v,
            "r_v": self.r_v,
            "x_uv": self.x_v * 1e6,
            "y_uv": self.y_v * 1e6,
            "r_uv": self.r_v * 1e6,
            "channel_index": self.channel_index,
            "stream_sequence": self.sequence,
            "stream_device_timestamp_s": self.device_timestamp_s,
            "stream_host_timestamp_s": self.host_timestamp_s,
        }


class ZurichContinuousSampleBuffer:
    """独占Zurich订阅并维护带设备时间戳的多通道环形缓冲区。"""

    def __init__(
        self,
        *,
        manager: Any,
        channel_indices: list[int],
        sample_rate_hz: float,
        poll_window_s: float,
        poll_timeout_s: float,
        buffer_seconds: float = 5.0,
    ) -> None:
        self.manager = manager
        self.channel_indices = sorted({int(index) for index in channel_indices})
        self.sample_rate_hz = max(10.0, float(sample_rate_hz))
        self.poll_window_s = max(0.001, float(poll_window_s))
        self.poll_timeout_s = max(self.poll_window_s, float(poll_timeout_s))
        capacity = max(
            256,
            int(math.ceil(self.sample_rate_hz * max(1.0, buffer_seconds))),
        )
        self._buffers: dict[int, deque[tuple[float, float, float, int]]] = {
            index: deque(maxlen=capacity) for index in self.channel_indices
        }
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._nodes: dict[int, Any] = {}
        self._original_rates: dict[int, float] = {}
        self._clockbase = 1.0
        self._host_offset_candidates: deque[float] = deque(maxlen=128)
        self._host_offset_s: float | None = None
        self._sequence = 0
        self._samples_received = 0
        self._poll_count = 0
        self._empty_poll_count = 0
        self._poll_durations_ms: deque[float] = deque(maxlen=1000)
        self._last_error = ""
        self._started_s = 0.0
        self._ordinary_sampler_was_running = False
        self._closed = False

    def start(self, warmup_timeout_s: float = 2.0) -> None:
        if self.manager.lockin_session is None or self.manager.lockin_device is None:
            raise RuntimeError("锁相未连接，无法启动Zurich连续订阅。")
        if not self.channel_indices:
            raise RuntimeError("没有可订阅的锁相通道。")
        self._ordinary_sampler_was_running = bool(
            self.manager.sampler_thread is not None
            and self.manager.sampler_thread.is_alive()
        )
        self.manager._stop_sampler()
        try:
            with self.manager.device_lock:
                self._clockbase = max(
                    1.0,
                    float(self.manager.lockin_device.clockbase()),
                )
                for channel_index in self.channel_indices:
                    demod_index = self.manager._demod_index_for_channel(
                        channel_index
                    )
                    demod = self.manager.lockin_device.demods[demod_index]
                    try:
                        self._original_rates[channel_index] = float(demod.rate())
                    except Exception:
                        self._original_rates[channel_index] = float(
                            self.manager.lockin_state["channels"][channel_index].get(
                                "sample_rate_hz",
                                self.sample_rate_hz,
                            )
                        )
                    demod.rate(self.sample_rate_hz)
                    node = demod.sample
                    self._nodes[channel_index] = node
                self.manager.lockin_session.sync()
                for node in self._nodes.values():
                    node.subscribe()
            self._started_s = time.perf_counter()
            self._thread = threading.Thread(
                target=self._poll_loop,
                name="zurich-stream-buffer",
                daemon=True,
            )
            self._thread.start()
            deadline_s = time.perf_counter() + max(0.2, warmup_timeout_s)
            with self._condition:
                while not all(self._buffers[index] for index in self.channel_indices):
                    remaining_s = deadline_s - time.perf_counter()
                    if remaining_s <= 0:
                        raise RuntimeError(
                            self._last_error
                            or "Zurich连续订阅启动后未收到有效样本。"
                        )
                    self._condition.wait(timeout=min(0.1, remaining_s))
            # 多收集几个poll批次，用最小传输延迟校准设备时钟到主机单调时钟的偏移。
            calibration_deadline_s = time.perf_counter() + min(
                0.15,
                max(0.03, warmup_timeout_s / 4.0),
            )
            while (
                time.perf_counter() < calibration_deadline_s
                and not self._stop_event.wait(0.005)
            ):
                pass
        except Exception:
            self.close()
            raise

    def _poll_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                poll_started_s = time.perf_counter()
                try:
                    polled = self.manager.lockin_session.poll(
                        recording_time=self.poll_window_s,
                        timeout=self.poll_timeout_s,
                    )
                except Exception as exc:
                    self._last_error = str(exc)
                    if self._stop_event.wait(self.poll_window_s):
                        break
                    continue
                received_s = time.perf_counter()
                self._poll_count += 1
                self._poll_durations_ms.append(
                    max(0.0, (received_s - poll_started_s) * 1000.0)
                )
                batch_has_samples = False
                prepared: dict[int, list[tuple[float, float, float]]] = {}
                latest_device_timestamp_s = -math.inf
                for channel_index, node in self._nodes.items():
                    payload = polled.get(node, {}) if hasattr(polled, "get") else {}
                    payload = payload or {}
                    timestamps = self.manager._to_float_list(
                        payload.get("timestamp")
                    )
                    x_values = self.manager._to_float_list(payload.get("x"))
                    y_values = self.manager._to_float_list(payload.get("y"))
                    count = min(len(timestamps), len(x_values), len(y_values))
                    rows: list[tuple[float, float, float]] = []
                    for item in range(count):
                        device_timestamp_s = (
                            float(timestamps[item]) / self._clockbase
                        )
                        x_v = float(x_values[item])
                        y_v = float(y_values[item])
                        if not all(
                            math.isfinite(value)
                            for value in (device_timestamp_s, x_v, y_v)
                        ):
                            continue
                        rows.append((device_timestamp_s, x_v, y_v))
                        latest_device_timestamp_s = max(
                            latest_device_timestamp_s,
                            device_timestamp_s,
                        )
                    if rows:
                        batch_has_samples = True
                        prepared[channel_index] = rows

                if not batch_has_samples:
                    self._empty_poll_count += 1
                    continue
                offset_candidate_s = received_s - latest_device_timestamp_s
                if math.isfinite(offset_candidate_s):
                    self._host_offset_candidates.append(offset_candidate_s)
                    self._host_offset_s = min(self._host_offset_candidates)

                with self._condition:
                    for channel_index, rows in prepared.items():
                        buffer = self._buffers[channel_index]
                        for device_timestamp_s, x_v, y_v in rows:
                            self._sequence += 1
                            buffer.append(
                                (
                                    device_timestamp_s,
                                    x_v,
                                    y_v,
                                    self._sequence,
                                )
                            )
                            self._samples_received += 1
                    self._condition.notify_all()
        finally:
            with self._condition:
                self._condition.notify_all()

    def wait_for_sample(
        self,
        *,
        channel_index: int,
        not_before_host_s: float,
        after_device_timestamp_s: float | None = None,
        timeout_s: float,
    ) -> StreamSample | None:
        channel_index = int(channel_index)
        deadline_s = time.perf_counter() + max(0.001, float(timeout_s))
        with self._condition:
            while True:
                host_offset_s = self._host_offset_s
                if host_offset_s is not None:
                    buffer = self._buffers.get(channel_index, ())
                    for device_timestamp_s, x_v, y_v, sequence in reversed(buffer):
                        if (
                            after_device_timestamp_s is not None
                            and device_timestamp_s <= after_device_timestamp_s
                        ):
                            continue
                        host_timestamp_s = device_timestamp_s + host_offset_s
                        if host_timestamp_s < not_before_host_s:
                            break
                        return StreamSample(
                            channel_index=channel_index,
                            device_timestamp_s=device_timestamp_s,
                            host_timestamp_s=host_timestamp_s,
                            x_v=x_v,
                            y_v=y_v,
                            sequence=sequence,
                        )
                if self._stop_event.is_set():
                    return None
                remaining_s = deadline_s - time.perf_counter()
                if remaining_s <= 0:
                    return None
                self._condition.wait(timeout=min(0.05, remaining_s))

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        position = (len(ordered) - 1) * percentile
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return float(ordered[lower])
        fraction = position - lower
        return float(
            ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction
        )

    def diagnostics(self) -> dict[str, Any]:
        with self._condition:
            durations = list(self._poll_durations_ms)
            offset_s = self._host_offset_s
            channel_counts = {
                str(index): len(buffer)
                for index, buffer in self._buffers.items()
            }
            sample_rate_estimates: list[float] = []
            for buffer in self._buffers.values():
                if len(buffer) < 2:
                    continue
                recent = list(buffer)[-min(len(buffer), 2000) :]
                differences = [
                    recent[index][0] - recent[index - 1][0]
                    for index in range(1, len(recent))
                    if recent[index][0] > recent[index - 1][0]
                ]
                if differences:
                    sample_rate_estimates.append(
                        1.0 / statistics.median(differences)
                    )
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "requested_sample_rate_hz": self.sample_rate_hz,
            "measured_sample_rate_hz": (
                statistics.fmean(sample_rate_estimates)
                if sample_rate_estimates
                else 0.0
            ),
            "poll_window_ms": self.poll_window_s * 1000.0,
            "poll_count": self._poll_count,
            "empty_poll_count": self._empty_poll_count,
            "samples_received": self._samples_received,
            "buffered_samples_by_channel": channel_counts,
            "poll_p50_ms": self._percentile(durations, 0.5),
            "poll_p95_ms": self._percentile(durations, 0.95),
            "device_to_host_offset_s": offset_s,
            "last_error": self._last_error,
            "ordinary_sampler_suspended": True,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, 2.0 * self.poll_timeout_s))
        try:
            with self.manager.device_lock:
                for channel_index, node in self._nodes.items():
                    try:
                        node.unsubscribe()
                    except Exception:
                        pass
                    original_rate = self._original_rates.get(channel_index)
                    if original_rate is not None:
                        try:
                            demod_index = self.manager._demod_index_for_channel(
                                channel_index
                            )
                            self.manager.lockin_device.demods[demod_index].rate(
                                original_rate
                            )
                        except Exception:
                            pass
        finally:
            if self._ordinary_sampler_was_running:
                self.manager._start_sampler()
