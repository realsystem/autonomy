"""Observability conventions (C17): JSON logs, traces, redaction.

- ``configure_logging``: one JSON object per line (spec log-record): ts, level,
  component_id, instance_id, msg, trace_id, span_id, correlationid and any ``extra``
  fields; every record passes the redactor first.
- ``Redactor``: masks known secret values (registered when ``secret://`` references are
  resolved) and secret-shaped text (tokens, private keys, credentials in URLs,
  ``password=...``), and fields whose names say they are secret.
- ``Tracer``: spans as OTLP/JSON lines in a local file (works offline; a hub collector
  can ingest them later). Context travels as a W3C ``traceparent``.
- ``reconstruct``: an action story from audit records alone, and its spans (IT-71).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

MASK = "[REDACTED]"
SECRET_FIELD = re.compile(
    r"(?i)(pass(word)?|secret|token|api[_-]?key|private[_-]?key|credential|authorization)"
)
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private key block",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"),
    ),
    ("telegram bot token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b")),
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("aws access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack token", re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}\b")),
    ("openai or anthropic key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{24,}\b")),
    ("age secret key", re.compile(r"\bAGE-SECRET-KEY-1[0-9A-Z]{58}\b")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}=*")),
    ("credentials in url", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{3,}@")),
    (
        "password assignment",
        # quoted literal values only: code and secret:// references are not secrets
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api_key|apikey|token)\w*['\"]?\s*[=:]\s*"
            r"['\"](?!secret://|\$|<|\[REDACTED\])[^'\"\s]{8,}['\"]"
        ),
    ),
]


class Redactor:
    """Thread-safe; one per process is enough (``REDACTOR``)."""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def register(self, value: str) -> None:
        """A secret value that must never appear in output (min. 6 characters)."""
        if isinstance(value, str) and len(value) >= 6:
            with self._lock:
                self._values.add(value)

    def text(self, s: str) -> str:
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for v in values:
            if v in s:
                s = s.replace(v, MASK)
        for _, rx in PATTERNS:
            s = rx.sub(MASK, s)
        return s

    def value(self, v: Any, key: str = "") -> Any:
        if key and SECRET_FIELD.search(key) and v not in (None, "", False):
            return MASK
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, Mapping):
            return {k: self.value(x, str(k)) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [self.value(x) for x in v]
        return v


REDACTOR = Redactor()


# --- trace context -----------------------------------------------------------------------

_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("autonomy_traceparent", default=None)
_correlation: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "autonomy_correlationid", default=None
)


def parse_traceparent(tp: str | None) -> tuple[str, str] | None:
    """(trace_id, span_id) of a valid version-00 traceparent."""
    if not isinstance(tp, str):
        return None
    m = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}", tp)
    if not m or m.group(1) == "0" * 32 or m.group(2) == "0" * 16:
        return None
    return m.group(1), m.group(2)


def current_traceparent() -> str | None:
    return _current.get()


@contextlib.contextmanager
def correlation(correlationid: str | None) -> Iterator[None]:
    token = _correlation.set(correlationid)
    try:
        yield
    finally:
        _correlation.reset(token)


class Span:
    def __init__(self, tracer: Tracer, name: str, trace_id: str, parent: str | None, attrs: dict[str, Any]):
        self.tracer, self.name, self.trace_id, self.parent = tracer, name, trace_id, parent
        self.span_id = secrets.token_hex(8)
        self.attrs = dict(attrs)
        self.start = time.time_ns()
        self.status = "ok"
        self.message = ""

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-01"

    def set(self, **attrs: Any) -> None:
        self.attrs.update(attrs)

    def error(self, message: str) -> None:
        self.status, self.message = "error", message


class Tracer:
    """Writes finished spans as OTLP/JSON ``resourceSpans`` lines to ``path``."""

    def __init__(self, path: Path | str | None, *, service: str, instance: str | None = None):
        self.path = Path(path) if path is not None else None
        self.service = service
        self.instance = instance or service
        self.lock = threading.Lock()
        self.finished: list[dict[str, Any]] = []  # also kept in memory (last 1000)

    @contextlib.contextmanager
    def span(self, name: str, traceparent: str | None = None, **attrs: Any) -> Iterator[Span]:
        """A child of ``traceparent`` (or of the current span, or a new trace)."""
        ctx = parse_traceparent(traceparent or _current.get())
        trace_id, parent = ctx if ctx else (secrets.token_hex(16), None)
        sp = Span(self, name, trace_id, parent, attrs)
        token = _current.set(sp.traceparent)
        try:
            yield sp
        except BaseException as exc:
            sp.error(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            _current.reset(token)
            self._export(sp)

    def _export(self, sp: Span) -> None:
        attrs = {k: v for k, v in REDACTOR.value(sp.attrs).items() if v is not None}
        corr = _correlation.get()
        if corr:
            attrs.setdefault("autonomy.correlationid", corr)
        span = {
            "traceId": sp.trace_id,
            "spanId": sp.span_id,
            "name": sp.name,
            "kind": 1,
            "startTimeUnixNano": str(sp.start),
            "endTimeUnixNano": str(time.time_ns()),
            "attributes": [{"key": k, "value": _otlp_value(v)} for k, v in sorted(attrs.items())],
            "status": {
                "code": 1 if sp.status == "ok" else 2,
                **({"message": sp.message[:300]} if sp.message else {}),
            },
        }
        if sp.parent:
            span["parentSpanId"] = sp.parent
        line = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": self.service}},
                            {"key": "service.instance.id", "value": {"stringValue": self.instance}},
                        ]
                    },
                    "scopeSpans": [{"scope": {"name": "autonomy_core"}, "spans": [span]}],
                }
            ]
        }
        with self.lock:
            self.finished = (self.finished + [span])[-1000:]
            if self.path is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")
                except OSError:
                    pass


def _otlp_value(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def read_spans(path: Path | str) -> list[dict[str, Any]]:
    """All spans in an OTLP/JSON lines file."""
    out: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        for rs in doc.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                out.extend(ss.get("spans", []))
    return out


def reconstruct(
    correlationid: str, audit: Iterable[Mapping[str, Any]], spans: Iterable[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """An action story (IT-71): its audit records in order, the ids they reference, and
    the spans of the trace(s) those records name."""
    records = [dict(r) for r in audit if r.get("correlationid") == correlationid]
    records.sort(key=lambda r: (str(r.get("time")), int(r.get("seq", 0))))
    refs: dict[str, str] = {}
    for r in records:
        refs.update({k: str(v) for k, v in dict(r.get("refs", {})).items()})
    trace_ids = sorted({str(r["trace_id"]) for r in records if r.get("trace_id")})
    story_spans = sorted(
        (dict(s) for s in spans if s.get("traceId") in trace_ids), key=lambda s: int(s["startTimeUnixNano"])
    )
    return {
        "correlationid": correlationid,
        "steps": [f"{r['time']} {r['kind']} by {r['actor']}: {r['summary']}" for r in records],
        "kinds": [r["kind"] for r in records],
        "refs": refs,
        "trace_ids": trace_ids,
        "spans": [s["name"] for s in story_spans],
    }


# --- JSON logs -----------------------------------------------------------------------------

_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """spec log-record; secrets redacted; trace and correlation ids from the context."""

    def __init__(self, component_id: str, instance_id: str | None = None, redactor: Redactor = REDACTOR):
        super().__init__()
        self.component_id, self.instance_id, self.redactor = component_id, instance_id, redactor

    def format(self, record: logging.LogRecord) -> str:
        level = {"WARNING": "warn", "CRITICAL": "error"}.get(record.levelname, record.levelname.lower())
        out: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": level if level in ("debug", "info", "warn", "error") else "info",
            "component_id": self.component_id,
        }
        if self.instance_id:
            out["instance_id"] = self.instance_id
        out["msg"] = self.redactor.text(record.getMessage())
        ctx = parse_traceparent(_current.get())
        if ctx:
            out["trace_id"], out["span_id"] = ctx
        corr = _correlation.get()
        if corr:
            out["correlationid"] = corr
        out["logger"] = record.name
        for k, v in vars(record).items():
            if k not in _STANDARD and not k.startswith("_"):
                out[k] = self.redactor.value(v, k)
        if record.exc_info:
            out["exception"] = self.redactor.text(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False, default=str)


class RedactingFilter(logging.Filter):
    """For handlers that keep their own (text) format: redact the message in place."""

    def __init__(self, redactor: Redactor = REDACTOR):
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg, record.args = self.redactor.text(msg), None
        return True


def configure_logging(
    component_id: str,
    instance_id: str | None = None,
    *,
    level: int | str = logging.INFO,
    stream: Any = None,
    json_format: bool | None = None,
) -> logging.Handler:
    """Root logger to stderr as JSON lines (``AUTONOMY_LOG_FORMAT=text`` keeps text)."""
    if json_format is None:
        json_format = os.environ.get("AUTONOMY_LOG_FORMAT", "json") != "text"
    handler = logging.StreamHandler(stream)
    if json_format:
        handler.setFormatter(JsonFormatter(component_id, instance_id))
    else:
        handler.addFilter(RedactingFilter())
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    return handler
