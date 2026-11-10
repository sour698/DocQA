"""Structured (JSON) logging with a per-request id, so one request can be followed through the logs."""

import contextvars
import json
import logging
import sys
import time

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id.get(),
        }
        data.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        rid = request_id.get()
        return f"{base} [{rid}]" if rid != "-" else base


def setup_logging(level: str = "INFO", as_json: bool = True) -> None:
    """Idempotent: safe to call from every worker process."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_docqa", False):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler._docqa = True  # type: ignore[attr-defined]
    handler.setFormatter(
        JsonFormatter()
        if as_json
        else TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our own access log line
    for name in ("uvicorn", "uvicorn.error"):  # route uvicorn's own logs through our handler
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
