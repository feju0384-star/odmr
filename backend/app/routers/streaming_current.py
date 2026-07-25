import asyncio

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend.app.schemas.streaming_current import StreamingCurrentTrackingRequest
from backend.app.services.instrument_manager import manager
from backend.app.services.streaming_current_tracking import (
    StreamingCurrentTrackingRuntime,
)


router = APIRouter(
    prefix="/api/streaming-current",
    tags=["streaming-current"],
)
runtime = StreamingCurrentTrackingRuntime(manager)


@router.post("/stop")
async def stop_streaming_current() -> dict:
    return manager.cancel_odmr_stream()


@router.get("/recording/status")
async def streaming_current_recording_status(
    session_id: str | None = Query(default=None),
) -> dict:
    return {
        "success": True,
        "data": runtime.recording_status(session_id),
    }


@router.get("/recording/download")
async def download_streaming_current_recording(
    session_id: str | None = Query(default=None),
) -> FileResponse:
    try:
        path, is_temporary = await asyncio.to_thread(
            runtime.export_recording,
            session_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"CSV导出失败: {exc}") from exc
    return FileResponse(
        path=path,
        filename=(
            path.name.replace(".snapshot_", "streaming_current_snapshot_")
            if is_temporary
            else path.name
        ),
        media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "no-store"},
        background=(
            BackgroundTask(path.unlink, missing_ok=True)
            if is_temporary
            else None
        ),
    )


@router.websocket("/ws")
async def streaming_current_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    worker: asyncio.Task | None = None
    request: StreamingCurrentTrackingRequest | None = None
    try:
        payload = await websocket.receive_json()
        if manager.measurement_state.get("running"):
            await websocket.send_json(
                {
                    "type": "streaming_current_error",
                    "message": "已有测量任务正在运行，请先停止当前任务。",
                }
            )
            return

        request = StreamingCurrentTrackingRequest(**payload)
        runtime.begin(request)
        await websocket.send_json(
            {
                "type": "streaming_current_started",
                "channel_index": manager._resolve_measurement_channel_index(
                    request.channel_index
                ),
                "stream_sample_rate_hz": request.stream_sample_rate_hz,
                "stream_poll_window_ms": request.stream_poll_window_ms,
                "recording": runtime.recording_status(),
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
                    "streaming_current_cancelled"
                    if result.get("status") == "cancelled"
                    else "streaming_current_complete"
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
                    timeout=15.0,
                )
                if request is not None:
                    runtime.finish(request, result)
            except Exception:
                pass
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
                    "type": "streaming_current_error",
                    "message": str(exc),
                    "recording": runtime.recording_status(),
                }
            )
        except Exception:
            pass
