from __future__ import annotations

from pydantic import Field, model_validator

from backend.app.schemas.instruments import CurrentTrackingRequest


class StreamingCurrentTrackingRequest(CurrentTrackingRequest):
    """独立的Zurich连续订阅双峰PID跟踪参数。"""

    tracking_settle_ms: float = Field(default=5.0, ge=0.0, le=5_000.0)
    stream_sample_rate_hz: float = Field(default=2_000.0, ge=100.0, le=200_000.0)
    stream_poll_window_ms: float = Field(default=5.0, ge=1.0, le=100.0)
    stream_poll_timeout_ms: float = Field(default=250.0, ge=10.0, le=5_000.0)
    stream_timestamp_margin_ms: float = Field(default=1.0, ge=0.0, le=20.0)
    stream_warmup_timeout_s: float = Field(default=2.0, ge=0.2, le=30.0)

    @model_validator(mode="after")
    def validate_stream_parameters(self) -> "StreamingCurrentTrackingRequest":
        if self.stream_poll_timeout_ms < self.stream_poll_window_ms:
            raise ValueError("流式poll超时不能小于poll记录窗口。")
        return self

    def pid_request(self) -> CurrentTrackingRequest:
        payload = {
            name: getattr(self, name)
            for name in CurrentTrackingRequest.model_fields
        }
        # 流式页面使用自己的逐输出点批量CSV记录器，不写入原PID页的独立会话。
        payload["record_enabled"] = False
        return CurrentTrackingRequest(**payload)
