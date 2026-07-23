from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import numpy as np

from backend.app.schemas.instruments import MicrowaveConfigRequest
from backend.app.schemas.state_estimation import StateEstimationTrackingRequest
from backend.app.services.dual_peak_tracker import (
    ComplexPeakModel,
    PeakMeasurement,
    find_fm_magnitude_resonances,
    fit_complex_affine_model,
    select_fm_resonance_pair,
)
from backend.app.services.joint_peak_estimator import (
    FilterUpdate,
    JointPeakStateEstimator,
    PeakName,
)


class StateEstimationCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class AcquiredSample:
    measurement: PeakMeasurement
    timing: dict[str, float]


class StateEstimationTrackingRuntime:
    """独立于 PID 电流页的 EKF/UKF 双峰跟踪运行时。"""

    MODE = "state_estimation_current"

    def __init__(self, instrument_manager: Any) -> None:
        self.manager = instrument_manager

    def begin(self, request: StateEstimationTrackingRequest) -> None:
        channel_index = self.manager._resolve_measurement_channel_index(
            request.channel_index
        )
        self.manager.odmr_stop_event.clear()
        self.manager.measurement_state.update(
            {
                "running": True,
                "mode": self.MODE,
                "status": "准备状态估计",
                "cancel_requested": False,
                "progress": 0.0,
                "current_point": 0,
                "current_frequency_hz": 0.0,
                "current_value": 0.0,
                "last_state_estimation_request": {
                    **request.model_dump(),
                    "channel_index": channel_index,
                },
            }
        )

    def finish(
        self,
        request: StateEstimationTrackingRequest,
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
                    last_point.get("commanded_frequency_hz", 0.0) or 0.0
                ),
                "current_value": float(last_point.get("splitting_hz", 0.0) or 0.0),
                "last_state_estimation_request": request.model_dump(),
                "last_state_estimation_result": result,
            }
        )

    def _check_hardware(self, request: StateEstimationTrackingRequest) -> int:
        if self.manager.lockin_device is None or self.manager.lockin_session is None:
            raise RuntimeError("锁相未连接，无法运行 EKF/UKF 状态估计。")
        if (
            self.manager.microwave_resource is None
            or not self.manager.microwave_state.get("connected")
        ):
            raise RuntimeError("微波源未连接，无法运行 EKF/UKF 状态估计。")
        if request.stop_hz <= request.start_hz:
            raise RuntimeError("跟踪终止频率必须大于起始频率。")
        return self.manager._resolve_measurement_channel_index(request.channel_index)

    def run(
        self,
        request: StateEstimationTrackingRequest,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        channel_index = self._check_hardware(request)
        manager = self.manager
        clock = time.perf_counter
        started_s = clock()
        started_iso = datetime.now().isoformat(timespec="seconds")
        original_microwave_config = dict(manager.microwave_state.get("config", {}))
        reacquire_count = 0
        cycle_index = 0
        accepted_update_count = 0
        rejected_update_count = 0
        last_point: dict[str, Any] = {}

        def publish(event: dict[str, Any]) -> None:
            if callable(event_callback):
                event_callback(event)

        def check_stopped() -> None:
            if manager.odmr_stop_event.is_set():
                raise StateEstimationCancelled("EKF/UKF 状态估计已停止。")

        def acquire_at(
            peak: PeakName,
            frequency_hz: float,
            settle_ms: float,
        ) -> AcquiredSample:
            check_stopped()
            frequency_hz = float(frequency_hz)
            if not math.isfinite(frequency_hz):
                raise RuntimeError("频率命令包含 NaN/Inf。")
            if not request.start_hz <= frequency_hz <= request.stop_hz:
                raise RuntimeError("频率命令越出状态估计扫频范围。")

            total_started_s = clock()
            command_started_s = clock()
            if not manager.set_microwave_frequency_fast(frequency_hz):
                raise RuntimeError(
                    manager.microwave_state.get("last_error")
                    or "微波快速捷变频失败。"
                )
            command_finished_s = clock()
            settle_s = manager._measurement_settle_s(channel_index, settle_ms)
            time.sleep(settle_s)
            settle_finished_s = clock()

            x_values: list[float] = []
            y_values: list[float] = []
            r_values: list[float] = []
            lock_wait_ms = 0.0
            lockin_read_ms = 0.0
            total_blocks = max(1, int(request.sample_averages))
            for _ in range(total_blocks):
                check_stopped()
                timed_reader = getattr(
                    manager,
                    "read_lockin_sample_for_channel_timed",
                    None,
                )
                if callable(timed_reader):
                    sample, read_timing = timed_reader(channel_index)
                else:
                    read_started_s = clock()
                    sample = manager.read_lockin_sample_for_channel(channel_index)
                    read_timing = {
                        "lock_wait_ms": 0.0,
                        "lockin_read_ms": (clock() - read_started_s) * 1000.0,
                    }
                lock_wait_ms += float(read_timing.get("lock_wait_ms", 0.0))
                lockin_read_ms += float(read_timing.get("lockin_read_ms", 0.0))
                x_value = float(sample.get("x_v", math.nan))
                y_value = float(sample.get("y_v", math.nan))
                r_value = float(sample.get("r_v", math.nan))
                if all(
                    math.isfinite(value)
                    for value in (x_value, y_value, r_value)
                ):
                    x_values.append(x_value)
                    y_values.append(y_value)
                    r_values.append(r_value)

            completed_s = clock()
            valid_blocks = min(len(x_values), len(y_values), len(r_values))
            measurement = PeakMeasurement(
                timestamp_s=completed_s,
                commanded_frequency_hz=frequency_hz,
                x1=(
                    float(statistics.median(x_values))
                    if x_values
                    else math.nan
                ),
                y1=(
                    float(statistics.median(y_values))
                    if y_values
                    else math.nan
                ),
                dc=(
                    float(statistics.median(r_values))
                    if r_values
                    else math.nan
                ),
                source_settled=True,
                adc_valid=valid_blocks > 0,
                detector_overload=False,
                source_fault=manager.microwave_resource is None,
                valid_blocks=valid_blocks,
                total_blocks=total_blocks,
            )
            return AcquiredSample(
                measurement=measurement,
                timing={
                    "total_ms": (completed_s - total_started_s) * 1000.0,
                    "microwave_command_ms": (
                        command_finished_s - command_started_s
                    )
                    * 1000.0,
                    "settle_ms": (settle_finished_s - command_finished_s)
                    * 1000.0,
                    "lock_wait_ms": lock_wait_ms,
                    "lockin_read_ms": lockin_read_ms,
                },
            )

        def calibrate_model(
            *,
            peak: PeakName,
            center_hz: float,
            fwhm_hz: float,
            depth_reference: float,
            center_r: float,
            lobe_level_r: float,
            band_min_hz: float,
            band_max_hz: float,
        ) -> tuple[ComplexPeakModel, float]:
            calibration_span_hz = min(
                0.35 * fwhm_hz,
                max(request.probe_offset_hz, 0.15 * fwhm_hz),
                center_hz - band_min_hz,
                band_max_hz - center_hz,
            )
            if not math.isfinite(calibration_span_hz) or calibration_span_hz <= 0:
                raise RuntimeError(f"{peak} 峰的复数模型标定窗口无效。")
            offsets = np.linspace(
                -calibration_span_hz,
                calibration_span_hz,
                2 * request.calibration_points_each_side + 1,
            )
            measurements = [
                acquire_at(
                    peak,
                    center_hz + float(offset_hz),
                    request.search_settle_ms,
                ).measurement
                for offset_hz in offsets
            ]
            if not all(item.basic_valid() for item in measurements):
                raise RuntimeError(f"{peak} 峰复数模型标定包含无效采样。")
            try:
                model = fit_complex_affine_model(
                    frequencies_hz=[
                        item.commanded_frequency_hz for item in measurements
                    ],
                    x_values=[item.x1 for item in measurements],
                    y_values=[item.y1 for item in measurements],
                    center_hz=center_hz,
                    fwhm_hz=fwhm_hz,
                    depth_reference=depth_reference,
                    dc_center_reference=center_r,
                    dc_baseline_at_center=lobe_level_r,
                    local_band_min_hz=band_min_hz,
                    local_band_max_hz=band_max_hz,
                    minimum_fit_r2=request.minimum_complex_fit_r2,
                    slope_epsilon=1e-30,
                    orthogonal_limit_fraction=0.5,
                )
            except ValueError as exc:
                raise RuntimeError(f"{peak} 峰复数模型标定失败：{exc}") from exc
            return model, measurements[-1].timestamp_s

        def full_acquire(
            reason: str,
        ) -> tuple[
            JointPeakStateEstimator,
            dict[PeakName, ComplexPeakModel],
            dict[PeakName, float],
        ]:
            check_stopped()
            phase = "FULL_SCAN" if reason == "initial" else "FULL_REACQUIRE"
            manager.measurement_state.update(
                {
                    "mode": self.MODE,
                    "status": "完整扫频" if reason == "initial" else "不确定度触发重新扫峰",
                    "progress": 0.0,
                }
            )
            publish(
                {
                    "type": "state_estimation_state",
                    "phase": phase,
                    "reason": reason,
                    "output_valid": False,
                }
            )
            frequencies = np.linspace(
                request.start_hz,
                request.stop_hz,
                request.search_points,
            )
            scan: list[PeakMeasurement] = []
            for index, frequency_hz in enumerate(frequencies):
                acquired = acquire_at(
                    "left",
                    float(frequency_hz),
                    request.search_settle_ms,
                )
                scan.append(acquired.measurement)
                progress = (index + 1) / frequencies.size
                manager.measurement_state["progress"] = progress
                publish(
                    {
                        "type": "state_estimation_scan_point",
                        "phase": phase,
                        "index": index + 1,
                        "points": int(frequencies.size),
                        "progress": progress,
                        "frequency_hz": float(frequency_hz),
                        "r_v": (
                            acquired.measurement.dc
                            if math.isfinite(acquired.measurement.dc)
                            else None
                        ),
                    }
                )
            if not all(item.basic_valid() for item in scan):
                raise RuntimeError("完整扫频包含无效采样，无法可靠初始化状态估计器。")

            candidates = find_fm_magnitude_resonances(
                frequencies_hz=frequencies,
                r_values=[item.dc for item in scan],
                complex_values=[item.z1 for item in scan],
                minimum_prominence_fraction=request.minimum_peak_prominence_fraction,
            )
            if len(candidates) < 2:
                raise RuntimeError("完整扫频未发现两个可靠的 FM 双瓣谷共振。")
            try:
                left_candidate, right_candidate = select_fm_resonance_pair(
                    candidates,
                    delta_f_min_hz=request.delta_f_min_hz,
                    delta_f_max_hz=request.delta_f_max_hz,
                    ambiguity_score_ratio=request.peak_pair_ambiguity_score_ratio,
                    maximum_slope_phase_difference_rad=math.pi / 2.0,
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc

            left_center_hz = float(left_candidate.center_hz)
            right_center_hz = float(right_candidate.center_hz)
            left_fwhm_hz = float(left_candidate.fwhm_hz)
            right_fwhm_hz = float(right_candidate.fwhm_hz)
            splitting_hz = right_center_hz - left_center_hz
            if left_center_hz >= right_center_hz:
                raise RuntimeError("双峰身份初始化失败：左峰频率不小于右峰。")
            if splitting_hz < request.minimum_resolvable_separation_factor * max(
                left_fwhm_hz,
                right_fwhm_hz,
            ):
                raise RuntimeError("两个共振峰间距低于可分辨阈值。")

            midpoint_hz = 0.5 * (left_center_hz + right_center_hz)
            identity_guard_hz = request.identity_guard_fraction * min(
                left_fwhm_hz,
                right_fwhm_hz,
            )
            left_band = (
                request.start_hz,
                min(request.stop_hz, midpoint_hz - identity_guard_hz),
            )
            right_band = (
                max(request.start_hz, midpoint_hz + identity_guard_hz),
                request.stop_hz,
            )
            publish(
                {
                    "type": "state_estimation_state",
                    "phase": "CALIBRATE",
                    "reason": "complex_model_calibration",
                    "left_center_hz": left_center_hz,
                    "right_center_hz": right_center_hz,
                    "output_valid": False,
                }
            )
            left_model, left_timestamp_s = calibrate_model(
                peak="left",
                center_hz=left_center_hz,
                fwhm_hz=left_fwhm_hz,
                depth_reference=float(left_candidate.prominence_r),
                center_r=float(left_candidate.center_r),
                lobe_level_r=float(left_candidate.lobe_level_r),
                band_min_hz=left_band[0],
                band_max_hz=left_band[1],
            )
            right_model, right_timestamp_s = calibrate_model(
                peak="right",
                center_hz=right_center_hz,
                fwhm_hz=right_fwhm_hz,
                depth_reference=float(right_candidate.prominence_r),
                center_r=float(right_candidate.center_r),
                lobe_level_r=float(right_candidate.lobe_level_r),
                band_min_hz=right_band[0],
                band_max_hz=right_band[1],
            )
            initialized_s = max(left_timestamp_s, right_timestamp_s)
            estimator = JointPeakStateEstimator(
                estimator_type=request.estimator_type,
                left_model=left_model,
                right_model=right_model,
                timestamp_s=initialized_s,
                initial_frequency_sigma_hz=request.initial_frequency_sigma_hz,
                initial_velocity_sigma_hz_per_s=(
                    request.initial_velocity_sigma_hz_per_s
                ),
                acceleration_noise_hz_per_s2=(
                    request.acceleration_noise_hz_per_s2
                ),
                baseline_process_noise_v_per_sqrt_s=(
                    request.baseline_process_noise_v_per_sqrt_s
                ),
                slope_relative_process_noise_per_sqrt_s=(
                    request.slope_relative_process_noise_per_sqrt_s
                ),
                measurement_noise_v=request.measurement_noise_v,
                innovation_gate_sigma=request.innovation_gate_sigma,
                calibration_slope_a_per_hz=(
                    request.calibration_slope_a_per_hz
                ),
                calibration_intercept_a=request.calibration_intercept_a,
                calibration_residual_sigma_a=(
                    request.calibration_residual_sigma_a
                ),
            )
            models: dict[PeakName, ComplexPeakModel] = {
                "left": left_model,
                "right": right_model,
            }
            last_accepted_s: dict[PeakName, float] = {
                "left": initialized_s,
                "right": initialized_s,
            }
            publish(
                {
                    "type": "state_estimation_initialized",
                    "phase": "TRACK",
                    "estimator_type": request.estimator_type,
                    "reason": reason,
                    "state_definition": list(estimator.STATE_LABELS),
                    "left_model": left_model.as_dict(),
                    "right_model": right_model.as_dict(),
                    "initial_state": estimator.state_vector(),
                }
            )
            manager.measurement_state.update(
                {
                    "mode": self.MODE,
                    "status": f"{request.estimator_type.upper()} 联合跟踪",
                    "progress": 0.0,
                }
            )
            return estimator, models, last_accepted_s

        def validity_and_reason(
            output: dict[str, Any],
            last_accepted_s: dict[PeakName, float],
            timestamp_s: float,
        ) -> tuple[bool, str, float]:
            ages = {
                peak: max(0.0, timestamp_s - last_accepted_s[peak])
                for peak in ("left", "right")
            }
            maximum_age_s = max(ages.values())
            reason = ""
            if output["f_left_hz"] >= output["f_right_hz"]:
                reason = "peak_identity_invalid"
            elif not (
                request.start_hz
                <= output["f_left_hz"]
                < output["f_right_hz"]
                <= request.stop_hz
            ):
                reason = "frequency_outside_tracking_range"
            elif not (
                request.delta_f_min_hz
                <= output["splitting_hz"]
                <= request.delta_f_max_hz
            ):
                reason = "splitting_outside_physical_range"
            elif (
                output["f_left_sigma_hz"] > request.maximum_frequency_sigma_hz
                or output["f_right_sigma_hz"]
                > request.maximum_frequency_sigma_hz
            ):
                reason = "frequency_uncertainty_too_large"
            elif (
                output["splitting_sigma_hz"]
                > request.maximum_delta_f_sigma_hz
            ):
                reason = "splitting_uncertainty_too_large"
            elif maximum_age_s > request.maximum_prediction_age_s:
                reason = "prediction_age_too_large"
            elif output["current_a"] is None:
                reason = "current_calibration_missing"
            elif (
                request.calibration_delta_f_min_hz is not None
                and request.calibration_delta_f_max_hz is not None
                and not (
                    request.calibration_delta_f_min_hz
                    <= output["splitting_hz"]
                    <= request.calibration_delta_f_max_hz
                )
            ):
                reason = "current_outside_calibration_range"
            return not reason, reason, maximum_age_s

        def reacquire_reason(
            output: dict[str, Any],
            last_accepted_s: dict[PeakName, float],
            rejected_by_peak: dict[PeakName, int],
            timestamp_s: float,
        ) -> str:
            if max(rejected_by_peak.values()) >= request.bad_updates_to_reacquire:
                return "consecutive_innovation_rejections"
            if max(
                timestamp_s - last_accepted_s["left"],
                timestamp_s - last_accepted_s["right"],
            ) > request.maximum_prediction_age_s:
                return "prediction_age_exceeded"
            if (
                output["f_left_sigma_hz"] > request.maximum_frequency_sigma_hz
                or output["f_right_sigma_hz"]
                > request.maximum_frequency_sigma_hz
                or output["splitting_sigma_hz"]
                > request.maximum_delta_f_sigma_hz
            ):
                return "posterior_uncertainty_exceeded"
            if not (
                request.start_hz
                <= output["f_left_hz"]
                < output["f_right_hz"]
                <= request.stop_hz
            ):
                return "peak_identity_or_tracking_range_invalid"
            if not (
                request.delta_f_min_hz
                <= output["splitting_hz"]
                <= request.delta_f_max_hz
            ):
                return "splitting_outside_physical_range"
            return ""

        try:
            if not manager.prepare_microwave_fast_tracking():
                raise RuntimeError(
                    manager.microwave_state.get("last_error")
                    or "微波源无法进入快速 CW 跟踪模式。"
                )
            estimator, models, last_accepted_s = full_acquire("initial")
            rejected_by_peak: dict[PeakName, int] = {"left": 0, "right": 0}
            next_peak: PeakName = "left"
            next_probe_sign: dict[PeakName, int] = {"left": -1, "right": -1}
            tracking_started_s = clock()

            while True:
                check_stopped()
                now_s = clock()
                if (
                    request.max_tracking_duration_s > 0
                    and now_s - tracking_started_s
                    >= request.max_tracking_duration_s
                ):
                    break

                estimator.predict_to(now_s)
                peak = next_peak
                next_peak = "right" if peak == "left" else "left"
                probe_sign = next_probe_sign[peak]
                next_probe_sign[peak] *= -1
                model = models[peak]
                probe_offset_hz = min(
                    request.probe_offset_hz,
                    0.25 * model.fwhm_hz,
                    0.8 * model.error_linear_limit_hz,
                )
                band_min_hz = max(request.start_hz, model.local_band_min_hz)
                band_max_hz = min(request.stop_hz, model.local_band_max_hz)
                predicted_center_hz = estimator.peak_frequency_hz(peak)
                commanded_frequency_hz = min(
                    max(
                        predicted_center_hz + probe_sign * probe_offset_hz,
                        band_min_hz,
                    ),
                    band_max_hz,
                )

                acquired = acquire_at(
                    peak,
                    commanded_frequency_hz,
                    request.tracking_settle_ms,
                )
                measurement = acquired.measurement
                estimator.predict_to(measurement.timestamp_s)
                if measurement.basic_valid():
                    update = estimator.update(
                        peak=peak,
                        commanded_frequency_hz=commanded_frequency_hz,
                        x_v=measurement.x1,
                        y_v=measurement.y1,
                    )
                else:
                    update = FilterUpdate(
                        accepted=False,
                        innovation_x_v=math.nan,
                        innovation_y_v=math.nan,
                        normalized_innovation_squared=math.inf,
                        gate_threshold=request.innovation_gate_sigma**2,
                        measurement_sigma_v=math.nan,
                        reason="measurement_invalid",
                    )

                if update.accepted:
                    accepted_update_count += 1
                    rejected_by_peak[peak] = 0
                    last_accepted_s[peak] = measurement.timestamp_s
                else:
                    rejected_update_count += 1
                    rejected_by_peak[peak] += 1

                cycle_index += 1
                output = estimator.output()
                output_valid, invalid_reason, maximum_prediction_age_s = (
                    validity_and_reason(
                        output,
                        last_accepted_s,
                        measurement.timestamp_s,
                    )
                )
                uncertainty_score = math.exp(
                    -0.5
                    * (
                        output["splitting_sigma_hz"]
                        / request.maximum_delta_f_sigma_hz
                    )
                    ** 2
                )
                freshness_score = max(
                    0.0,
                    1.0
                    - maximum_prediction_age_s
                    / request.maximum_prediction_age_s,
                )
                innovation_score = (
                    math.exp(
                        -0.5
                        * min(
                            update.normalized_innovation_squared
                            / max(update.gate_threshold, 1e-12),
                            10.0,
                        )
                    )
                    if math.isfinite(update.normalized_innovation_squared)
                    else 0.0
                )
                confidence = max(
                    0.0,
                    min(
                        1.0,
                        uncertainty_score
                        * freshness_score
                        * innovation_score,
                    ),
                )
                elapsed_tracking_s = max(
                    measurement.timestamp_s - tracking_started_s,
                    1e-9,
                )
                point = {
                    **output,
                    "cycle_index": cycle_index,
                    "elapsed_s": measurement.timestamp_s - started_s,
                    "estimator_type": request.estimator_type,
                    "phase": "TRACK",
                    "active_peak": peak,
                    "probe_sign": probe_sign,
                    "commanded_frequency_hz": commanded_frequency_hz,
                    "measured_x_v": (
                        measurement.x1
                        if math.isfinite(measurement.x1)
                        else None
                    ),
                    "measured_y_v": (
                        measurement.y1
                        if math.isfinite(measurement.y1)
                        else None
                    ),
                    "measured_r_v": (
                        measurement.dc
                        if math.isfinite(measurement.dc)
                        else None
                    ),
                    "measurement_valid": measurement.basic_valid(),
                    "measurement_update_accepted": update.accepted,
                    "prediction_only": not update.accepted,
                    "innovation": update.as_dict(),
                    "output_valid": output_valid,
                    "invalid_reason": invalid_reason,
                    "confidence_0_to_1": confidence,
                    "maximum_prediction_age_s": maximum_prediction_age_s,
                    "left_prediction_age_s": max(
                        0.0,
                        measurement.timestamp_s - last_accepted_s["left"],
                    ),
                    "right_prediction_age_s": max(
                        0.0,
                        measurement.timestamp_s - last_accepted_s["right"],
                    ),
                    "accepted_update_count": accepted_update_count,
                    "rejected_update_count": rejected_update_count,
                    "consecutive_rejections": dict(rejected_by_peak),
                    "reacquire_count": reacquire_count,
                    "update_rate_hz": cycle_index / elapsed_tracking_s,
                    "timing": acquired.timing,
                    "state": estimator.state_vector(),
                }
                last_point = point
                manager.measurement_state.update(
                    {
                        "status": (
                            f"{request.estimator_type.upper()} 预测"
                            if point["prediction_only"]
                            else f"{request.estimator_type.upper()} 测量更新"
                        ),
                        "current_point": cycle_index,
                        "current_frequency_hz": commanded_frequency_hz,
                        "current_value": float(output["splitting_hz"]),
                        "state_estimation": point,
                    }
                )
                publish({"type": "state_estimation_point", "point": point})

                reason = reacquire_reason(
                    output,
                    last_accepted_s,
                    rejected_by_peak,
                    measurement.timestamp_s,
                )
                if reason:
                    if reacquire_count >= request.max_reacquire_attempts:
                        raise RuntimeError(
                            "状态估计失锁且重新扫峰次数已达上限："
                            f"{reason}"
                        )
                    reacquire_count += 1
                    publish(
                        {
                            "type": "state_estimation_reacquire",
                            "reason": reason,
                            "reacquire_count": reacquire_count,
                            "last_point": point,
                        }
                    )
                    estimator, models, last_accepted_s = full_acquire(reason)
                    rejected_by_peak = {"left": 0, "right": 0}
                    next_peak = "left"
                    next_probe_sign = {"left": -1, "right": -1}

            status = "completed"
        except StateEstimationCancelled:
            status = "cancelled"
        finally:
            try:
                if original_microwave_config:
                    manager.update_microwave(
                        MicrowaveConfigRequest(**original_microwave_config)
                    )
            except Exception:
                pass

        return {
            "status": status,
            "estimator_type": request.estimator_type,
            "started_at": started_iso,
            "duration_s": clock() - started_s,
            "cycle_count": cycle_index,
            "accepted_update_count": accepted_update_count,
            "rejected_update_count": rejected_update_count,
            "reacquire_count": reacquire_count,
            "last_point": last_point,
        }
