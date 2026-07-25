import csv
import tempfile
import unittest
from pathlib import Path

from backend.app.services.high_rate_csv_recorder import (
    HighRateCsvRecordingManager,
)


def tracking_point(index: int, *, valid: bool = True) -> dict:
    elapsed_s = index / 20.0
    return {
        "elapsed_s": elapsed_s,
        "cycle_index": index + 1,
        "valid": valid,
        "invalid_reason": "" if valid else "left_stale",
        "estimated_current_a": 2.0 + index * 1e-6 if valid else None,
        "current_sigma_a": 0.004,
        "left_frequency_hz": 2.865e9 + index,
        "right_frequency_hz": 2.875e9 + index,
        "splitting_hz": 10e6,
        "delta_f_sigma_hz": 1500.0,
        "common_mode_hz": 2.87e9 + index,
        "tracking_target": "complex_projection",
        "global_state": "TRACK",
        "left_state": "LOCKED",
        "right_state": "LOCKED",
        "left_quality": 0.95,
        "right_quality": 0.96,
        "dc_independent": False,
        "left_error_hz": 120.0,
        "right_error_hz": -80.0,
        "relock_count": 0,
        "lost_lock_count": 0,
        "timing": {
            "measured_update_rate_hz": 20.0,
            "acquisition_median_ms": 6.0,
            "cycle_median_ms": 50.0,
        },
    }


class CurrentTrackingCsvRecorderTests(unittest.TestCase):
    def test_every_pid_output_is_saved_in_batched_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = HighRateCsvRecordingManager(
                Path(temporary_directory),
                filename_prefix="current_tracking",
            )
            started = manager.start(
                label="2A_13h",
                batch_points=64,
                flush_interval_s=0.2,
                queue_capacity=5000,
            )
            point_count = 1000
            for index in range(point_count):
                self.assertTrue(
                    manager.enqueue(
                        tracking_point(index, valid=index % 10 != 0)
                    )
                )
            finished = manager.finish("completed")

            self.assertEqual(finished["session_id"], started["session_id"])
            self.assertEqual(finished["rows_written"], point_count)
            self.assertEqual(finished["enqueued_rows"], point_count)
            self.assertEqual(finished["dropped_rows"], 0)
            self.assertLess(finished["write_batches"], point_count / 10)
            csv_path = Path(finished["csv_path"])
            self.assertTrue(csv_path.name.startswith("current_tracking_"))
            self.assertFalse(any(csv_path.parent.glob("*.xlsx")))

            with csv_path.open(
                "r",
                newline="",
                encoding="utf-8-sig",
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), point_count)
            self.assertEqual(rows[0]["valid"], "false")
            self.assertEqual(rows[0]["invalid_reason"], "left_stale")
            self.assertEqual(rows[1]["tracking_target"], "complex_projection")

    def test_active_download_is_a_complete_csv_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = HighRateCsvRecordingManager(
                Path(temporary_directory),
                filename_prefix="current_tracking",
            )
            started = manager.start(
                label="live",
                batch_points=100,
                flush_interval_s=10.0,
                queue_capacity=1000,
            )
            for index in range(25):
                self.assertTrue(manager.enqueue(tracking_point(index)))

            snapshot, temporary = manager.export(started["session_id"])
            self.assertTrue(temporary)
            with snapshot.open(
                "r",
                newline="",
                encoding="utf-8-sig",
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 25)
            manager.finish("cancelled")


if __name__ == "__main__":
    unittest.main()
