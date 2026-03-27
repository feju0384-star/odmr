from fastapi import APIRouter

from backend.app.core.config import settings

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
    }
