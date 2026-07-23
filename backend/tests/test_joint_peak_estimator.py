import threading
import unittest

from backend.app.schemas.instruments import MicrowaveConfigRequest
from backend.app.schemas.state_estimation import StateEstimationTrackingRequest
from backend.app.services.dual_peak_simulator import DualPeakSimulator
from backend.app.services.dual_peak_tracker import ComplexPeakModel
from backend.app.services.dual_peak_tracker import PeakId
from backend.app.services.instrument_manager import InstrumentManager
from backend.app.services.joint_peak_estimator import JointPeakStateEstimator
from backend.app.services.state_estimation_tracking import (
    StateEstimationTrackingRuntime,
)


def make_model(center_hz: float, b: complex, g: complex) -> ComplexPeakModel:
    return ComplexPeakModel(
        center_reference_hz=center_hz,
        b=b,
        g=g,
        fwhm_hz=1e6,
        depth_reference=0.05,
        dc_center_reference=0.0,
        dc_baseline_at_center=0.0,
        error_linear_limit_hz=300_000.0,
        orthogonal_limit_hz=100_000.0,
        sigma_error_hz=100.0,
        sigma_q_hz=100.0,
        local_band_min_hz=center_hz - 5e6,
        local_band_max_hz=center_hz + 5e6,
        model_fit_r2=1.0,
        model_max_residual=0.0,
    )


def make_estimator(estimator_type: str) -> JointPeakStateEstimator:
    return JointPeakStateEstimator(
        estimator_type=estimator_type,
        left_model=make_model(
            2.865e9 + 180e3,
            complex(0.2, -0.1),
            complex(2e-9, -3e-9),
        ),
        right_model=make_model(
            2.875e9 - 160e3,
            complex(0.21, -0.08),
            complex(2.2e-9, -2.8e-9),
        ),
        timestamp_s=0.0,
        initial_frequency_sigma_hz=300e3,
        initial_velocity_sigma_hz_per_s=1e6,
        acceleration_noise_hz_per_s2=2e5,
        baseline_process_noise_v_per_sqrt_s=1e-7,
        slope_relative_process_noise_per_sqrt_s=0.001,
        measurement_noise_v=2e-6,
        innovation_gate_sigma=8.0,
        calibration_slope_a_per_hz=1e-7,
        calibration_intercept_a=0.0,
        calibration_residual_sigma_a=1e-3,
    )


class JointPeakStateEstimatorTests(unittest.TestCase):
    def test_ekf_and_ukf_track_moving_complex_resonances(self) -> None:
        for estimator_type in ("ekf", "ukf"):
            with self.subTest(estimator_type=estimator_type):
                estimator = make_estimator(estimator_type)
                true_left_initial_hz = 2.865e9
                true_right_initial_hz = 2.875e9
                for index in range(1, 201):
                    timestamp_s = index * 0.02
                    estimator.predict_to(timestamp_s)
                    peak = "left" if index % 2 else "right"
                    probe_sign = -1 if (index // 2) % 2 else 1
                    command_hz = (
                        estimator.peak_frequency_hz(peak)
                        + probe_sign * 250_000.0
                    )
                    if peak == "left":
                        true_center_hz = true_left_initial_hz + 25_000.0 * timestamp_s
                        b = complex(0.2, -0.1)
                        g = complex(2e-9, -3e-9)
                    else:
                        true_center_hz = true_right_initial_hz + 35_000.0 * timestamp_s
                        b = complex(0.21, -0.08)
                        g = complex(2.2e-9, -2.8e-9)
                    z = b + g * (command_hz - true_center_hz)
                    update = estimator.update(
                        peak=peak,
                        commanded_frequency_hz=command_hz,
                        x_v=z.real,
                        y_v=z.imag,
                    )
                    self.assertTrue(update.accepted)

                output = estimator.output()
                self.assertAlmostEqual(
                    output["f_left_hz"],
                    true_left_initial_hz + 25_000.0 * 4.0,
                    delta=2_000.0,
                )
                self.assertAlmostEqual(
                    output["f_right_hz"],
                    true_right_initial_hz + 35_000.0 * 4.0,
                    delta=2_000.0,
                )
                self.assertLess(output["splitting_sigma_hz"], 10_000.0)
                self.assertIsNotNone(output["current_a"])
                self.assertIsNotNone(output["current_sigma_a"])

    def test_prediction_only_interval_increases_uncertainty(self) -> None:
        estimator = make_estimator("ekf")
        before = estimator.output()["splitting_sigma_hz"]
        estimator.predict_to(0.8)
        after = estimator.output()["splitting_sigma_hz"]
        self.assertGreater(after, before)

    def test_innovation_gate_rejects_outlier_without_moving_state(self) -> None:
        estimator = make_estimator("ukf")
        estimator.predict_to(0.02)
        before = estimator.peak_frequency_hz("left")
        update = estimator.update(
            peak="left",
            commanded_frequency_hz=before + 250_000.0,
            x_v=100.0,
            y_v=-100.0,
        )
        self.assertFalse(update.accepted)
        self.assertEqual(update.reason, "innovation_gate_rejected")
        self.assertAlmostEqual(estimator.peak_frequency_hz("left"), before)

    def test_current_is_derived_from_peak_splitting(self) -> None:
        estimator = make_estimator("ekf")
        output = estimator.output()
        expected = 1e-7 * (
            output["f_right_hz"] - output["f_left_hz"]
        )
        self.assertAlmostEqual(output["current_a"], expected, places=12)


class StateEstimationRuntimeTests(unittest.TestCase):
    def test_isolated_runtime_scans_calibrates_and_streams_points(self) -> None:
        simulator = DualPeakSimulator(
            dc_noise_rms=0.0,
            complex_noise_rms=0.0,
            seed=11,
        )
        manager = object.__new__(InstrumentManager)
        manager.lockin_device = object()
        manager.lockin_session = object()
        manager.microwave_resource = object()
        manager.lockin_state = {"connected": True}
        manager.microwave_state = {
            "connected": True,
            "last_error": "",
            "config": MicrowaveConfigRequest().model_dump(),
        }
        manager.measurement_state = {}
        manager.odmr_stop_event = threading.Event()
        manager._resolve_measurement_channel_index = lambda index: int(index)
        manager.prepare_microwave_fast_tracking = lambda: True
        manager.update_microwave = lambda request: {"success": True}
        manager._measurement_settle_s = lambda index, settle_ms: 0.00001
        manager.read_lockin_sample_for_channel_timed = None
        current_frequency_hz = simulator.left.center_hz

        def set_frequency(frequency_hz: float) -> bool:
            nonlocal current_frequency_hz
            current_frequency_hz = float(frequency_hz)
            return True

        manager.set_microwave_frequency_fast = set_frequency

        def read_channel(channel_index: int) -> dict[str, float]:
            midpoint_hz = 0.5 * (
                simulator.left.center_hz + simulator.right.center_hz
            )
            peak_id = (
                PeakId.LEFT
                if current_frequency_hz < midpoint_hz
                else PeakId.RIGHT
            )
            z = simulator.z1(peak_id, current_frequency_hz, 0.0)
            return {"x_v": z.real, "y_v": z.imag, "r_v": abs(z)}

        manager.read_lockin_sample_for_channel = read_channel
        runtime = StateEstimationTrackingRuntime(manager)
        points: list[dict] = []
        initialized_events: list[dict] = []

        def on_event(event: dict) -> None:
            if event.get("type") == "state_estimation_initialized":
                initialized_events.append(event)
            if event.get("type") == "state_estimation_point":
                points.append(event["point"])
                if len(points) >= 6:
                    manager.odmr_stop_event.set()

        request = StateEstimationTrackingRequest(
            estimator_type="ekf",
            start_hz=2.858e9,
            stop_hz=2.882e9,
            search_points=121,
            search_settle_ms=0.1,
            tracking_settle_ms=0.1,
            minimum_complex_fit_r2=0.4,
            delta_f_min_hz=1e6,
            delta_f_max_hz=20e6,
            maximum_frequency_sigma_hz=20e6,
            maximum_delta_f_sigma_hz=20e6,
            calibration_slope_a_per_hz=1e-7,
            calibration_intercept_a=0.0,
            calibration_delta_f_min_hz=5e6,
            calibration_delta_f_max_hz=15e6,
            innovation_gate_sigma=8.0,
        )
        runtime.begin(request)
        result = runtime.run(request, on_event)
        runtime.finish(request, result)

        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(initialized_events)
        self.assertEqual(len(points), 6)
        self.assertTrue(points[-1]["output_valid"])
        self.assertAlmostEqual(
            points[-1]["splitting_hz"],
            simulator.right.center_hz - simulator.left.center_hz,
            delta=300_000.0,
        )
        self.assertIn("current_a", points[-1]["state"])
        self.assertEqual(manager.measurement_state["mode"], "idle")
