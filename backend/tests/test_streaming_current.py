import csv
import math
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.schemas.instruments import MicrowaveConfigRequest
from backend.app.schemas.streaming_current import StreamingCurrentTrackingRequest
from backend.app.services.dual_peak_simulator import DualPeakSimulator
from backend.app.services.dual_peak_tracker import PeakId
from backend.app.services.high_rate_csv_recorder import HighRateCsvRecorder
from backend.app.services.instrument_manager import InstrumentManager
from backend.app.services.streaming_current_tracking import (
    StreamingCurrentTrackingRuntime,
)
from backend.app.services.zurich_stream_buffer import (
    StreamSample,
    ZurichContinuousSampleBuffer,
)


class _FakeNode:
    def __init__(self) -> None:
        self.subscribed = False

    def subscribe(self) -> None:
        self.subscribed = True

    def unsubscribe(self) -> None:
        self.subscribed = False


class _FakeRate:
    def __init__(self, value: float) -> None:
        self.value = float(value)

    def __call__(self, value: float | None = None) -> float:
        if value is not None:
            self.value = float(value)
        return self.value


class _FakeDemod:
    def __init__(self) -> None:
        self.rate = _FakeRate(1000.0)
        self.sample = _FakeNode()


class _FakeDevice:
    def __init__(self) -> None:
        self.demods = [_FakeDemod()]

    def clockbase(self) -> float:
        return 1e6


class _FakeSession:
    def __init__(self, node: _FakeNode) -> None:
        self.node = node
        self.counter = 0

    def sync(self) -> None:
        return None

    def poll(self, *, recording_time: float, timeout: float):
        del timeout
        time.sleep(min(recording_time, 0.002))
        self.counter += 1
        timestamp = int(time.perf_counter() * 1e6)
        return {
            self.node: {
                "timestamp": [timestamp],
                "x": [self.counter * 1e-6],
                "y": [-self.counter * 2e-6],
            }
        }


class _AliveThread:
    def is_alive(self) -> bool:
        return True


class _FakeBufferManager:
    def __init__(self) -> None:
        self.lockin_device = _FakeDevice()
        self.lockin_session = _FakeSession(
            self.lockin_device.demods[0].sample
        )
        self.device_lock = threading.Lock()
        self.sampler_thread = _AliveThread()
        self.stop_calls = 0
        self.start_calls = 0
        self.lockin_state = {
            "channels": [{"sample_rate_hz": 1000.0, "demod_index": 0}]
        }

    def _stop_sampler(self) -> None:
        self.stop_calls += 1

    def _start_sampler(self) -> None:
        self.start_calls += 1

    def _demod_index_for_channel(self, channel_index: int) -> int:
        return int(channel_index)

    @staticmethod
    def _to_float_list(value):
        return [float(item) for item in value]


class StreamingBufferTests(unittest.TestCase):
    def test_wait_returns_only_sample_after_requested_stable_time(self) -> None:
        manager = _FakeBufferManager()
        buffer = ZurichContinuousSampleBuffer(
            manager=manager,
            channel_indices=[0],
            sample_rate_hz=2000.0,
            poll_window_s=0.002,
            poll_timeout_s=0.1,
        )
        buffer.start(warmup_timeout_s=0.3)
        stable_after_s = time.perf_counter() + 0.01
        sample = buffer.wait_for_sample(
            channel_index=0,
            not_before_host_s=stable_after_s,
            timeout_s=0.5,
        )
        diagnostics = buffer.diagnostics()
        buffer.close()

        self.assertIsNotNone(sample)
        self.assertGreaterEqual(sample.host_timestamp_s, stable_after_s)
        self.assertGreater(diagnostics["samples_received"], 0)
        self.assertEqual(manager.stop_calls, 1)
        self.assertEqual(manager.start_calls, 1)
        self.assertEqual(manager.lockin_device.demods[0].rate(), 1000.0)


class HighRateCsvRecorderTests(unittest.TestCase):
    def test_every_output_is_saved_but_disk_writes_are_batched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recorder = HighRateCsvRecorder(
                base_dir=Path(temporary_directory),
                label="throughput",
                batch_points=64,
                flush_interval_s=0.2,
                queue_capacity=5000,
            )
            point_count = 1000
            for index in range(point_count):
                accepted = recorder.enqueue(
                    {
                        "elapsed_s": index / 20.0,
                        "cycle_index": index + 1,
                        "valid": True,
                        "estimated_current_a": 2.0 + index * 1e-6,
                        "current_sigma_a": 0.001,
                        "left_frequency_hz": 2.85e9,
                        "right_frequency_hz": 2.90e9,
                        "splitting_hz": 50e6,
                        "delta_f_sigma_hz": 20e3,
                        "common_mode_hz": 2.875e9,
                        "left_state": "LOCKED",
                        "right_state": "LOCKED",
                        "timing": {
                            "measured_update_rate_hz": 20.0,
                            "acquisition_median_ms": 6.5,
                            "cycle_median_ms": 25.0,
                        },
                        "stream": {"measured_sample_rate_hz": 2000.0},
                    }
                )
                self.assertTrue(accepted)
            status = recorder.finalize("completed")

            with recorder.csv_path.open(
                "r",
                newline="",
                encoding="utf-8-sig",
            ) as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), point_count)
            self.assertEqual(status["rows_written"], point_count)
            self.assertEqual(status["dropped_rows"], 0)
            self.assertLess(status["write_batches"], point_count / 10)
            self.assertGreater(status["average_rows_per_batch"], 10)
            self.assertTrue(rows[0]["timestamp_utc"].endswith("+00:00"))
            self.assertEqual(rows[0]["single_acquisition_p50_ms"], "6.5")
            self.assertEqual(rows[0]["full_cycle_p50_ms"], "25.0")

    def test_active_snapshot_flushes_all_queued_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recorder = HighRateCsvRecorder(
                base_dir=Path(temporary_directory),
                label="snapshot",
                batch_points=100,
                flush_interval_s=10.0,
                queue_capacity=1000,
            )
            for index in range(25):
                self.assertTrue(
                    recorder.enqueue(
                        {
                            "elapsed_s": index * 0.1,
                            "cycle_index": index + 1,
                            "valid": False,
                            "invalid_reason": "test",
                        }
                    )
                )
            snapshot = recorder.snapshot()
            with snapshot.open(
                "r",
                newline="",
                encoding="utf-8-sig",
            ) as handle:
                rows = list(csv.DictReader(handle))
            recorder.finalize("cancelled")
            self.assertEqual(len(rows), 25)


class _SimulatedStreamingBuffer:
    def __init__(self, *, manager, **kwargs) -> None:
        del kwargs
        self.manager = manager
        self.sequence = 0
        self.device_timestamp_s = 0.0

    def start(self, warmup_timeout_s: float) -> None:
        del warmup_timeout_s

    def wait_for_sample(
        self,
        *,
        channel_index: int,
        not_before_host_s: float,
        after_device_timestamp_s: float | None = None,
        timeout_s: float,
    ) -> StreamSample:
        del channel_index, after_device_timestamp_s, timeout_s
        self.sequence += 1
        self.device_timestamp_s += 0.001
        frequency_hz = self.manager._test_frequency_hz
        simulator = self.manager._test_simulator
        midpoint_hz = 0.5 * (
            simulator.left.center_hz + simulator.right.center_hz
        )
        peak_id = (
            PeakId.LEFT
            if frequency_hz < midpoint_hz
            else PeakId.RIGHT
        )
        z = simulator.z1(peak_id, frequency_hz, 0.0)
        return StreamSample(
            channel_index=0,
            device_timestamp_s=self.device_timestamp_s,
            host_timestamp_s=max(time.perf_counter(), not_before_host_s),
            x_v=z.real,
            y_v=z.imag,
            sequence=self.sequence,
        )

    def diagnostics(self):
        return {
            "running": True,
            "requested_sample_rate_hz": 2000.0,
            "measured_sample_rate_hz": 2000.0,
            "poll_window_ms": 5.0,
            "poll_p50_ms": 5.0,
            "poll_p95_ms": 6.0,
            "samples_received": self.sequence,
        }

    def close(self) -> None:
        return None


class StreamingRuntimeTests(unittest.TestCase):
    def test_streaming_page_reuses_pid_core_without_synchronous_sample(self) -> None:
        simulator = DualPeakSimulator(
            dc_noise_rms=0.0,
            complex_noise_rms=0.0,
            seed=19,
        )
        manager = object.__new__(InstrumentManager)
        manager.lockin_device = object()
        manager.lockin_session = object()
        manager.microwave_resource = object()
        manager.lockin_state = {
            "connected": True,
            "channels": [{"time_constant_ms": 0.0}],
        }
        manager.microwave_state = {
            "connected": True,
            "last_error": "",
            "config": MicrowaveConfigRequest().model_dump(),
        }
        manager.measurement_state = {}
        manager.odmr_stop_event = threading.Event()
        manager.device_lock = threading.Lock()
        manager._test_simulator = simulator
        manager._test_frequency_hz = simulator.left.center_hz
        manager._resolve_measurement_channel_index = lambda index: int(index)
        manager._measurement_settle_s = lambda index, settle_ms: 0.00001
        manager.read_lockin_filter_runtime_diagnostics = lambda index: {}
        manager.prepare_microwave_fast_tracking = lambda: True
        manager.update_microwave = lambda request: {"success": True}

        def set_frequency(frequency_hz: float) -> bool:
            manager._test_frequency_hz = float(frequency_hz)
            return True

        manager.set_microwave_frequency_fast = set_frequency
        points: list[dict] = []

        def on_event(event: dict) -> None:
            if event.get("type") == "streaming_current_point":
                points.append(event["point"])
                if len(points) >= 5:
                    manager.odmr_stop_event.set()

        request = StreamingCurrentTrackingRequest(
            record_enabled=False,
            start_hz=2.858e9,
            stop_hz=2.882e9,
            search_points=121,
            search_settle_ms=0.1,
            tracking_settle_ms=0.1,
            probe_offset_hz=250_000.0,
            minimum_complex_fit_r2=0.4,
            good_samples_to_lock=2,
            verify_interval_visits=20,
            minimum_calibration_slope_a_per_hz=1e-7,
            minimum_calibration_intercept_a=0.0,
            calibration_delta_f_min_hz=5e6,
            calibration_delta_f_max_hz=15e6,
            delta_f_min_hz=1e6,
            delta_f_max_hz=20e6,
            maximum_delta_f_sigma_hz=20e6,
        )
        runtime = StreamingCurrentTrackingRuntime(manager)
        with patch(
            "backend.app.services.streaming_current_tracking."
            "ZurichContinuousSampleBuffer",
            _SimulatedStreamingBuffer,
        ):
            runtime.begin(request)
            result = runtime.run(request, on_event)
            runtime.finish(request, result)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(points), 5)
        self.assertTrue(points[-1]["valid"])
        self.assertTrue(points[-1]["stream_sequence"] > 0)
        self.assertAlmostEqual(
            points[-1]["splitting_hz"],
            simulator.right.center_hz - simulator.left.center_hz,
            delta=300_000.0,
        )


class StreamingRequestTests(unittest.TestCase):
    def test_pid_request_disables_original_page_recording(self) -> None:
        request = StreamingCurrentTrackingRequest(record_enabled=True)
        pid_request = request.pid_request()
        self.assertFalse(pid_request.record_enabled)
        self.assertEqual(pid_request.kp, request.kp)
        self.assertEqual(request.tracking_settle_ms, 5.0)


if __name__ == "__main__":
    unittest.main()
