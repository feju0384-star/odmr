from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class StateEstimationTrackingRequest(BaseModel):
    """独立的 EKF/UKF 双峰状态估计跟踪参数。"""

    estimator_type: Literal["ekf", "ukf"] = "ekf"
    channel_index: int = Field(default=0, ge=0)

    start_hz: float = 2.83e9
    stop_hz: float = 2.91e9
    search_points: int = Field(default=121, ge=11, le=4001)
    search_settle_ms: float = Field(default=10.0, ge=0.1, le=5000.0)
    tracking_settle_ms: float = Field(default=3.0, ge=0.1, le=5000.0)
    sample_averages: int = Field(default=1, ge=1, le=100)
    probe_offset_hz: float = Field(default=250_000.0, gt=0.0)
    calibration_points_each_side: int = Field(default=2, ge=1, le=10)
    minimum_complex_fit_r2: float = Field(default=0.60, ge=0.0, le=1.0)
    minimum_peak_prominence_fraction: float = Field(default=0.05, ge=0.0, le=1.0)
    peak_pair_ambiguity_score_ratio: float = Field(default=0.90, gt=0.0, le=1.0)

    delta_f_min_hz: float = Field(default=0.0, ge=0.0)
    delta_f_max_hz: float = Field(default=1.0e9, gt=0.0)
    minimum_resolvable_separation_factor: float = Field(default=0.75, ge=0.0, le=10.0)
    identity_guard_fraction: float = Field(default=0.20, ge=0.0, le=2.0)

    measurement_noise_v: float = Field(
        default=0.0,
        ge=0.0,
        description="X/Y 单轴测量噪声；0 表示根据初始复数拟合残差自动估计。",
    )
    initial_frequency_sigma_hz: float = Field(default=250_000.0, gt=0.0)
    initial_velocity_sigma_hz_per_s: float = Field(default=2.0e6, gt=0.0)
    acceleration_noise_hz_per_s2: float = Field(default=5.0e6, gt=0.0)
    baseline_process_noise_v_per_sqrt_s: float = Field(default=2.0e-5, ge=0.0)
    slope_relative_process_noise_per_sqrt_s: float = Field(default=0.02, ge=0.0)
    calibration_residual_sigma_a: float = Field(default=0.0, ge=0.0)

    innovation_gate_sigma: float = Field(default=4.0, ge=1.0, le=20.0)
    bad_updates_to_reacquire: int = Field(default=4, ge=1, le=1000)
    maximum_frequency_sigma_hz: float = Field(default=1.5e6, gt=0.0)
    maximum_delta_f_sigma_hz: float = Field(default=2.0e6, gt=0.0)
    maximum_prediction_age_s: float = Field(default=1.0, gt=0.0, le=60.0)
    max_reacquire_attempts: int = Field(default=5, ge=0, le=1000)
    max_tracking_duration_s: float = Field(default=0.0, ge=0.0, le=604800.0)

    calibration_slope_a_per_hz: float | None = None
    calibration_intercept_a: float | None = None
    calibration_delta_f_min_hz: float | None = None
    calibration_delta_f_max_hz: float | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "StateEstimationTrackingRequest":
        if self.stop_hz <= self.start_hz:
            raise ValueError("跟踪终止频率必须大于起始频率。")
        if self.delta_f_max_hz <= self.delta_f_min_hz:
            raise ValueError("Δf 物理上限必须大于下限。")
        bounds = (
            self.calibration_delta_f_min_hz,
            self.calibration_delta_f_max_hz,
        )
        if all(value is not None for value in bounds) and bounds[1] <= bounds[0]:
            raise ValueError("电流标定 Δf 上限必须大于下限。")
        return self
