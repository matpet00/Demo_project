import json
import logging
import os
from datetime import datetime, timezone

SECRET_KEYS = {"password", "api_key", "authorization", "token", "secret"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service):
        super().__init__()
        self.service = service

    def format(self, record):
        fields = dict(getattr(record, "desk", {}) or {})
        for key in list(fields):
            if key.lower() in SECRET_KEYS:
                fields.pop(key)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "event": fields.pop("event", record.getMessage()),
            "message": record.getMessage(),
        }
        payload.update(fields)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service):
    logger = logging.getLogger("desk")
    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_event(event, **fields):
    clean = {key: value for key, value in fields.items() if key.lower() not in SECRET_KEYS}
    clean["event"] = event
    logging.getLogger("desk").info(event, extra={"desk": clean})


def log_message(event, message, **fields):
    payload = {
        "correlation_id": message.get("correlation_id"),
        "message_id": message.get("id"),
        "user_uuid": message.get("user_uuid"),
    }
    if message.get("queue_name"):
        payload["queue"] = message["queue_name"]
    payload.update(fields)
    log_event(event, **payload)
