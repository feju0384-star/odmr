import math
import threading
import unittest

from pydantic import ValidationError

from backend.app.schemas.instruments import CurrentTrackingRequest, MicrowaveConfigRequest
from backend.app.services.dual_peak_simulator import DualPeakSimulator
from backend.app.services.dual_peak_tracker import PeakId
from backend.app.services.instrument_manager import InstrumentManager


class CurrentTrackingRequestTests(unittest.TestCase):
    def test_defaults_use_complex_projection(self) -> None:
        request = CurrentTrackingRequest()
        self.assertEqual(request.tracking_target, "complex_projection")
        self.assertGreater(request.verify_interval_visits, 0)
        self.assertTrue(request.record_enabled)
        self.assertEqual(request.record_batch_points, 100)
        self.assertEqual(request.record_flush_interval_s, 1.0)
        self.assertEqual(request.record_queue_capacity, 100_000)

    def test_legacy_r_or_zero_crossing_target_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CurrentTrackingRequest(tracking_target="minimum")

    def test_invalid_probe_offset_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CurrentTrackingRequest(probe_offset_hz=0.0)

    def test_csv_queue_must_hold_at_least_two_batches(self) -> None:
        with self.assertRaises(ValidationError):
            CurrentTrackingRequest(
                record_batch_points=600,
                record_queue_capacity=1000,
            )


class MeasurementSettleTests(unittest.TestCase):
    def test_no_fixed_five_millisecond_floor(self) -> None:
        manager = object.__new__(InstrumentManager)
        manager.lockin_state = {
            "channels": [
                {
                    "input_signal": 0,
                    "time_constant_ms": 0.82,
                }
            ]
        }
        self.assertAlmostEqual(
            manager._measurement_settle_s(0, 0.1),
            0.0041,
            places=7,
        )


class HardwareLoopSimulationTests(unittest.TestCase):
    def test_complex_projection_loop_reaches_valid_output(self) -> None:
        simulator = DualPeakSimulator(
            dc_noise_rms=0.0,
            complex_noise_rms=0.0,
            seed=7,
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
        manager.measurement_state = {"tracking": {}}
        manager.odmr_stop_event = threading.Event()
        manager._resolve_measurement_channel_index = lambda channel_index: int(channel_index)
        manager.prepare_microwave_fast_tracking = lambda: True
        manager.update_microwave = lambda request: {"success": True}
        manager._measurement_settle_s = lambda channel_index, settle_ms: 0.00001
        current_frequency_hz = simulator.left.center_hz

        def set_frequency(frequency_hz):
            nonlocal current_frequency_hz
            current_frequency_hz = float(frequency_hz)
            return True

        manager.set_microwave_frequency_fast = set_frequency

        def read_channel(channel_index):
            midpoint_hz = 0.5 * (
                simulator.left.center_at(0.0) + simulator.right.center_at(0.0)
            )
            peak_id = PeakId.LEFT if current_frequency_hz < midpoint_hz else PeakId.RIGHT
            z1 = simulator.z1(peak_id, current_frequency_hz, 0.0)
            return {
                "x_v": z1.real,
                "y_v": z1.imag,
                "r_v": abs(z1),
            }

        manager.read_lockin_sample_for_channel = read_channel
        points = []
        timing_events = []
        recorded_points = []

        class RecordingCollector:
            @staticmethod
            def enqueue(point):
                recorded_points.append(dict(point))
                return True

            @staticmethod
            def status():
                return {
                    "status": "recording",
                    "enqueued_rows": len(recorded_points),
                    "rows_written": len(recorded_points),
                    "dropped_rows": 0,
                }

        manager.current_tracking_recordings = RecordingCollector()

        def on_event(event):
            if event.get("type") == "current_tracking_timing":
                timing_events.append(event["timing"])
            if event.get("type") == "current_tracking_point":
                points.append(event["point"])
                if len(points) >= 5:
                    manager.odmr_stop_event.set()

        request = CurrentTrackingRequest(
            channel_index=0,
            independent_dc_channel_index=-1,
            start_hz=2.858e9,
            stop_hz=2.882e9,
            search_points=121,
            search_settle_ms=0.1,
            tracking_settle_ms=0.1,
            sample_averages=1,
            probe_offset_hz=250_000.0,
            minimum_complex_fit_r2=0.5,
            good_samples_to_lock=2,
            verify_interval_visits=3,
            minimum_calibration_slope_a_per_hz=1e-9,
            minimum_calibration_intercept_a=0.0,
            calibration_delta_f_min_hz=5e6,
            calibration_delta_f_max_hz=15e6,
            delta_f_min_hz=1e6,
            delta_f_max_hz=20e6,
            maximum_delta_f_sigma_hz=20e6,
        )
        result = manager.run_current_tracking(request, on_event)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(points), 5)
        self.assertEqual(len(recorded_points), 5)
        self.assertTrue(timing_events)
        self.assertIn("bottleneck", timing_events[-1])
        self.assertGreaterEqual(timing_events[-1]["measured_update_rate_hz"], 0.0)
        valid_points = [point for point in points if point["valid"]]
        self.assertTrue(valid_points)
        self.assertAlmostEqual(
            valid_points[-1]["splitting_hz"],
            simulator.right.center_hz - simulator.left.center_hz,
            delta=200_000.0,
        )
        self.assertTrue(math.isfinite(valid_points[-1]["estimated_current_a"]))


if __name__ == "__main__":
    unittest.main()
