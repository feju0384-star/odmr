import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.app.schemas.instruments import (
    LabOneServerConfig,
    LockinChannelConfig,
    LockinConnectRequest,
    MicrowaveConfigRequest,
    MicrowaveConnectRequest,
)
from backend.app.services.instrument_manager import manager

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


@router.get("/dashboard")
async def dashboard() -> dict:
    return manager.get_dashboard()


@router.websocket("/lockin/ws")
async def lockin_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    last_stream_seq: int | None = None
    try:
        while True:
            payload = manager.get_lockin_live(last_stream_seq)["data"]
            stream_seq = int(payload.get("stream_seq", 0))
            if last_stream_seq is None or stream_seq != last_stream_seq:
                last_stream_seq = stream_seq
                await websocket.send_json(
                    {
                        "type": "lockin_live",
                        "timestamp": float(payload.get("stream_timestamp", manager.last_signal_update or 0.0)),
                        **payload,
                    }
                )
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        return


@router.get("/lockin/discover")
async def discover_lockins(
    server_host: str = Query(default="localhost"),
    server_port: int = Query(default=8004),
    hf2: bool = Query(default=False),
) -> dict:
    return manager.discover_lockins(
        LabOneServerConfig(server_host=server_host, server_port=server_port, hf2=hf2)
    )


@router.post("/lockin/connect")
async def connect_lockin(request: LockinConnectRequest) -> dict:
    return manager.connect_lockin(request)


@router.post("/lockin/disconnect")
async def disconnect_lockin() -> dict:
    return manager.disconnect_lockin()


@router.post("/lockin/config")
async def update_lockin(request: LockinChannelConfig) -> dict:
    return manager.update_lockin(request)


@router.get("/lockin/node-tree")
async def get_lockin_node_tree(limit: int = Query(default=200, ge=1, le=5000)) -> dict:
    return manager.get_lockin_node_tree(limit)


@router.get("/microwave/discover")
async def discover_microwaves() -> dict:
    return manager.discover_microwaves()


@router.post("/microwave/connect")
async def connect_microwave(request: MicrowaveConnectRequest) -> dict:
    return manager.connect_microwave(request)


@router.post("/microwave/disconnect")
async def disconnect_microwave() -> dict:
    return manager.disconnect_microwave()


@router.post("/microwave/config")
async def update_microwave(request: MicrowaveConfigRequest) -> dict:
    return manager.update_microwave(request)
