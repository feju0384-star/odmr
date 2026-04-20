from __future__ import annotations

import math
import random
import re
import threading
import time
from collections import deque
from collections.abc import Iterable
from datetime import datetime
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    import pyvisa
except ImportError:  # pragma: no cover
    pyvisa = None

try:
    from zhinst.toolkit import Session
except ImportError:  # pragma: no cover
    Session = None

try:
    from zhinst.utils import bw2tc, tc2bw
except ImportError:  # pragma: no cover
    bw2tc = None
    tc2bw = None

from backend.app.schemas.instruments import (
    CurrentScanRequest,
    LabOneServerConfig,
    LockinChannelConfig,
    LockinConnectRequest,
    MicrowaveConfigRequest,
    MicrowaveConnectRequest,
    ODMRRequest,
    SensitivityRequest,
)

BWTC_SCALING = {
    1: 1.0,
    2: 0.643594,
    3: 0.509825,
    4: 0.434979,
    5: 0.385614,
    6: 0.349946,
    7: 0.322629,
    8: 0.300845,
}
INPUT_SIGNAL_OPTIONS = [
    {"value": 0, "label": "输入1信号", "enum": "sigin0"},
    {"value": 1, "label": "电流1输入", "enum": "currin0"},
    {"value": 2, "label": "触发1", "enum": "trigin0"},
    {"value": 3, "label": "触发2", "enum": "trigin1"},
    {"value": 4, "label": "辅助1输出", "enum": "auxout0"},
    {"value": 5, "label": "辅助2输出", "enum": "auxout1"},
    {"value": 6, "label": "辅助3输出", "enum": "auxout2"},
    {"value": 7, "label": "辅助4输出", "enum": "auxout3"},
    {"value": 8, "label": "辅助1输入", "enum": "auxin0"},
    {"value": 9, "label": "辅助2输入", "enum": "auxin1"},
    {"value": 174, "label": "常数", "enum": "demod_constant_input"},
]
TRIGGER_MODE_OPTIONS = [
    {"value": 0, "label": "连续", "enum": "continuous"},
    {"value": 1, "label": "触发1上升沿", "enum": "trigin0_rising"},
    {"value": 2, "label": "触发1下降沿", "enum": "trigin0_falling"},
    {"value": 3, "label": "触发1双沿", "enum": "trigin0_both"},
    {"value": 4, "label": "触发2上升沿", "enum": "trigin1_rising"},
    {"value": 5, "label": "触发1或2上升沿", "enum": "trigin0or1_rising"},
    {"value": 8, "label": "触发2下降沿", "enum": "trigin1_falling"},
    {"value": 10, "label": "触发1或2下降沿", "enum": "trigin0or1_falling"},
    {"value": 12, "label": "触发2双沿", "enum": "trigin1_both"},
    {"value": 15, "label": "触发1或2双沿", "enum": "trigin0or1_both"},
    {"value": 16, "label": "触发1低电平", "enum": "trigin0_low"},
    {"value": 32, "label": "触发1高电平", "enum": "trigin0_high"},
    {"value": 64, "label": "触发2低电平", "enum": "trigin1_low"},
    {"value": 80, "label": "触发1或2低电平", "enum": "trigin0or1_low"},
    {"value": 128, "label": "触发2高电平", "enum": "trigin1_high"},
    {"value": 160, "label": "触发1或2高电平", "enum": "trigin0or1_high"},
]
REFERENCE_SOURCE_OPTIONS = [
    {"value": "internal", "label": "内部参考"},
    {"value": "external", "label": "外部参考"},
]


def _default_lockin_channels(count: int = 4) -> list[dict[str, Any]]:
    return [
        LockinChannelConfig(channel_index=index, demod_index=index, osc_index=index).model_dump()
        for index in range(max(1, count))
    ]


class InstrumentManager:
    def __init__(self) -> None:
        self.rm = pyvisa.ResourceManager() if pyvisa else None
        self.lockin_session: Any | None = None
        self.lockin_device: Any | None = None
        self.microwave_resource: Any | None = None
        self.logs: deque[dict[str, str]] = deque(maxlen=40)
        self.last_signal_channels: list[dict[str, float]] = []
        self.device_lock = threading.Lock()
        self.signal_lock = threading.Lock()
        self.last_signal_update = 0.0
        self.signal_packet_seq = 0
        self.signal_packets: deque[dict[str, Any]] = deque(maxlen=256)
        self.initial_signal_packet_count = 8
        self.sampling_interval_connected = 0.05
        self.sampling_timeout_connected = 0.25
        self.sampler_thread: threading.Thread | None = None
        self.sampler_stop_event = threading.Event()
        self.odmr_stop_event = threading.Event()

        self.lockin_state: dict[str, Any] = {
            "connected": False,
            "serial": "",
            "name": "",
            "server_host": "localhost",
            "server_port": 8004,
            "hf2": False,
            "interface": "",
            "active_channel": 0,
            "last_discovery": {"visible": [], "connected": [], "error": ""},
            "channels": _default_lockin_channels(),
            "selectors": {
                "input_signals": INPUT_SIGNAL_OPTIONS,
                "trigger_modes": TRIGGER_MODE_OPTIONS,
                "filter_orders": [
                    {"value": index, "label": f"{index} 阶 ({index * 6} dB/oct)"}
                    for index in range(1, 9)
                ],
                "reference_sources": REFERENCE_SOURCE_OPTIONS,
                "external_reference_inputs": INPUT_SIGNAL_OPTIONS,
            },
        }
        self.microwave_state: dict[str, Any] = {
            "connected": False,
            "address": "",
            "idn": "",
            "available_resources": [],
            "last_error": "",
            "config": MicrowaveConfigRequest().model_dump(),
        }
        self.measurement_state: dict[str, Any] = {
            "running": False,
            "mode": "idle",
            "status": "idle",
            "progress": 0.0,
            "current_point": 0,
            "current_frequency_hz": 0.0,
            "current_value": 0.0,
            "estimated_duration_s": 0.0,
            "cancel_requested": False,
            "last_request": ODMRRequest().model_dump(),
            "last_trace": self._generate_trace(ODMRRequest()),
            "last_sensitivity_request": SensitivityRequest().model_dump(),
            "last_sensitivity_result": {},
            "last_current_request": CurrentScanRequest().model_dump(),
            "last_current_result": {},
        }
        self.last_signal_channels = self._simulate_signal_channels()
        self._log("系统启动，后端进入待机状态。")

    def _log(self, message: str, level: str = "info") -> None:
        self.logs.appendleft(
            {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "message": message,
            }
        )

    def _serialize_device_item(self, value: Any) -> dict[str, str]:
        if isinstance(value, str):
            return {"serial": value, "label": value}
        serial = getattr(value, "serial", "") or str(value)
        dev_type = getattr(value, "device_type", "")
        label = f"{serial} {dev_type}".strip()
        return {"serial": serial, "label": label}

    def _bandwidth_hz(self, timeconstant_seconds: float, order: int) -> float:
        if timeconstant_seconds <= 0:
            return 0.0
        if callable(tc2bw):
            try:
                return float(tc2bw(timeconstant_seconds, order))
            except Exception:
                pass
        factor = BWTC_SCALING.get(order, BWTC_SCALING[4])
        return factor / (2 * math.pi * timeconstant_seconds)

    def _timeconstant_ms(self, bandwidth_hz: float, order: int) -> float:
        if bandwidth_hz <= 0:
            return 0.0
        if callable(bw2tc):
            try:
                return float(bw2tc(bandwidth_hz, order) * 1000.0)
            except Exception:
                pass
        factor = BWTC_SCALING.get(order, BWTC_SCALING[4])
        return factor / (2 * math.pi * bandwidth_hz) * 1000.0

    def _to_scalar(self, value: Any, default: float = 0.0) -> float:
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, (list, tuple)):
            if not value:
                return default
            return self._to_scalar(value[0], default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _to_float_list(self, value: Any) -> list[float]:
        if value is None:
            return []
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            return []
        result: list[float] = []
        for item in value:
            try:
                result.append(float(item))
            except (TypeError, ValueError):
                continue
        return result

    def _bool_from_value(self, value: Any, true_enums: tuple[str, ...] = ("on",)) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        suffix = str(value).split(".")[-1].strip().lower()
        return suffix in true_enums

    def _enum_from_value(self, value: Any, options: Iterable[dict[str, Any]], default: int = 0) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        suffix = str(value).split(".")[-1].strip().lower()
        for option in options:
            if suffix == str(option.get("enum", "")).lower():
                return int(option["value"])
        return default

    def _demod_count(self) -> int:
        if self.lockin_device is None:
            return len(self.lockin_state["channels"])
        try:
            return max(1, len(self.lockin_device.demods))
        except Exception:
            count = 0
            while True:
                try:
                    self.lockin_device.demods[count]
                except Exception:
                    return max(1, count)
                count += 1

    def _safe_channel_count(self, requested: int | None = None) -> int:
        current = len(self.lockin_state.get("channels", []))
        minimum = self._demod_count() if self.lockin_device is not None else current
        if requested is None:
            return max(1, current, minimum)
        return max(1, requested, current, minimum)

    def _external_reference_capable_demods(self) -> list[int]:
        return [index for index in (1, 3) if index < self._demod_count()]

    def _resolve_extref_tracker_demod_index(self, demod_index: int) -> int:
        candidates = self._external_reference_capable_demods()
        if not candidates:
            return demod_index
        if demod_index in candidates:
            return demod_index
        if demod_index < 2 and 1 in candidates:
            return 1
        if demod_index >= 2 and 3 in candidates:
            return 3
        return candidates[0]

    def _channel_index_for_demod_index(self, demod_index: int) -> int:
        channels = self.lockin_state.get("channels", [])
        for index, channel in enumerate(channels):
            if int(channel.get("demod_index", index)) == demod_index:
                return index
        if not channels:
            return 0
        return max(0, min(demod_index, len(channels) - 1))

    def _normalize_lockin_channel_index(self, channel_index: int | None = None) -> int:
        channels = self.lockin_state.get("channels", [])
        if not channels:
            return 0
        if channel_index is None:
            channel_index = int(self.lockin_state.get("active_channel", 0))
        return max(0, min(int(channel_index), len(channels) - 1))

    def _resolve_measurement_channel_index(self, channel_index: int | None = None) -> int:
        resolved = self._normalize_lockin_channel_index(channel_index)
        channels = self.lockin_state.get("channels", [])
        if not channels:
            return resolved
        signal_index = int(channels[resolved].get("input_signal", 0))
        if signal_index not in (8, 9):
            return resolved
        paired = resolved - 1 if resolved % 2 else resolved
        if 0 <= paired < len(channels):
            return paired
        return resolved

    def _measurement_settle_s(self, channel_index: int, settle_ms: float) -> float:
        channel_index = self._normalize_lockin_channel_index(channel_index)
        channels = self.lockin_state.get("channels", [])
        tc_ms = 0.0
        if 0 <= channel_index < len(channels):
            tc_ms = float(channels[channel_index].get("time_constant_ms", 0.0) or 0.0)
        return max(float(settle_ms) / 1000.0, tc_ms / 1000.0 * 5.0, 0.005)

    def _demod_index_for_channel(self, channel_index: int) -> int:
        channel_index = self._normalize_lockin_channel_index(channel_index)
        channels = self.lockin_state.get("channels", [])
        if 0 <= channel_index < len(channels):
            return int(channels[channel_index].get("demod_index", channel_index))
        return channel_index

    def _recommended_sensitivity_sample_rate_hz(self, channel_index: int) -> float:
        channel_index = self._normalize_lockin_channel_index(channel_index)
        channels = self.lockin_state.get("channels", [])
        current_rate_hz = 0.0
        low_pass_bandwidth_hz = 0.0
        if 0 <= channel_index < len(channels):
            channel = channels[channel_index]
            current_rate_hz = float(channel.get("sample_rate_hz", 0.0) or 0.0)
            low_pass_bandwidth_hz = float(channel.get("low_pass_bandwidth_hz", 0.0) or 0.0)
        # Sensitivity capture needs a denser time stream than the default UI rate.
        target_rate_hz = max(current_rate_hz, 10_000.0, low_pass_bandwidth_hz * 128.0)
        return min(target_rate_hz, 200_000.0)

    def _set_channel_sample_rate_hz(self, channel_index: int, sample_rate_hz: float) -> float:
        channel_index = self._normalize_lockin_channel_index(channel_index)
        demod_index = self._demod_index_for_channel(channel_index)
        applied_rate_hz = float(sample_rate_hz)
        if self.lockin_device is not None:
            with self.device_lock:
                self.lockin_device.demods[demod_index].rate(applied_rate_hz)
                try:
                    applied_rate_hz = float(self.lockin_device.demods[demod_index].rate())
                except Exception:
                    pass
        channels = self.lockin_state.get("channels", [])
        if 0 <= channel_index < len(channels):
            channels[channel_index]["sample_rate_hz"] = applied_rate_hz
        return applied_rate_hz

    def read_lockin_sample_for_channel(self, channel_index: int) -> dict[str, float]:
        channel_index = self._normalize_lockin_channel_index(channel_index)
        with self.device_lock:
            return self._read_lockin_sample(channel_index)

    def _set_lockin_phase_deg(self, channel_index: int, phase_deg: float) -> float:
        channel_index = self._normalize_lockin_channel_index(channel_index)
        channels = self.lockin_state.get("channels", [])
        if channel_index >= len(channels):
            raise RuntimeError("锁相通道不存在。")
        demod_index = int(channels[channel_index].get("demod_index", channel_index))
        if self.lockin_device is None:
            channels[channel_index]["phase_deg"] = float(phase_deg)
            return float(phase_deg)
        with self.device_lock:
            self.lockin_device.demods[demod_index].phaseshift(float(phase_deg))
        channels[channel_index]["phase_deg"] = float(phase_deg)
        return float(phase_deg)

    def _rotate_quadratures(
        self, x_values: Any, y_values: Any, angle_deg: float
    ) -> tuple[Any, Any]:
        angle_rad = math.radians(float(angle_deg))
        cos_value = math.cos(angle_rad)
        sin_value = math.sin(angle_rad)
        x_array = np.asarray(x_values, dtype=float)
        y_array = np.asarray(y_values, dtype=float)
        rotated_x = x_array * cos_value + y_array * sin_value
        rotated_y = -x_array * sin_value + y_array * cos_value
        return rotated_x, rotated_y

    def _wrap_phase_deg(self, phase_deg: float) -> float:
        wrapped = math.fmod(float(phase_deg), 360.0)
        if wrapped <= -180.0:
            wrapped += 360.0
        elif wrapped > 180.0:
            wrapped -= 360.0
        return wrapped

    def _build_phase_gradient_mask(self, gradient_magnitude: Any) -> Any:
        gradient_array = np.asarray(gradient_magnitude, dtype=float)
        mask = np.zeros(gradient_array.shape, dtype=bool)
        if not gradient_array.size:
            return mask
        finite_mask = np.isfinite(gradient_array)
        if not np.any(finite_mask):
            return mask
        peak_gradient = float(np.max(gradient_array[finite_mask]))
        if peak_gradient > 0:
            mask = finite_mask & (gradient_array >= peak_gradient * 0.35)
        min_points = min(max(7, gradient_array.size // 10), gradient_array.size)
        if int(np.count_nonzero(mask)) < min_points:
            ranked = np.argsort(np.nan_to_num(gradient_array, nan=-np.inf))
            mask = np.zeros(gradient_array.shape, dtype=bool)
            mask[ranked[-min_points:]] = True
            mask &= finite_mask
        return mask

    def _estimate_phase_delta_deg_from_trace(
        self, frequency_hz: Any, x_values: Any, y_values: Any
    ) -> dict[str, float]:
        frequency_array = np.asarray(frequency_hz, dtype=float)
        x_array = np.asarray(x_values, dtype=float)
        y_array = np.asarray(y_values, dtype=float)
        gradient_x = np.gradient(x_array, frequency_array)
        gradient_y = np.gradient(y_array, frequency_array)
        gradient_magnitude = np.hypot(gradient_x, gradient_y)
        mask = self._build_phase_gradient_mask(gradient_magnitude)

        if int(np.count_nonzero(mask)) >= 2:
            weights = gradient_magnitude[mask] ** 2
            dx = gradient_x[mask]
            dy = gradient_y[mask]
            m_xx = float(np.sum(weights * dx * dx))
            m_xy = float(np.sum(weights * dx * dy))
            m_yy = float(np.sum(weights * dy * dy))
            phase_delta_deg = math.degrees(0.5 * math.atan2(2.0 * m_xy, m_xx - m_yy))
            window_frequency = frequency_array[mask]
            window_start_hz = float(np.min(window_frequency))
            window_stop_hz = float(np.max(window_frequency))
            window_points = int(np.count_nonzero(mask))
        else:
            slope_index = int(np.nanargmax(gradient_magnitude))
            phase_delta_deg = math.degrees(
                math.atan2(float(gradient_y[slope_index]), float(gradient_x[slope_index]))
            )
            window_start_hz = float(frequency_array[slope_index])
            window_stop_hz = float(frequency_array[slope_index])
            window_points = 1

        return {
            "phase_delta_deg": float(phase_delta_deg),
            "gradient_peak_v_per_hz": float(np.nanmax(gradient_magnitude)),
            "gradient_window_start_hz": window_start_hz,
            "gradient_window_stop_hz": window_stop_hz,
            "gradient_window_points": window_points,
        }

    def _evaluate_phase_trace(
        self,
        frequency_hz: Any,
        x_values: Any,
        y_values: Any,
        search_center_hz: float,
        slope_fit_points: int,
    ) -> dict[str, Any]:
        frequency_array = np.asarray(frequency_hz, dtype=float)
        x_array = np.asarray(x_values, dtype=float)
        y_array = np.asarray(y_values, dtype=float)
        gradient_x = np.gradient(x_array, frequency_array)
        gradient_y = np.gradient(y_array, frequency_array)
        gradient_magnitude = np.hypot(gradient_x, gradient_y)
        mask = self._build_phase_gradient_mask(gradient_magnitude)
        window_points = int(np.count_nonzero(mask))
        span_hz = max(float(np.max(frequency_array) - np.min(frequency_array)), 1.0)

        axis_metrics: dict[str, dict[str, float]] = {}
        for axis_name in ("x_v", "y_v"):
            selected_signal = x_array if axis_name == "x_v" else y_array
            orthogonal_signal = y_array if axis_name == "x_v" else x_array
            selected_gradient = np.gradient(selected_signal, frequency_array)
            orthogonal_gradient = np.gradient(orthogonal_signal, frequency_array)
            zero_crossing = self._find_zero_crossing_and_slope(
                frequency_hz=frequency_array,
                signal_v=selected_signal,
                search_center_hz=search_center_hz,
                slope_fit_points=slope_fit_points,
            )

            if window_points:
                weights = gradient_magnitude[mask] ** 2
                selected_window = selected_gradient[mask]
                orthogonal_window = orthogonal_gradient[mask]
                selected_signal_window = selected_signal[mask]
                orthogonal_signal_window = orthogonal_signal[mask]
            else:
                weights = None
                selected_window = selected_gradient
                orthogonal_window = orthogonal_gradient
                selected_signal_window = selected_signal
                orthogonal_signal_window = orthogonal_signal

            if weights is not None and np.any(weights > 0):
                selected_gradient_rms = math.sqrt(
                    float(np.average(selected_window * selected_window, weights=weights))
                )
                orthogonal_gradient_rms = math.sqrt(
                    float(np.average(orthogonal_window * orthogonal_window, weights=weights))
                )
                orthogonal_signal_rms = math.sqrt(
                    float(np.average(orthogonal_signal_window * orthogonal_signal_window, weights=weights))
                )
            else:
                selected_gradient_rms = math.sqrt(float(np.mean(selected_window * selected_window)))
                orthogonal_gradient_rms = math.sqrt(float(np.mean(orthogonal_window * orthogonal_window)))
                orthogonal_signal_rms = math.sqrt(
                    float(np.mean(orthogonal_signal_window * orthogonal_signal_window))
                )

            selected_span_v = max(float(np.ptp(selected_signal_window)), 1e-30)
            zero_frequency = float(zero_crossing["zero_crossing_hz"])
            orthogonal_at_zero_v = abs(float(np.interp(zero_frequency, frequency_array, orthogonal_signal)))
            center_distance_hz = abs(zero_frequency - float(search_center_hz))
            leakage_penalty = (
                1.0
                + orthogonal_gradient_rms / max(selected_gradient_rms, 1e-30)
                + orthogonal_at_zero_v / selected_span_v
                + orthogonal_signal_rms / selected_span_v
            )
            score = abs(float(zero_crossing["slope_v_per_hz"])) / leakage_penalty
            score /= 1.0 + center_distance_hz / span_hz
            if not zero_crossing.get("has_bracketed_zero", False):
                score *= 0.35

            axis_metrics[axis_name] = {
                "score": float(score),
                "selected_gradient_rms_v_per_hz": float(selected_gradient_rms),
                "orthogonal_gradient_rms_v_per_hz": float(orthogonal_gradient_rms),
                "orthogonal_signal_rms_v": float(orthogonal_signal_rms),
                "orthogonal_at_zero_v": float(orthogonal_at_zero_v),
                "center_distance_hz": float(center_distance_hz),
                "zero_crossing_hz": zero_frequency,
                "slope_v_per_hz": float(zero_crossing["slope_v_per_hz"]),
                "fit_start_index": int(zero_crossing["fit_start_index"]),
                "fit_stop_index": int(zero_crossing["fit_stop_index"]),
                "has_bracketed_zero": bool(zero_crossing.get("has_bracketed_zero", False)),
            }

        best_axis = (
            "x_v"
            if axis_metrics["x_v"]["score"] >= axis_metrics["y_v"]["score"]
            else "y_v"
        )
        return {
            "best_axis": best_axis,
            "gradient_window_points": window_points,
            "axes": axis_metrics,
        }

    def _downsample_series(
        self, x_values: list[float], y_values: list[float], max_points: int = 2048
    ) -> tuple[list[float], list[float]]:
        if len(x_values) <= max_points:
            return x_values, y_values
        stride = max(1, math.ceil(len(x_values) / max_points))
        return x_values[::stride], y_values[::stride]

    def _compute_one_sided_asd(
        self, values: Any, sample_rate_hz: float, min_frequency_hz: float
    ) -> tuple[Any, Any]:
        if np is None:
            raise RuntimeError("numpy 不可用，无法计算 ASD。")
        value_array = np.asarray(values, dtype=float)
        if value_array.size < 16:
            raise RuntimeError("用于 ASD 的锁相采样点数不足。")
        sample_rate_hz = float(sample_rate_hz)
        if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
            raise RuntimeError("锁相采样率无效，无法计算 ASD。")
        centered = value_array - float(np.mean(value_array))
        fft_values = np.fft.rfft(centered)
        frequencies = np.fft.rfftfreq(centered.size, d=1.0 / sample_rate_hz)
        asd = np.sqrt(2.0 / (sample_rate_hz * centered.size)) * np.abs(fft_values)
        if asd.size:
            asd[0] = 0.0
            if centered.size % 2 == 0 and asd.size > 1:
                asd[-1] /= math.sqrt(2.0)
        mask = frequencies >= float(min_frequency_hz)
        return frequencies[mask], asd[mask]

    def _capture_lockin_time_series(
        self, channel_index: int, duration_s: float
    ) -> dict[str, Any]:
        if self.lockin_session is None or self.lockin_device is None:
            raise RuntimeError("锁相未连接，无法采集定频时间序列。")
        channel_index = self._normalize_lockin_channel_index(channel_index)
        demod_index = int(self.lockin_state["channels"][channel_index].get("demod_index", channel_index))
        sample_node = None
        clockbase = 1.0
        timestamps_raw: list[float] = []
        x_values: list[float] = []
        y_values: list[float] = []
        last_timestamp = -1.0
        self._stop_sampler()
        try:
            with self.device_lock:
                sample_node = self.lockin_device.demods[demod_index].sample
                clockbase = max(1.0, float(self.lockin_device.clockbase()))
                self.lockin_session.sync()
                sample_node.subscribe()

            deadline = time.time() + float(duration_s)
            while time.time() < deadline:
                if self.odmr_stop_event.is_set():
                    raise RuntimeError("灵敏度测量已停止。")
                remaining = max(0.0, deadline - time.time())
                recording_time = min(0.25, max(0.05, remaining))
                with self.device_lock:
                    polled = self.lockin_session.poll(
                        recording_time=recording_time,
                        timeout=max(self.sampling_timeout_connected, recording_time + 0.1),
                    )
                payload = polled.get(sample_node, {}) or {}
                timestamps = self._to_float_list(payload.get("timestamp"))
                batch_x = self._to_float_list(payload.get("x"))
                batch_y = self._to_float_list(payload.get("y"))
                sample_count = min(len(timestamps), len(batch_x), len(batch_y))
                for index in range(sample_count):
                    timestamp = float(timestamps[index])
                    if timestamp <= last_timestamp:
                        continue
                    last_timestamp = timestamp
                    timestamps_raw.append(timestamp / clockbase)
                    x_values.append(float(batch_x[index]))
                    y_values.append(float(batch_y[index]))
            if sample_node is not None:
                with self.device_lock:
                    sample_node.unsubscribe()
        finally:
            self._start_sampler()

        if len(timestamps_raw) < 16:
            raise RuntimeError("锁相时间序列样本不足，无法计算 ASD。")
        times_array = np.asarray(timestamps_raw, dtype=float)
        times_array = times_array - float(times_array[0])
        dt = np.diff(times_array)
        if not dt.size:
            raise RuntimeError("锁相时间戳无效，无法估算采样率。")
        sample_rate_hz = float(1.0 / np.median(dt))
        return {
            "time_s": times_array.tolist(),
            "x_v": x_values,
            "y_v": y_values,
            "r_v": [math.hypot(x_values[index], y_values[index]) for index in range(len(x_values))],
            "sample_rate_hz": sample_rate_hz,
        }

    def _find_zero_crossing_and_slope(
        self,
        frequency_hz: Any,
        signal_v: Any,
        search_center_hz: float,
        slope_fit_points: int,
    ) -> dict[str, float]:
        frequency_array = np.asarray(frequency_hz, dtype=float)
        signal_array = np.asarray(signal_v, dtype=float)
        gradient = np.gradient(signal_array, frequency_array)
        candidates: list[tuple[float, float, int, float]] = []
        for index in range(signal_array.size - 1):
            y0 = float(signal_array[index])
            y1 = float(signal_array[index + 1])
            if y0 == 0.0:
                zero_frequency = float(frequency_array[index])
            elif y1 == 0.0:
                zero_frequency = float(frequency_array[index + 1])
            elif y0 * y1 > 0:
                continue
            else:
                zero_frequency = float(
                    frequency_array[index]
                    - y0 * (frequency_array[index + 1] - frequency_array[index]) / (y1 - y0)
                )
            local_slope = abs(float((y1 - y0) / (frequency_array[index + 1] - frequency_array[index])))
            candidates.append((abs(zero_frequency - float(search_center_hz)), -local_slope, index, zero_frequency))

        has_bracketed_zero = bool(candidates)
        if candidates:
            _, _, pair_index, zero_frequency = min(candidates)
            center_index = pair_index + 1
        else:
            center_index = int(np.nanargmax(np.abs(gradient)))
            zero_frequency = float(frequency_array[center_index])

        fit_count = max(3, int(slope_fit_points))
        fit_half = max(1, fit_count // 2)
        start = max(0, center_index - fit_half)
        stop = min(signal_array.size, start + fit_count)
        start = max(0, stop - fit_count)
        fit_x = frequency_array[start:stop]
        fit_y = signal_array[start:stop]
        slope, intercept = np.polyfit(fit_x, fit_y, 1)
        if slope:
            zero_frequency = float(-intercept / slope)
        return {
            "zero_crossing_hz": float(zero_frequency),
            "slope_v_per_hz": float(slope),
            "fit_start_index": float(start),
            "fit_stop_index": float(stop),
            "has_bracketed_zero": has_bracketed_zero,
        }

    def _refine_extremum_frequency(
        self, frequency_hz: Any, signal_v: Any, center_index: int
    ) -> float:
        frequency_array = np.asarray(frequency_hz, dtype=float)
        signal_array = np.asarray(signal_v, dtype=float)
        center_index = max(0, min(int(center_index), signal_array.size - 1))
        if center_index <= 0 or center_index >= signal_array.size - 1:
            return float(frequency_array[center_index])
        fit_x = frequency_array[center_index - 1 : center_index + 2]
        fit_y = signal_array[center_index - 1 : center_index + 2]
        try:
            quadratic, linear, _ = np.polyfit(fit_x, fit_y, 2)
            if math.isfinite(quadratic) and abs(quadratic) > 1e-30:
                vertex = float(-linear / (2.0 * quadratic))
                if float(np.min(fit_x)) <= vertex <= float(np.max(fit_x)):
                    return vertex
        except Exception:
            pass
        return float(frequency_array[center_index])

    def _find_split_resonance_pair(
        self, frequency_hz: Any, signal_v: Any, search_center_hz: float
    ) -> dict[str, float]:
        frequency_array = np.asarray(frequency_hz, dtype=float)
        signal_array = np.asarray(signal_v, dtype=float)
        if frequency_array.size < 5 or signal_array.size != frequency_array.size:
            raise RuntimeError("频谱点数不足，无法识别左右共振峰。")

        span_hz = max(float(np.max(frequency_array) - np.min(frequency_array)), 1.0)
        center_hz = float(
            min(max(float(search_center_hz), float(np.min(frequency_array))), float(np.max(frequency_array)))
        )
        baseline = float(np.nanmedian(signal_array))
        candidates: list[tuple[int, float, float]] = []
        for index in range(1, signal_array.size - 1):
            left_value = float(signal_array[index - 1])
            center_value = float(signal_array[index])
            right_value = float(signal_array[index + 1])
            if not all(math.isfinite(value) for value in (left_value, center_value, right_value)):
                continue
            if center_value <= left_value and center_value <= right_value:
                local_reference = max(left_value, right_value, baseline)
                depth = max(0.0, local_reference - center_value)
                score = depth / (1.0 + abs(float(frequency_array[index]) - center_hz) / span_hz)
                candidates.append((index, depth, score))

        if not candidates:
            for index in range(1, signal_array.size - 1):
                value = float(signal_array[index])
                if not math.isfinite(value):
                    continue
                depth = max(0.0, baseline - value)
                score = depth / (1.0 + abs(float(frequency_array[index]) - center_hz) / span_hz)
                candidates.append((index, depth, score))

        if not candidates:
            raise RuntimeError("无法识别左右共振峰。")

        left_candidates = [item for item in candidates if float(frequency_array[item[0]]) < center_hz]
        right_candidates = [item for item in candidates if float(frequency_array[item[0]]) > center_hz]

        if not left_candidates:
            left_index = int(np.nanargmin(signal_array[: max(2, signal_array.size // 2)]))
            left_depth = max(0.0, baseline - float(signal_array[left_index]))
            left_candidates = [(left_index, left_depth, left_depth)]
        if not right_candidates:
            right_offset = max(1, signal_array.size // 2)
            right_local = signal_array[right_offset:]
            if not right_local.size:
                raise RuntimeError("无法识别右侧共振峰。")
            right_index = right_offset + int(np.nanargmin(right_local))
            right_depth = max(0.0, baseline - float(signal_array[right_index]))
            right_candidates = [(right_index, right_depth, right_depth)]

        left_index, left_depth, _ = max(left_candidates, key=lambda item: (item[2], item[1]))
        right_index, right_depth, _ = max(right_candidates, key=lambda item: (item[2], item[1]))
        if left_index >= right_index:
            raise RuntimeError("左右共振峰位置异常，无法计算劈裂。")

        left_resonance_hz = self._refine_extremum_frequency(frequency_array, signal_array, left_index)
        right_resonance_hz = self._refine_extremum_frequency(frequency_array, signal_array, right_index)
        return {
            "left_index": int(left_index),
            "right_index": int(right_index),
            "left_resonance_hz": float(left_resonance_hz),
            "right_resonance_hz": float(right_resonance_hz),
            "center_hz": float((left_resonance_hz + right_resonance_hz) / 2.0),
            "splitting_hz": float(right_resonance_hz - left_resonance_hz),
            "left_depth": float(left_depth),
            "right_depth": float(right_depth),
        }

    def _evaluate_split_phase_trace(
        self,
        frequency_hz: Any,
        x_values: Any,
        y_values: Any,
        search_center_hz: float,
        slope_fit_points: int,
    ) -> dict[str, Any]:
        frequency_array = np.asarray(frequency_hz, dtype=float)
        x_array = np.asarray(x_values, dtype=float)
        y_array = np.asarray(y_values, dtype=float)
        r_array = np.hypot(x_array, y_array)
        resonance_pair = self._find_split_resonance_pair(
            frequency_hz=frequency_array,
            signal_v=r_array,
            search_center_hz=search_center_hz,
        )
        span_hz = max(float(np.max(frequency_array) - np.min(frequency_array)), 1.0)
        window_radius = max(2, int(slope_fit_points))
        left_index = int(resonance_pair["left_index"])
        right_index = int(resonance_pair["right_index"])
        window_indices = sorted(
            set(
                range(max(0, left_index - window_radius), min(frequency_array.size, left_index + window_radius + 1))
            ).union(
                range(max(0, right_index - window_radius), min(frequency_array.size, right_index + window_radius + 1))
            )
        )
        window_points = len(window_indices)

        axis_metrics: dict[str, dict[str, float]] = {}
        for axis_name in ("x_v", "y_v"):
            selected_signal = x_array if axis_name == "x_v" else y_array
            orthogonal_signal = y_array if axis_name == "x_v" else x_array
            selected_gradient = np.gradient(selected_signal, frequency_array)
            orthogonal_gradient = np.gradient(orthogonal_signal, frequency_array)

            left_zero = self._find_zero_crossing_and_slope(
                frequency_hz=frequency_array,
                signal_v=selected_signal,
                search_center_hz=float(resonance_pair["left_resonance_hz"]),
                slope_fit_points=slope_fit_points,
            )
            right_zero = self._find_zero_crossing_and_slope(
                frequency_hz=frequency_array,
                signal_v=selected_signal,
                search_center_hz=float(resonance_pair["right_resonance_hz"]),
                slope_fit_points=slope_fit_points,
            )

            selected_window = selected_signal[window_indices]
            orthogonal_window = orthogonal_signal[window_indices]
            gradient_window = selected_gradient[window_indices]
            orthogonal_gradient_window = orthogonal_gradient[window_indices]
            selected_span_v = max(float(np.ptp(selected_window)), 1e-30)
            selected_gradient_rms = math.sqrt(float(np.mean(gradient_window * gradient_window)))
            orthogonal_gradient_rms = math.sqrt(
                float(np.mean(orthogonal_gradient_window * orthogonal_gradient_window))
            )
            orthogonal_signal_rms = math.sqrt(float(np.mean(orthogonal_window * orthogonal_window)))

            left_zero_hz = float(left_zero["zero_crossing_hz"])
            right_zero_hz = float(right_zero["zero_crossing_hz"])
            left_orthogonal_v = abs(float(np.interp(left_zero_hz, frequency_array, orthogonal_signal)))
            right_orthogonal_v = abs(float(np.interp(right_zero_hz, frequency_array, orthogonal_signal)))
            zero_center_hz = (left_zero_hz + right_zero_hz) / 2.0
            zero_splitting_hz = right_zero_hz - left_zero_hz
            leakage_penalty = (
                1.0
                + orthogonal_gradient_rms / max(selected_gradient_rms, 1e-30)
                + (left_orthogonal_v + right_orthogonal_v) / max(selected_span_v, 1e-30)
                + orthogonal_signal_rms / max(selected_span_v, 1e-30)
            )
            symmetry_penalty = 1.0 + abs(zero_center_hz - float(resonance_pair["center_hz"])) / span_hz
            score = (
                abs(float(left_zero["slope_v_per_hz"])) + abs(float(right_zero["slope_v_per_hz"]))
            ) / leakage_penalty
            score /= symmetry_penalty
            if zero_splitting_hz <= 0:
                score = 0.0
            if not (left_zero.get("has_bracketed_zero", False) and right_zero.get("has_bracketed_zero", False)):
                score *= 0.35

            axis_metrics[axis_name] = {
                "score": float(score),
                "selected_gradient_rms_v_per_hz": float(selected_gradient_rms),
                "orthogonal_gradient_rms_v_per_hz": float(orthogonal_gradient_rms),
                "orthogonal_signal_rms_v": float(orthogonal_signal_rms),
                "left_resonance_hz": float(resonance_pair["left_resonance_hz"]),
                "right_resonance_hz": float(resonance_pair["right_resonance_hz"]),
                "resonance_center_hz": float(resonance_pair["center_hz"]),
                "resonance_splitting_hz": float(resonance_pair["splitting_hz"]),
                "left_zero_crossing_hz": left_zero_hz,
                "right_zero_crossing_hz": right_zero_hz,
                "zero_crossing_center_hz": float(zero_center_hz),
                "zero_crossing_splitting_hz": float(zero_splitting_hz),
                "left_slope_v_per_hz": float(left_zero["slope_v_per_hz"]),
                "right_slope_v_per_hz": float(right_zero["slope_v_per_hz"]),
                "left_orthogonal_at_zero_v": float(left_orthogonal_v),
                "right_orthogonal_at_zero_v": float(right_orthogonal_v),
                "left_fit_start_index": int(left_zero["fit_start_index"]),
                "left_fit_stop_index": int(left_zero["fit_stop_index"]),
                "right_fit_start_index": int(right_zero["fit_start_index"]),
                "right_fit_stop_index": int(right_zero["fit_stop_index"]),
                "left_has_bracketed_zero": bool(left_zero.get("has_bracketed_zero", False)),
                "right_has_bracketed_zero": bool(right_zero.get("has_bracketed_zero", False)),
            }

        best_axis = (
            "x_v"
            if axis_metrics["x_v"]["score"] >= axis_metrics["y_v"]["score"]
            else "y_v"
        )
        return {
            "best_axis": best_axis,
            "gradient_window_points": int(window_points),
            "resonances": resonance_pair,
            "axes": axis_metrics,
        }

    def _collect_complex_odmr_trace(
        self,
        channel_index: int,
        center_hz: float,
        span_hz: float,
        points: int,
        settle_ms: float,
        progress_callback: Any | None = None,
        progress_start: float = 0.0,
        progress_span: float = 1.0,
    ) -> dict[str, Any]:
        if np is None:
            raise RuntimeError("numpy 不可用，无法进行灵敏度扫频。")
        if self.lockin_device is None or self.microwave_resource is None:
            raise RuntimeError("锁相或微波源未连接，无法进行灵敏度扫频。")
        channel_index = self._normalize_lockin_channel_index(channel_index)
        frequencies = np.linspace(
            float(center_hz) - float(span_hz) / 2.0,
            float(center_hz) + float(span_hz) / 2.0,
            int(points),
        )
        x_values: list[float] = []
        y_values: list[float] = []
        restore_output = bool(self.microwave_state.get("config", {}).get("output_enabled", False))
        if not self.set_microwave_output_enabled(True):
            raise RuntimeError(self.microwave_state.get("last_error") or "无法开启微波输出。")
        try:
            for index, frequency in enumerate(frequencies):
                if self.odmr_stop_event.is_set():
                    raise RuntimeError("灵敏度测量已停止。")
                if not self.set_microwave_frequency(float(frequency)):
                    raise RuntimeError(self.microwave_state.get("last_error") or "无法更新微波频率。")
                time.sleep(self._measurement_settle_s(channel_index, settle_ms))
                sample = self.read_lockin_sample_for_channel(channel_index)
                x_values.append(float(sample.get("x_v", 0.0) or 0.0))
                y_values.append(float(sample.get("y_v", 0.0) or 0.0))
                if callable(progress_callback):
                    progress_callback(
                        progress_start + progress_span * ((index + 1) / max(1, frequencies.size))
                    )
        finally:
            self.set_microwave_output_enabled(restore_output)
        r_values = [math.hypot(x_values[index], y_values[index]) for index in range(len(x_values))]
        return {
            "frequency_hz": frequencies.tolist(),
            "x_v": x_values,
            "y_v": y_values,
            "r_v": r_values,
        }

    def _refresh_lockin_channels(self) -> None:
        target = self._demod_count() if self.lockin_device is not None else len(self.lockin_state["channels"])
        channels: list[dict[str, Any]] = []
        for index in range(target):
            previous = (
                dict(self.lockin_state["channels"][index])
                if index < len(self.lockin_state["channels"])
                else LockinChannelConfig(channel_index=index, demod_index=index, osc_index=0).model_dump()
            )
            if self.lockin_device is None:
                previous["channel_index"] = index
                channels.append(previous)
                continue
            try:
                demod = self.lockin_device.demods[index]
                osc_index = self._enum_from_value(
                    demod.oscselect(), [{"value": idx, "enum": str(idx)} for idx in range(4)], 0
                )
                order = int(self._to_scalar(demod.order(), previous.get("low_pass_order", 4)))
                timeconstant_seconds = self._to_scalar(
                    demod.timeconstant(), previous.get("time_constant_ms", 10.0) / 1000.0
                )
                channel = {
                    **previous,
                    "channel_index": index,
                    "demod_index": index,
                    "osc_index": osc_index,
                    "enabled": self._bool_from_value(demod.enable(), ("on",)),
                    "input_signal": self._enum_from_value(demod.adcselect(), INPUT_SIGNAL_OPTIONS, previous.get("input_signal", 0)),
                    "demod_freq_hz": self._to_scalar(
                        self.lockin_device.oscs[osc_index].freq(), previous.get("demod_freq_hz", 0.0)
                    ),
                    "time_constant_ms": timeconstant_seconds * 1000.0,
                    "low_pass_order": order,
                    "low_pass_bandwidth_hz": self._bandwidth_hz(timeconstant_seconds, order),
                    "phase_deg": self._to_scalar(demod.phaseshift(), previous.get("phase_deg", 0.0)),
                    "harmonic": int(self._to_scalar(demod.harmonic(), previous.get("harmonic", 1))),
                    "sample_rate_hz": self._to_scalar(demod.rate(), previous.get("sample_rate_hz", 1000.0)),
                    "trigger_mode": self._enum_from_value(demod.trigger(), TRIGGER_MODE_OPTIONS, previous.get("trigger_mode", 0)),
                    "sinc_enabled": self._bool_from_value(demod.sinc(), ("on",)),
                    "reference_source": previous.get("reference_source", "internal"),
                    "external_reference_index": previous.get("external_reference_index", 0),
                    "aux_output_channel": previous.get("aux_output_channel", 0),
                    "aux_output_offset_v": previous.get("aux_output_offset_v", 0.0),
                }
                try:
                    channel["input_range_mv"] = self._to_scalar(self.lockin_device.sigins[0].range(), previous.get("input_range_mv", 100.0) / 1000.0) * 1000.0
                    channel["input_impedance_50ohm"] = self._bool_from_value(
                        self.lockin_device.sigins[0].imp50(),
                        ("imp50", "50_ohm", "on"),
                    )
                    channel["input_voltage_scaling"] = self._to_scalar(self.lockin_device.sigins[0].scaling(), previous.get("input_voltage_scaling", 1.0))
                    channel["input_ac_coupling"] = self._bool_from_value(self.lockin_device.sigins[0].ac(), ("on", "ac"))
                    channel["input_differential"] = self._bool_from_value(self.lockin_device.sigins[0].diff(), ("on",))
                    channel["input_float"] = self._bool_from_value(self.lockin_device.sigins[0].float(), ("on",))
                except Exception:
                    pass
                try:
                    channel["current_range_ma"] = self._to_scalar(self.lockin_device.currins[0].range(), previous.get("current_range_ma", 10.0) / 1000.0) * 1000.0
                    channel["current_scaling"] = self._to_scalar(self.lockin_device.currins[0].scaling(), previous.get("current_scaling", 1.0))
                    channel["current_float"] = self._bool_from_value(self.lockin_device.currins[0].float(), ("on",))
                except Exception:
                    pass
                try:
                    if hasattr(self.lockin_device, "extrefs") and len(self.lockin_device.extrefs):
                        extref = self.lockin_device.extrefs[0]
                        extref_enabled = self._bool_from_value(extref.enable())
                        extref_demod_index = int(
                            self._to_scalar(extref.demodselect(), previous.get("demod_index", index))
                        )
                        if extref_enabled and extref_demod_index == index:
                            channel["reference_source"] = "external"
                            channel["external_reference_index"] = self._enum_from_value(
                                extref.adcselect(),
                                INPUT_SIGNAL_OPTIONS,
                                previous.get("external_reference_index", 0),
                            )
                        else:
                            channel["reference_source"] = "internal"
                except Exception:
                    pass
                try:
                    if hasattr(self.lockin_device, "auxouts"):
                        aux_channel = int(previous.get("aux_output_channel", 0))
                        auxout = self.lockin_device.auxouts[aux_channel]
                        channel["aux_output_channel"] = aux_channel
                        channel["aux_output_offset_v"] = self._to_scalar(
                            auxout.offset(),
                            previous.get("aux_output_offset_v", 0.0),
                        )
                except Exception:
                    pass
                channels.append(channel)
            except Exception:
                previous["channel_index"] = index
                channels.append(previous)
        self.lockin_state["channels"] = channels

    def _set_signal_channels(self, channels: list[dict[str, float]]) -> None:
        with self.signal_lock:
            self.last_signal_channels = [dict(channel) for channel in channels]
            self.last_signal_update = time.time()

    def _get_cached_signal_channels(self) -> list[dict[str, float]]:
        with self.signal_lock:
            return [dict(channel) for channel in self.last_signal_channels]

    def _empty_signal_batch(self, channel_index: int) -> dict[str, Any]:
        return {
            "channel_index": channel_index,
            "times_s": [],
            "x_uv": [],
            "y_uv": [],
            "r_uv": [],
        }

    def _single_point_signal_batches(
        self, signal_channels: list[dict[str, float]], timestamp: float
    ) -> list[dict[str, Any]]:
        batches: list[dict[str, Any]] = []
        for index, channel in enumerate(signal_channels):
            batches.append(
                {
                    "channel_index": index,
                    "times_s": [timestamp],
                    "x_uv": [float(channel.get("x_uv", 0.0) or 0.0)],
                    "y_uv": [float(channel.get("y_uv", 0.0) or 0.0)],
                    "r_uv": [float(channel.get("r_uv", 0.0) or 0.0)],
                }
            )
        return batches

    def _record_signal_packet(
        self,
        signal_channels: list[dict[str, float]],
        signal_batches: list[dict[str, Any]],
        timestamp: float,
    ) -> None:
        copied_channels = [dict(channel) for channel in signal_channels]
        copied_batches = [
            {
                "channel_index": int(batch.get("channel_index", index)),
                "times_s": [float(value) for value in batch.get("times_s", [])],
                "x_uv": [float(value) for value in batch.get("x_uv", [])],
                "y_uv": [float(value) for value in batch.get("y_uv", [])],
                "r_uv": [float(value) for value in batch.get("r_uv", [])],
            }
            for index, batch in enumerate(signal_batches)
        ]
        with self.signal_lock:
            self.last_signal_channels = copied_channels
            self.last_signal_update = float(timestamp)
            self.signal_packet_seq += 1
            self.signal_packets.append(
                {
                    "seq": self.signal_packet_seq,
                    "timestamp": float(timestamp),
                    "signal_channels": copied_channels,
                    "signal_batches": copied_batches,
                }
            )

    def _merge_signal_packets(
        self, packets: list[dict[str, Any]], channel_count: int
    ) -> list[dict[str, Any]]:
        merged = [self._empty_signal_batch(index) for index in range(channel_count)]
        for packet in packets:
            for index in range(channel_count):
                if index >= len(packet.get("signal_batches", [])):
                    continue
                batch = packet["signal_batches"][index]
                merged[index]["times_s"].extend(float(value) for value in batch.get("times_s", []))
                merged[index]["x_uv"].extend(float(value) for value in batch.get("x_uv", []))
                merged[index]["y_uv"].extend(float(value) for value in batch.get("y_uv", []))
                merged[index]["r_uv"].extend(float(value) for value in batch.get("r_uv", []))
        return merged

    def _stop_sampler(self) -> None:
        self.sampler_stop_event.set()
        thread = self.sampler_thread
        self.sampler_thread = None
        if thread is None:
            return
        try:
            thread.join(timeout=3)
        except Exception:
            pass

    def _start_sampler(self) -> None:
        self._stop_sampler()
        if not self.lockin_state.get("connected") or not self.lockin_state.get("serial"):
            return
        self.sampler_stop_event = threading.Event()
        self.sampler_thread = threading.Thread(
            target=self._sampler_loop,
            name="lockin-sampler",
            daemon=True,
        )
        self.sampler_thread.start()

    def _sampler_loop(self) -> None:
        if self.lockin_session is None or self.lockin_device is None:
            while not self.sampler_stop_event.is_set():
                channels = self._sample_all_channels()
                if channels:
                    timestamp = time.time()
                    self._record_signal_packet(
                        channels,
                        self._single_point_signal_batches(channels, timestamp),
                        timestamp,
                    )
                self.sampler_stop_event.wait(self.sampling_interval_connected)
            return

        sample_nodes: list[Any] = []
        clockbase = 1.0
        try:
            with self.device_lock:
                sample_nodes = [
                    self.lockin_device.demods[index].sample
                    for index in range(self._demod_count())
                ]
                clockbase = max(1.0, float(self.lockin_device.clockbase()))
                self.lockin_session.sync()
                for node in sample_nodes:
                    node.subscribe()

            while not self.sampler_stop_event.is_set():
                try:
                    with self.device_lock:
                        polled = self.lockin_session.poll(
                            recording_time=self.sampling_interval_connected,
                            timeout=self.sampling_timeout_connected,
                        )
                    packet = self._extract_polled_signal_packet(polled, sample_nodes, clockbase)
                    if packet is not None:
                        signal_channels, signal_batches, timestamp = packet
                        self._record_signal_packet(signal_channels, signal_batches, timestamp)
                except Exception:
                    fallback = self._sample_all_channels()
                    if fallback:
                        timestamp = time.time()
                        self._record_signal_packet(
                            fallback,
                            self._single_point_signal_batches(fallback, timestamp),
                            timestamp,
                        )
                    self.sampler_stop_event.wait(self.sampling_interval_connected)
        finally:
            if sample_nodes:
                with self.device_lock:
                    for node in sample_nodes:
                        try:
                            node.unsubscribe()
                        except Exception:
                            pass

    def _release_lockin_connection(self, serial: str | None = None) -> None:
        self._stop_sampler()
        session = self.lockin_session
        target_serial = serial or self.lockin_state.get("serial", "")
        if session is not None and target_serial:
            try:
                session.disconnect_device(str(target_serial))
                time.sleep(0.2)
            except Exception:
                pass
        with self.device_lock:
            self.lockin_device = None
            self.lockin_session = None

    def discover_lockins(self, config: LabOneServerConfig) -> dict[str, Any]:
        self.lockin_state["server_host"] = config.server_host
        self.lockin_state["server_port"] = config.server_port
        self.lockin_state["hf2"] = config.hf2

        if Session is None:
            message = "未安装 zhinst-toolkit，无法发现锁相设备。"
            self.lockin_state["last_discovery"] = {
                "visible": [],
                "connected": [],
                "error": message,
            }
            self._log(message, "warning")
            return {"success": False, "message": message, "data": self.lockin_state["last_discovery"]}

        try:
            session = Session(
                config.server_host,
                config.server_port,
                hf2=config.hf2,
                allow_version_mismatch=True,
            )
            visible = [self._serialize_device_item(item) for item in session.devices.visible()]
            connected = [self._serialize_device_item(item) for item in session.devices.connected()]
            self.lockin_state["last_discovery"] = {
                "visible": visible,
                "connected": connected,
                "error": "",
            }
            self._log(f"LabOne 发现完成，可见设备 {len(visible)} 台。")
            return {
                "success": True,
                "message": "已获取 LabOne Data Server 的设备列表。",
                "data": self.lockin_state["last_discovery"],
            }
        except Exception as exc:  # pragma: no cover
            message = f"锁相发现失败: {exc}"
            self.lockin_state["last_discovery"] = {
                "visible": [],
                "connected": [],
                "error": message,
            }
            self._log(message, "error")
            return {"success": False, "message": message, "data": self.lockin_state["last_discovery"]}

    def connect_lockin(self, request: LockinConnectRequest) -> dict[str, Any]:
        discovery = self.discover_lockins(
            LabOneServerConfig(
                server_host=request.server_host,
                server_port=request.server_port,
                hf2=request.hf2,
            )
        )
        if not discovery["success"]:
            return discovery

        if Session is None:
            return {
                "success": False,
                "message": "未安装 zhinst-toolkit，无法连接锁相设备。",
                "data": self.lockin_state,
            }

        self._release_lockin_connection()
        try:
            session = Session(
                request.server_host,
                request.server_port,
                hf2=request.hf2,
                allow_version_mismatch=True,
            )
            with self.device_lock:
                device = (
                    session.connect_device(request.serial, interface=request.interface)
                    if request.interface
                    else session.connect_device(request.serial)
                )
                self.lockin_session = session
                self.lockin_device = device
                self.lockin_state.update(
                    {
                        "connected": True,
                        "serial": request.serial,
                        "name": getattr(device, "device_type", "Zurich Instruments Device"),
                        "server_host": request.server_host,
                        "server_port": request.server_port,
                        "hf2": request.hf2,
                        "interface": request.interface or "",
                    }
                )
                self._refresh_lockin_channels()
            self._set_signal_channels(self._sample_all_channels())
            self._start_sampler()
            self._log(f"锁相设备已连接: {request.serial}")
            return {
                "success": True,
                "message": f"已连接锁相设备 {request.serial}",
                "data": self.lockin_state,
            }
        except Exception as exc:  # pragma: no cover
            message = f"锁相连接失败: {exc}"
            self._release_lockin_connection()
            self.lockin_state["connected"] = False
            self._log(message, "error")
            return {"success": False, "message": message, "data": self.lockin_state}

    def disconnect_lockin(self) -> dict[str, Any]:
        self._release_lockin_connection()
        self.lockin_state.update(
            {
                "connected": False,
                "serial": "",
                "name": "",
                "interface": "",
                "active_channel": 0,
            }
        )
        zeroed = [self._zero_signal(index) for index in range(len(self.lockin_state.get("channels", [])) or 1)]
        self._set_signal_channels(zeroed)
        self._log("锁相设备已断开连接。")
        return {
            "success": True,
            "message": "已断开锁相设备连接。",
            "data": self.lockin_state,
        }

    def update_lockin(self, request: LockinChannelConfig) -> dict[str, Any]:
        channel_index = request.channel_index
        target_count = self._safe_channel_count(channel_index + 1)
        while len(self.lockin_state["channels"]) < target_count:
            index = len(self.lockin_state["channels"])
            self.lockin_state["channels"].append(
                LockinChannelConfig(channel_index=index, demod_index=index, osc_index=0).model_dump()
            )
        self.lockin_state["active_channel"] = channel_index
        payload = request.model_dump()
        if payload["low_pass_bandwidth_hz"] <= 0 and payload["time_constant_ms"] > 0:
            payload["low_pass_bandwidth_hz"] = self._bandwidth_hz(
                payload["time_constant_ms"] / 1000.0, payload["low_pass_order"]
            )
        elif payload["low_pass_bandwidth_hz"] > 0 and payload["time_constant_ms"] <= 0:
            payload["time_constant_ms"] = self._timeconstant_ms(
                payload["low_pass_bandwidth_hz"], payload["low_pass_order"]
            )
        self.lockin_state["channels"][channel_index] = payload
        notes: list[str] = []
        tracker_demod_index = request.demod_index
        if request.reference_source == "external":
            tracker_demod_index = self._resolve_extref_tracker_demod_index(request.demod_index)

        if self.lockin_device is not None:
            with self.device_lock:
                try:
                    self.lockin_device.demods[request.demod_index].enable(int(request.enabled))
                    notes.append("demod.enable")
                except Exception:
                    pass

                try:
                    self.lockin_device.demods[request.demod_index].adcselect(request.input_signal)
                    notes.append("demod.adcselect")
                except Exception:
                    pass

                try:
                    self.lockin_device.demods[request.demod_index].oscselect(request.osc_index)
                    notes.append("demod.oscselect")
                except Exception:
                    pass

                try:
                    self.lockin_device.oscs[request.osc_index].freq(request.demod_freq_hz)
                    notes.append("osc.freq")
                except Exception:
                    pass

                try:
                    self.lockin_device.demods[request.demod_index].timeconstant(
                        payload["time_constant_ms"] / 1000.0
                    )
                    notes.append("demod.timeconstant")
                except Exception:
                    pass

                try:
                    self.lockin_device.demods[request.demod_index].order(request.low_pass_order)
                    notes.append("demod.order")
                except Exception:
                    pass

                try:
                    self.lockin_device.demods[request.demod_index].phaseshift(request.phase_deg)
                    notes.append("demod.phaseshift")
                except Exception:
                    pass

                try:
                    self.lockin_device.demods[request.demod_index].harmonic(request.harmonic)
                    notes.append("demod.harmonic")
                except Exception:
                    pass

                try:
                    self.lockin_device.demods[request.demod_index].rate(request.sample_rate_hz)
                    notes.append("demod.rate")
                except Exception:
                    pass

                try:
                    self.lockin_device.demods[request.demod_index].trigger(request.trigger_mode)
                    notes.append("demod.trigger")
                except Exception:
                    pass

                try:
                    self.lockin_device.demods[request.demod_index].sinc(int(request.sinc_enabled))
                    notes.append("demod.sinc")
                except Exception:
                    pass

                try:
                    self.lockin_device.sigins[request.input_index].range(request.input_range_mv / 1000.0)
                    notes.append("sigin.range")
                except Exception:
                    pass

                try:
                    self.lockin_device.sigins[request.input_index].imp50(int(request.input_impedance_50ohm))
                    notes.append("sigin.imp50")
                except Exception:
                    pass

                try:
                    self.lockin_device.sigins[request.input_index].scaling(request.input_voltage_scaling)
                    notes.append("sigin.scaling")
                except Exception:
                    pass

                try:
                    self.lockin_device.sigins[request.input_index].ac(int(request.input_ac_coupling))
                    notes.append("sigin.ac")
                except Exception:
                    pass

                try:
                    self.lockin_device.sigins[request.input_index].diff(int(request.input_differential))
                    notes.append("sigin.diff")
                except Exception:
                    pass

                try:
                    self.lockin_device.sigins[request.input_index].float(int(request.input_float))
                    notes.append("sigin.float")
                except Exception:
                    pass

                try:
                    self.lockin_device.currins[0].range(request.current_range_ma / 1000.0)
                    notes.append("currin.range")
                except Exception:
                    pass

                try:
                    self.lockin_device.currins[0].scaling(request.current_scaling)
                    notes.append("currin.scaling")
                except Exception:
                    pass

                try:
                    self.lockin_device.currins[0].float(int(request.current_float))
                    notes.append("currin.float")
                except Exception:
                    pass

                try:
                    if hasattr(self.lockin_device, "extrefs") and len(self.lockin_device.extrefs):
                        extref = self.lockin_device.extrefs[0]
                        extref.enable(0)
                        extref.demodselect(tracker_demod_index)
                        if request.reference_source == "external":
                            try:
                                self.lockin_device.demods[tracker_demod_index].enable(1)
                            except Exception:
                                pass
                            try:
                                self.lockin_device.demods[tracker_demod_index].adcselect(request.external_reference_index)
                            except Exception:
                                pass
                            try:
                                self.lockin_device.demods[tracker_demod_index].oscselect(request.osc_index)
                            except Exception:
                                pass
                            try:
                                self.lockin_device.demods[tracker_demod_index].harmonic(request.harmonic)
                            except Exception:
                                pass
                            try:
                                extref.automode(4)
                            except Exception:
                                pass
                            extref.enable(1)
                            notes.append(f"extref.enable.demod{tracker_demod_index + 1}")
                        else:
                            notes.append("extref.disable")
                except Exception:
                    pass

                try:
                    if hasattr(self.lockin_device, "auxouts"):
                        auxout = self.lockin_device.auxouts[request.aux_output_channel]
                        try:
                            auxout.outputselect(-1)
                        except Exception:
                            pass
                        try:
                            auxout.demodselect(request.demod_index)
                        except Exception:
                            pass
                        auxout.offset(request.aux_output_offset_v)
                        notes.append("auxout.offset")
                except Exception:
                    pass

                self._refresh_lockin_channels()
                self.lockin_state["channels"][channel_index]["display_source"] = request.display_source

        note_text = ", ".join(notes) if notes else "当前只更新后端状态。"
        self._log(
            f"锁相通道 {channel_index + 1} 已更新: {request.demod_freq_hz:.1f} Hz, "
            f"BW {payload['low_pass_bandwidth_hz']:.3f} Hz"
        )
        return {
            "success": True,
            "message": f"锁相通道 {channel_index + 1} 参数已保存。{note_text}",
            "data": self.lockin_state,
        }

    def discover_microwaves(self) -> dict[str, Any]:
        if self.rm is None:
            message = "未安装 PyVISA，无法枚举 VISA 资源。"
            self.microwave_state["available_resources"] = []
            self.microwave_state["last_error"] = message
            self._log(message, "warning")
            return {"success": False, "message": message, "data": {"resources": []}}

        try:
            resources = list(self.rm.list_resources())
            self.microwave_state["available_resources"] = resources
            self.microwave_state["last_error"] = ""
            self._log(f"VISA 资源发现完成，共 {len(resources)} 个。")
            return {
                "success": True,
                "message": "已获取 VISA 资源列表。",
                "data": {"resources": resources},
            }
        except Exception as exc:  # pragma: no cover
            message = f"微波源发现失败: {exc}"
            self.microwave_state["last_error"] = message
            self._log(message, "error")
            return {"success": False, "message": message, "data": {"resources": []}}

    def connect_microwave(self, request: MicrowaveConnectRequest) -> dict[str, Any]:
        if self.rm is None:
            message = "未安装 PyVISA，无法连接微波源。"
            self._log(message, "warning")
            return {"success": False, "message": message, "data": self.microwave_state}

        resource = None
        try:
            resource = self.rm.open_resource(request.address)
            probe_timeout_ms = max(250, min(int(request.timeout_ms), 1000))
            resource.timeout = probe_timeout_ms
            idn = str(resource.query("*IDN?")).strip()
            resource.timeout = request.timeout_ms
            self.microwave_resource = resource
            self.microwave_state.update(
                {
                    "connected": True,
                    "address": request.address,
                    "idn": idn,
                    "last_error": "",
                }
            )
            self._log(f"微波源已连接: {request.address}")
            return {
                "success": True,
                "message": f"已连接微波源 {request.address}",
                "data": self.microwave_state,
            }
        except Exception as exc:  # pragma: no cover
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
            message = f"微波源连接失败: {exc}"
            self.microwave_state["connected"] = False
            self.microwave_state["last_error"] = message
            self._log(message, "error")
            return {"success": False, "message": message, "data": self.microwave_state}

    def _microwave_clear_status(self) -> None:
        return

    def _microwave_read_errors(self, limit: int = 8) -> list[str]:
        if self.microwave_resource is None:
            return []
        errors: list[str] = []
        for _ in range(limit):
            try:
                item = str(self.microwave_resource.query(":SYST:ERR?")).strip()
            except Exception:
                break
            if item.startswith("+0") or item.startswith("0"):
                break
            errors.append(item)
        return errors

    def _microwave_write_checked(self, command: str, errors: list[str]) -> bool:
        if self.microwave_resource is None:
            return False
        resource = self.microwave_resource
        original_timeout = getattr(resource, "timeout", None)
        try:
            if isinstance(original_timeout, (int, float)):
                try:
                    resource.timeout = min(int(original_timeout), 500)
                except Exception:
                    pass
            resource.write(command)
            return True
        except Exception as exc:
            errors.append(f"{command} -> write failed: {exc}")
            return False
        finally:
            if isinstance(original_timeout, (int, float)):
                try:
                    resource.timeout = original_timeout
                except Exception:
                    pass

    def _microwave_close_resource(self, resource: Any | None = None) -> None:
        target = resource if resource is not None else self.microwave_resource
        if target is None:
            return
        try:
            target.close()
        except Exception:
            pass

    def _microwave_mark_io_failure(self, message: str) -> None:
        resource = self.microwave_resource
        self.microwave_resource = None
        self._microwave_close_resource(resource)
        self.microwave_state["connected"] = False
        self.microwave_state["idn"] = ""
        self.microwave_state["last_error"] = message
        self._log(f"Microwave IO failed: {message}", "error")

    def _microwave_apply_commands(self, commands: list[str], errors: list[str]) -> bool:
        for command in commands:
            if not self._microwave_write_checked(command, errors):
                return False
        return True

    def disconnect_microwave(self) -> dict[str, Any]:
        resource = self.microwave_resource
        self.microwave_resource = None
        if resource is not None:
            self._microwave_close_resource(resource)
        self.microwave_state.update(
            {
                "connected": False,
                "address": "",
                "idn": "",
            }
        )
        self.microwave_state["config"]["output_enabled"] = False
        self._log("微波源已断开连接。")
        return {
            "success": True,
            "message": "已断开微波源连接。",
            "data": self.microwave_state,
        }

    def update_microwave(self, request: MicrowaveConfigRequest) -> dict[str, Any]:
        self.microwave_state["config"] = request.model_dump()
        notes: list[str] = []
        errors: list[str] = []

        if self.microwave_resource is not None:
            frequency_commands = (
                [":FREQ:MODE CW", f":FREQ {request.frequency_hz}"]
                if request.mode == "cw"
                else [
                    ":ABOR",
                    ":FREQ:MODE LIST",
                    ":LIST:DWEL:TYPE STEP",
                    ":TRIG:SOUR IMM",
                    ":LIST:TRIG:SOUR IMM",
                    ":LIST:TYPE STEP",
                    ":INIT:CONT ON",
                    f":SWE:DWEL {max(request.dwell_ms / 1000.0, 0.005)}",
                    f":FREQ:STAR {request.sweep_start_hz}",
                    f":FREQ:STOP {request.sweep_stop_hz}",
                    f":SWE:POIN {request.sweep_points}",
                ]
            )
            if not self._microwave_apply_commands(frequency_commands, errors):
                self._microwave_mark_io_failure(errors[0])
                return {"success": False, "message": errors[0], "data": self.microwave_state}
            notes.append("frequency")

            if not self._microwave_apply_commands([f":POW {request.power_dbm}"], errors):
                self._microwave_mark_io_failure(errors[0])
                return {"success": False, "message": errors[0], "data": self.microwave_state}
            notes.append("power")

            if not self._microwave_apply_commands([f":OUTP {'ON' if request.output_enabled else 'OFF'}"], errors):
                self._microwave_mark_io_failure(errors[0])
                return {"success": False, "message": errors[0], "data": self.microwave_state}
            notes.append("output")

            if not self._microwave_apply_commands([f":IQ:STAT {'ON' if request.iq_enabled else 'OFF'}"], errors):
                self._microwave_mark_io_failure(errors[0])
                return {"success": False, "message": errors[0], "data": self.microwave_state}
            notes.append("iq")

            fm_commands: list[str] = []
            if request.fm_enabled:
                fm_source = "FUNCTION1" if request.fm_source == "internal" else "EXT1"
                fm_commands.extend([f":FM:SOUR {fm_source}", f":FM {request.fm_deviation_hz}"])
                if request.fm_source == "internal" or request.lf_output_source == "monitor":
                    fm_commands.append(f":FM:INT:FUNC:FREQ {request.fm_rate_hz}")
            fm_commands.append(f":FM:STAT {'ON' if request.fm_enabled else 'OFF'}")
            if not self._microwave_apply_commands(fm_commands, errors):
                self._microwave_mark_io_failure(errors[0])
                return {"success": False, "message": errors[0], "data": self.microwave_state}
            notes.append("fm")

            lf_commands = [
                f":LFO:LOAD:IMP {request.lf_output_load_ohm}",
                f":LFO:OFFS {request.lf_output_offset_v}",
                f":LFO:AMPL {request.lf_output_amplitude_v}",
            ]
            if request.lf_output_source == "monitor":
                lf_commands.extend([":LFO:SOUR MON", ":LFO:SOUR:MON FUNC1"])
            elif request.lf_output_source == "function1":
                lf_commands.extend([":LFO:SOUR FUNC1", f":LFO:FUNC:FREQ {request.fm_rate_hz}"])
            else:
                lf_commands.append(":LFO:SOUR DC")
            lf_commands.append(f":LFO:STAT {'ON' if request.lf_output_enabled else 'OFF'}")
            if not self._microwave_apply_commands(lf_commands, errors):
                self._microwave_mark_io_failure(errors[0])
                return {"success": False, "message": errors[0], "data": self.microwave_state}
            notes.append("lf_output")

        note_text = ", ".join(notes) if notes else "当前只更新后端状态。"
        self.microwave_state["last_error"] = "; ".join(errors)
        if errors:
            note_text = f"{note_text} | debug: {errors[0]}"
        self._log(
            f"微波参数已更新: mode={request.mode}, "
            f"power={request.power_dbm:.1f} dBm, fm={'on' if request.fm_enabled else 'off'}, "
            f"lf_out={'on' if request.lf_output_enabled else 'off'}"
        )
        return {
            "success": True,
            "message": f"微波参数已保存。{note_text}",
            "data": self.microwave_state,
        }

    def set_microwave_output_enabled(self, enabled: bool) -> bool:
        self.microwave_state["config"]["output_enabled"] = bool(enabled)
        if self.microwave_resource is None:
            return False
        errors: list[str] = []
        ok = self._microwave_apply_commands([f":OUTP {'ON' if enabled else 'OFF'}"], errors)
        if not ok:
            self._microwave_mark_io_failure(errors[0])
        return ok

    def set_microwave_frequency(self, frequency_hz: float) -> bool:
        self.microwave_state["config"]["frequency_hz"] = float(frequency_hz)
        if self.microwave_resource is None:
            return False
        errors: list[str] = []
        ok = self._microwave_apply_commands([":FREQ:MODE CW", f":FREQ {frequency_hz}"], errors)
        if not ok:
            self._microwave_mark_io_failure(errors[0])
        return ok

    def _odmr_delay_s(self, request: ODMRRequest) -> float:
        return min(max(request.dwell_ms / 1000.0, 0.005), 1.0)

    def estimate_odmr_duration_s(self, request: ODMRRequest) -> float:
        return request.points * self._odmr_delay_s(request)

    def build_odmr_frequency_axis(self, request: ODMRRequest) -> list[float]:
        return [
            request.start_hz
            + index * (request.stop_hz - request.start_hz) / (request.points - 1)
            for index in range(request.points)
        ]

    def simulate_odmr_value(self, request: ODMRRequest, freq: float) -> float:
        center_a = 2.8704e9
        center_b = 2.8732e9
        width = 4.0e6 if request.scan_mode == "software_sync" else 4.8e6
        readout_scale = {"x_v": 0.92, "y_v": 0.87, "r_v": 1.0}[request.readout_source]
        dip_a = 0.032 / (1.0 + ((freq - center_a) / width) ** 2)
        dip_b = 0.028 / (1.0 + ((freq - center_b) / width) ** 2)
        baseline = 0.996 + 0.003 * math.sin((freq - request.start_hz) / 1.5e7)
        noise = random.uniform(-0.002, 0.002) / request.averages
        return round((baseline - dip_a - dip_b + noise) * readout_scale, 6)

    def can_run_live_odmr(self, request: ODMRRequest) -> bool:
        return (
            request.scan_mode == "software_sync"
            and self.lockin_device is not None
            and self.microwave_resource is not None
            and self.lockin_state.get("connected", False)
            and self.microwave_state.get("connected", False)
        )

    def read_odmr_value(self, readout_source: str) -> float:
        active_channel = self._resolve_measurement_channel_index(self.lockin_state.get("active_channel", 0))
        with self.device_lock:
            sample = self._read_lockin_sample(active_channel)
        return float(sample.get(readout_source, 0.0) or 0.0)

    def estimate_current_duration_s(self, request: CurrentScanRequest) -> float:
        channel_index = self._resolve_measurement_channel_index(request.channel_index)
        sweep_time = request.search_points * self._measurement_settle_s(channel_index, request.settle_ms)
        return sweep_time * 3.0

    def _current_scan_window(self, request: CurrentScanRequest) -> tuple[float, float]:
        start_hz = float(request.start_hz)
        stop_hz = float(request.stop_hz)
        if not math.isfinite(start_hz) or not math.isfinite(stop_hz):
            raise RuntimeError("电流扫描频率范围无效。")
        if stop_hz <= start_hz:
            raise RuntimeError("终止频率必须大于起始频率。")
        center_hz = (start_hz + stop_hz) / 2.0
        span_hz = stop_hz - start_hz
        return center_hz, span_hz

    def begin_current_stream(self, request: CurrentScanRequest) -> None:
        resolved_channel = self._resolve_measurement_channel_index(request.channel_index)
        self.odmr_stop_event.clear()
        self.measurement_state.update(
            {
                "running": True,
                "mode": "current",
                "status": "running",
                "progress": 0.0,
                "current_point": 0,
                "current_frequency_hz": 0.0,
                "current_value": 0.0,
                "estimated_duration_s": self.estimate_current_duration_s(request),
                "cancel_requested": False,
                "last_current_request": {**request.model_dump(), "channel_index": resolved_channel},
            }
        )

    def finish_current_stream(
        self, request: CurrentScanRequest, result: dict[str, Any], status: str = "completed"
    ) -> dict[str, Any]:
        self.measurement_state.update(
            {
                "running": False,
                "mode": "idle",
                "status": status,
                "progress": 1.0 if status == "completed" else self.measurement_state.get("progress", 0.0),
                "current_point": 0,
                "current_frequency_hz": float(
                    result.get("zero_crossing_center_hz", result.get("resonance_center_hz", 0.0)) or 0.0
                ),
                "current_value": float(result.get("zero_crossing_splitting_hz", 0.0) or 0.0),
                "cancel_requested": False,
                "last_current_request": request.model_dump(),
                "last_current_result": result,
            }
        )
        return result

    def estimate_sensitivity_duration_s(self, request: SensitivityRequest) -> float:
        channel_index = self._resolve_measurement_channel_index(request.channel_index)
        sweep_time = request.search_points * self._measurement_settle_s(channel_index, request.settle_ms)
        return sweep_time * 3.0 + float(request.asd_duration_s)

    def begin_sensitivity_stream(self, request: SensitivityRequest) -> None:
        resolved_channel = self._resolve_measurement_channel_index(request.channel_index)
        self.odmr_stop_event.clear()
        self.measurement_state.update(
            {
                "running": True,
                "mode": "sensitivity",
                "status": "running",
                "progress": 0.0,
                "current_point": 0,
                "current_frequency_hz": 0.0,
                "current_value": 0.0,
                "estimated_duration_s": self.estimate_sensitivity_duration_s(request),
                "cancel_requested": False,
                "last_sensitivity_request": {**request.model_dump(), "channel_index": resolved_channel},
            }
        )

    def finish_sensitivity_stream(
        self, request: SensitivityRequest, result: dict[str, Any], status: str = "completed"
    ) -> dict[str, Any]:
        self.measurement_state.update(
            {
                "running": False,
                "mode": "idle",
                "status": status,
                "progress": 1.0 if status == "completed" else self.measurement_state.get("progress", 0.0),
                "current_point": 0,
                "current_frequency_hz": float(result.get("zero_crossing_hz", 0.0) or 0.0),
                "current_value": float(result.get("best_sensitivity_t_per_sqrt_hz", 0.0) or 0.0),
                "cancel_requested": False,
                "last_sensitivity_request": request.model_dump(),
                "last_sensitivity_result": result,
            }
        )
        return result

    def run_sensitivity_measurement(
        self,
        request: SensitivityRequest,
        progress_callback: Any | None = None,
        stage_callback: Any | None = None,
    ) -> dict[str, Any]:
        if np is None:
            raise RuntimeError("numpy 不可用，无法计算灵敏度。")
        if self.lockin_device is None or self.lockin_session is None:
            raise RuntimeError("锁相未连接，无法计算灵敏度。")
        if self.microwave_resource is None or not self.microwave_state.get("connected"):
            raise RuntimeError("微波源未连接，无法计算灵敏度。")

        channel_index = self._resolve_measurement_channel_index(request.channel_index)
        self.lockin_state["active_channel"] = channel_index
        channels = self.lockin_state.get("channels", [])
        channel_state = channels[channel_index]
        if not channel_state.get("enabled", True):
            try:
                with self.device_lock:
                    demod_index = int(channel_state.get("demod_index", channel_index))
                    self.lockin_device.demods[demod_index].enable(1)
                channel_state["enabled"] = True
            except Exception as exc:
                raise RuntimeError(f"测量通道未启用，且无法自动开启: {exc}") from exc

        original_microwave_config = dict(self.microwave_state.get("config", {}))
        original_phase_deg = float(channel_state.get("phase_deg", 0.0) or 0.0)
        original_sample_rate_hz = float(channel_state.get("sample_rate_hz", 0.0) or 0.0)

        def emit_progress(progress: float, stage: str, frequency_hz: float = 0.0, value: float = 0.0) -> None:
            self.measurement_state.update(
                {
                    "progress": max(0.0, min(1.0, float(progress))),
                    "status": stage,
                    "current_frequency_hz": float(frequency_hz),
                    "current_value": float(value),
                    "mode": "sensitivity",
                }
            )
            if callable(progress_callback):
                progress_callback(self.measurement_state["progress"])
            if callable(stage_callback):
                stage_callback(stage)

        try:
            emit_progress(0.02, "扫描共振附近并估计最佳相位")
            initial_trace = self._collect_complex_odmr_trace(
                channel_index=channel_index,
                center_hz=request.search_center_hz,
                span_hz=request.search_span_hz,
                points=request.search_points,
                settle_ms=request.settle_ms,
                progress_callback=lambda value: emit_progress(
                    0.05 + 0.25 * float(value),
                    "扫描共振附近并估计最佳相位",
                ),
                progress_start=0.0,
                progress_span=1.0,
            )

            phase_estimate = self._estimate_phase_delta_deg_from_trace(
                initial_trace["frequency_hz"],
                initial_trace["x_v"],
                initial_trace["y_v"],
            )
            target_offset_deg = 90.0 if request.phase_target == "y_v" else 0.0
            candidate_phase_deltas: list[float] = []
            for raw_delta in (
                float(phase_estimate["phase_delta_deg"]) + target_offset_deg,
                -float(phase_estimate["phase_delta_deg"]) + target_offset_deg,
            ):
                wrapped_delta = self._wrap_phase_deg(raw_delta)
                if not any(
                    abs(self._wrap_phase_deg(wrapped_delta - existing_delta)) < 1e-6
                    for existing_delta in candidate_phase_deltas
                ):
                    candidate_phase_deltas.append(wrapped_delta)

            emit_progress(0.35, "已自动调相位，重新扫描并寻找过零点")
            phase_candidates: list[dict[str, Any]] = []
            candidate_progress_span = 0.30 / max(1, len(candidate_phase_deltas))
            for candidate_index, candidate_delta_deg in enumerate(candidate_phase_deltas):
                candidate_phase_deg = self._wrap_phase_deg(original_phase_deg + candidate_delta_deg)
                applied_phase_deg = self._set_lockin_phase_deg(channel_index, candidate_phase_deg)
                phase_trace = self._collect_complex_odmr_trace(
                    channel_index=channel_index,
                    center_hz=request.search_center_hz,
                    span_hz=request.search_span_hz,
                    points=request.search_points,
                    settle_ms=request.settle_ms,
                    progress_callback=lambda value, idx=candidate_index: emit_progress(
                        0.35 + candidate_progress_span * (idx + float(value)),
                        "已自动调相位，重新扫描并寻找过零点",
                    ),
                    progress_start=0.0,
                    progress_span=1.0,
                )
                phase_quality = self._evaluate_phase_trace(
                    frequency_hz=phase_trace["frequency_hz"],
                    x_values=phase_trace["x_v"],
                    y_values=phase_trace["y_v"],
                    search_center_hz=request.search_center_hz,
                    slope_fit_points=request.slope_fit_points,
                )
                selected_axis = (
                    str(phase_quality["best_axis"])
                    if request.phase_target == "auto"
                    else request.phase_target
                )
                axis_quality = dict(phase_quality["axes"][selected_axis])
                phase_candidates.append(
                    {
                        "phase_delta_deg": float(candidate_delta_deg),
                        "phase_deg": float(applied_phase_deg),
                        "selected_axis": selected_axis,
                        "score": float(axis_quality["score"]),
                        "zero_crossing_hz": float(axis_quality["zero_crossing_hz"]),
                        "slope_v_per_hz": float(axis_quality["slope_v_per_hz"]),
                        "orthogonal_at_zero_v": float(axis_quality["orthogonal_at_zero_v"]),
                        "has_bracketed_zero": bool(axis_quality["has_bracketed_zero"]),
                        "gradient_window_points": int(phase_quality["gradient_window_points"]),
                        "phase_trace": phase_trace,
                        "axis_quality": axis_quality,
                    }
                )

            best_phase_candidate = max(phase_candidates, key=lambda item: float(item["score"]))
            optimized_phase_deg = self._set_lockin_phase_deg(
                channel_index,
                float(best_phase_candidate["phase_deg"]),
            )
            phase_delta_deg = float(best_phase_candidate["phase_delta_deg"])
            phase_trace = dict(best_phase_candidate["phase_trace"])
            selected_axis = str(best_phase_candidate["selected_axis"])
            orthogonal_axis = "y_v" if selected_axis == "x_v" else "x_v"
            frequency_array = np.asarray(phase_trace["frequency_hz"], dtype=float)
            x_values = np.asarray(phase_trace["x_v"], dtype=float)
            y_values = np.asarray(phase_trace["y_v"], dtype=float)
            selected_signal = x_values if selected_axis == "x_v" else y_values
            orthogonal_signal = y_values if selected_axis == "x_v" else x_values

            zero_crossing = {
                "zero_crossing_hz": float(best_phase_candidate["axis_quality"]["zero_crossing_hz"]),
                "slope_v_per_hz": float(best_phase_candidate["axis_quality"]["slope_v_per_hz"]),
                "fit_start_index": int(best_phase_candidate["axis_quality"]["fit_start_index"]),
                "fit_stop_index": int(best_phase_candidate["axis_quality"]["fit_stop_index"]),
                "has_bracketed_zero": bool(best_phase_candidate["axis_quality"]["has_bracketed_zero"]),
            }
            zero_crossing_hz = float(zero_crossing["zero_crossing_hz"])
            slope_v_per_hz = float(zero_crossing["slope_v_per_hz"])
            if not math.isfinite(slope_v_per_hz) or abs(slope_v_per_hz) <= 0:
                raise RuntimeError("无法在共振附近得到有效斜率。")

            emit_progress(0.70, "锁定过零点并采集 ASD")
            if not self.set_microwave_output_enabled(True):
                raise RuntimeError(self.microwave_state.get("last_error") or "无法开启微波输出。")
            if not self.set_microwave_frequency(zero_crossing_hz):
                raise RuntimeError(self.microwave_state.get("last_error") or "无法锁定到过零点频率。")
            target_sample_rate_hz = self._recommended_sensitivity_sample_rate_hz(channel_index)
            if target_sample_rate_hz > 0:
                self._set_channel_sample_rate_hz(channel_index, target_sample_rate_hz)
            time.sleep(self._measurement_settle_s(channel_index, request.settle_ms))
            series = self._capture_lockin_time_series(channel_index, request.asd_duration_s)

            signal_series = list(series[selected_axis])
            time_trace_x, time_trace_y = self._downsample_series(
                list(series["time_s"]),
                signal_series,
                max_points=2048,
            )
            asd_frequency, asd_values = self._compute_one_sided_asd(
                signal_series,
                float(series["sample_rate_hz"]),
                float(request.asd_min_frequency_hz),
            )
            sensitivity_denominator = abs(slope_v_per_hz) * float(request.gamma_hz_per_t) * float(request.cos_alpha)
            sensitivity_values = asd_values / max(sensitivity_denominator, 1e-30)
            if not sensitivity_values.size:
                raise RuntimeError("ASD 频谱为空，无法计算灵敏度。")
            best_index = int(np.nanargmin(sensitivity_values))

            result = {
                "channel_index": channel_index,
                "selected_axis": selected_axis,
                "orthogonal_axis": orthogonal_axis,
                "optimized_phase_deg": float(optimized_phase_deg),
                "phase_delta_deg": float(phase_delta_deg),
                "estimated_phase_delta_deg": float(phase_estimate["phase_delta_deg"]),
                "phase_alignment_score": float(best_phase_candidate["score"]),
                "phase_gradient_peak_v_per_hz": float(phase_estimate["gradient_peak_v_per_hz"]),
                "phase_gradient_window_start_hz": float(phase_estimate["gradient_window_start_hz"]),
                "phase_gradient_window_stop_hz": float(phase_estimate["gradient_window_stop_hz"]),
                "phase_gradient_window_points": int(phase_estimate["gradient_window_points"]),
                "zero_crossing_hz": zero_crossing_hz,
                "slope_v_per_hz": slope_v_per_hz,
                "slope_uv_per_mhz": slope_v_per_hz * 1e12,
                "sample_rate_hz": float(series["sample_rate_hz"]),
                "best_sensitivity_t_per_sqrt_hz": float(sensitivity_values[best_index]),
                "best_sensitivity_frequency_hz": float(asd_frequency[best_index]),
                "phase_candidates": [
                    {
                        "phase_delta_deg": float(candidate["phase_delta_deg"]),
                        "phase_deg": float(candidate["phase_deg"]),
                        "selected_axis": str(candidate["selected_axis"]),
                        "score": float(candidate["score"]),
                        "zero_crossing_hz": float(candidate["zero_crossing_hz"]),
                        "slope_v_per_hz": float(candidate["slope_v_per_hz"]),
                        "orthogonal_at_zero_v": float(candidate["orthogonal_at_zero_v"]),
                        "has_bracketed_zero": bool(candidate["has_bracketed_zero"]),
                        "gradient_window_points": int(candidate["gradient_window_points"]),
                    }
                    for candidate in phase_candidates
                ],
                "phase_trace": {
                    "frequency_hz": phase_trace["frequency_hz"],
                    "x_v": phase_trace["x_v"],
                    "y_v": phase_trace["y_v"],
                    "selected_v": selected_signal.tolist(),
                    "orthogonal_v": orthogonal_signal.tolist(),
                    "zero_crossing_hz": zero_crossing_hz,
                    "fit_start_index": int(zero_crossing["fit_start_index"]),
                    "fit_stop_index": int(zero_crossing["fit_stop_index"]),
                },
                "time_trace": {
                    "time_s": time_trace_x,
                    "signal_v": time_trace_y,
                },
                "asd_spectrum": {
                    "frequency_hz": asd_frequency.tolist(),
                    "asd_v_per_sqrt_hz": asd_values.tolist(),
                    "sensitivity_t_per_sqrt_hz": sensitivity_values.tolist(),
                },
            }
            emit_progress(1.0, "灵敏度计算完成", zero_crossing_hz, result["best_sensitivity_t_per_sqrt_hz"])
            self.measurement_state["last_sensitivity_result"] = result
            self._log(
                f"灵敏度测量完成: axis={selected_axis}, "
                f"f0={zero_crossing_hz / 1e9:.6f} GHz, "
                f"k={slope_v_per_hz * 1e12:.3f} uV/MHz"
            )
            return result
        finally:
            try:
                if original_sample_rate_hz > 0:
                    self._set_channel_sample_rate_hz(channel_index, original_sample_rate_hz)
            except Exception:
                pass
            try:
                if original_microwave_config:
                    self.update_microwave(MicrowaveConfigRequest(**original_microwave_config))
            except Exception:
                pass

    def run_current_measurement(
        self,
        request: CurrentScanRequest,
        progress_callback: Any | None = None,
        stage_callback: Any | None = None,
    ) -> dict[str, Any]:
        if np is None:
            raise RuntimeError("numpy 不可用，无法执行电流扫描。")
        if self.lockin_device is None or self.lockin_session is None:
            raise RuntimeError("锁相未连接，无法执行电流扫描。")
        if self.microwave_resource is None or not self.microwave_state.get("connected"):
            raise RuntimeError("微波源未连接，无法执行电流扫描。")

        channel_index = self._resolve_measurement_channel_index(request.channel_index)
        self.lockin_state["active_channel"] = channel_index
        channels = self.lockin_state.get("channels", [])
        channel_state = channels[channel_index]
        if not channel_state.get("enabled", True):
            try:
                with self.device_lock:
                    demod_index = int(channel_state.get("demod_index", channel_index))
                    self.lockin_device.demods[demod_index].enable(1)
                channel_state["enabled"] = True
            except Exception as exc:
                raise RuntimeError(f"测量通道未启用，且无法自动开启: {exc}") from exc

        original_microwave_config = dict(self.microwave_state.get("config", {}))
        original_phase_deg = float(channel_state.get("phase_deg", 0.0) or 0.0)
        scan_center_hz, scan_span_hz = self._current_scan_window(request)

        def emit_progress(progress: float, stage: str, frequency_hz: float = 0.0, value: float = 0.0) -> None:
            self.measurement_state.update(
                {
                    "progress": max(0.0, min(1.0, float(progress))),
                    "status": stage,
                    "current_frequency_hz": float(frequency_hz),
                    "current_value": float(value),
                    "mode": "current",
                }
            )
            if callable(progress_callback):
                progress_callback(self.measurement_state["progress"])
            if callable(stage_callback):
                stage_callback(stage)

        try:
            emit_progress(0.02, "扫描双峰并估计最佳相位")
            initial_trace = self._collect_complex_odmr_trace(
                channel_index=channel_index,
                center_hz=scan_center_hz,
                span_hz=scan_span_hz,
                points=request.search_points,
                settle_ms=request.settle_ms,
                progress_callback=lambda value: emit_progress(
                    0.05 + 0.25 * float(value),
                    "扫描双峰并估计最佳相位",
                ),
                progress_start=0.0,
                progress_span=1.0,
            )

            initial_resonances = self._find_split_resonance_pair(
                frequency_hz=initial_trace["frequency_hz"],
                signal_v=initial_trace["r_v"],
                search_center_hz=scan_center_hz,
            )
            resonance_center_hz = float(initial_resonances["center_hz"])

            phase_estimate = self._estimate_phase_delta_deg_from_trace(
                initial_trace["frequency_hz"],
                initial_trace["x_v"],
                initial_trace["y_v"],
            )
            target_offset_deg = 90.0 if request.phase_target == "y_v" else 0.0
            candidate_phase_deltas: list[float] = []
            for raw_delta in (
                float(phase_estimate["phase_delta_deg"]) + target_offset_deg,
                -float(phase_estimate["phase_delta_deg"]) + target_offset_deg,
            ):
                wrapped_delta = self._wrap_phase_deg(raw_delta)
                if not any(
                    abs(self._wrap_phase_deg(wrapped_delta - existing_delta)) < 1e-6
                    for existing_delta in candidate_phase_deltas
                ):
                    candidate_phase_deltas.append(wrapped_delta)

            emit_progress(0.35, "自动调相并定位左右过零点", resonance_center_hz)
            phase_candidates: list[dict[str, Any]] = []
            candidate_progress_span = 0.55 / max(1, len(candidate_phase_deltas))
            for candidate_index, candidate_delta_deg in enumerate(candidate_phase_deltas):
                candidate_phase_deg = self._wrap_phase_deg(original_phase_deg + candidate_delta_deg)
                applied_phase_deg = self._set_lockin_phase_deg(channel_index, candidate_phase_deg)
                phase_trace = self._collect_complex_odmr_trace(
                    channel_index=channel_index,
                    center_hz=scan_center_hz,
                    span_hz=scan_span_hz,
                    points=request.search_points,
                    settle_ms=request.settle_ms,
                    progress_callback=lambda value, idx=candidate_index: emit_progress(
                        0.35 + candidate_progress_span * (idx + float(value)),
                        "自动调相并定位左右过零点",
                        scan_center_hz,
                    ),
                    progress_start=0.0,
                    progress_span=1.0,
                )
                phase_quality = self._evaluate_split_phase_trace(
                    frequency_hz=phase_trace["frequency_hz"],
                    x_values=phase_trace["x_v"],
                    y_values=phase_trace["y_v"],
                    search_center_hz=scan_center_hz,
                    slope_fit_points=request.slope_fit_points,
                )
                selected_axis = (
                    str(phase_quality["best_axis"])
                    if request.phase_target == "auto"
                    else request.phase_target
                )
                axis_quality = dict(phase_quality["axes"][selected_axis])
                phase_candidates.append(
                    {
                        "phase_delta_deg": float(candidate_delta_deg),
                        "phase_deg": float(applied_phase_deg),
                        "selected_axis": selected_axis,
                        "score": float(axis_quality["score"]),
                        "gradient_window_points": int(phase_quality["gradient_window_points"]),
                        "phase_trace": phase_trace,
                        "axis_quality": axis_quality,
                    }
                )

            best_phase_candidate = max(phase_candidates, key=lambda item: float(item["score"]))
            optimized_phase_deg = self._set_lockin_phase_deg(
                channel_index,
                float(best_phase_candidate["phase_deg"]),
            )
            phase_delta_deg = float(best_phase_candidate["phase_delta_deg"])
            phase_trace = dict(best_phase_candidate["phase_trace"])
            selected_axis = str(best_phase_candidate["selected_axis"])
            orthogonal_axis = "y_v" if selected_axis == "x_v" else "x_v"
            frequency_array = np.asarray(phase_trace["frequency_hz"], dtype=float)
            x_values = np.asarray(phase_trace["x_v"], dtype=float)
            y_values = np.asarray(phase_trace["y_v"], dtype=float)
            selected_signal = x_values if selected_axis == "x_v" else y_values
            orthogonal_signal = y_values if selected_axis == "x_v" else x_values

            axis_quality = dict(best_phase_candidate["axis_quality"])
            left_zero_crossing_hz = float(axis_quality["left_zero_crossing_hz"])
            right_zero_crossing_hz = float(axis_quality["right_zero_crossing_hz"])
            zero_crossing_center_hz = float(axis_quality["zero_crossing_center_hz"])
            zero_crossing_splitting_hz = float(axis_quality["zero_crossing_splitting_hz"])
            left_slope_v_per_hz = float(axis_quality["left_slope_v_per_hz"])
            right_slope_v_per_hz = float(axis_quality["right_slope_v_per_hz"])
            slope_v_per_hz = (abs(left_slope_v_per_hz) + abs(right_slope_v_per_hz)) / 2.0
            if zero_crossing_splitting_hz <= 0:
                raise RuntimeError("无法在双峰附近得到有效劈裂。")
            if not math.isfinite(slope_v_per_hz) or slope_v_per_hz <= 0:
                raise RuntimeError("无法在左右过零点附近得到有效斜率。")

            result = {
                "channel_index": channel_index,
                "selected_axis": selected_axis,
                "orthogonal_axis": orthogonal_axis,
                "left_resonance_hz": float(axis_quality["left_resonance_hz"]),
                "right_resonance_hz": float(axis_quality["right_resonance_hz"]),
                "resonance_frequency_hz": float(axis_quality["resonance_center_hz"]),
                "resonance_center_hz": float(axis_quality["resonance_center_hz"]),
                "resonance_splitting_hz": float(axis_quality["resonance_splitting_hz"]),
                "optimized_phase_deg": float(optimized_phase_deg),
                "phase_delta_deg": float(phase_delta_deg),
                "estimated_phase_delta_deg": float(phase_estimate["phase_delta_deg"]),
                "phase_alignment_score": float(best_phase_candidate["score"]),
                "phase_gradient_peak_v_per_hz": float(phase_estimate["gradient_peak_v_per_hz"]),
                "phase_gradient_window_start_hz": float(phase_estimate["gradient_window_start_hz"]),
                "phase_gradient_window_stop_hz": float(phase_estimate["gradient_window_stop_hz"]),
                "phase_gradient_window_points": int(phase_estimate["gradient_window_points"]),
                "left_zero_crossing_hz": left_zero_crossing_hz,
                "right_zero_crossing_hz": right_zero_crossing_hz,
                "zero_crossing_hz": zero_crossing_center_hz,
                "zero_crossing_center_hz": zero_crossing_center_hz,
                "zero_crossing_splitting_hz": zero_crossing_splitting_hz,
                "slope_v_per_hz": slope_v_per_hz,
                "left_slope_v_per_hz": left_slope_v_per_hz,
                "right_slope_v_per_hz": right_slope_v_per_hz,
                "slope_uv_per_mhz": slope_v_per_hz * 1e12,
                "phase_candidates": [
                    {
                        "phase_delta_deg": float(candidate["phase_delta_deg"]),
                        "phase_deg": float(candidate["phase_deg"]),
                        "selected_axis": str(candidate["selected_axis"]),
                        "score": float(candidate["score"]),
                        "left_resonance_hz": float(candidate["axis_quality"]["left_resonance_hz"]),
                        "right_resonance_hz": float(candidate["axis_quality"]["right_resonance_hz"]),
                        "resonance_splitting_hz": float(candidate["axis_quality"]["resonance_splitting_hz"]),
                        "left_zero_crossing_hz": float(candidate["axis_quality"]["left_zero_crossing_hz"]),
                        "right_zero_crossing_hz": float(candidate["axis_quality"]["right_zero_crossing_hz"]),
                        "zero_crossing_splitting_hz": float(candidate["axis_quality"]["zero_crossing_splitting_hz"]),
                        "left_slope_v_per_hz": float(candidate["axis_quality"]["left_slope_v_per_hz"]),
                        "right_slope_v_per_hz": float(candidate["axis_quality"]["right_slope_v_per_hz"]),
                        "left_orthogonal_at_zero_v": float(candidate["axis_quality"]["left_orthogonal_at_zero_v"]),
                        "right_orthogonal_at_zero_v": float(candidate["axis_quality"]["right_orthogonal_at_zero_v"]),
                        "left_has_bracketed_zero": bool(candidate["axis_quality"]["left_has_bracketed_zero"]),
                        "right_has_bracketed_zero": bool(candidate["axis_quality"]["right_has_bracketed_zero"]),
                        "gradient_window_points": int(candidate["gradient_window_points"]),
                    }
                    for candidate in phase_candidates
                ],
                "initial_trace": initial_trace,
                "phase_trace": {
                    "frequency_hz": phase_trace["frequency_hz"],
                    "x_v": phase_trace["x_v"],
                    "y_v": phase_trace["y_v"],
                    "selected_v": selected_signal.tolist(),
                    "orthogonal_v": orthogonal_signal.tolist(),
                    "left_resonance_hz": float(axis_quality["left_resonance_hz"]),
                    "right_resonance_hz": float(axis_quality["right_resonance_hz"]),
                    "left_zero_crossing_hz": left_zero_crossing_hz,
                    "right_zero_crossing_hz": right_zero_crossing_hz,
                    "zero_crossing_center_hz": zero_crossing_center_hz,
                    "zero_crossing_splitting_hz": zero_crossing_splitting_hz,
                    "left_fit_start_index": int(axis_quality["left_fit_start_index"]),
                    "left_fit_stop_index": int(axis_quality["left_fit_stop_index"]),
                    "right_fit_start_index": int(axis_quality["right_fit_start_index"]),
                    "right_fit_stop_index": int(axis_quality["right_fit_stop_index"]),
                },
            }
            emit_progress(1.0, "电流扫描完成", zero_crossing_center_hz, zero_crossing_splitting_hz)
            self.measurement_state["last_current_result"] = result
            self._log(
                f"电流扫描完成: axis={selected_axis}, "
                f"split={zero_crossing_splitting_hz / 1e6:.3f} MHz, "
                f"left={left_zero_crossing_hz / 1e9:.6f} GHz, "
                f"right={right_zero_crossing_hz / 1e9:.6f} GHz"
            )
            return result
        finally:
            try:
                if original_microwave_config:
                    self.update_microwave(MicrowaveConfigRequest(**original_microwave_config))
            except Exception:
                pass

    def update_odmr_progress(self, request: ODMRRequest, index: int, freq: float, value: float) -> None:
        self.measurement_state.update(
            {
                "running": True,
                "mode": "odmr",
                "status": "running",
                "progress": index / max(1, request.points),
                "current_point": index,
                "current_frequency_hz": float(freq),
                "current_value": float(value),
                "cancel_requested": self.odmr_stop_event.is_set(),
            }
        )

    def cancel_odmr_stream(self) -> dict[str, Any]:
        self.odmr_stop_event.set()
        self.measurement_state["cancel_requested"] = True
        if self.measurement_state.get("running"):
            self.measurement_state["status"] = "cancelling"
        return {
            "success": True,
            "message": "已请求停止 ODMR 扫描。",
            "data": self.measurement_state,
        }

    def cancel_odmr_stream_result(
        self, request: ODMRRequest, frequencies: list[float], values: list[float]
    ) -> dict[str, Any]:
        trace = {
            "frequency_hz": frequencies,
            "intensity": values,
            "scan_mode": request.scan_mode,
            "readout_source": request.readout_source,
        }
        self.measurement_state.update(
            {
                "running": False,
                "mode": "idle",
                "status": "cancelled",
                "progress": len(values) / max(1, request.points),
                "current_point": len(values),
                "current_frequency_hz": frequencies[-1] if frequencies else 0.0,
                "current_value": values[-1] if values else 0.0,
                "cancel_requested": False,
                "last_trace": trace,
            }
        )
        self._log("ODMR 扫描已停止。", "warning")
        return trace

    def run_odmr(self, request: ODMRRequest) -> dict[str, Any]:
        self.measurement_state["running"] = True
        self.measurement_state["mode"] = "odmr"
        self.measurement_state["status"] = "running"
        self.measurement_state["progress"] = 0.0
        self.measurement_state["estimated_duration_s"] = self.estimate_odmr_duration_s(request)
        self.measurement_state["last_request"] = request.model_dump()
        trace = self._generate_trace(request)
        self.measurement_state["last_trace"] = trace
        self.measurement_state["running"] = False
        self.measurement_state["mode"] = "idle"
        self.measurement_state["status"] = "completed"
        self.measurement_state["progress"] = 1.0
        self._log(
            f"ODMR 扫频完成: mode={request.scan_mode}, "
            f"{request.start_hz / 1e9:.6f}-{request.stop_hz / 1e9:.6f} GHz"
        )
        return {
            "success": True,
            "message": "ODMR 扫频完成。",
            "data": {"trace": trace, "measurement": self.measurement_state},
        }

    def generate_odmr_stream(
        self, request: ODMRRequest
    ) -> tuple[list[float], list[float]]:
        frequencies = self.build_odmr_frequency_axis(request)
        values = [self.simulate_odmr_value(request, freq) for freq in frequencies]
        return frequencies, values

    def begin_odmr_stream(self, request: ODMRRequest) -> None:
        self.odmr_stop_event.clear()
        self.measurement_state.update(
            {
                "running": True,
                "mode": "odmr",
                "status": "running",
                "progress": 0.0,
                "current_point": 0,
                "current_frequency_hz": 0.0,
                "current_value": 0.0,
                "estimated_duration_s": self.estimate_odmr_duration_s(request),
                "cancel_requested": False,
                "last_request": request.model_dump(),
            }
        )

    def finish_odmr_stream(
        self, request: ODMRRequest, frequencies: list[float], values: list[float]
    ) -> dict[str, Any]:
        trace = {
            "frequency_hz": frequencies,
            "intensity": values,
            "scan_mode": request.scan_mode,
            "readout_source": request.readout_source,
        }
        self.measurement_state.update(
            {
                "last_trace": trace,
                "running": False,
                "mode": "idle",
                "status": "completed",
                "progress": 1.0,
                "current_point": len(values),
                "current_frequency_hz": frequencies[-1] if frequencies else 0.0,
                "current_value": values[-1] if values else 0.0,
                "cancel_requested": False,
            }
        )
        self._log(
            f"ODMR 扫频完成: mode={request.scan_mode}, "
            f"{request.start_hz / 1e9:.6f}-{request.stop_hz / 1e9:.6f} GHz"
        )
        return trace

    def _zero_signal(self, channel_index: int) -> dict[str, float]:
        return {
            "x_v": 0.0,
            "y_v": 0.0,
            "r_v": 0.0,
            "x_uv": 0.0,
            "y_uv": 0.0,
            "r_uv": 0.0,
            "channel_index": channel_index,
        }

    def _cached_signal_for_channel(self, channel_index: int) -> dict[str, float]:
        cached = self._get_cached_signal_channels()
        if 0 <= channel_index < len(cached):
            return dict(cached[channel_index])
        return self._zero_signal(channel_index)

    def _read_lockin_sample(self, channel_index: int) -> dict[str, float]:
        if self.lockin_device is None:
            return self._cached_signal_for_channel(channel_index)
        channel_state = self.lockin_state.get("channels", [])
        if 0 <= channel_index < len(channel_state) and not channel_state[channel_index].get("enabled", True):
            return self._zero_signal(channel_index)
        try:
            demod_index = self._demod_index_for_channel(channel_index)
            sample = self.lockin_device.demods[demod_index].sample()
            x_val = self._to_scalar(sample.get("x", 0.0), 0.0)
            y_val = self._to_scalar(sample.get("y", 0.0), 0.0)
            r_val = math.sqrt(x_val**2 + y_val**2)
            return {
                "x_v": x_val,
                "y_v": y_val,
                "r_v": r_val,
                "x_uv": x_val * 1e6,
                "y_uv": y_val * 1e6,
                "r_uv": r_val * 1e6,
                "channel_index": channel_index,
            }
        except Exception:
            return self._cached_signal_for_channel(channel_index)

    def _sample_all_channels(self) -> list[dict[str, float]]:
        if self.lockin_device is None:
            return self._simulate_signal_channels()
        channel_count = self._demod_count()
        channels: list[dict[str, float]] = []
        with self.device_lock:
            for index in range(channel_count):
                channels.append(self._read_lockin_sample(index))
        return channels

    def _read_signal_channels(self) -> list[dict[str, float]]:
        channels = self._get_cached_signal_channels()
        if channels:
            return channels
        fallback = self._sample_all_channels()
        self._set_signal_channels(fallback)
        return fallback

    def _extract_polled_signal_packet(
        self,
        polled: Any,
        sample_nodes: list[Any],
        clockbase: float,
    ) -> tuple[list[dict[str, float]], list[dict[str, Any]], float] | None:
        if not hasattr(polled, "get"):
            return None

        channel_state = self.lockin_state.get("channels", [])
        signal_channels: list[dict[str, float]] = []
        signal_batches: list[dict[str, Any]] = []
        latest_timestamp = 0.0
        has_samples = False

        for index, node in enumerate(sample_nodes):
            payload = polled.get(node, {}) or {}
            timestamps = self._to_float_list(payload.get("timestamp"))
            x_values = self._to_float_list(payload.get("x"))
            y_values = self._to_float_list(payload.get("y"))
            sample_count = min(len(timestamps), len(x_values), len(y_values))

            if sample_count:
                has_samples = True
                batch_times = [value / clockbase for value in timestamps[:sample_count]]
                batch_x_uv = [value * 1e6 for value in x_values[:sample_count]]
                batch_y_uv = [value * 1e6 for value in y_values[:sample_count]]
                batch_r_uv = [
                    math.hypot(x_values[item], y_values[item]) * 1e6
                    for item in range(sample_count)
                ]
                latest_x = x_values[sample_count - 1]
                latest_y = y_values[sample_count - 1]
                latest_r = math.hypot(latest_x, latest_y)
                signal_channels.append(
                    {
                        "x_v": latest_x,
                        "y_v": latest_y,
                        "r_v": latest_r,
                        "x_uv": latest_x * 1e6,
                        "y_uv": latest_y * 1e6,
                        "r_uv": latest_r * 1e6,
                        "channel_index": index,
                    }
                )
                signal_batches.append(
                    {
                        "channel_index": index,
                        "times_s": batch_times,
                        "x_uv": batch_x_uv,
                        "y_uv": batch_y_uv,
                        "r_uv": batch_r_uv,
                    }
                )
                latest_timestamp = max(latest_timestamp, batch_times[sample_count - 1])
                continue

            if 0 <= index < len(channel_state) and not channel_state[index].get("enabled", True):
                signal_channels.append(self._zero_signal(index))
            else:
                signal_channels.append(self._cached_signal_for_channel(index))
            signal_batches.append(self._empty_signal_batch(index))

        if not signal_channels or not has_samples:
            return None
        return signal_channels, signal_batches, latest_timestamp

    def _sample_rate_for_channel(self, channel_index: int) -> float:
        channels = self.lockin_state.get("channels", [])
        if 0 <= channel_index < len(channels):
            return float(channels[channel_index].get("sample_rate_hz", 0.0) or 0.0)
        return 0.0

    def get_lockin_live(self, last_packet_seq: int | None = None) -> dict[str, Any]:
        signal_channels = self._read_signal_channels()
        with self.signal_lock:
            packets = list(self.signal_packets)
            latest_seq = self.signal_packet_seq
            timestamp = float(self.last_signal_update or 0.0)

        if last_packet_seq is None:
            selected_packets = packets[-self.initial_signal_packet_count :]
        else:
            oldest_seq = packets[0]["seq"] if packets else latest_seq
            if packets and last_packet_seq < oldest_seq:
                selected_packets = packets
            else:
                selected_packets = [packet for packet in packets if packet["seq"] > last_packet_seq]

        signal_batches = self._merge_signal_packets(selected_packets, len(signal_channels))
        if last_packet_seq is None and signal_channels and not any(batch["times_s"] for batch in signal_batches):
            signal_batches = self._single_point_signal_batches(
                signal_channels,
                timestamp or time.time(),
            )

        active_channel = min(self.lockin_state["active_channel"], len(signal_channels) - 1)
        return {
            "success": True,
            "message": "已返回锁相实时数据。",
            "data": {
                "connected": self.lockin_state.get("connected", False),
                "serial": self.lockin_state.get("serial", ""),
                "signal": signal_channels[active_channel],
                "signal_channels": signal_channels,
                "signal_batches": signal_batches,
                "active_channel": active_channel,
                "sample_rate_hz": self._sample_rate_for_channel(active_channel),
                "stream_seq": latest_seq,
                "stream_timestamp": timestamp,
            },
        }

    def get_dashboard(self) -> dict[str, Any]:
        signal_channels = self._read_signal_channels()
        active_channel = min(self.lockin_state["active_channel"], len(signal_channels) - 1)
        active_signal = signal_channels[active_channel]
        return {
            "lockin": self.lockin_state,
            "microwave": self.microwave_state,
            "measurement": self.measurement_state,
            "signal": active_signal,
            "signal_channels": signal_channels,
            "logs": list(self.logs),
            "capabilities": {
                "zhinst_toolkit": Session is not None,
                "pyvisa": pyvisa is not None,
            },
        }

    def _simulate_signal_channels(self) -> list[dict[str, float]]:
        microwave_cfg = self.microwave_state["config"]
        base_frequency = (
            microwave_cfg["frequency_hz"]
            if microwave_cfg["mode"] == "cw"
            else microwave_cfg["center_frequency_hz"]
        )
        channels: list[dict[str, float]] = []
        for index, channel in enumerate(self.lockin_state["channels"]):
            freq_offset_mhz = (base_frequency - 2.87e9) / 1e6
            detune = (channel["demod_freq_hz"] - 13_700.0) / 13_700.0
            phase_rad = math.radians(channel["phase_deg"] + index * 9.0)
            gain = 2.5e-6 * max(0.45, 1.0 - abs(freq_offset_mhz) * 0.01)
            noise = random.uniform(-0.35e-6, 0.35e-6)
            x_val = gain * math.cos(phase_rad) * (1.0 - detune * 0.15) + noise
            y_val = gain * math.sin(phase_rad) * (0.75 + detune * 0.1) + noise
            r_val = math.sqrt(x_val**2 + y_val**2)
            channels.append(
                {
                    "x_v": round(x_val, 9),
                    "y_v": round(y_val, 9),
                    "r_v": round(r_val, 9),
                    "x_uv": round(x_val * 1e6, 3),
                    "y_uv": round(y_val * 1e6, 3),
                    "r_uv": round(r_val * 1e6, 3),
                    "channel_index": index,
                }
            )
        return channels

    def _generate_trace(self, request: ODMRRequest) -> dict[str, list[float] | str]:
        frequencies = [
            request.start_hz
            + index * (request.stop_hz - request.start_hz) / (request.points - 1)
            for index in range(request.points)
        ]

        center_a = 2.8704e9
        center_b = 2.8732e9
        width = 4.0e6 if request.scan_mode == "software_sync" else 4.8e6
        readout_scale = {"x_v": 0.92, "y_v": 0.87, "r_v": 1.0}[request.readout_source]

        values: list[float] = []
        for freq in frequencies:
            dip_a = 0.032 / (1.0 + ((freq - center_a) / width) ** 2)
            dip_b = 0.028 / (1.0 + ((freq - center_b) / width) ** 2)
            baseline = 0.996 + 0.003 * math.sin((freq - request.start_hz) / 1.5e7)
            noise = random.uniform(-0.002, 0.002) / request.averages
            values.append(round((baseline - dip_a - dip_b + noise) * readout_scale, 6))

        return {
            "frequency_hz": frequencies,
            "intensity": values,
            "scan_mode": request.scan_mode,
            "readout_source": request.readout_source,
        }


manager = InstrumentManager()
