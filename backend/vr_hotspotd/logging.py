import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Optional structured fields
        if hasattr(record, "correlation_id"):
            payload["correlation_id"] = getattr(record, "correlation_id")
        for k in ("op", "bind", "path", "method", "result_code"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(",", ":"))


def setup_logging(level: Optional[str] = None) -> None:
    lvl = (level or os.environ.get("VR_HOTSPOT_LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, lvl, logging.INFO))

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
