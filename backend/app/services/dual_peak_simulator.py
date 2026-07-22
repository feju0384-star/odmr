from __future__ import annotations

import math
import random
from dataclasses import dataclass

from backend.app.services.dual_peak_tracker import PeakId, PeakMeasurement


@dataclass
class SimulatedPeak:
    center_hz: float
    fwhm_hz: float
    depth: float
    b: complex
    g: complex
    velocity_hz_per_s: float = 0.0
    acceleration_hz_per_s2: float = 0.0
    quadratic_per_hz2: complex = 0j
    enabled: bool = True

    def center_at(self, timestamp_s: float) -> float:
        return (
            self.center_hz
            + self.velocity_hz_per_s * timestamp_s
            + 0.5 * self.acceleration_hz_per_s2 * timestamp_s * timestamp_s
        )


class DualPeakSimulator:
    """固定随机种子的双 Lorentzian DC + 局部复数 1f 仿真器。"""

    def __init__(
        self,
        *,
        left: SimulatedPeak | None = None,
        right: SimulatedPeak | None = None,
        baseline: float = 1.0,
        baseline_slope_per_hz: float = 0.0,
        dc_noise_rms: float = 0.0,
        complex_noise_rms: float = 0.0,
        seed: int = 20260720,
    ) -> None:
        self.left = left or SimulatedPeak(
            center_hz=2.865e9,
            fwhm_hz=2.0e6,
            depth=0.04,
            b=complex(0.3e-6, -0.2e-6),
            g=cmath_from_polar(2.0e-12, 0.65),
        )
        self.right = right or SimulatedPeak(
            center_hz=2.875e9,
            fwhm_hz=2.2e6,
            depth=0.035,
            b=complex(-0.1e-6, 0.25e-6),
            g=cmath_from_polar(1.7e-12, -0.4),
        )
        self.baseline = float(baseline)
        self.baseline_slope_per_hz = float(baseline_slope_per_hz)
        self.dc_noise_rms = float(dc_noise_rms)
        self.complex_noise_rms = float(complex_noise_rms)
        self.rng = random.Random(seed)
        self.reference_hz = 0.5 * (self.left.center_hz + self.right.center_hz)

    def _noise(self, sigma: float) -> float:
        return self.rng.gauss(0.0, sigma) if sigma > 0 else 0.0

    def dc(self, frequency_hz: float, timestamp_s: float) -> float:
        value = self.baseline + self.baseline_slope_per_hz * (
            frequency_hz - self.reference_hz
        )
        for peak in (self.left, self.right):
            if not peak.enabled:
                continue
            detuning_hz = frequency_hz - peak.center_at(timestamp_s)
            value -= peak.depth / (1.0 + (2.0 * detuning_hz / peak.fwhm_hz) ** 2)
        return value + self._noise(self.dc_noise_rms)

    def z1(self, peak_id: PeakId, frequency_hz: float, timestamp_s: float) -> complex:
        peak = self.left if peak_id == PeakId.LEFT else self.right
        if not peak.enabled:
            return complex(
                self._noise(self.complex_noise_rms),
                self._noise(self.complex_noise_rms),
            )
        detuning_hz = frequency_hz - peak.center_at(timestamp_s)
        local = peak.b + peak.g * detuning_hz + peak.quadratic_per_hz2 * detuning_hz**2
        # 远离峰时 1f 回到接近零的背景，用于验证“假零点”失锁检测。
        envelope = 1.0 / (1.0 + (2.0 * detuning_hz / (3.0 * peak.fwhm_hz)) ** 4)
        value = local * envelope
        return value + complex(
            self._noise(self.complex_noise_rms),
            self._noise(self.complex_noise_rms),
        )

    def measure(
        self,
        peak_id: PeakId,
        frequency_hz: float,
        timestamp_s: float,
    ) -> PeakMeasurement:
        z1 = self.z1(peak_id, frequency_hz, timestamp_s)
        return PeakMeasurement(
            timestamp_s=float(timestamp_s),
            commanded_frequency_hz=float(frequency_hz),
            x1=float(z1.real),
            y1=float(z1.imag),
            dc=float(self.dc(frequency_hz, timestamp_s)),
        )


def cmath_from_polar(magnitude: float, phase_rad: float) -> complex:
    return complex(
        float(magnitude) * math.cos(float(phase_rad)),
        float(magnitude) * math.sin(float(phase_rad)),
    )
