from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

from zhinst.toolkit import Session


def _scalar(value, default=0.0):
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        if not value:
            return default
        return _scalar(value[0], default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as tmp:
        json.dump(payload, tmp, ensure_ascii=False)
        temp_name = tmp.name
    os.replace(temp_name, path)


def main() -> int:
    if len(sys.argv) < 8:
        return 2

    cache_path = Path(sys.argv[1])
    host = sys.argv[2]
    port = int(sys.argv[3])
    hf2 = sys.argv[4].lower() == "true"
    serial = sys.argv[5]
    interface = None if sys.argv[6] == "-" else sys.argv[6]
    demod_count = max(1, int(sys.argv[7]))
    interval = float(sys.argv[8]) if len(sys.argv) > 8 else 0.12

    session = Session(host, port, hf2=hf2, allow_version_mismatch=True)
    device = session.connect_device(serial, interface=interface) if interface else session.connect_device(serial)

    last_channels = [
        {
            "x_v": 0.0,
            "y_v": 0.0,
            "r_v": 0.0,
            "x_uv": 0.0,
            "y_uv": 0.0,
            "r_uv": 0.0,
            "channel_index": index,
        }
        for index in range(demod_count)
    ]

    while True:
        channels = []
        for index in range(demod_count):
            try:
                sample = device.demods[index].sample()
                x_val = _scalar(sample.get("x", 0.0), 0.0)
                y_val = _scalar(sample.get("y", 0.0), 0.0)
                r_val = math.sqrt(x_val**2 + y_val**2)
                channels.append(
                    {
                        "x_v": x_val,
                        "y_v": y_val,
                        "r_v": r_val,
                        "x_uv": x_val * 1e6,
                        "y_uv": y_val * 1e6,
                        "r_uv": r_val * 1e6,
                        "channel_index": index,
                    }
                )
            except Exception:
                channels.append(dict(last_channels[index]))

        last_channels = channels
        _atomic_write(
            cache_path,
            {
                "timestamp": time.time(),
                "signal_channels": channels,
            },
        )
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
