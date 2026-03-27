from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "NV Measurement Backend"
    app_version: str = "0.1.0"
    cors_origins: list[str] = [
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]


settings = Settings()
