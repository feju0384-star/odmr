from fastapi import APIRouter

from backend.app.schemas.instruments import ODMRRequest
from backend.app.services.instrument_manager import manager

router = APIRouter(prefix="/api/measurement", tags=["measurement"])


@router.post("/odmr")
async def run_odmr(request: ODMRRequest) -> dict:
    return manager.run_odmr(request)
