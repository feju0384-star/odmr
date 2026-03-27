from fastapi import APIRouter, Query

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


@router.post("/lockin/config")
async def update_lockin(request: LockinChannelConfig) -> dict:
    return manager.update_lockin(request)


@router.get("/microwave/discover")
async def discover_microwaves() -> dict:
    return manager.discover_microwaves()


@router.post("/microwave/connect")
async def connect_microwave(request: MicrowaveConnectRequest) -> dict:
    return manager.connect_microwave(request)


@router.post("/microwave/config")
async def update_microwave(request: MicrowaveConfigRequest) -> dict:
    return manager.update_microwave(request)
