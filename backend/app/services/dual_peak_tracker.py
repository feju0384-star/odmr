from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


class PeakId(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class PeakState(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    ACQUIRING = "ACQUIRING"
    LOCKED = "LOCKED"
    SUSPECT = "SUSPECT"
    LOCAL_REACQUIRE = "LOCAL_REACQUIRE"
    LOST = "LOST"


class GlobalState(str, Enum):
    BOOT = "BOOT"
    FULL_SCAN = "FULL_SCAN"
    CALIBRATE = "CALIBRATE"
    TRACK = "TRACK"
    FULL_REACQUIRE = "FULL_REACQUIRE"
    FAULT = "FAULT"
    STOPPED = "STOPPED"


@dataclass
class ComplexPeakModel:
    center_reference_hz: float
    b: complex
    g: complex
    fwhm_hz: float
    depth_reference: float
    dc_center_reference: float
    dc_baseline_at_center: float
    error_linear_limit_hz: float
    orthogonal_limit_hz: float
    sigma_error_hz: float
    sigma_q_hz: float
    local_band_min_hz: float
    local_band_max_hz: float
    model_fit_r2: float
    model_max_residual: float
    version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "center_reference_hz": self.center_reference_hz,
            "b_re": self.b.real,
            "b_im": self.b.imag,
            "g_re_per_hz": self.g.real,
            "g_im_per_hz": self.g.imag,
            "g_magnitude_per_hz": abs(self.g),
            "g_phase_rad": cmath.phase(self.g),
            "fwhm_hz": self.fwhm_hz,
            "depth_reference": self.depth_reference,
            "dc_center_reference": self.dc_center_reference,
            "dc_baseline_at_center": self.dc_baseline_at_center,
            "error_linear_limit_hz": self.error_linear_limit_hz,
            "orthogonal_limit_hz": self.orthogonal_limit_hz,
            "sigma_error_hz": self.sigma_error_hz,
            "sigma_q_hz": self.sigma_q_hz,
            "local_band_min_hz": self.local_band_min_hz,
            "local_band_max_hz": self.local_band_max_hz,
            "model_fit_r2": self.model_fit_r2,
            "model_max_residual": self.model_max_residual,
            "version": self.version,
        }


@dataclass
class PeakMeasurement:
    timestamp_s: float
    commanded_frequency_hz: float
    x1: float
    y1: float
    dc: float
    source_settled: bool = True
    adc_valid: bool = True
    detector_overload: bool = False
    source_fault: bool = False
    valid_blocks: int = 1
    total_blocks: int = 1

    @property
    def z1(self) -> complex:
        return complex(self.x1, self.y1)

    def basic_valid(self, minimum_valid_block_fraction: float = 0.7) -> bool:
        values = (
            self.timestamp_s,
            self.commanded_frequency_hz,
            self.x1,
            self.y1,
            self.dc,
        )
        block_fraction = self.valid_blocks / max(1, self.total_blocks)
        return (
            all(math.isfinite(value) for value in values)
            and self.source_settled
            and self.adc_valid
            and not self.detector_overload
            and not self.source_fault
            and block_fraction >= minimum_valid_block_fraction
        )


@dataclass
class FrequencyError:
    valid: bool
    e_hz: float = math.nan
    q_hz: float = math.nan
    residual_magnitude: float = math.nan
    center_measurement_hz: float = math.nan
    reason: str = ""


@dataclass
class QualityResult:
    measurement_valid: bool
    error_valid: bool
    depth_valid: bool
    orthogonal_valid: bool
    slope_recent: bool
    hardware_valid: bool
    identity_valid: bool
    good: bool
    severe_failure: bool
    score_0_to_1: float
    reason: str


@dataclass
class MotionEstimate:
    center_hz: float = 0.0
    velocity_hz_per_s: float = 0.0
    center_variance_hz2: float = 0.0
    velocity_variance_hz2: float = 0.0
    timestamp_s: float = 0.0
    initialized: bool = False

    def update(
        self,
        center_hz: float,
        timestamp_s: float,
        velocity_filter_tau_s: float,
        maximum_velocity_hz_per_s: float,
        maximum_acceleration_hz_per_s2: float,
    ) -> None:
        center_hz = float(center_hz)
        timestamp_s = float(timestamp_s)
        if not math.isfinite(center_hz) or not math.isfinite(timestamp_s):
            raise ValueError("运动估计收到 NaN/Inf。")
        if not self.initialized:
            self.center_hz = center_hz
            self.timestamp_s = timestamp_s
            self.initialized = True
            return
        dt_s = timestamp_s - self.timestamp_s
        if dt_s <= 0:
            raise ValueError("运动估计时间戳非单调。")
        residual_hz = center_hz - self.center_hz
        raw_velocity = residual_hz / dt_s
        raw_velocity = max(
            -maximum_velocity_hz_per_s,
            min(maximum_velocity_hz_per_s, raw_velocity),
        )
        acceleration_limit = maximum_acceleration_hz_per_s2 * dt_s
        raw_velocity = max(
            self.velocity_hz_per_s - acceleration_limit,
            min(self.velocity_hz_per_s + acceleration_limit, raw_velocity),
        )
        alpha = dt_s / max(dt_s + velocity_filter_tau_s, 1e-12)
        previous_velocity = self.velocity_hz_per_s
        self.velocity_hz_per_s += alpha * (raw_velocity - self.velocity_hz_per_s)
        self.center_variance_hz2 = (
            (1.0 - alpha) * self.center_variance_hz2 + alpha * residual_hz * residual_hz
        )
        velocity_residual = raw_velocity - previous_velocity
        self.velocity_variance_hz2 = (
            (1.0 - alpha) * self.velocity_variance_hz2
            + alpha * velocity_residual * velocity_residual
        )
        self.center_hz = center_hz
        self.timestamp_s = timestamp_s

    def predict(self, timestamp_s: float, maximum_age_s: float) -> tuple[float, float, float]:
        if not self.initialized:
            raise ValueError("运动估计尚未初始化。")
        age_s = float(timestamp_s) - self.timestamp_s
        if age_s < 0:
            raise ValueError("输出时间早于峰测量时间。")
        if age_s > maximum_age_s:
            raise ValueError("峰测量数据已过期。")
        center_hz = self.center_hz + self.velocity_hz_per_s * age_s
        variance_hz2 = self.center_variance_hz2 + age_s * age_s * self.velocity_variance_hz2
        return center_hz, max(0.0, variance_hz2), age_s


@dataclass
class SpecPidController:
    kp: float
    ki_per_s: float
    kd_s: float
    derivative_filter_tau_s: float
    antiwindup_gain_per_s: float
    integrator_limit_hz: float
    maximum_step_hz: float
    maximum_slew_hz_per_s: float
    integrator_hz: float = 0.0
    derivative_filtered_hz_per_s: float = 0.0
    previous_error_hz: float = 0.0
    previous_timestamp_s: float = 0.0
    initialized: bool = False
    saturation_count: int = 0

    def reset(self, error_hz: float = 0.0, timestamp_s: float = 0.0) -> None:
        self.integrator_hz = 0.0
        self.derivative_filtered_hz_per_s = 0.0
        self.previous_error_hz = float(error_hz)
        self.previous_timestamp_s = float(timestamp_s)
        self.initialized = timestamp_s > 0
        self.saturation_count = 0

    def update(
        self,
        *,
        command_hz: float,
        error_hz: float,
        timestamp_s: float,
        base_hz: float,
        capture_min_hz: float,
        capture_max_hz: float,
        hardware_min_hz: float,
        hardware_max_hz: float,
        identity_min_hz: float,
        identity_max_hz: float,
        quality_good: bool,
        integration_enabled: bool,
        gain_scale: float = 1.0,
    ) -> tuple[float, dict[str, float | bool]]:
        values = (
            command_hz,
            error_hz,
            timestamp_s,
            base_hz,
            capture_min_hz,
            capture_max_hz,
            hardware_min_hz,
            hardware_max_hz,
            identity_min_hz,
            identity_max_hz,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("PID 输入包含 NaN/Inf。")
        if not (
            hardware_min_hz <= capture_min_hz <= capture_max_hz <= hardware_max_hz
            and hardware_min_hz <= identity_min_hz <= identity_max_hz <= hardware_max_hz
        ):
            raise ValueError("PID 限幅边界无效。")
        if not self.initialized:
            self.reset(error_hz, timestamp_s)
            return float(command_hz), {
                "p_hz": 0.0,
                "i_hz": 0.0,
                "d_hz": 0.0,
                "raw_hz": float(command_hz),
                "applied_hz": float(command_hz),
                "saturation_error_hz": 0.0,
                "saturated": False,
            }

        dt_s = timestamp_s - self.previous_timestamp_s
        if dt_s <= 0:
            raise ValueError("PID 时间戳非单调。")
        raw_derivative = (error_hz - self.previous_error_hz) / dt_s
        alpha_d = dt_s / max(self.derivative_filter_tau_s + dt_s, 1e-12)
        self.derivative_filtered_hz_per_s += alpha_d * (
            raw_derivative - self.derivative_filtered_hz_per_s
        )
        gain_scale = max(0.0, min(1.0, float(gain_scale)))
        p_hz = -gain_scale * self.kp * error_hz
        d_hz = -gain_scale * self.kd_s * self.derivative_filtered_hz_per_s
        raw_hz = base_hz + p_hz + self.integrator_hz + d_hz

        limited_hz = max(capture_min_hz, min(capture_max_hz, raw_hz))
        limited_hz = max(hardware_min_hz, min(hardware_max_hz, limited_hz))
        max_step_hz = min(
            self.maximum_step_hz,
            max(0.0, self.maximum_slew_hz_per_s * dt_s),
        )
        limited_hz = max(command_hz - max_step_hz, min(command_hz + max_step_hz, limited_hz))
        applied_hz = max(identity_min_hz, min(identity_max_hz, limited_hz))
        saturation_error_hz = applied_hz - raw_hz

        if quality_good and integration_enabled:
            integrator_drive_hz_per_s = -gain_scale * self.ki_per_s * error_hz
            if (
                saturation_error_hz != 0
                and saturation_error_hz * integrator_drive_hz_per_s < 0
            ):
                integrator_drive_hz_per_s = 0.0
            self.integrator_hz += dt_s * integrator_drive_hz_per_s
            self.integrator_hz += (
                dt_s * self.antiwindup_gain_per_s * saturation_error_hz
            )
            self.integrator_hz = max(
                -self.integrator_limit_hz,
                min(self.integrator_limit_hz, self.integrator_hz),
            )

        saturated = abs(saturation_error_hz) > 1e-9
        self.saturation_count = self.saturation_count + 1 if saturated else 0
        self.previous_error_hz = error_hz
        self.previous_timestamp_s = timestamp_s
        return applied_hz, {
            "p_hz": p_hz,
            "i_hz": self.integrator_hz,
            "d_hz": d_hz,
            "raw_hz": raw_hz,
            "applied_hz": applied_hz,
            "saturation_error_hz": saturation_error_hz,
            "saturated": saturated,
            "dt_s": dt_s,
        }


@dataclass
class PeakTracker:
    id: PeakId
    model: ComplexPeakModel
    pid: SpecPidController
    state: PeakState = PeakState.ACQUIRING
    motion: MotionEstimate = field(default_factory=MotionEstimate)
    command_hz: float = 0.0
    last_error_hz: float = 0.0
    last_q_hz: float = 0.0
    last_quality: float = 0.0
    last_good_timestamp_s: float = 0.0
    last_slope_verification_s: float = 0.0
    good_count: int = 0
    bad_count: int = 0
    visit_count: int = 0
    reacquire_attempts: int = 0
    valid_samples_since_relock: int = 0
    last_measurement_valid: bool = False


@dataclass
class TrackerOutput:
    timestamp_s: float
    f_left_hz: float | None
    f_right_hz: float | None
    delta_f_hz: float | None
    common_mode_hz: float | None
    current_a: float | None
    delta_f_sigma_hz: float | None
    current_sigma_a: float | None
    valid: bool
    invalid_reason: str
    left_state: PeakState
    right_state: PeakState
    left_quality: float
    right_quality: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp_s": self.timestamp_s,
            "f_left_hz": self.f_left_hz,
            "f_right_hz": self.f_right_hz,
            "delta_f_hz": self.delta_f_hz,
            "common_mode_hz": self.common_mode_hz,
            "current_a": self.current_a,
            "delta_f_sigma_hz": self.delta_f_sigma_hz,
            "current_sigma_a": self.current_sigma_a,
            "valid": self.valid,
            "invalid_reason": self.invalid_reason,
            "left_state": self.left_state.value,
            "right_state": self.right_state.value,
            "left_quality": self.left_quality,
            "right_quality": self.right_quality,
        }


def fit_complex_affine_model(
    *,
    frequencies_hz: list[float],
    x_values: list[float],
    y_values: list[float],
    center_hz: float,
    fwhm_hz: float,
    depth_reference: float,
    dc_center_reference: float,
    dc_baseline_at_center: float,
    local_band_min_hz: float,
    local_band_max_hz: float,
    minimum_fit_r2: float,
    slope_epsilon: float,
    orthogonal_limit_fraction: float,
) -> ComplexPeakModel:
    if np is None:
        raise RuntimeError("numpy 不可用，无法拟合复数鉴频模型。")
    frequency = np.asarray(frequencies_hz, dtype=float)
    x_array = np.asarray(x_values, dtype=float)
    y_array = np.asarray(y_values, dtype=float)
    if frequency.size < 3 or x_array.size != frequency.size or y_array.size != frequency.size:
        raise ValueError("复数模型标定点不足。")
    if not (
        np.all(np.isfinite(frequency))
        and np.all(np.isfinite(x_array))
        and np.all(np.isfinite(y_array))
        and math.isfinite(center_hz)
    ):
        raise ValueError("复数模型标定数据包含 NaN/Inf。")
    offsets = frequency - float(center_hz)
    design = np.column_stack((np.ones(frequency.size), offsets))
    b_x, g_x = np.linalg.lstsq(design, x_array, rcond=None)[0]
    b_y, g_y = np.linalg.lstsq(design, y_array, rcond=None)[0]
    b = complex(float(b_x), float(b_y))
    g = complex(float(g_x), float(g_y))
    if abs(g) ** 2 <= slope_epsilon:
        raise ValueError("复数鉴频斜率过小。")
    measured = x_array + 1j * y_array
    predicted = b + g * offsets
    residual = measured - predicted
    residual_ss = float(np.sum(np.abs(residual) ** 2))
    total_ss = float(np.sum(np.abs(measured - np.mean(measured)) ** 2))
    fit_r2 = 1.0 - residual_ss / total_ss if total_ss > 1e-30 else 1.0
    if not math.isfinite(fit_r2) or fit_r2 < minimum_fit_r2:
        raise ValueError(f"复数鉴频模型 R²={fit_r2:.4f} 低于阈值。")
    max_residual = float(np.max(np.abs(residual)))
    sigma_complex = math.sqrt(residual_ss / max(1, frequency.size - 2))
    sigma_error_hz = sigma_complex / max(abs(g), math.sqrt(slope_epsilon))
    linear_limit_hz = float(np.max(np.abs(offsets)))
    orthogonal_limit_hz = max(
        5.0 * sigma_error_hz,
        orthogonal_limit_fraction * linear_limit_hz,
    )
    return ComplexPeakModel(
        center_reference_hz=float(center_hz),
        b=b,
        g=g,
        fwhm_hz=max(float(fwhm_hz), 1.0),
        depth_reference=max(float(depth_reference), 0.0),
        dc_center_reference=float(dc_center_reference),
        dc_baseline_at_center=float(dc_baseline_at_center),
        error_linear_limit_hz=max(linear_limit_hz, 1.0),
        orthogonal_limit_hz=max(orthogonal_limit_hz, 1.0),
        sigma_error_hz=max(sigma_error_hz, 0.0),
        sigma_q_hz=max(sigma_error_hz, 0.0),
        local_band_min_hz=float(local_band_min_hz),
        local_band_max_hz=float(local_band_max_hz),
        model_fit_r2=fit_r2,
        model_max_residual=max_residual,
    )


def calculate_frequency_error(
    measurement: PeakMeasurement,
    model: ComplexPeakModel,
    slope_epsilon: float,
) -> FrequencyError:
    if not measurement.basic_valid():
        return FrequencyError(valid=False, reason="measurement_invalid")
    g2 = abs(model.g) ** 2
    if not math.isfinite(g2) or g2 <= slope_epsilon:
        return FrequencyError(valid=False, reason="slope_too_small")
    dz = measurement.z1 - model.b
    projected = model.g.conjugate() * dz
    denominator = g2 + slope_epsilon
    e_hz = projected.real / denominator
    q_hz = projected.imag / denominator
    residual = dz - model.g * e_hz
    center_hz = measurement.commanded_frequency_hz - e_hz
    values = (e_hz, q_hz, abs(residual), center_hz)
    if not all(math.isfinite(value) for value in values):
        return FrequencyError(valid=False, reason="non_finite_error")
    return FrequencyError(
        valid=True,
        e_hz=float(e_hz),
        q_hz=float(q_hz),
        residual_magnitude=float(abs(residual)),
        center_measurement_hz=float(center_hz),
    )


def update_quality_state(
    tracker: PeakTracker,
    quality: QualityResult,
    *,
    bad_samples_to_suspect: int,
    bad_samples_to_lose: int,
    good_samples_to_lock: int,
) -> PeakState:
    if quality.severe_failure:
        tracker.bad_count = bad_samples_to_lose
        tracker.good_count = 0
    elif not quality.good:
        tracker.bad_count += 1
        tracker.good_count = 0
    else:
        tracker.bad_count = max(0, tracker.bad_count - 1)
        tracker.good_count += 1

    if tracker.state == PeakState.LOCKED and tracker.bad_count >= bad_samples_to_suspect:
        tracker.state = PeakState.SUSPECT
    elif tracker.state == PeakState.SUSPECT:
        if tracker.good_count >= good_samples_to_lock:
            tracker.state = PeakState.LOCKED
        elif tracker.bad_count >= bad_samples_to_lose:
            tracker.state = PeakState.LOCAL_REACQUIRE
    elif tracker.state in (PeakState.ACQUIRING, PeakState.LOCAL_REACQUIRE):
        if tracker.good_count >= good_samples_to_lock:
            tracker.state = PeakState.LOCKED
        elif tracker.bad_count >= bad_samples_to_lose:
            tracker.state = PeakState.LOCAL_REACQUIRE
    return tracker.state


def calculate_aligned_output(
    *,
    left: PeakTracker,
    right: PeakTracker,
    timestamp_s: float,
    maximum_extrapolation_age_s: float,
    maximum_delta_f_sigma_hz: float,
    delta_f_min_hz: float,
    delta_f_max_hz: float,
    calibration_slope_a_per_hz: float | None,
    calibration_intercept_a: float | None,
    calibration_min_hz: float | None = None,
    calibration_max_hz: float | None = None,
) -> TrackerOutput:
    invalid_reason = ""
    left_hz: float | None = None
    right_hz: float | None = None
    delta_hz: float | None = None
    common_hz: float | None = None
    delta_sigma_hz: float | None = None
    current_a: float | None = None
    current_sigma_a: float | None = None
    if left.state != PeakState.LOCKED or right.state != PeakState.LOCKED:
        invalid_reason = "both_peaks_not_locked"
    else:
        try:
            left_hz, left_var, _ = left.motion.predict(timestamp_s, maximum_extrapolation_age_s)
            right_hz, right_var, _ = right.motion.predict(timestamp_s, maximum_extrapolation_age_s)
            delta_hz = right_hz - left_hz
            common_hz = 0.5 * (right_hz + left_hz)
            delta_sigma_hz = math.sqrt(max(0.0, left_var + right_var))
            if not left_hz < right_hz:
                invalid_reason = "peak_identity_invalid"
            elif not delta_f_min_hz <= delta_hz <= delta_f_max_hz:
                invalid_reason = "delta_f_outside_physical_range"
            elif delta_sigma_hz > maximum_delta_f_sigma_hz:
                invalid_reason = "delta_f_uncertainty_too_large"
            elif (
                calibration_min_hz is not None
                and calibration_max_hz is not None
                and not calibration_min_hz <= delta_hz <= calibration_max_hz
            ):
                invalid_reason = "current_outside_calibration_range"
            elif calibration_slope_a_per_hz is None or calibration_intercept_a is None:
                invalid_reason = "current_calibration_missing"
            else:
                current_a = (
                    float(calibration_slope_a_per_hz) * delta_hz
                    + float(calibration_intercept_a)
                )
                current_sigma_a = abs(float(calibration_slope_a_per_hz)) * delta_sigma_hz
        except ValueError as exc:
            invalid_reason = str(exc)
    return TrackerOutput(
        timestamp_s=float(timestamp_s),
        f_left_hz=left_hz,
        f_right_hz=right_hz,
        delta_f_hz=delta_hz,
        common_mode_hz=common_hz,
        current_a=current_a,
        delta_f_sigma_hz=delta_sigma_hz,
        current_sigma_a=current_sigma_a,
        valid=not invalid_reason,
        invalid_reason=invalid_reason,
        left_state=left.state,
        right_state=right.state,
        left_quality=left.last_quality,
        right_quality=right.last_quality,
    )
