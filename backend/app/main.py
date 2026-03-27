import uvicorn
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.config import settings
from backend.app.routers.instruments import router as instrument_router
from backend.app.routers.measurement import router as measurement_router
from backend.app.routers.system import router as system_router

app = FastAPI(title=settings.app_name, version=settings.app_version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system_router)
app.include_router(instrument_router)
app.include_router(measurement_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "message": "NV Measurement Backend is running.",
        "frontend": "Use frontend/index.html with a static file server.",
    }


if __name__ == "__main__":
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=False)
