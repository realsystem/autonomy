import copy
import json
import shutil
from pathlib import Path

import pytest

from autonomy_conformance import checks, cli, compat, crypto
from autonomy_conformance.jcs import CanonicalizationError, canonicalize
from autonomy_conformance.schemas import CONTRACTS, schema_dir, schema_names

SPEC = Path(__file__).resolve().parent.parent
VECTORS = SPEC / "vectors"
KEYS = json.loads((SPEC / "fixtures" / "keys" / "test-keys.json").read_text())["public"]


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# --- schemas ---------------------------------------------------------------

def test_every_contract_schema_exists():
    names = set(schema_names())
    for contract, schemas in CONTRACTS.items():
        assert set(schemas) <= names, contract
    assert "manifest" in names and "common" in names


@pytest.mark.parametrize("path", sorted(schema_dir().glob("*.schema.json")), ids=lambda p: p.name)
def test_schema_is_valid_draft_2020_12(path):
    from jsonschema import Draft202012Validator
    doc = load(path)
    assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(doc)


def test_ids_match_file_names():
    for p in schema_dir().glob("*.schema.json"):
        assert load(p)["$id"].endswith("/v0/" + p.name)


def test_every_schema_has_fixtures():
    fixtures = {p.name for p in (SPEC / "fixtures" / "v0").iterdir()}
    assert set(schema_names()) - {"common"} <= fixtures


def test_selftest_passes(capsys):
    assert cli.main(["selftest"]) == 0
    assert "PASS" in capsys.readouterr().out


# --- canonical JSON, hashes, signatures -------------------------------------

@pytest.mark.parametrize("path", sorted((VECTORS / "jcs").glob("*.json")), ids=lambda p: p.stem)
def test_jcs_vectors(path):
    v = load(path)
    assert canonicalize(v["input"]).decode() == v["canonical"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 2**53, "\ud800", {1: "x"}])
def test_jcs_rejects(bad):
    with pytest.raises(CanonicalizationError):
        canonicalize(bad)


def test_params_hash_vector():
    v = load(VECTORS / "hash" / "params-hash.json")
    assert canonicalize(v["action"]).decode() == v["canonical"]
    assert crypto.params_hash(v["action"]) == v["params_hash"]


def test_audit_hash_vector():
    v = load(VECTORS / "hash" / "audit-hash.json")
    assert crypto.audit_hash(v["record"]) == v["hash"]
    assert v["hash_input"].startswith(crypto.GENESIS_HASH + "{")


def test_signature_vector():
    v = load(VECTORS / "signature" / "envelope.json")
    assert crypto.signing_input(v["envelope"], "autonomysig").decode() == v["signing_input"]
    assert crypto.verify(v["envelope"], "envelope", {v["signer"]: v["public_key"]}) is None
    tampered = copy.deepcopy(v["envelope"])
    tampered["seq"] += 1
    assert "does not verify" in crypto.verify(tampered, "envelope", {v["signer"]: v["public_key"]})


def test_key_lookup_falls_back_to_component():
    assert crypto.lookup_key({"svc:pdp": "k"}, "svc:pdp@field-station") == "k"
    assert crypto.lookup_key({"svc:pdp@field-station": "x", "svc:pdp": "k"}, "svc:pdp@field-station") == "x"


# --- version ranges ----------------------------------------------------------

@pytest.mark.parametrize("rng,version,ok", [
    ("^0.1", "0.1.0", True), ("^0.1", "0.4.2", True), ("^0.1", "1.0.0", False), ("^0.2", "0.1.0", False),
    ("~0.1", "0.1.9", True), ("~0.1", "0.2.0", False), ("0.1", "0.1.3", True), ("0.1.0", "0.1.1", False),
    ("^1", "1.9.0", True), ("^1.2", "1.1.0", False), ("^1.2.3", "1.2.3-rc.1", True),
])
def test_range_admits(rng, version, ok):
    assert checks.range_admits(rng, version) is ok


# --- check command on the example components (C07, C10) --------------------

@pytest.mark.parametrize("example,level", [("job-service", "L2"), ("cell-runtime", "L0")])
def test_examples_pass(example, level, capsys):
    d = SPEC / "examples" / example
    rc = cli.main(["check", "--manifest", str(d / "autonomy.manifest.json"), "--samples", str(d / "samples"),
                   "--level", level, "--keys", str(SPEC / "fixtures" / "keys" / "test-keys.json"), "--strict"])
    out = capsys.readouterr().out
    assert rc == 0, out


def test_check_reports_level_gap(capsys):
    d = SPEC / "examples" / "cell-runtime"
    assert cli.main(["check", "--manifest", str(d / "autonomy.manifest.json"), "--level", "L1"]) == 1
    assert "requires contract action-lifecycle" in capsys.readouterr().out


def test_check_rejects_foreign_source_and_duplicate_seq(tmp_path, capsys):
    d = SPEC / "examples" / "job-service"
    samples = tmp_path / "samples"
    shutil.copytree(d / "samples", samples)
    shutil.copy(SPEC / "fixtures" / "v0" / "envelope" / "valid" / "observation.json", samples / "foreign.json")
    shutil.copy(samples / "job-queued.json", samples / "job-queued-again.json")
    assert cli.main(["check", "--manifest", str(d / "autonomy.manifest.json"), "--samples", str(samples)]) == 1
    out = capsys.readouterr().out
    assert "is not the manifest's component" in out
    assert "already used" in out


def test_check_unknown_sample_kind(tmp_path, capsys):
    (tmp_path / "mystery.json").write_text("{}")
    d = SPEC / "examples" / "job-service"
    assert cli.main(["check", "--manifest", str(d / "autonomy.manifest.json"), "--samples", str(tmp_path)]) == 1
    assert "cannot tell which schema" in capsys.readouterr().out


def test_audit_chain_continuity(tmp_path, capsys):
    lines = (SPEC / "fixtures" / "v0" / "audit-record" / "valid" / "chain.jsonl").read_text().splitlines()
    broken = tmp_path / "audit-record" / "chain.jsonl"
    broken.parent.mkdir()
    broken.write_text("\n".join([lines[0], lines[2]]) + "\n")  # record 1 missing
    assert cli.main(["validate", "audit-record", str(broken)]) == 1
    out = capsys.readouterr().out
    assert "expected seq 0 + 1" in out and "previous record's hash" in out


def test_validate_without_keys_skips_signatures(tmp_path, capsys):
    e = load(SPEC / "fixtures" / "v0" / "envelope" / "valid" / "observation.json")
    e["data"]["value"] = 1.0
    f = tmp_path / "e.json"
    f.write_text(json.dumps(e))
    assert cli.main(["validate", "envelope", str(f)]) == 0
    assert cli.main(["validate", "envelope", str(f), "--keys", str(SPEC / "fixtures" / "keys" / "test-keys.json")]) == 1
    capsys.readouterr()


# --- compat checker ----------------------------------------------------------

@pytest.fixture
def schemas_copy(tmp_path):
    new = tmp_path / "new"
    shutil.copytree(schema_dir(), new)
    return new


def edit(dir_, name, fn):
    p = dir_ / f"{name}.schema.json"
    doc = load(p)
    fn(doc)
    p.write_text(json.dumps(doc))


def breaking(new):
    return [c for c in compat.compare_dirs(schema_dir(), new) if c.breaking]


def test_compat_identical(schemas_copy):
    assert compat.compare_dirs(schema_dir(), schemas_copy) == []


def test_compat_allows_optional_field_and_new_schema(schemas_copy):
    edit(schemas_copy, "job", lambda d: d["properties"].update(labels={"type": "object"}))
    edit(schemas_copy, "envelope", lambda d: d.update(description="reworded"))
    (schemas_copy / "observation.schema.json").write_text(json.dumps({"$id": "x", "type": "object"}))
    assert breaking(schemas_copy) == []


@pytest.mark.parametrize("name,mutate,needle", [
    ("job", lambda d: d["properties"].pop("priority"), "removed"),
    ("job", lambda d: d["required"].append("priority"), "newly required"),
    ("job", lambda d: d["required"].remove("traceparent"), "no longer required"),
    ("decision", lambda d: d["properties"]["effect"]["enum"].append("defer"), "values added"),
    ("decision", lambda d: d["properties"]["effect"]["enum"].remove("deny"), "values removed"),
    ("job", lambda d: d["properties"]["priority"].update(maximum=10), "tightened"),
    ("job", lambda d: d["properties"]["priority"].update(type="number"), "type changed"),
    ("job", lambda d: d.update(additionalProperties=False), "tightened"),
    ("common", lambda d: d["$defs"]["name"].update(pattern="^[a-z]+$"), "pattern changed"),
    ("common", lambda d: d["$defs"].pop("job_id"), "removed"),
    ("ready", lambda d: d.update(allOf=[{"required": ["component_id"]}]), "added"),
])
def test_compat_flags_breaking(schemas_copy, name, mutate, needle):
    edit(schemas_copy, name, mutate)
    found = breaking(schemas_copy)
    assert found and any(needle in c.message for c in found), [str(c) for c in found]


def test_compat_removed_schema(schemas_copy):
    (schemas_copy / "job.schema.json").unlink()
    assert any("schema removed" in c.message for c in breaking(schemas_copy))


def test_compat_loosening_is_not_breaking(schemas_copy):
    edit(schemas_copy, "job", lambda d: d["properties"]["priority"].update(maximum=1000))
    edit(schemas_copy, "grant", lambda d: d.update(additionalProperties=True))
    assert breaking(schemas_copy) == []


def test_compat_open_enum(schemas_copy):
    def mark_and_add(d):
        d["properties"]["level"]["enum"].append("trace")
    edit(schemas_copy, "log-record", mark_and_add)
    assert breaking(schemas_copy)
    old = schemas_copy.parent / "old"
    shutil.copytree(schema_dir(), old)
    edit(old, "log-record", lambda d: d["properties"]["level"].update({"x-autonomy-open-enum": True}))
    edit(schemas_copy, "log-record", lambda d: d["properties"]["level"].update({"x-autonomy-open-enum": True}))
    assert [c for c in compat.compare_dirs(old, schemas_copy) if c.breaking] == []
