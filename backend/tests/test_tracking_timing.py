import unittest

from backend.app.services.tracking_runtime import TrackingTimingAnalyzer


class TrackingTimingAnalyzerTests(unittest.TestCase):
    def test_reports_stage_bottleneck_and_measured_rate(self) -> None:
        analyzer = TrackingTimingAnalyzer()
        for _ in range(10):
            analyzer.record_acquisition(
                "TRACK",
                {
                    "total_ms": 100.0,
                    "microwave_command_ms": 60.0,
                    "settle_ms": 5.0,
                    "lock_wait_ms": 20.0,
                    "lockin_read_ms": 10.0,
                },
            )
            analyzer.record_cycle(0.2)

        result = analyzer.snapshot()
        self.assertEqual(result["bottleneck"], "microwave_command_ms")
        self.assertAlmostEqual(result["stage_mean_ms"]["other_ms"], 5.0)
        self.assertAlmostEqual(result["measured_update_rate_hz"], 5.0)
        self.assertAlmostEqual(result["cycle_median_ms"], 200.0)

    def test_tracking_samples_take_priority_over_scan_samples(self) -> None:
        analyzer = TrackingTimingAnalyzer()
        analyzer.record_acquisition(
            "FULL_SCAN",
            {
                "total_ms": 1000.0,
                "microwave_command_ms": 900.0,
                "settle_ms": 100.0,
            },
        )
        analyzer.record_acquisition(
            "TRACK",
            {
                "total_ms": 20.0,
                "microwave_command_ms": 1.0,
                "settle_ms": 5.0,
                "lock_wait_ms": 2.0,
                "lockin_read_ms": 10.0,
            },
        )

        result = analyzer.snapshot()
        self.assertEqual(result["acquisition_count"], 1)
        self.assertAlmostEqual(result["acquisition_median_ms"], 20.0)
        self.assertEqual(result["bottleneck"], "lockin_read_ms")


if __name__ == "__main__":
    unittest.main()
