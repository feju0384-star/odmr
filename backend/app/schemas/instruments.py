from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LabOneServerConfig(BaseModel):
    server_host: str = "localhost"
    server_port: int = 8004
    hf2: bool = False


class LockinConnectRequest(LabOneServerConfig):
    serial: str = "dev1234"
    interface: str | None = None


class LockinChannelConfig(BaseModel):
    channel_index: int = Field(default=0, ge=0)
    demod_index: int = Field(default=0, ge=0)
    osc_index: int = Field(default=0, ge=0)
    input_index: int = Field(default=0, ge=0)
    enabled: bool = True
    input_signal: int = 0
    demod_freq_hz: float = 13_700.0
    time_constant_ms: float = 10.0
    low_pass_order: int = Field(default=4, ge=1, le=8)
    low_pass_bandwidth_hz: float = 6.922905799116954
    input_range_mv: float = 100.0
    input_impedance_50ohm: bool = False
    input_voltage_scaling: float = 1.0
    input_ac_coupling: bool = False
    input_differential: bool = False
    input_float: bool = False
    current_range_ma: float = 10.0
    current_scaling: float = 1.0
    current_float: bool = False
    phase_deg: float = 0.0
    harmonic: int = Field(default=1, ge=1, le=64)
    sinc_enabled: bool = False
    sample_rate_hz: float = 1_000.0
    trigger_mode: int = 0
    reference_source: Literal["internal", "external"] = "internal"
    external_reference_index: int = Field(default=0, ge=0)
    aux_output_channel: int = Field(default=0, ge=0)
    aux_output_offset_v: float = 0.0
    display_source: Literal["x_v", "y_v", "r_v"] = "r_v"


class MicrowaveConnectRequest(BaseModel):
    address: str = "TCPIP0::192.168.1.100::inst0::INSTR"
    timeout_ms: int = 3000


class MicrowaveConfigRequest(BaseModel):
    mode: Literal["cw", "sweep"] = "cw"
    frequency_hz: float = 2.87e9
    center_frequency_hz: float = 2.87e9
    sweep_start_hz: float = 2.82e9
    sweep_stop_hz: float = 2.92e9
    sweep_points: int = Field(default=101, ge=2, le=5000)
    dwell_ms: float = Field(default=5.0, ge=0.1)
    power_dbm: float = -10.0
    output_enabled: bool = False
    iq_enabled: bool = False
    fm_enabled: bool = False
    fm_source: Literal["internal", "external"] = "external"
    fm_deviation_hz: float = 100_000.0
    fm_rate_hz: float = 1_000.0
    lf_output_enabled: bool = False
    lf_output_source: Literal["monitor", "function1", "dc"] = "monitor"
    lf_output_amplitude_v: float = 1.0
    lf_output_offset_v: float = 0.0
    lf_output_load_ohm: Literal[50, 600, 1000000] = 1000000


class ODMRRequest(BaseModel):
    scan_mode: Literal["software_sync", "aux_map"] = "software_sync"
    readout_source: Literal["x_v", "y_v", "r_v"] = "r_v"
    start_hz: float = Field(default=2.83e9)
    stop_hz: float = Field(default=2.91e9)
    points: int = Field(default=161, ge=3, le=2001)
    dwell_ms: float = Field(default=8.0, ge=0.1)
    averages: int = Field(default=4, ge=1, le=1000)
    aux_voltage_min_v: float = 0.0
    aux_voltage_max_v: float = 10.0
    aux_frequency_min_hz: float = 2.82e9
    aux_frequency_max_hz: float = 2.92e9


class SensitivityRequest(BaseModel):
    channel_index: int = Field(default=0, ge=0)
    search_center_hz: float = 2.87e9
    search_span_hz: float = Field(default=20e6, gt=0)
    search_points: int = Field(default=121, ge=11, le=4001)
    settle_ms: float = Field(default=30.0, ge=1.0)
    slope_fit_points: int = Field(default=9, ge=3, le=41)
    asd_duration_s: float = Field(default=5.0, ge=1.0, le=120.0)
    asd_min_frequency_hz: float = Field(default=1.0, ge=0.0)
    phase_target: Literal["x_v", "y_v", "auto"] = "auto"
    cos_alpha: float = Field(default=1.0, gt=0.0, le=1.0)
    gamma_hz_per_t: float = Field(default=28e9, gt=0.0)


class CurrentScanRequest(BaseModel):
    channel_index: int = Field(default=0, ge=0)
    start_hz: float = Field(default=2.83e9)
    stop_hz: float = Field(default=2.91e9)
    search_points: int = Field(default=121, ge=11, le=4001)
    settle_ms: float = Field(default=30.0, ge=1.0)
    slope_fit_points: int = Field(default=9, ge=3, le=41)
    phase_target: Literal["x_v", "y_v", "auto"] = "auto"


class ApiResponse(BaseModel):
    success: bool
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
