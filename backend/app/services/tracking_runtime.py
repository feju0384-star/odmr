from __future__ import annotations

import math
import statistics
from collections import deque
from typing import Any


TIMING_STAGE_KEYS = (
    "microwave_command_ms",
    "settle_ms",
    "lock_wait_ms",
    "lockin_read_ms",
    "other_ms",
)


class TrackingTimingAnalyzer:
    """跟踪热路径的滚动耗时分析，不把初始扫频混入闭环统计。"""

    def __init__(self, max_acquisitions: int = 400, max_cycles: int = 200) -> None:
        self.acquisitions: deque[dict[str, float | str]] = deque(maxlen=max_acquisitions)
        self.cycles_ms: deque[float] = deque(maxlen=max_cycles)

    def record_acquisition(self, phase: str, values: dict[str, float]) -> None:
        total_ms = max(0.0, float(values.get("total_ms", 0.0)))
        known_ms = sum(max(0.0, float(values.get(key, 0.0))) for key in TIMING_STAGE_KEYS[:-1])
        payload: dict[str, float | str] = {
            "phase": str(phase),
            "total_ms": total_ms,
            "microwave_command_ms": max(
                0.0, float(values.get("microwave_command_ms", 0.0))
            ),
            "settle_ms": max(0.0, float(values.get("settle_ms", 0.0))),
            "lock_wait_ms": max(0.0, float(values.get("lock_wait_ms", 0.0))),
            "lockin_read_ms": max(0.0, float(values.get("lockin_read_ms", 0.0))),
            "other_ms": max(0.0, total_ms - known_ms),
        }
        self.acquisitions.append(payload)

    def record_cycle(self, duration_s: float) -> None:
        if math.isfinite(duration_s) and duration_s > 0:
            self.cycles_ms.append(float(duration_s) * 1000.0)

    @staticmethod
    def _percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        position = (len(ordered) - 1) * max(0.0, min(1.0, fraction))
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    def snapshot(self) -> dict[str, Any]:
        tracking = [
            item
            for item in self.acquisitions
            if str(item.get("phase", "")).lower() == "track"
        ]
        if not tracking:
            tracking = list(self.acquisitions)

        stage_mean_ms: dict[str, float] = {}
        for key in TIMING_STAGE_KEYS:
            values = [float(item.get(key, 0.0)) for item in tracking]
            stage_mean_ms[key] = statistics.fmean(values) if values else 0.0

        total_values = [float(item.get("total_ms", 0.0)) for item in tracking]
        cycle_values = list(self.cycles_ms)
        cycle_median_ms = statistics.median(cycle_values) if cycle_values else 0.0
        stage_total = sum(stage_mean_ms.values())
        stage_share = {
            key: (value / stage_total if stage_total > 0 else 0.0)
            for key, value in stage_mean_ms.items()
        }
        bottleneck_key = (
            max(stage_share, key=stage_share.get) if stage_share else "unknown"
        )
        return {
            "acquisition_count": len(tracking),
            "cycle_count": len(cycle_values),
            "acquisition_median_ms": (
                statistics.median(total_values) if total_values else 0.0
            ),
            "acquisition_p95_ms": self._percentile(total_values, 0.95),
            "cycle_median_ms": cycle_median_ms,
            "cycle_p95_ms": self._percentile(cycle_values, 0.95),
            "measured_update_rate_hz": (
                1000.0 / cycle_median_ms if cycle_median_ms > 0 else 0.0
            ),
            "stage_mean_ms": stage_mean_ms,
            "stage_share": stage_share,
            "bottleneck": bottleneck_key,
        }
