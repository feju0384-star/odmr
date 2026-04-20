import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.schemas.instruments import CurrentScanRequest, ODMRRequest, SensitivityRequest
from backend.app.services.instrument_manager import manager

router = APIRouter(prefix="/api/measurement", tags=["measurement"])


@router.post("/odmr")
async def run_odmr(request: ODMRRequest) -> dict:
    return manager.run_odmr(request)


@router.post("/odmr/stop")
async def stop_odmr() -> dict:
    return manager.cancel_odmr_stream()


@router.post("/sensitivity/stop")
async def stop_sensitivity() -> dict:
    return manager.cancel_odmr_stream()


@router.post("/current/stop")
async def stop_current() -> dict:
    return manager.cancel_odmr_stream()


@router.websocket("/odmr/ws")
async def odmr_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            if manager.measurement_state.get("running"):
                await websocket.send_json(
                    {
                        "type": "odmr_error",
                        "message": "已有 ODMR 扫描正在运行，请先停止当前任务。",
                    }
                )
                continue
            request = ODMRRequest(**payload)
            manager.begin_odmr_stream(request)
            frequencies = manager.build_odmr_frequency_axis(request)
            streamed_freq: list[float] = []
            streamed_values: list[float] = []
            use_live_readout = manager.can_run_live_odmr(request)
            restore_output = bool(manager.microwave_state.get("config", {}).get("output_enabled", False))

            async def send_cancelled() -> None:
                trace = manager.cancel_odmr_stream_result(request, streamed_freq, streamed_values)
                await websocket.send_json(
                    {
                        "type": "odmr_cancelled",
                        "trace": trace,
                        "progress": len(streamed_values) / max(1, request.points),
                    }
                )

            await websocket.send_json(
                {
                    "type": "odmr_started",
                    "points": request.points,
                    "scan_mode": request.scan_mode,
                    "readout_source": request.readout_source,
                    "estimated_duration_s": manager.estimate_odmr_duration_s(request),
                    "live_readout": use_live_readout,
                }
            )
            try:
                if use_live_readout:
                    if not manager.set_microwave_output_enabled(True):
                        raise RuntimeError(manager.microwave_state.get("last_error") or "微波输出开启失败。")
                for index, freq in enumerate(frequencies, start=1):
                    if manager.odmr_stop_event.is_set():
                        await send_cancelled()
                        break

                    if use_live_readout:
                        if not manager.set_microwave_frequency(freq):
                            raise RuntimeError(manager.microwave_state.get("last_error") or "微波频率更新失败。")
                    await asyncio.sleep(manager._odmr_delay_s(request))
                    if manager.odmr_stop_event.is_set():
                        await send_cancelled()
                        break
                    value = (
                        manager.read_odmr_value(request.readout_source)
                        if use_live_readout
                        else manager.simulate_odmr_value(request, freq)
                    )
                    if manager.odmr_stop_event.is_set():
                        await send_cancelled()
                        break
                    streamed_freq.append(freq)
                    streamed_values.append(value)
                    manager.update_odmr_progress(request, index, freq, value)
                    await websocket.send_json(
                        {
                            "type": "odmr_point",
                            "index": index,
                            "points": request.points,
                            "progress": index / request.points,
                            "frequency_hz": freq,
                            "value": value,
                            "readout_source": request.readout_source,
                            "scan_mode": request.scan_mode,
                            "live_readout": use_live_readout,
                        }
                    )
                else:
                    trace = manager.finish_odmr_stream(request, streamed_freq, streamed_values)
                    await websocket.send_json({"type": "odmr_complete", "trace": trace})
            except Exception as exc:
                manager.measurement_state["running"] = False
                manager.measurement_state["status"] = "error"
                await websocket.send_json({"type": "odmr_error", "message": str(exc)})
            finally:
                if use_live_readout and manager.microwave_state.get("connected"):
                    manager.set_microwave_output_enabled(restore_output)
    except WebSocketDisconnect:
        manager.measurement_state["running"] = False
        return


@router.websocket("/sensitivity/ws")
async def sensitivity_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            if manager.measurement_state.get("running"):
                await websocket.send_json(
                    {
                        "type": "sensitivity_error",
                        "message": "已有测量任务正在运行，请先停止当前任务。",
                    }
                )
                continue
            request = SensitivityRequest(**payload)
            manager.begin_sensitivity_stream(request)
            await websocket.send_json(
                {
                    "type": "sensitivity_started",
                    "estimated_duration_s": manager.estimate_sensitivity_duration_s(request),
                    "channel_index": manager._resolve_measurement_channel_index(request.channel_index),
                }
            )
            try:
                result = await asyncio.to_thread(manager.run_sensitivity_measurement, request)
                result = manager.finish_sensitivity_stream(request, result, status="completed")
                await websocket.send_json({"type": "sensitivity_complete", "result": result})
            except Exception as exc:
                is_cancelled = "已停止" in str(exc)
                manager.measurement_state["running"] = False
                manager.measurement_state["mode"] = "idle"
                manager.measurement_state["status"] = "cancelled" if is_cancelled else "error"
                manager.measurement_state["cancel_requested"] = False
                await websocket.send_json(
                    {
                        "type": "sensitivity_cancelled" if is_cancelled else "sensitivity_error",
                        "message": str(exc),
                    }
                )
    except WebSocketDisconnect:
        manager.measurement_state["running"] = False
        return


@router.websocket("/current/ws")
async def current_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            if manager.measurement_state.get("running"):
                await websocket.send_json(
                    {
                        "type": "current_error",
                        "message": "已有测量任务正在运行，请先停止当前任务。",
                    }
                )
                continue
            request = CurrentScanRequest(**payload)
            manager.begin_current_stream(request)
            await websocket.send_json(
                {
                    "type": "current_started",
                    "estimated_duration_s": manager.estimate_current_duration_s(request),
                    "channel_index": manager._resolve_measurement_channel_index(request.channel_index),
                }
            )
            try:
                result = await asyncio.to_thread(manager.run_current_measurement, request)
                result = manager.finish_current_stream(request, result, status="completed")
                await websocket.send_json({"type": "current_complete", "result": result})
            except Exception as exc:
                is_cancelled = "已停止" in str(exc)
                manager.measurement_state["running"] = False
                manager.measurement_state["mode"] = "idle"
                manager.measurement_state["status"] = "cancelled" if is_cancelled else "error"
                manager.measurement_state["cancel_requested"] = False
                await websocket.send_json(
                    {
                        "type": "current_cancelled" if is_cancelled else "current_error",
                        "message": str(exc),
                    }
                )
    except WebSocketDisconnect:
        manager.measurement_state["running"] = False
        return
