import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.schemas.state_estimation import StateEstimationTrackingRequest
from backend.app.services.instrument_manager import manager
from backend.app.services.state_estimation_tracking import (
    StateEstimationTrackingRuntime,
)


router = APIRouter(
    prefix="/api/state-estimation-current",
    tags=["state-estimation-current"],
)
runtime = StateEstimationTrackingRuntime(manager)


@router.post("/stop")
async def stop_state_estimation_current() -> dict:
    return manager.cancel_odmr_stream()


@router.websocket("/ws")
async def state_estimation_current_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    worker: asyncio.Task | None = None
    request: StateEstimationTrackingRequest | None = None
    try:
        payload = await websocket.receive_json()
        if manager.measurement_state.get("running"):
            await websocket.send_json(
                {
                    "type": "state_estimation_error",
                    "message": "已有测量任务正在运行，请先停止当前任务。",
                }
            )
            return

        request = StateEstimationTrackingRequest(**payload)
        runtime.begin(request)
        await websocket.send_json(
            {
                "type": "state_estimation_started",
                "estimator_type": request.estimator_type,
                "channel_index": manager._resolve_measurement_channel_index(
                    request.channel_index
                ),
                "max_tracking_duration_s": request.max_tracking_duration_s,
            }
        )

        event_queue: asyncio.Queue[dict] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def publish_event(event: dict) -> None:
            loop.call_soon_threadsafe(event_queue.put_nowait, event)

        worker = asyncio.create_task(
            asyncio.to_thread(runtime.run, request, publish_event)
        )
        while not worker.done() or not event_queue.empty():
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            await websocket.send_json(event)

        result = await worker
        runtime.finish(request, result)
        await websocket.send_json(
            {
                "type": (
                    "state_estimation_cancelled"
                    if result.get("status") == "cancelled"
                    else "state_estimation_complete"
                ),
                "result": result,
            }
        )
    except WebSocketDisconnect:
        if worker is not None:
            manager.cancel_odmr_stream()
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=10.0,
                )
                if request is not None:
                    runtime.finish(request, result)
            except Exception:
                pass
        elif (
            request is not None
            and manager.measurement_state.get("mode") == runtime.MODE
        ):
            manager.measurement_state.update(
                {
                    "running": False,
                    "mode": "idle",
                    "status": "cancelled",
                    "cancel_requested": False,
                }
            )
        return
    except Exception as exc:
        manager.measurement_state.update(
            {
                "running": False,
                "mode": "idle",
                "status": "error",
                "cancel_requested": False,
            }
        )
        try:
            await websocket.send_json(
                {
                    "type": "state_estimation_error",
                    "message": str(exc),
                }
            )
        except Exception:
            pass
