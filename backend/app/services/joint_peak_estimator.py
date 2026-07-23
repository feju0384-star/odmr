from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from backend.app.services.dual_peak_tracker import ComplexPeakModel


PeakName = Literal["left", "right"]


@dataclass(frozen=True)
class FilterUpdate:
    accepted: bool
    innovation_x_v: float
    innovation_y_v: float
    normalized_innovation_squared: float
    gate_threshold: float
    measurement_sigma_v: float
    reason: str = ""

    def as_dict(self) -> dict[str, float | bool | str | None]:
        def finite_or_none(value: float) -> float | None:
            return float(value) if math.isfinite(value) else None

        return {
            "accepted": self.accepted,
            "innovation_x_v": finite_or_none(self.innovation_x_v),
            "innovation_y_v": finite_or_none(self.innovation_y_v),
            "normalized_innovation_squared": finite_or_none(
                self.normalized_innovation_squared
            ),
            "gate_threshold": self.gate_threshold,
            "measurement_sigma_v": finite_or_none(self.measurement_sigma_v),
            "reason": self.reason,
        }


class JointPeakStateEstimator:
    """EKF/UKF 联合估计两个物理峰、速度、复数模型和标定电流。

    物理状态为：
    [fL, fR, dfL/dt, dfR/dt,
     Re(bL), Im(bL), Re(gL), Im(gL),
     Re(bR), Im(bR), Re(gR), Im(gR), I]

    ``I`` 由 I=a*(fR-fL)+b 作为增广派生状态约束。复数 b/g 展开成实部和
    虚部，既保留完整的 FM 相位信息，也让协方差矩阵保持为实数。
    """

    F_LEFT = 0
    F_RIGHT = 1
    V_LEFT = 2
    V_RIGHT = 3
    B_LEFT_RE = 4
    B_LEFT_IM = 5
    G_LEFT_RE = 6
    G_LEFT_IM = 7
    B_RIGHT_RE = 8
    B_RIGHT_IM = 9
    G_RIGHT_RE = 10
    G_RIGHT_IM = 11
    CURRENT = 12
    DIMENSION = 13

    STATE_LABELS = (
        "f_left_hz",
        "f_right_hz",
        "f_left_velocity_hz_per_s",
        "f_right_velocity_hz_per_s",
        "b_left_re_v",
        "b_left_im_v",
        "g_left_re_v_per_hz",
        "g_left_im_v_per_hz",
        "b_right_re_v",
        "b_right_im_v",
        "g_right_re_v_per_hz",
        "g_right_im_v_per_hz",
        "current_a",
    )

    def __init__(
        self,
        *,
        estimator_type: Literal["ekf", "ukf"],
        left_model: ComplexPeakModel,
        right_model: ComplexPeakModel,
        timestamp_s: float,
        initial_frequency_sigma_hz: float,
        initial_velocity_sigma_hz_per_s: float,
        acceleration_noise_hz_per_s2: float,
        baseline_process_noise_v_per_sqrt_s: float,
        slope_relative_process_noise_per_sqrt_s: float,
        measurement_noise_v: float,
        innovation_gate_sigma: float,
        calibration_slope_a_per_hz: float | None,
        calibration_intercept_a: float | None,
        calibration_residual_sigma_a: float = 0.0,
    ) -> None:
        if estimator_type not in {"ekf", "ukf"}:
            raise ValueError("estimator_type 必须为 ekf 或 ukf。")
        self.estimator_type = estimator_type
        self.timestamp_s = float(timestamp_s)
        self.acceleration_noise_hz_per_s2 = float(acceleration_noise_hz_per_s2)
        self.baseline_process_noise_v_per_sqrt_s = float(
            baseline_process_noise_v_per_sqrt_s
        )
        self.slope_relative_process_noise_per_sqrt_s = float(
            slope_relative_process_noise_per_sqrt_s
        )
        self.innovation_gate_sigma = float(innovation_gate_sigma)
        self.calibration_slope_a_per_hz = (
            None
            if calibration_slope_a_per_hz is None
            else float(calibration_slope_a_per_hz)
        )
        self.calibration_intercept_a = (
            None
            if calibration_intercept_a is None
            else float(calibration_intercept_a)
        )
        self.calibration_residual_sigma_a = float(calibration_residual_sigma_a)

        self.x = np.zeros(self.DIMENSION, dtype=float)
        self.x[self.F_LEFT] = float(left_model.center_reference_hz)
        self.x[self.F_RIGHT] = float(right_model.center_reference_hz)
        self.x[self.B_LEFT_RE] = float(left_model.b.real)
        self.x[self.B_LEFT_IM] = float(left_model.b.imag)
        self.x[self.G_LEFT_RE] = float(left_model.g.real)
        self.x[self.G_LEFT_IM] = float(left_model.g.imag)
        self.x[self.B_RIGHT_RE] = float(right_model.b.real)
        self.x[self.B_RIGHT_IM] = float(right_model.b.imag)
        self.x[self.G_RIGHT_RE] = float(right_model.g.real)
        self.x[self.G_RIGHT_IM] = float(right_model.g.imag)

        auto_left_noise = max(
            abs(left_model.g) * max(left_model.sigma_error_hz, 0.0) / math.sqrt(2.0),
            1e-12,
        )
        auto_right_noise = max(
            abs(right_model.g) * max(right_model.sigma_error_hz, 0.0) / math.sqrt(2.0),
            1e-12,
        )
        configured_noise = float(measurement_noise_v)
        self.measurement_sigma_v = {
            "left": max(configured_noise, auto_left_noise),
            "right": max(configured_noise, auto_right_noise),
        }

        baseline_scale = max(
            abs(left_model.b),
            abs(right_model.b),
            max(self.measurement_sigma_v.values()),
            1e-6,
        )
        slope_scale = max(abs(left_model.g), abs(right_model.g), 1e-15)
        current_guess = self._current_from_frequencies(self.x)
        current_scale = max(
            abs(current_guess or 0.0),
            self.calibration_residual_sigma_a,
            1e-3,
        )
        self._scales = np.asarray(
            [
                1e6,
                1e6,
                1e6,
                1e6,
                baseline_scale,
                baseline_scale,
                slope_scale,
                slope_scale,
                baseline_scale,
                baseline_scale,
                slope_scale,
                slope_scale,
                current_scale,
            ],
            dtype=float,
        )

        self.P = np.zeros((self.DIMENSION, self.DIMENSION), dtype=float)
        frequency_variance = float(initial_frequency_sigma_hz) ** 2
        velocity_variance = float(initial_velocity_sigma_hz_per_s) ** 2
        self.P[self.F_LEFT, self.F_LEFT] = frequency_variance
        self.P[self.F_RIGHT, self.F_RIGHT] = frequency_variance
        self.P[self.V_LEFT, self.V_LEFT] = velocity_variance
        self.P[self.V_RIGHT, self.V_RIGHT] = velocity_variance
        for b_index in (
            self.B_LEFT_RE,
            self.B_LEFT_IM,
            self.B_RIGHT_RE,
            self.B_RIGHT_IM,
        ):
            self.P[b_index, b_index] = (4.0 * max(self.measurement_sigma_v.values())) ** 2
        for g_index, value in (
            (self.G_LEFT_RE, left_model.g.real),
            (self.G_LEFT_IM, left_model.g.imag),
            (self.G_RIGHT_RE, right_model.g.real),
            (self.G_RIGHT_IM, right_model.g.imag),
        ):
            self.P[g_index, g_index] = max(
                (0.25 * abs(float(value))) ** 2,
                (0.05 * slope_scale) ** 2,
            )
        self._enforce_current_relation()
        self.P = self._stabilize_covariance(self.P)

    @property
    def current_calibrated(self) -> bool:
        return (
            self.calibration_slope_a_per_hz is not None
            and self.calibration_intercept_a is not None
        )

    def _current_from_frequencies(self, state: np.ndarray) -> float | None:
        if not self.current_calibrated:
            return None
        return (
            float(self.calibration_slope_a_per_hz)
            * (float(state[self.F_RIGHT]) - float(state[self.F_LEFT]))
            + float(self.calibration_intercept_a)
        )

    def _enforce_current_relation(self) -> None:
        if not self.current_calibrated:
            self.x[self.CURRENT] = 0.0
            self.P[self.CURRENT, :] = 0.0
            self.P[:, self.CURRENT] = 0.0
            self.P[self.CURRENT, self.CURRENT] = 1.0
            return
        slope = float(self.calibration_slope_a_per_hz)
        self.x[self.CURRENT] = float(self._current_from_frequencies(self.x))
        base_covariance = self.P[: self.CURRENT, : self.CURRENT]
        current_jacobian = np.zeros(self.CURRENT, dtype=float)
        current_jacobian[self.F_LEFT] = -slope
        current_jacobian[self.F_RIGHT] = slope
        covariance_with_base = current_jacobian @ base_covariance
        self.P[self.CURRENT, : self.CURRENT] = covariance_with_base
        self.P[: self.CURRENT, self.CURRENT] = covariance_with_base
        self.P[self.CURRENT, self.CURRENT] = max(
            float(current_jacobian @ base_covariance @ current_jacobian),
            0.0,
        ) + self.calibration_residual_sigma_a**2

    def _stabilize_covariance(self, covariance: np.ndarray) -> np.ndarray:
        scale_outer = np.outer(self._scales, self._scales)
        normalized = np.asarray(covariance, dtype=float) / scale_outer
        normalized = 0.5 * (normalized + normalized.T)
        eigenvalues, eigenvectors = np.linalg.eigh(normalized)
        clipped = np.maximum(eigenvalues, 1e-15)
        stable = (eigenvectors * clipped) @ eigenvectors.T
        return 0.5 * ((stable * scale_outer) + (stable * scale_outer).T)

    def _transition(self, state: np.ndarray, dt_s: float) -> np.ndarray:
        predicted = np.asarray(state, dtype=float).copy()
        predicted[self.F_LEFT] += predicted[self.V_LEFT] * dt_s
        predicted[self.F_RIGHT] += predicted[self.V_RIGHT] * dt_s
        current = self._current_from_frequencies(predicted)
        predicted[self.CURRENT] = 0.0 if current is None else current
        return predicted

    def _transition_jacobian(self, dt_s: float) -> np.ndarray:
        jacobian = np.eye(self.DIMENSION, dtype=float)
        jacobian[self.F_LEFT, self.V_LEFT] = dt_s
        jacobian[self.F_RIGHT, self.V_RIGHT] = dt_s
        if self.current_calibrated:
            slope = float(self.calibration_slope_a_per_hz)
            jacobian[self.CURRENT, :] = 0.0
            jacobian[self.CURRENT, self.F_LEFT] = -slope
            jacobian[self.CURRENT, self.F_RIGHT] = slope
            jacobian[self.CURRENT, self.V_LEFT] = -slope * dt_s
            jacobian[self.CURRENT, self.V_RIGHT] = slope * dt_s
        return jacobian

    def _process_covariance(self, dt_s: float) -> np.ndarray:
        dt_s = max(float(dt_s), 1e-9)
        q = np.zeros_like(self.P)
        acceleration_variance = self.acceleration_noise_hz_per_s2**2
        position_variance = 0.25 * dt_s**4 * acceleration_variance
        position_velocity_covariance = 0.5 * dt_s**3 * acceleration_variance
        velocity_variance = dt_s**2 * acceleration_variance
        for frequency_index, velocity_index in (
            (self.F_LEFT, self.V_LEFT),
            (self.F_RIGHT, self.V_RIGHT),
        ):
            q[frequency_index, frequency_index] = position_variance
            q[frequency_index, velocity_index] = position_velocity_covariance
            q[velocity_index, frequency_index] = position_velocity_covariance
            q[velocity_index, velocity_index] = velocity_variance
        baseline_variance = self.baseline_process_noise_v_per_sqrt_s**2 * dt_s
        for index in (
            self.B_LEFT_RE,
            self.B_LEFT_IM,
            self.B_RIGHT_RE,
            self.B_RIGHT_IM,
        ):
            q[index, index] = baseline_variance
        slope_sigma = (
            self.slope_relative_process_noise_per_sqrt_s
            * max(
                math.hypot(self.x[self.G_LEFT_RE], self.x[self.G_LEFT_IM]),
                math.hypot(self.x[self.G_RIGHT_RE], self.x[self.G_RIGHT_IM]),
                1e-15,
            )
        )
        slope_variance = slope_sigma**2 * dt_s
        for index in (
            self.G_LEFT_RE,
            self.G_LEFT_IM,
            self.G_RIGHT_RE,
            self.G_RIGHT_IM,
        ):
            q[index, index] = slope_variance
        return q

    def _sigma_points(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        dimension = self.DIMENSION
        alpha = 0.35
        beta = 2.0
        kappa = 0.0
        lam = alpha**2 * (dimension + kappa) - dimension
        denominator = dimension + lam
        normalized = covariance / np.outer(self._scales, self._scales)
        normalized = 0.5 * (normalized + normalized.T)
        jitter = 1e-12
        for _ in range(8):
            try:
                root = np.linalg.cholesky(denominator * (normalized + np.eye(dimension) * jitter))
                break
            except np.linalg.LinAlgError:
                jitter *= 10.0
        else:
            eigenvalues, eigenvectors = np.linalg.eigh(normalized)
            root = (
                eigenvectors
                * np.sqrt(denominator * np.maximum(eigenvalues, 1e-10))
            )
        root = self._scales[:, None] * root
        points = np.empty((2 * dimension + 1, dimension), dtype=float)
        points[0] = mean
        for index in range(dimension):
            points[index + 1] = mean + root[:, index]
            points[dimension + index + 1] = mean - root[:, index]
        weights_mean = np.full(2 * dimension + 1, 1.0 / (2.0 * denominator))
        weights_covariance = weights_mean.copy()
        weights_mean[0] = lam / denominator
        weights_covariance[0] = weights_mean[0] + (1.0 - alpha**2 + beta)
        return points, weights_mean, weights_covariance

    def predict_to(self, timestamp_s: float) -> float:
        timestamp_s = float(timestamp_s)
        dt_s = timestamp_s - self.timestamp_s
        if dt_s <= 0.0:
            return 0.0
        process_covariance = self._process_covariance(dt_s)
        if self.estimator_type == "ekf":
            transition_jacobian = self._transition_jacobian(dt_s)
            self.x = self._transition(self.x, dt_s)
            self.P = (
                transition_jacobian @ self.P @ transition_jacobian.T
                + process_covariance
            )
        else:
            sigma_points, weights_mean, weights_covariance = self._sigma_points(
                self.x,
                self.P,
            )
            propagated = np.asarray(
                [self._transition(point, dt_s) for point in sigma_points]
            )
            self.x = np.sum(weights_mean[:, None] * propagated, axis=0)
            deviations = propagated - self.x
            self.P = process_covariance.copy()
            for index in range(propagated.shape[0]):
                self.P += weights_covariance[index] * np.outer(
                    deviations[index],
                    deviations[index],
                )
        self.timestamp_s = timestamp_s
        self._enforce_current_relation()
        self.P = self._stabilize_covariance(self.P)
        return dt_s

    @classmethod
    def _indices_for_peak(cls, peak: PeakName) -> tuple[int, int, int, int, int]:
        if peak == "left":
            return (
                cls.F_LEFT,
                cls.B_LEFT_RE,
                cls.B_LEFT_IM,
                cls.G_LEFT_RE,
                cls.G_LEFT_IM,
            )
        if peak == "right":
            return (
                cls.F_RIGHT,
                cls.B_RIGHT_RE,
                cls.B_RIGHT_IM,
                cls.G_RIGHT_RE,
                cls.G_RIGHT_IM,
            )
        raise ValueError("peak 必须为 left 或 right。")

    @classmethod
    def measurement_model(
        cls,
        state: np.ndarray,
        *,
        peak: PeakName,
        commanded_frequency_hz: float,
    ) -> np.ndarray:
        f_index, b_re, b_im, g_re, g_im = cls._indices_for_peak(peak)
        offset_hz = float(commanded_frequency_hz) - float(state[f_index])
        return np.asarray(
            [
                state[b_re] + state[g_re] * offset_hz,
                state[b_im] + state[g_im] * offset_hz,
            ],
            dtype=float,
        )

    @classmethod
    def _measurement_jacobian(
        cls,
        state: np.ndarray,
        *,
        peak: PeakName,
        commanded_frequency_hz: float,
    ) -> np.ndarray:
        f_index, b_re, b_im, g_re, g_im = cls._indices_for_peak(peak)
        offset_hz = float(commanded_frequency_hz) - float(state[f_index])
        jacobian = np.zeros((2, cls.DIMENSION), dtype=float)
        jacobian[0, f_index] = -state[g_re]
        jacobian[1, f_index] = -state[g_im]
        jacobian[0, b_re] = 1.0
        jacobian[1, b_im] = 1.0
        jacobian[0, g_re] = offset_hz
        jacobian[1, g_im] = offset_hz
        return jacobian

    def update(
        self,
        *,
        peak: PeakName,
        commanded_frequency_hz: float,
        x_v: float,
        y_v: float,
        measurement_sigma_v: float | None = None,
    ) -> FilterUpdate:
        measurement = np.asarray([x_v, y_v], dtype=float)
        if not np.all(np.isfinite(measurement)):
            return FilterUpdate(
                accepted=False,
                innovation_x_v=math.nan,
                innovation_y_v=math.nan,
                normalized_innovation_squared=math.inf,
                gate_threshold=self.innovation_gate_sigma**2,
                measurement_sigma_v=math.nan,
                reason="measurement_non_finite",
            )
        sigma_v = max(
            float(
                self.measurement_sigma_v[peak]
                if measurement_sigma_v is None
                else measurement_sigma_v
            ),
            1e-15,
        )
        measurement_covariance = np.eye(2, dtype=float) * sigma_v**2

        if self.estimator_type == "ekf":
            prediction = self.measurement_model(
                self.x,
                peak=peak,
                commanded_frequency_hz=commanded_frequency_hz,
            )
            jacobian = self._measurement_jacobian(
                self.x,
                peak=peak,
                commanded_frequency_hz=commanded_frequency_hz,
            )
            innovation_covariance = (
                jacobian @ self.P @ jacobian.T + measurement_covariance
            )
            cross_covariance = self.P @ jacobian.T
        else:
            sigma_points, weights_mean, weights_covariance = self._sigma_points(
                self.x,
                self.P,
            )
            measurement_points = np.asarray(
                [
                    self.measurement_model(
                        point,
                        peak=peak,
                        commanded_frequency_hz=commanded_frequency_hz,
                    )
                    for point in sigma_points
                ]
            )
            prediction = np.sum(
                weights_mean[:, None] * measurement_points,
                axis=0,
            )
            measurement_deviations = measurement_points - prediction
            state_deviations = sigma_points - self.x
            innovation_covariance = measurement_covariance.copy()
            cross_covariance = np.zeros((self.DIMENSION, 2), dtype=float)
            for index in range(sigma_points.shape[0]):
                innovation_covariance += weights_covariance[index] * np.outer(
                    measurement_deviations[index],
                    measurement_deviations[index],
                )
                cross_covariance += weights_covariance[index] * np.outer(
                    state_deviations[index],
                    measurement_deviations[index],
                )

        innovation = measurement - prediction
        try:
            solved_innovation = np.linalg.solve(
                innovation_covariance,
                innovation,
            )
            nis = float(innovation @ solved_innovation)
            gain = np.linalg.solve(
                innovation_covariance,
                cross_covariance.T,
            ).T
        except np.linalg.LinAlgError:
            return FilterUpdate(
                accepted=False,
                innovation_x_v=float(innovation[0]),
                innovation_y_v=float(innovation[1]),
                normalized_innovation_squared=math.inf,
                gate_threshold=self.innovation_gate_sigma**2,
                measurement_sigma_v=sigma_v,
                reason="innovation_covariance_singular",
            )

        gate_threshold = self.innovation_gate_sigma**2
        if not math.isfinite(nis) or nis > gate_threshold:
            return FilterUpdate(
                accepted=False,
                innovation_x_v=float(innovation[0]),
                innovation_y_v=float(innovation[1]),
                normalized_innovation_squared=nis,
                gate_threshold=gate_threshold,
                measurement_sigma_v=sigma_v,
                reason="innovation_gate_rejected",
            )

        self.x = self.x + gain @ innovation
        if self.estimator_type == "ekf":
            identity = np.eye(self.DIMENSION, dtype=float)
            posterior_factor = identity - gain @ jacobian
            self.P = (
                posterior_factor @ self.P @ posterior_factor.T
                + gain @ measurement_covariance @ gain.T
            )
        else:
            self.P = self.P - gain @ innovation_covariance @ gain.T
        self._enforce_current_relation()
        self.P = self._stabilize_covariance(self.P)
        return FilterUpdate(
            accepted=True,
            innovation_x_v=float(innovation[0]),
            innovation_y_v=float(innovation[1]),
            normalized_innovation_squared=nis,
            gate_threshold=gate_threshold,
            measurement_sigma_v=sigma_v,
        )

    def peak_frequency_hz(self, peak: PeakName) -> float:
        index = self.F_LEFT if peak == "left" else self.F_RIGHT
        return float(self.x[index])

    def peak_frequency_sigma_hz(self, peak: PeakName) -> float:
        index = self.F_LEFT if peak == "left" else self.F_RIGHT
        return math.sqrt(max(float(self.P[index, index]), 0.0))

    def output(self) -> dict[str, float | None | dict[str, float]]:
        splitting_hz = float(self.x[self.F_RIGHT] - self.x[self.F_LEFT])
        delta_variance = float(
            self.P[self.F_RIGHT, self.F_RIGHT]
            + self.P[self.F_LEFT, self.F_LEFT]
            - 2.0 * self.P[self.F_LEFT, self.F_RIGHT]
        )
        sigma_delta_hz = math.sqrt(max(delta_variance, 0.0))
        current_a = (
            float(self.x[self.CURRENT]) if self.current_calibrated else None
        )
        current_sigma_a = (
            math.sqrt(max(float(self.P[self.CURRENT, self.CURRENT]), 0.0))
            if self.current_calibrated
            else None
        )
        confidence_multiplier_95 = 1.959963984540054
        f_left_hz = float(self.x[self.F_LEFT])
        f_right_hz = float(self.x[self.F_RIGHT])
        f_left_sigma_hz = self.peak_frequency_sigma_hz("left")
        f_right_sigma_hz = self.peak_frequency_sigma_hz("right")
        return {
            "timestamp_s": self.timestamp_s,
            "f_left_hz": f_left_hz,
            "f_right_hz": f_right_hz,
            "f_left_velocity_hz_per_s": float(self.x[self.V_LEFT]),
            "f_right_velocity_hz_per_s": float(self.x[self.V_RIGHT]),
            "splitting_hz": splitting_hz,
            "common_mode_hz": float(
                0.5 * (self.x[self.F_LEFT] + self.x[self.F_RIGHT])
            ),
            "current_a": current_a,
            "f_left_sigma_hz": f_left_sigma_hz,
            "f_right_sigma_hz": f_right_sigma_hz,
            "splitting_sigma_hz": sigma_delta_hz,
            "current_sigma_a": current_sigma_a,
            "confidence_level": 0.95,
            "f_left_ci95_hz": [
                f_left_hz - confidence_multiplier_95 * f_left_sigma_hz,
                f_left_hz + confidence_multiplier_95 * f_left_sigma_hz,
            ],
            "f_right_ci95_hz": [
                f_right_hz - confidence_multiplier_95 * f_right_sigma_hz,
                f_right_hz + confidence_multiplier_95 * f_right_sigma_hz,
            ],
            "splitting_ci95_hz": [
                splitting_hz - confidence_multiplier_95 * sigma_delta_hz,
                splitting_hz + confidence_multiplier_95 * sigma_delta_hz,
            ],
            "current_ci95_a": (
                [
                    current_a - confidence_multiplier_95 * current_sigma_a,
                    current_a + confidence_multiplier_95 * current_sigma_a,
                ]
                if current_a is not None and current_sigma_a is not None
                else None
            ),
            "b_left": {
                "real": float(self.x[self.B_LEFT_RE]),
                "imag": float(self.x[self.B_LEFT_IM]),
            },
            "g_left": {
                "real": float(self.x[self.G_LEFT_RE]),
                "imag": float(self.x[self.G_LEFT_IM]),
            },
            "b_right": {
                "real": float(self.x[self.B_RIGHT_RE]),
                "imag": float(self.x[self.B_RIGHT_IM]),
            },
            "g_right": {
                "real": float(self.x[self.G_RIGHT_RE]),
                "imag": float(self.x[self.G_RIGHT_IM]),
            },
        }

    def state_vector(self) -> dict[str, float]:
        return {
            label: float(self.x[index])
            for index, label in enumerate(self.STATE_LABELS)
        }
