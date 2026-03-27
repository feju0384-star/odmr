from __future__ import annotations

import math
import random
from collections import deque
from datetime import datetime
from typing import Any

try:
    import pyvisa
except ImportError:  # pragma: no cover
    pyvisa = None

try:
    from zhinst.toolkit import Session
except ImportError:  # pragma: no cover
    Session = None

from backend.app.schemas.instruments import (
    LabOneServerConfig,
    LockinChannelConfig,
    LockinConnectRequest,
    MicrowaveConfigRequest,
    MicrowaveConnectRequest,
    ODMRRequest,
)


def _default_lockin_channels() -> list[dict[str, Any]]:
    return [
        LockinChannelConfig(channel_index=index, demod_index=index, osc_index=index).model_dump()
        for index in range(4)
    ]


class InstrumentManager:
    def __init__(self) -> None:
        self.rm = pyvisa.ResourceManager() if pyvisa else None
        self.lockin_session: Any | None = None
        self.lockin_device: Any | None = None
        self.microwave_resource: Any | None = None
        self.logs: deque[dict[str, str]] = deque(maxlen=40)

        self.lockin_state: dict[str, Any] = {
            "connected": False,
            "serial": "",
            "name": "",
            "server_host": "localhost",
            "server_port": 8004,
            "hf2": False,
            "interface": "",
            "active_channel": 0,
            "last_discovery": {"visible": [], "connected": [], "error": ""},
            "channels": _default_lockin_channels(),
        }
        self.microwave_state: dict[str, Any] = {
            "connected": False,
            "address": "",
            "idn": "",
            "available_resources": [],
            "last_error": "",
            "config": MicrowaveConfigRequest().model_dump(),
        }
        self.measurement_state: dict[str, Any] = {
            "running": False,
            "last_request": ODMRRequest().model_dump(),
            "last_trace": self._generate_trace(ODMRRequest()),
        }
        self._log("系统启动，后端进入待机状态。")

    def _log(self, message: str, level: str = "info") -> None:
        self.logs.appendleft(
            {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "message": message,
            }
        )

    def _serialize_device_item(self, value: Any) -> dict[str, str]:
        if isinstance(value, str):
            return {"serial": value, "label": value}
        serial = getattr(value, "serial", "") or str(value)
        dev_type = getattr(value, "device_type", "")
        label = f"{serial} {dev_type}".strip()
        return {"serial": serial, "label": label}

    def discover_lockins(self, config: LabOneServerConfig) -> dict[str, Any]:
        self.lockin_state["server_host"] = config.server_host
        self.lockin_state["server_port"] = config.server_port
        self.lockin_state["hf2"] = config.hf2

        if Session is None:
            message = "未安装 zhinst-toolkit，无法发现锁相设备。"
            self.lockin_state["last_discovery"] = {
                "visible": [],
                "connected": [],
                "error": message,
            }
            self._log(message, "warning")
            return {"success": False, "message": message, "data": self.lockin_state["last_discovery"]}

        try:
            session = Session(
                config.server_host,
                config.server_port,
                hf2=config.hf2,
                allow_version_mismatch=True,
            )
            visible = [self._serialize_device_item(item) for item in session.devices.visible()]
            connected = [self._serialize_device_item(item) for item in session.devices.connected()]
            self.lockin_state["last_discovery"] = {
                "visible": visible,
                "connected": connected,
                "error": "",
            }
            self._log(f"LabOne 发现完成，可见设备 {len(visible)} 台。")
            return {
                "success": True,
                "message": "已获取 LabOne Data Server 的设备列表。",
                "data": self.lockin_state["last_discovery"],
            }
        except Exception as exc:  # pragma: no cover
            message = f"锁相发现失败: {exc}"
            self.lockin_state["last_discovery"] = {
                "visible": [],
                "connected": [],
                "error": message,
            }
            self._log(message, "error")
            return {"success": False, "message": message, "data": self.lockin_state["last_discovery"]}

    def connect_lockin(self, request: LockinConnectRequest) -> dict[str, Any]:
        discovery = self.discover_lockins(
            LabOneServerConfig(
                server_host=request.server_host,
                server_port=request.server_port,
                hf2=request.hf2,
            )
        )
        if not discovery["success"]:
            return discovery

        if Session is None:
            return {
                "success": False,
                "message": "未安装 zhinst-toolkit，无法连接锁相设备。",
                "data": self.lockin_state,
            }

        try:
            session = Session(
                request.server_host,
                request.server_port,
                hf2=request.hf2,
                allow_version_mismatch=True,
            )
            device = (
                session.connect_device(request.serial, interface=request.interface)
                if request.interface
                else session.connect_device(request.serial)
            )
            self.lockin_session = session
            self.lockin_device = device
            self.lockin_state.update(
                {
                    "connected": True,
                    "serial": request.serial,
                    "name": getattr(device, "device_type", "Zurich Instruments Device"),
                    "server_host": request.server_host,
                    "server_port": request.server_port,
                    "hf2": request.hf2,
                    "interface": request.interface or "",
                }
            )
            self._log(f"锁相设备已连接: {request.serial}")
            return {
                "success": True,
                "message": f"已连接锁相设备 {request.serial}",
                "data": self.lockin_state,
            }
        except Exception as exc:  # pragma: no cover
            message = f"锁相连接失败: {exc}"
            self.lockin_state["connected"] = False
            self._log(message, "error")
            return {"success": False, "message": message, "data": self.lockin_state}

    def update_lockin(self, request: LockinChannelConfig) -> dict[str, Any]:
        channel_index = request.channel_index
        self.lockin_state["active_channel"] = channel_index
        self.lockin_state["channels"][channel_index] = request.model_dump()
        notes: list[str] = []

        if self.lockin_device is not None:
            try:
                self.lockin_device.oscs[request.osc_index].freq(request.demod_freq_hz)
                notes.append("osc.freq")
            except Exception:
                pass

            try:
                self.lockin_device.demods[request.demod_index].timeconstant(
                    request.time_constant_ms / 1000.0
                )
                notes.append("demod.timeconstant")
            except Exception:
                pass

            try:
                self.lockin_device.demods[request.demod_index].phaseshift(request.phase_deg)
                notes.append("demod.phaseshift")
            except Exception:
                pass

            try:
                self.lockin_device.sigins[request.input_index].range(
                    request.input_range_mv / 1000.0
                )
                notes.append("sigin.range")
            except Exception:
                pass

        note_text = ", ".join(notes) if notes else "当前只更新后端状态。"
        self._log(
            f"锁相通道 {channel_index + 1} 已更新: {request.demod_freq_hz:.1f} Hz, "
            f"TC {request.time_constant_ms:.1f} ms"
        )
        return {
            "success": True,
            "message": f"锁相通道 {channel_index + 1} 参数已保存。{note_text}",
            "data": self.lockin_state,
        }

    def discover_microwaves(self) -> dict[str, Any]:
        if self.rm is None:
            message = "未安装 PyVISA，无法枚举 VISA 资源。"
            self.microwave_state["available_resources"] = []
            self.microwave_state["last_error"] = message
            self._log(message, "warning")
            return {"success": False, "message": message, "data": {"resources": []}}

        try:
            resources = list(self.rm.list_resources())
            self.microwave_state["available_resources"] = resources
            self.microwave_state["last_error"] = ""
            self._log(f"VISA 资源发现完成，共 {len(resources)} 个。")
            return {
                "success": True,
                "message": "已获取 VISA 资源列表。",
                "data": {"resources": resources},
            }
        except Exception as exc:  # pragma: no cover
            message = f"微波源发现失败: {exc}"
            self.microwave_state["last_error"] = message
            self._log(message, "error")
            return {"success": False, "message": message, "data": {"resources": []}}

    def connect_microwave(self, request: MicrowaveConnectRequest) -> dict[str, Any]:
        if self.rm is None:
            message = "未安装 PyVISA，无法连接微波源。"
            self._log(message, "warning")
            return {"success": False, "message": message, "data": self.microwave_state}

        try:
            resource = self.rm.open_resource(request.address)
            resource.timeout = request.timeout_ms
            try:
                idn = str(resource.query("*IDN?")).strip()
            except Exception:
                idn = "Unknown Instrument"
            self.microwave_resource = resource
            self.microwave_state.update(
                {
                    "connected": True,
                    "address": request.address,
                    "idn": idn,
                    "last_error": "",
                }
            )
            self._log(f"微波源已连接: {request.address}")
            return {
                "success": True,
                "message": f"已连接微波源 {request.address}",
                "data": self.microwave_state,
            }
        except Exception as exc:  # pragma: no cover
            message = f"微波源连接失败: {exc}"
            self.microwave_state["connected"] = False
            self.microwave_state["last_error"] = message
            self._log(message, "error")
            return {"success": False, "message": message, "data": self.microwave_state}

    def update_microwave(self, request: MicrowaveConfigRequest) -> dict[str, Any]:
        self.microwave_state["config"] = request.model_dump()
        notes: list[str] = []

        if self.microwave_resource is not None:
            try:
                if request.mode == "cw":
                    self.microwave_resource.write(f":FREQ {request.frequency_hz}")
                else:
                    self.microwave_resource.write(f":FREQ:STAR {request.sweep_start_hz}")
                    self.microwave_resource.write(f":FREQ:STOP {request.sweep_stop_hz}")
                    self.microwave_resource.write(f":SWE:POIN {request.sweep_points}")
                notes.append("frequency")
            except Exception:
                pass

            try:
                self.microwave_resource.write(f":POW {request.power_dbm}")
                notes.append("power")
            except Exception:
                pass

            try:
                self.microwave_resource.write(f":OUTP {'ON' if request.output_enabled else 'OFF'}")
                notes.append("output")
            except Exception:
                pass

            try:
                self.microwave_resource.write(f":IQ:STAT {'ON' if request.iq_enabled else 'OFF'}")
                notes.append("iq")
            except Exception:
                pass

        note_text = ", ".join(notes) if notes else "当前只更新后端状态。"
        self._log(
            f"微波参数已更新: mode={request.mode}, "
            f"power={request.power_dbm:.1f} dBm, fm={'on' if request.fm_enabled else 'off'}"
        )
        return {
            "success": True,
            "message": f"微波参数已保存。{note_text}",
            "data": self.microwave_state,
        }

    def run_odmr(self, request: ODMRRequest) -> dict[str, Any]:
        self.measurement_state["running"] = True
        self.measurement_state["last_request"] = request.model_dump()
        trace = self._generate_trace(request)
        self.measurement_state["last_trace"] = trace
        self.measurement_state["running"] = False
        self._log(
            f"ODMR 扫频完成: mode={request.scan_mode}, "
            f"{request.start_hz / 1e9:.6f}-{request.stop_hz / 1e9:.6f} GHz"
        )
        return {
            "success": True,
            "message": "ODMR 扫频完成。",
            "data": {"trace": trace, "measurement": self.measurement_state},
        }

    def get_dashboard(self) -> dict[str, Any]:
        signal_channels = self._simulate_signal_channels()
        active_channel = self.lockin_state["active_channel"]
        active_signal = signal_channels[active_channel]
        return {
            "lockin": self.lockin_state,
            "microwave": self.microwave_state,
            "measurement": self.measurement_state,
            "signal": active_signal,
            "signal_channels": signal_channels,
            "logs": list(self.logs),
            "capabilities": {
                "zhinst_toolkit": Session is not None,
                "pyvisa": pyvisa is not None,
            },
        }

    def _simulate_signal_channels(self) -> list[dict[str, float]]:
        microwave_cfg = self.microwave_state["config"]
        base_frequency = (
            microwave_cfg["frequency_hz"]
            if microwave_cfg["mode"] == "cw"
            else microwave_cfg["center_frequency_hz"]
        )
        channels: list[dict[str, float]] = []
        for index, channel in enumerate(self.lockin_state["channels"]):
            freq_offset_mhz = (base_frequency - 2.87e9) / 1e6
            detune = (channel["demod_freq_hz"] - 13_700.0) / 13_700.0
            phase_rad = math.radians(channel["phase_deg"] + index * 9.0)
            gain = 0.14 * max(0.45, 1.0 - abs(freq_offset_mhz) * 0.01)
            noise = random.uniform(-0.0035, 0.0035)
            x_val = gain * math.cos(phase_rad) * (1.0 - detune * 0.15) + noise
            y_val = gain * math.sin(phase_rad) * (0.75 + detune * 0.1) + noise
            r_val = math.sqrt(x_val**2 + y_val**2)
            channels.append(
                {
                    "x_v": round(x_val, 6),
                    "y_v": round(y_val, 6),
                    "r_v": round(r_val, 6),
                    "channel_index": index,
                }
            )
        return channels

    def _generate_trace(self, request: ODMRRequest) -> dict[str, list[float] | str]:
        frequencies = [
            request.start_hz
            + index * (request.stop_hz - request.start_hz) / (request.points - 1)
            for index in range(request.points)
        ]

        center_a = 2.8704e9
        center_b = 2.8732e9
        width = 4.0e6 if request.scan_mode == "software_sync" else 4.8e6
        readout_scale = {"x_v": 0.92, "y_v": 0.87, "r_v": 1.0}[request.readout_source]

        values: list[float] = []
        for freq in frequencies:
            dip_a = 0.032 / (1.0 + ((freq - center_a) / width) ** 2)
            dip_b = 0.028 / (1.0 + ((freq - center_b) / width) ** 2)
            baseline = 0.996 + 0.003 * math.sin((freq - request.start_hz) / 1.5e7)
            noise = random.uniform(-0.002, 0.002) / request.averages
            values.append(round((baseline - dip_a - dip_b + noise) * readout_scale, 6))

        return {
            "frequency_hz": frequencies,
            "intensity": values,
            "scan_mode": request.scan_mode,
            "readout_source": request.readout_source,
        }


manager = InstrumentManager()
