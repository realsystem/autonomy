"""Python <-> Go: the same bytes, the same signatures, the same verdicts.

Builds core/go/cmd/autonomy-core and drives it. Skipped when Go is not installed; CI runs
it in a job with both toolchains (AUTONOMY_REQUIRE_GO=1 turns the skip into a failure).
"""

import json
import math
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

import autonomy_core as ac

GO_DIR = Path(__file__).resolve().parents[2] / "go"
SPEC = Path(__file__).resolve().parents[3] / "spec"
TEST_KEYS = SPEC / "fixtures" / "keys" / "test-keys.json"
SEEDS = json.loads(TEST_KEYS.read_text())["private_seed_hex"]
NOW = "2027-01-15T08:00:00Z"


@pytest.fixture(scope="module")
def go_tool(tmp_path_factory):
    if shutil.which("go") is None:
        if os.environ.get("AUTONOMY_REQUIRE_GO"):
            pytest.fail("go is required (AUTONOMY_REQUIRE_GO is set)")
        pytest.skip("go not installed")
    out = tmp_path_factory.mktemp("go") / "autonomy-core"
    subprocess.run(["go", "build", "-o", str(out), "./cmd/autonomy-core"], cwd=GO_DIR, check=True)
    return out


def run(tool, *args, stdin=""):
    return subprocess.run([str(tool), *args], input=stdin, capture_output=True, text=True)


def random_json(rng, depth=0):
    kind = rng.randrange(8 if depth < 3 else 5)
    if kind == 0:
        return rng.choice([None, True, False])
    if kind == 1:
        return rng.randint(-(2**53) + 1, 2**53 - 1)
    if kind == 2:
        x = rng.choice([rng.uniform(-1, 1), rng.uniform(-1e6, 1e6)]) * 10 ** rng.randint(-320, 300)
        return x if math.isfinite(x) else 1.5
    if kind == 3:
        alphabet = 'aZ09 _"\\\n\t\x01\x1f\x7f/éß€ \U0001f600｡'
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
    if kind == 4:
        return rng.choice([0.0, -0.0, 1e21, 1e-7, 5e-324, 1.7976931348623157e308, 0.1, 100.0])
    if kind in (5, 6):
        return {
            rng.choice(["a", "b", "é", "€", "\U0001f600", "｡", "A", "1", ""]) + str(i): random_json(
                rng, depth + 1
            )
            for i in range(rng.randint(0, 5))
        }
    return [random_json(rng, depth + 1) for _ in range(rng.randint(0, 5))]


def test_canonicalization_agrees_on_random_values(go_tool):
    rng = random.Random(8785)
    for i in range(300):
        value = random_json(rng)
        doc = json.dumps(value, ensure_ascii=False)
        got = run(go_tool, "canonicalize", stdin=doc)
        assert got.returncode == 0, got.stderr
        assert got.stdout == ac.canonicalize(value).decode(), f"case {i}: {doc}"


def test_python_envelope_verified_by_go(go_tool):
    key = ac.private_key_from_seed(bytes.fromhex(SEEDS["cell:field-station"]))
    env = ac.make_envelope(
        key,
        source="cell:field-station@field-station",
        type="health.heartbeat",
        data={"uptime": 12.5, "roles": ["hub"], "note": "€ \U0001f600"},
        seq=42,
        now=ac.parse_rfc3339(NOW),
        ttl=180,
    )
    ok = run(go_tool, "verify", "--keys", str(TEST_KEYS), "--now", NOW, stdin=json.dumps(env))
    assert ok.returncode == 0 and ok.stdout.strip() == "cell:field-station@field-station", (
        ok.stdout + ok.stderr
    )
    env["data"]["uptime"] = 13
    bad = run(go_tool, "verify", "--keys", str(TEST_KEYS), "--now", NOW, stdin=json.dumps(env))
    assert bad.returncode == 1 and "bad signature" in bad.stdout


def test_go_envelope_verified_by_python(go_tool):
    made = run(
        go_tool,
        "make",
        "--seed",
        SEEDS["svc:job-service"],
        "--source",
        "svc:job-service",
        "--type",
        "job.queued",
        "--data",
        '{"n": 1.0, "s": "x"}',
        "--seq",
        "9",
        "--ttl",
        "60",
        "--now",
        NOW,
    )
    assert made.returncode == 0, made.stderr
    env = json.loads(made.stdout)
    keys = ac.KeyRing(json.loads(TEST_KEYS.read_text())["public"])
    assert ac.verify_envelope(env, keys, now=ac.parse_rfc3339(NOW)) == "svc:job-service"
    with pytest.raises(ac.Reject, match="expired"):
        ac.verify_envelope(env, keys, now=ac.parse_rfc3339(NOW) + 61)
