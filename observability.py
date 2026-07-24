import contextvars
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, text
from sqlalchemy.orm import Session

import models
from database import get_db
from services.help_crypto import HelpDataCipher
from services.help_notifications import ResendProvider


router = APIRouter(tags=["sistema"])
logger = logging.getLogger("rescate.security")
request_correlation_id = contextvars.ContextVar("request_correlation_id", default=None)
FORBIDDEN_LOG_KEYS = frozenset({
    "account", "account_id", "account_identifier", "api_key", "authorization",
    "body", "ciphertext", "cookie", "email", "identifier", "ip", "ip_hash",
    "path", "private_path", "query", "query_string", "storage_path", "token",
})
_SENSITIVE_VALUE = re.compile(r"(?:[^\s@]+@[^\s@]+|\benc:v\d+:|bearer\s+)", re.IGNORECASE)
_SAFE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._-]{8,100}$")


def _safe_log_value(key, value):
    if str(key).lower() in FORBIDDEN_LOG_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(nested_key)[:100]: _safe_log_value(nested_key, nested_value)
            for nested_key, nested_value in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_log_value(key, item) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    rendered = str(value)[:200]
    return "[REDACTED]" if _SENSITIVE_VALUE.search(rendered) else rendered


def safe_log(target_logger, level, event, **fields):
    marker = {
        "event": _safe_log_value("event", str(event)[:100]),
        "correlation_id": request_correlation_id.get(),
    }
    marker.update({key: _safe_log_value(key, value) for key, value in fields.items()})
    target_logger.log(level, json.dumps(marker, sort_keys=True, separators=(",", ":")))


class Metrics:
    REQUEST_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    BCV_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self):
        self._lock = threading.Lock()
        self.clear()

    def clear(self):
        with getattr(self, "_lock", threading.Lock()):
            self._counters = defaultdict(float)
            self._observations = defaultdict(list)

    @staticmethod
    def _key(name, labels):
        return name, tuple(sorted((str(key), str(value)) for key, value in labels.items()))

    def increment(self, name, amount=1, **labels):
        with self._lock:
            self._counters[self._key(name, labels)] += amount

    def observe(self, name, value, **labels):
        with self._lock:
            self._observations[self._key(name, labels)].append(float(value))

    @staticmethod
    def _labels(labels, extra=None):
        values = list(labels)
        if extra:
            values.extend(extra.items())
        if not values:
            return ""
        encoded = ",".join(
            f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
            for key, value in sorted(values)
        )
        return "{" + encoded + "}"

    @staticmethod
    def _number(value):
        return str(int(value)) if float(value).is_integer() else str(value)

    def render(self):
        with self._lock:
            counters = dict(self._counters)
            observations = {key: list(values) for key, values in self._observations.items()}
        lines = []
        for (name, labels), value in sorted(counters.items()):
            lines.append(f"rescate_{name}{self._labels(labels)} {self._number(value)}")
        for (name, labels), values in sorted(observations.items()):
            buckets = self.BCV_BUCKETS if name.startswith("bcv_") else self.REQUEST_BUCKETS
            for boundary in buckets:
                count = sum(value <= boundary for value in values)
                lines.append(
                    f'rescate_{name}_bucket{self._labels(labels, {"le": str(boundary)})} {count}'
                )
            lines.append(f'rescate_{name}_bucket{self._labels(labels, {"le": "+Inf"})} {len(values)}')
            lines.append(f"rescate_{name}_count{self._labels(labels)} {len(values)}")
            lines.append(f"rescate_{name}_sum{self._labels(labels)} {sum(values):.6f}")
        return "\n".join(lines) + ("\n" if lines else "")


metrics = Metrics()


def install_observability(app):
    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        correlation_id = supplied if _SAFE_CORRELATION_ID.fullmatch(supplied) else str(uuid4())
        token = request_correlation_id.set(correlation_id)
        started = time.monotonic()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            safe_log(logger, logging.ERROR, "request_error", method=request.method, status=500)
            raise
        finally:
            elapsed = time.monotonic() - started
            status_class = f"{status_code // 100}xx"
            metrics.increment("http_requests_total", method=request.method, status_class=status_class)
            metrics.observe("http_request_duration_seconds", elapsed, method=request.method)
            if status_code in {401, 403, 429}:
                reason = f"http_{status_code}"
                metrics.increment("access_denials_total", reason=reason)
                if status_code == 429:
                    metrics.increment("rate_limits_total", scope="http")
                safe_log(logger, logging.WARNING, "access_denied", method=request.method, reason=reason)
            if "response" in locals():
                response.headers["X-Request-ID"] = correlation_id
            request_correlation_id.reset(token)


def _age_seconds(value, now):
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((now - value).total_seconds()))


def _database_metrics(db):
    now = datetime.now(timezone.utc)
    pending_states = {"pendiente_confirmacion"}
    review_states = {"problema_reportado", "en_revision"}
    pending_count, pending_oldest = db.query(
        func.count(models.AyudaMonetaria.id), func.min(models.AyudaMonetaria.created_at)
    ).filter(models.AyudaMonetaria.estado.in_(pending_states)).one()
    review_count, review_oldest = db.query(
        func.count(models.AyudaMonetaria.id), func.min(models.AyudaMonetaria.created_at)
    ).filter(models.AyudaMonetaria.estado.in_(review_states)).one()
    lines = [
        f"rescate_aid_pending_count {pending_count}",
        f"rescate_aid_pending_oldest_age_seconds {_age_seconds(pending_oldest, now)}",
        f"rescate_aid_review_count {review_count}",
        f"rescate_aid_review_oldest_age_seconds {_age_seconds(review_oldest, now)}",
    ]
    for state in ("pendiente", "procesando", "enviada", "fallida", "cancelada"):
        count, oldest = db.query(
            func.count(models.NotificacionCasoAyuda.id), func.min(models.NotificacionCasoAyuda.created_at)
        ).filter(models.NotificacionCasoAyuda.estado == state).one()
        lines.append(f'rescate_notification_queue_count{{state="{state}"}} {count}')
        if state == "pendiente":
            lines.append(f"rescate_notification_pending_oldest_age_seconds {_age_seconds(oldest, now)}")
    return "\n".join(lines) + "\n"


@router.get("/live")
def live():
    return {"status": "alive"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        HelpDataCipher.from_environment()
        upload_root = Path(os.getenv("HELP_PRIVATE_UPLOAD_ROOT", "uploads"))
        if not upload_root.is_dir():
            raise RuntimeError("upload_root")
        with tempfile.NamedTemporaryFile(dir=upload_root):
            pass
        provider = ResendProvider()
        if not provider.configured:
            raise RuntimeError("notification_provider")
        provider._configuration()
        db.query(models.NotificacionCasoAyuda.id).limit(1).all()
    except Exception:
        safe_log(logger, logging.ERROR, "readiness_failed", reason="dependency")
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready"}


@router.get("/metrics")
def openmetrics(db: Session = Depends(get_db)):
    body = metrics.render() + _database_metrics(db) + "# EOF\n"
    return Response(
        content=body,
        media_type="application/openmetrics-text; version=1.0.0; charset=utf-8",
    )
