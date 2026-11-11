"""C17/C18 (M9): JSON logs, traces, redaction, secret:// references, the secret scanner;
IT-71 (an action story from audit records alone and as one trace), IT-81 (no secrets).

Fake secrets are assembled at run time so this file itself passes the scanner.
"""

import io
import json
import logging
import os
import stat
import sys
from pathlib import Path

import pytest

import autonomy_core as ac
from autonomy_core import secrets as sec
from autonomy_core.cli import main as cli
from autonomy_core.gateway import ActionGateway, AdapterResult, MockAdapter
from autonomy_core.telemetry import Redactor, Tracer, read_spans, reconstruct

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pdp_gateway import (  # noqa: E402
    PDP_KEYS,
    SPEC,
    TK,
    Clock,
    key,
    pdp,
    permit_bundle,
    proposal,
    request,
)

REPO = Path(__file__).resolve().parents[3]
TG = "123456789:" + "AA" + "x" * 33
GH = "gh" + "p_" + "A1b2C3d4" * 5
PEM = "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----"


def conforms(kind, obj):
    sys.path.insert(0, str(SPEC))
    from autonomy_conformance import checks

    return [str(f) for f in checks.validate(kind, obj, "$", checks.Context()) if f.level == "error"]


# --- redaction ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"token {TG} ok",
        f"push with {GH}",
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "db at postgres://admin:" + "hunter2x@db.local/x",
        'password = "' + 'correct-horse-battery"',
        "Authorization: Bearer " + "abc.def.ghijklmnopqrstuvwxyz",
        PEM,
        "sk-ant-" + "a" * 30,
    ],
)
def test_redaction_patterns(text):
    out = Redactor().text(text)
    assert ac.telemetry.MASK in out
    assert not sec.scan_text(out), out


def test_registered_values_and_fields():
    r = Redactor()
    r.register("s3cr3t-value-123")
    r.register("abc")  # too short to register safely
    assert r.text("x s3cr3t-value-123 y") == "x [REDACTED] y" and r.text("abc") == "abc"
    v = r.value(
        {
            "ha_token": "anything",
            "user": "bob",
            "nested": {"api_key": 5, "note": "s3cr3t-value-123"},
            "list": ["s3cr3t-value-123"],
        }
    )
    assert v == {
        "ha_token": "[REDACTED]",
        "user": "bob",
        "nested": {"api_key": "[REDACTED]", "note": "[REDACTED]"},
        "list": ["[REDACTED]"],
    }


# --- logs ------------------------------------------------------------------------------------


def test_json_logs_conform_and_redact(tmp_path):
    stream = io.StringIO()
    handler = ac.configure_logging(
        "svc:action-gateway", "svc:action-gateway@field-station", stream=stream, json_format=True
    )
    tracer = Tracer(None, service="svc:action-gateway")
    log = logging.getLogger("test.m9")
    ac.REDACTOR.register("very-secret-value-42")
    try:
        with ac.correlation("corr_story1"), tracer.span("work") as sp:
            log.info("using very-secret-value-42 and %s", TG, extra={"action_id": "act_1", "password": "x"})
        try:
            leaked = 'password="' + 'abcdefghij"'
            raise ValueError(f"bad {leaked}")
        except ValueError:
            log.exception("failed")
    finally:
        logging.getLogger().removeHandler(handler)
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    first, second = lines
    assert first["msg"] == "using [REDACTED] and [REDACTED]" and first["password"] == "[REDACTED]"
    assert (
        first["trace_id"] == sp.trace_id
        and first["span_id"] == sp.span_id
        and first["correlationid"] == "corr_story1"
    )
    assert (
        first["action_id"] == "act_1"
        and second["level"] == "error"
        and "abcdefghij" not in second["exception"]
    )
    for line in lines:
        assert conforms("log-record", line) == []
    assert not sec.scan_text(stream.getvalue())


def test_text_logs_are_redacted_too():
    stream = io.StringIO()
    handler = ac.configure_logging("svc:x", stream=stream, json_format=False)
    try:
        logging.getLogger("t").warning("token %s", TG)
    finally:
        logging.getLogger().removeHandler(handler)
    assert TG not in stream.getvalue() and "[REDACTED]" in stream.getvalue()


# --- traces ----------------------------------------------------------------------------------


def test_spans_are_otlp_json_with_parents(tmp_path):
    tracer = Tracer(tmp_path / "traces.jsonl", service="svc:x", instance="svc:x@c")
    upstream = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    with tracer.span("outer", upstream, token="t0ps3cret-value") as outer:
        with tracer.span("inner", step=2):
            pass
        with pytest.raises(RuntimeError), tracer.span("broken"):
            raise RuntimeError("boom")
    spans = read_spans(tmp_path / "traces.jsonl")
    by = {s["name"]: s for s in spans}
    assert {s["traceId"] for s in spans} == {"a" * 32}
    assert by["outer"]["parentSpanId"] == "b" * 16 and by["inner"]["parentSpanId"] == outer.span_id
    assert by["broken"]["status"]["code"] == 2 and "boom" in by["broken"]["status"]["message"]
    assert {"key": "token", "value": {"stringValue": "[REDACTED]"}} in by["outer"]["attributes"]
    line = json.loads((tmp_path / "traces.jsonl").read_text().splitlines()[0])
    assert line["resourceSpans"][0]["resource"]["attributes"][0]["value"]["stringValue"] == "svc:x"
    assert ac.current_traceparent() is None
    with tracer.span("fresh", "garbage") as sp:
        assert len(sp.trace_id) == 32 and sp.parent is None


def test_it71_one_action_story(tmp_path):
    clock = Clock()
    tracer = Tracer(tmp_path / "traces.jsonl", service="cell:field-station")
    audit = ac.AuditLog(
        tmp_path / "audit.jsonl",
        cell="field-station",
        actor="svc:action-gateway@field-station",
        key=key("svc:action-gateway"),
    )

    class Slow(MockAdapter):
        def execute(self, action_id, action):
            super().execute(action_id, action)
            return AdapterResult("started")

    gw = ActionGateway(
        executor="svc:action-gateway@field-station",
        key=key("svc:action-gateway"),
        state_dir=tmp_path / "gw",
        pdp_keys=PDP_KEYS,
        registry=None,
        adapters=[Slow("adapter:ha@field-station", {"ha.helper.set"})],
        audit=audit,
        clock=clock,
        tracer=tracer,
    )
    decider = pdp(permit_bundle(), clock)
    decider.tracer = tracer
    corr = "corr_story_it71"
    envelope_tp = "00-" + "c" * 32 + "-" + "d" * 16 + "-01"  # from the action.proposed envelope
    with ac.correlation(corr), tracer.span("evaluator.evaluate", envelope_tp):
        p = proposal(key_suffix="it71")
        d = decider.decide(request(p))
        audit.append(
            "action.decided",
            summary="permit",
            correlationid=corr,
            refs={"proposal": p["proposal_id"], "decision": d["decision_id"]},
        )
        tp = ac.current_traceparent()
    r = {
        "action_id": ac.prefixed_id("act"),
        "proposal": p,
        "decision": d,
        "idempotency_key": p["idempotency_key"],
    }
    assert gw.execute(r, traceparent=tp, correlationid=corr)["status"] == "started"
    # later, the outcome arrives (another thread, no context): it joins the same trace
    gw.complete(r["action_id"], AdapterResult("succeeded"))
    records = list(audit.records())
    story = reconstruct(corr, records, read_spans(tmp_path / "traces.jsonl"))
    assert story["kinds"] == ["action.decided", "action.started", "action.succeeded"]
    assert story["refs"]["proposal"] == p["proposal_id"] and story["refs"]["action"] == r["action_id"]
    assert story["trace_ids"] == ["c" * 32]  # one trace
    assert story["spans"][:2] == ["evaluator.evaluate", "pdp.decide"] and {
        "gateway.execute",
        "adapter.execute",
        "gateway.complete",
    } <= set(story["spans"])
    assert ac.verify_chain(records, ac.KeyRing(TK["public"])) == []
    for rec in records:
        assert conforms("audit-record", rec) == []
    # and from the command line
    assert (
        cli(
            [
                "trace",
                corr,
                "--audit",
                str(tmp_path / "audit.jsonl"),
                "--traces",
                str(tmp_path / "traces.jsonl"),
            ]
        )
        == 0
    )
    assert cli(["trace", "corr_none", "--audit", str(tmp_path / "audit.jsonl")]) == 1


# --- secret:// ----------------------------------------------------------------------------------


def test_env_file_store_refs(tmp_path, monkeypatch):
    r = Redactor()
    assert (
        sec.resolve("secret://env/HA_TOKEN", env={"HA_TOKEN": "env-secret-1"}, redactor=r) == "env-secret-1"
    )
    f = tmp_path / "token"
    f.write_text("file-secret-2\n")
    f.chmod(0o600)
    assert sec.resolve(f"secret://file{f}", redactor=r) == "file-secret-2"
    f.chmod(0o644)
    with pytest.raises(ac.Reject, match="readable by others"):
        sec.resolve(f"secret://file{f}", redactor=r)
    sec.register_provider("edge", lambda name: {"ha_token": "store-secret-3"}[name])
    assert sec.resolve("secret://store/ha_token", redactor=r) == "store-secret-3"
    with pytest.raises(ac.Reject, match="not found"):
        sec.resolve("secret://store/nope", redactor=r)
    with pytest.raises(ac.Reject, match="not set"):
        sec.resolve("secret://env/MISSING", env={}, redactor=r)
    with pytest.raises(ac.Reject, match="malformed"):
        sec.resolve("secret://vault/x", redactor=r)
    assert r.text("env-secret-1 file-secret-2 store-secret-3") == "[REDACTED] [REDACTED] [REDACTED]"


def test_resolve_config_and_sops_age(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sops").write_text('#!/bin/sh\necho \'{"ha": {"token": "sops-secret-4"}}\'\n')
    (bin_dir / "age").write_text("#!/bin/sh\necho age-secret-5\n")
    for b in ("sops", "age"):
        (bin_dir / b).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    r = Redactor()
    cfg = {
        "ha": {"url": "http://ha:8123", "token": "secret://sops/secrets.yaml#ha.token"},
        "keys": ["secret://age/k.age"],
        "n": 3,
    }
    out = sec.resolve_config(cfg, redactor=r, env={"AGE_IDENTITY": str(tmp_path / "id")})
    assert out == {
        "ha": {"url": "http://ha:8123", "token": "sops-secret-4"},
        "keys": ["age-secret-5"],
        "n": 3,
    }
    assert cfg["ha"]["token"].startswith("secret://")  # the original is untouched
    assert r.text("sops-secret-4") == "[REDACTED]"
    with pytest.raises(ac.Reject, match="secret unavailable"):
        sec.resolve("secret://sops/x.yaml#ha.missing", redactor=r)


# --- scanner (IT-81) ----------------------------------------------------------------------------------


def test_scanner_rules_allow_and_skip(tmp_path, capsys):
    (tmp_path / "config.env").write_text(f"TELEGRAM={TG}\n")
    (tmp_path / "ok.txt").write_text(f"fixture {GH}  # secret-scan: allow\n")
    (tmp_path / "id_ed25519").write_text(PEM + "\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "objects").write_text(TG)
    (tmp_path / "pic.png").write_bytes(TG.encode())
    found = sec.scan([tmp_path], root=tmp_path)
    assert sorted((f.path, f.rule) for f in found) == [
        ("config.env", "telegram bot token"),
        ("id_ed25519", "private key block"),
    ]
    allow = tmp_path / "allow"
    allow.write_text("id_ed25519 private key block  # test key\nconfig.env\n")
    assert sec.scan([tmp_path], root=tmp_path, allowlist=allow) == []
    assert cli(["secrets", "scan", str(tmp_path)]) == 1
    assert "telegram bot token" in capsys.readouterr().out
    assert cli(["secrets", "scan", str(tmp_path), "--allowlist", str(allow)]) == 0


def test_it81_this_repository_is_clean(monkeypatch):
    monkeypatch.chdir(REPO)
    findings = sec.scan([REPO], allowlist=REPO / ".secrets-allowlist", root=REPO)
    assert findings == [], "\n".join(map(str, findings))


def test_produced_artifacts_are_clean(tmp_path):
    """Logs, envelopes, audit and traces made while handling secrets contain none."""
    r = ac.REDACTOR
    token = "store-" + "secret-" + "abcdef123"
    sec.register_provider("t", lambda name: token)
    sec.resolve("secret://store/x")
    tracer = Tracer(tmp_path / "traces.jsonl", service="svc:x")
    stream = io.StringIO()
    handler = ac.configure_logging("svc:x", stream=stream, json_format=True)
    try:
        with tracer.span("call", header=f"Bearer {token}"):
            logging.getLogger("x").info("calling with %s", token)
    finally:
        logging.getLogger().removeHandler(handler)
    (tmp_path / "log.jsonl").write_text(stream.getvalue())
    for f in tmp_path.iterdir():
        assert token not in f.read_text(), f
    assert r.text(token) == "[REDACTED]"
    assert stat.S_IMODE(0o600) == 0o600
