import math
import unittest

from backend.app.services.dual_peak_simulator import DualPeakSimulator, SimulatedPeak
from backend.app.services.dual_peak_tracker import (
    ComplexPeakModel,
    GlobalState,
    MotionEstimate,
    PeakId,
    PeakState,
    PeakTracker,
    QualityResult,
    SpecPidController,
    blend_symmetric_complex_probe,
    calculate_aligned_output,
    calculate_frequency_error,
    find_fm_magnitude_resonances,
    fit_complex_affine_model,
    select_fm_resonance_pair,
    update_quality_state,
)


def make_model(center_hz: float, g: complex = complex(2e-9, -3e-9)) -> ComplexPeakModel:
    return ComplexPeakModel(
        center_reference_hz=center_hz,
        b=complex(0.2, -0.1),
        g=g,
        fwhm_hz=1e6,
        depth_reference=0.05,
        dc_center_reference=0.95,
        dc_baseline_at_center=1.0,
        error_linear_limit_hz=300_000.0,
        orthogonal_limit_hz=100_000.0,
        sigma_error_hz=10.0,
        sigma_q_hz=10.0,
        local_band_min_hz=center_hz - 2e6,
        local_band_max_hz=center_hz + 2e6,
        model_fit_r2=1.0,
        model_max_residual=0.0,
    )


def make_pid() -> SpecPidController:
    return SpecPidController(
        kp=0.5,
        ki_per_s=0.1,
        kd_s=0.0,
        derivative_filter_tau_s=0.1,
        antiwindup_gain_per_s=1.0,
        integrator_limit_hz=100_000.0,
        maximum_step_hz=100_000.0,
        maximum_slew_hz_per_s=1e9,
    )


def good_quality() -> QualityResult:
    return QualityResult(
        measurement_valid=True,
        error_valid=True,
        depth_valid=True,
        orthogonal_valid=True,
        slope_recent=True,
        hardware_valid=True,
        identity_valid=True,
        good=True,
        severe_failure=False,
        score_0_to_1=1.0,
        reason="",
    )


class ComplexProjectionTests(unittest.TestCase):
    def test_arbitrary_phase_and_nonzero_offset_return_signed_hz_error(self) -> None:
        center_hz = 1_000_000.0
        phase = 1.234
        g = complex(math.cos(phase), math.sin(phase)) * 2e-6
        b = complex(3e-3, -2e-3)
        frequencies = [center_hz + offset for offset in (-1000, -500, 0, 500, 1000)]
        z_values = [b + g * (frequency - center_hz) for frequency in frequencies]
        model = fit_complex_affine_model(
            frequencies_hz=frequencies,
            x_values=[value.real for value in z_values],
            y_values=[value.imag for value in z_values],
            center_hz=center_hz,
            fwhm_hz=10_000.0,
            depth_reference=0.1,
            dc_center_reference=0.9,
            dc_baseline_at_center=1.0,
            local_band_min_hz=center_hz - 10_000,
            local_band_max_hz=center_hz + 10_000,
            minimum_fit_r2=0.99,
            slope_epsilon=1e-30,
            orthogonal_limit_fraction=0.5,
        )
        simulator = DualPeakSimulator(
            left=SimulatedPeak(
                center_hz=center_hz,
                fwhm_hz=10_000,
                depth=0.1,
                b=b,
                g=g,
            ),
            complex_noise_rms=0.0,
            dc_noise_rms=0.0,
        )
        measurement = simulator.measure(PeakId.LEFT, center_hz + 321.0, 0.0)
        result = calculate_frequency_error(measurement, model, 1e-30)
        self.assertTrue(result.valid)
        self.assertAlmostEqual(result.e_hz, 321.0, places=3)
        self.assertAlmostEqual(result.center_measurement_hz, center_hz, places=3)
        self.assertAlmostEqual(result.q_hz, 0.0, places=3)

    def test_zero_slope_is_invalid(self) -> None:
        model = make_model(1000.0, 0j)
        measurement = DualPeakSimulator().measure(PeakId.LEFT, 1000.0, 0.0)
        result = calculate_frequency_error(measurement, model, 1e-20)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "slope_too_small")

    def test_live_symmetric_probe_updates_intercept_and_slope(self) -> None:
        model = make_model(1000.0, complex(2e-6, -1e-6))
        old_b = model.b
        b_live = complex(0.35, -0.22)
        g_live = complex(3e-6, 4e-6)
        delta_hz = 25.0
        returned_b, returned_g = blend_symmetric_complex_probe(
            model,
            center_hz=1010.0,
            minus_z=b_live - g_live * delta_hz,
            plus_z=b_live + g_live * delta_hz,
            delta_hz=delta_hz,
            blend_fraction=0.2,
        )
        self.assertAlmostEqual(returned_b.real, b_live.real)
        self.assertAlmostEqual(returned_b.imag, b_live.imag)
        self.assertAlmostEqual(returned_g.real, g_live.real)
        self.assertAlmostEqual(returned_g.imag, g_live.imag)
        expected_b = 0.8 * old_b + 0.2 * b_live
        expected_g = 0.8 * complex(2e-6, -1e-6) + 0.2 * g_live
        self.assertAlmostEqual(model.b.real, expected_b.real)
        self.assertAlmostEqual(model.b.imag, expected_b.imag)
        self.assertAlmostEqual(model.g.real, expected_g.real)
        self.assertAlmostEqual(model.g.imag, expected_g.imag)
        self.assertEqual(model.center_reference_hz, 1010.0)
        self.assertEqual(model.version, 2)


class FmMagnitudeResonanceTests(unittest.TestCase):
    @staticmethod
    def _lorentzian_derivative(frequency_hz: float, center_hz: float) -> float:
        gamma_hz = 24.0
        normalized = (frequency_hz - center_hz) / gamma_hz
        return 2.0 * normalized / (gamma_hz * (1.0 + normalized**2) ** 2)

    def test_r_double_lobes_are_grouped_into_two_physical_resonances(self) -> None:
        frequencies = [float(value) for value in range(0, 1001, 2)]
        phase = complex(math.cos(0.73), math.sin(0.73))
        complex_values = [
            phase
            * (
                self._lorentzian_derivative(frequency, 300.0)
                + 0.85 * self._lorentzian_derivative(frequency, 700.0)
            )
            for frequency in frequencies
        ]
        r_values = [abs(value) for value in complex_values]
        candidates = find_fm_magnitude_resonances(
            frequencies,
            r_values,
            complex_values,
            minimum_prominence_fraction=0.05,
        )
        left, right = select_fm_resonance_pair(
            candidates,
            delta_f_min_hz=350.0,
            delta_f_max_hz=450.0,
            ambiguity_score_ratio=0.98,
        )
        self.assertAlmostEqual(left.center_hz, 300.0, delta=3.0)
        self.assertAlmostEqual(right.center_hz, 700.0, delta=3.0)
        self.assertGreater(left.left_lobe_r, left.center_r)
        self.assertGreater(left.right_lobe_r, left.center_r)
        self.assertGreater(right.left_lobe_r, right.center_r)
        self.assertGreater(right.right_lobe_r, right.center_r)
        self.assertGreater(
            left.complex_slope.real * phase.real
            + left.complex_slope.imag * phase.imag,
            0.0,
        )
        self.assertGreater(
            right.complex_slope.real * phase.real
            + right.complex_slope.imag * phase.imag,
            0.0,
        )


class SpecPidTests(unittest.TestCase):
    def test_pid_sign_moves_command_toward_center(self) -> None:
        pid = make_pid()
        pid.update(
            command_hz=1100.0,
            error_hz=100.0,
            timestamp_s=1.0,
            base_hz=1100.0,
            capture_min_hz=500.0,
            capture_max_hz=1500.0,
            hardware_min_hz=0.0,
            hardware_max_hz=2000.0,
            identity_min_hz=0.0,
            identity_max_hz=2000.0,
            quality_good=True,
            integration_enabled=True,
        )
        applied, _ = pid.update(
            command_hz=1100.0,
            error_hz=100.0,
            timestamp_s=2.0,
            base_hz=1100.0,
            capture_min_hz=500.0,
            capture_max_hz=1500.0,
            hardware_min_hz=0.0,
            hardware_max_hz=2000.0,
            identity_min_hz=0.0,
            identity_max_hz=2000.0,
            quality_good=True,
            integration_enabled=True,
        )
        self.assertLess(applied, 1100.0)

    def test_all_limits_feed_antiwindup_and_integrator_is_bounded(self) -> None:
        pid = make_pid()
        pid.reset(error_hz=0.0, timestamp_s=1.0)
        for index in range(2, 200):
            applied, diagnostics = pid.update(
                command_hz=1000.0,
                error_hz=1e6,
                timestamp_s=float(index),
                base_hz=1000.0,
                capture_min_hz=900.0,
                capture_max_hz=1100.0,
                hardware_min_hz=0.0,
                hardware_max_hz=2000.0,
                identity_min_hz=950.0,
                identity_max_hz=1050.0,
                quality_good=True,
                integration_enabled=True,
            )
            self.assertGreaterEqual(applied, 950.0)
            self.assertLessEqual(applied, 1050.0)
            self.assertTrue(diagnostics["saturated"])
        self.assertLessEqual(abs(pid.integrator_hz), pid.integrator_limit_hz)

    def test_non_monotonic_timestamp_is_rejected(self) -> None:
        pid = make_pid()
        pid.reset(timestamp_s=2.0)
        with self.assertRaisesRegex(ValueError, "非单调"):
            pid.update(
                command_hz=1000.0,
                error_hz=0.0,
                timestamp_s=1.0,
                base_hz=1000.0,
                capture_min_hz=900.0,
                capture_max_hz=1100.0,
                hardware_min_hz=0.0,
                hardware_max_hz=2000.0,
                identity_min_hz=0.0,
                identity_max_hz=2000.0,
                quality_good=True,
                integration_enabled=True,
            )


class StateAndAlignmentTests(unittest.TestCase):
    def test_quality_hysteresis_does_not_lose_on_one_bad_sample(self) -> None:
        tracker = PeakTracker(PeakId.LEFT, make_model(1000.0), make_pid(), state=PeakState.LOCKED)
        bad = good_quality()
        bad.good = False
        bad.reason = "temporary_noise"
        update_quality_state(
            tracker,
            bad,
            bad_samples_to_suspect=2,
            bad_samples_to_lose=4,
            good_samples_to_lock=3,
        )
        self.assertEqual(tracker.state, PeakState.LOCKED)

    def test_time_alignment_removes_first_order_skew(self) -> None:
        left = PeakTracker(PeakId.LEFT, make_model(1000.0), make_pid(), state=PeakState.LOCKED)
        right = PeakTracker(PeakId.RIGHT, make_model(2000.0), make_pid(), state=PeakState.LOCKED)
        left.motion = MotionEstimate(
            center_hz=1010.0,
            velocity_hz_per_s=10.0,
            timestamp_s=1.0,
            initialized=True,
        )
        right.motion = MotionEstimate(
            center_hz=2020.0,
            velocity_hz_per_s=10.0,
            timestamp_s=2.0,
            initialized=True,
        )
        output = calculate_aligned_output(
            left=left,
            right=right,
            timestamp_s=3.0,
            maximum_extrapolation_age_s=5.0,
            maximum_delta_f_sigma_hz=1e6,
            delta_f_min_hz=0.0,
            delta_f_max_hz=1e6,
            calibration_slope_a_per_hz=1e-3,
            calibration_intercept_a=0.0,
        )
        self.assertTrue(output.valid)
        self.assertAlmostEqual(output.f_left_hz, 1030.0)
        self.assertAlmostEqual(output.f_right_hz, 2030.0)
        self.assertAlmostEqual(output.delta_f_hz, 1000.0)

    def test_stale_peak_data_makes_output_invalid(self) -> None:
        left = PeakTracker(PeakId.LEFT, make_model(1000.0), make_pid(), state=PeakState.LOCKED)
        right = PeakTracker(PeakId.RIGHT, make_model(2000.0), make_pid(), state=PeakState.LOCKED)
        left.motion = MotionEstimate(center_hz=1000.0, timestamp_s=1.0, initialized=True)
        right.motion = MotionEstimate(center_hz=2000.0, timestamp_s=1.0, initialized=True)
        output = calculate_aligned_output(
            left=left,
            right=right,
            timestamp_s=10.0,
            maximum_extrapolation_age_s=1.0,
            maximum_delta_f_sigma_hz=1e6,
            delta_f_min_hz=0.0,
            delta_f_max_hz=1e6,
            calibration_slope_a_per_hz=1.0,
            calibration_intercept_a=0.0,
        )
        self.assertFalse(output.valid)
        self.assertIn("过期", output.invalid_reason)

    def test_crossed_peak_identity_is_invalid(self) -> None:
        left = PeakTracker(PeakId.LEFT, make_model(2000.0), make_pid(), state=PeakState.LOCKED)
        right = PeakTracker(PeakId.RIGHT, make_model(1000.0), make_pid(), state=PeakState.LOCKED)
        left.motion = MotionEstimate(center_hz=2000.0, timestamp_s=1.0, initialized=True)
        right.motion = MotionEstimate(center_hz=1000.0, timestamp_s=1.0, initialized=True)
        output = calculate_aligned_output(
            left=left,
            right=right,
            timestamp_s=1.1,
            maximum_extrapolation_age_s=1.0,
            maximum_delta_f_sigma_hz=1e6,
            delta_f_min_hz=0.0,
            delta_f_max_hz=1e6,
            calibration_slope_a_per_hz=1.0,
            calibration_intercept_a=0.0,
        )
        self.assertFalse(output.valid)
        self.assertEqual(output.invalid_reason, "peak_identity_invalid")

    def test_current_is_not_extrapolated_outside_calibration_range(self) -> None:
        left = PeakTracker(PeakId.LEFT, make_model(1000.0), make_pid(), state=PeakState.LOCKED)
        right = PeakTracker(PeakId.RIGHT, make_model(3000.0), make_pid(), state=PeakState.LOCKED)
        left.motion = MotionEstimate(center_hz=1000.0, timestamp_s=1.0, initialized=True)
        right.motion = MotionEstimate(center_hz=3000.0, timestamp_s=1.0, initialized=True)
        output = calculate_aligned_output(
            left=left,
            right=right,
            timestamp_s=1.1,
            maximum_extrapolation_age_s=1.0,
            maximum_delta_f_sigma_hz=1e6,
            delta_f_min_hz=0.0,
            delta_f_max_hz=1e6,
            calibration_slope_a_per_hz=1.0,
            calibration_intercept_a=0.0,
            calibration_min_hz=500.0,
            calibration_max_hz=1500.0,
        )
        self.assertFalse(output.valid)
        self.assertEqual(output.invalid_reason, "current_outside_calibration_range")
        self.assertIsNone(output.current_a)

    def test_enums_cover_required_global_states(self) -> None:
        self.assertEqual(GlobalState.FULL_REACQUIRE.value, "FULL_REACQUIRE")


if __name__ == "__main__":
    unittest.main()
