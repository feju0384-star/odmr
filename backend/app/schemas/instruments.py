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
    channel_index: int = Field(default=0, ge=0, le=3)
    demod_index: int = Field(default=0, ge=0)
    osc_index: int = Field(default=0, ge=0)
    input_index: int = Field(default=0, ge=0)
    enabled: bool = True
    demod_freq_hz: float = 13_700.0
    time_constant_ms: float = 10.0
    low_pass_order: int = Field(default=4, ge=1, le=8)
    input_range_mv: float = 100.0
    phase_deg: float = 0.0
    harmonic: int = Field(default=1, ge=1, le=64)
    sample_rate_hz: float = 1_000.0
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
    fm_source: Literal["internal", "external"] = "internal"
    fm_deviation_hz: float = 100_000.0
    fm_rate_hz: float = 1_000.0


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


class ApiResponse(BaseModel):
    success: bool
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
