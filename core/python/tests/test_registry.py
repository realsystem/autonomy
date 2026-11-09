import base64
import copy
import json
import sys
from pathlib import Path

import pytest

import autonomy_core as ac
from autonomy_core import Reject
from autonomy_core.cli import main as cli

REPO = Path(__file__).resolve().parents[3]
SPEC = REPO / "spec"
FIX = SPEC / "fixtures" / "v0"
TEST_KEYS = json.loads((SPEC / "fixtures" / "keys" / "test-keys.json").read_text())
ROOT = TEST_KEYS["public"]["owner:root"]
NOW = ac.parse_rfc3339("2026-11-08T12:30:00Z")


def load(p):
    return json.loads(Path(p).read_text())


def key(name):
    return ac.private_key_from_seed(bytes.fromhex(TEST_KEYS["private_seed_hex"][name]))


def fixture():
    return load(FIX / "registry" / "valid" / "two-site.json"), load(
        FIX / "registry-signature" / "valid" / "ops.json"
    )


def resign(doc, serial=1, signer="owner:ops", now=NOW):
    return ac.sign_registry(doc, key(signer), serial=serial, signer=signer, now=now)


@pytest.fixture(scope="module")
def conformance():
    sys.path.insert(0, str(SPEC))
    try:
        from autonomy_conformance import checks
    finally:
        sys.path.remove(str(SPEC))

    def validate(kind, obj):
        errors = [f for f in checks.validate(kind, obj, "$", checks.Context()) if f.level == "error"]
        assert not errors, "\n".join(map(str, errors))

    return validate


# --- verification ---------------------------------------------------------------


def test_spec_fixture_verifies():
    doc, sig = fixture()
    reg = ac.verify_registry(doc, sig, root=ROOT, now=NOW)
    assert reg.serial == 1 and reg.signer == "owner:ops" and reg.hash == sig["registry_hash"]
    assert set(reg.cell_keys) == {"field-station", "home-hub"}
    assert [c.id for c in reg.components_for("home-hub")] == ["svc:job-service"]
    ring = reg.keyring("field-station")
    assert "cell:home-hub" in ring and "agent:field-station" in ring and "svc:job-service" not in ring
    assert "svc:job-service@home-hub" in reg.keyring()
    revoked = ac.decode_public_key(doc["revoked"][0]["key"])
    assert reg.is_revoked(revoked) and not reg.is_revoked(ring.get("cell:home-hub"))


@pytest.mark.parametrize(
    "name,reason",
    [
        ("ops-added-without-root", "bad owners signature"),
        ("shared-key", "invalid registry"),
        ("unknown-cell", "invalid registry"),
        ("revoked-still-listed", "invalid registry"),
        ("unknown-site", "invalid registry"),
        ("double-hyphen-name", "invalid registry"),
    ],
)
def test_spec_invalid_registries(name, reason):
    doc = load(FIX / "registry" / "invalid" / f"{name}.json")["instance"]
    with pytest.raises(Reject) as why:
        ac.verify_registry(doc, resign(doc), root=ROOT, now=NOW)
    assert why.value.reason == reason


def test_tampered_signature_fixture():
    doc, _ = fixture()
    sig = load(FIX / "registry-signature" / "invalid" / "tampered-serial.json")["instance"]
    with pytest.raises(Reject, match="bad signature"):
        ac.verify_registry(doc, sig, root=ROOT, now=NOW)


def test_any_change_breaks_the_hash():
    doc, sig = fixture()
    doc["nodes"]["home-hub"]["roles"].append("agent")
    with pytest.raises(Reject, match="registry hash mismatch"):
        ac.verify_registry(doc, sig, root=ROOT, now=NOW)


def test_pinned_root():
    doc, sig = fixture()
    with pytest.raises(Reject, match="untrusted root"):
        ac.verify_registry(doc, sig, root=TEST_KEYS["public"]["owner:ops"], now=NOW)
    # a forger with their own root: valid by itself, refused by a pinned cell
    forged = copy.deepcopy(doc)
    forged["owners"] = ac.sign_owners({"ops": doc["owners"]["ops"]}, key("svc:verifier"))
    fsig = resign(forged)
    assert ac.verify_registry(forged, fsig, root=None, now=NOW).root == TEST_KEYS["public"]["svc:verifier"]
    with pytest.raises(Reject, match="untrusted root"):
        ac.verify_registry(forged, fsig, root=ROOT, now=NOW)
    # root encodings are interchangeable
    raw = base64.urlsafe_b64decode(ROOT[8:] + "=")
    ac.verify_registry(doc, sig, root=base64.b64encode(raw).decode(), now=NOW)


def test_signers():
    doc, _ = fixture()
    ac.verify_registry(doc, resign(doc, signer="owner:root"), root=ROOT, now=NOW)
    stranger = ac.sign_registry(doc, key("svc:verifier"), serial=1, signer="owner:ops", now=NOW)
    with pytest.raises(Reject, match="unknown signing key"):
        ac.verify_registry(doc, stranger, root=ROOT, now=NOW)
    ops_as_root = ac.sign_registry(doc, key("owner:ops"), serial=1, signer="owner:root", now=NOW)
    with pytest.raises(Reject, match="unknown signing key"):
        ac.verify_registry(doc, ops_as_root, root=ROOT, now=NOW)
    late = resign(doc, now=ac.parse_rfc3339("2027-12-01T00:00:00Z"))
    with pytest.raises(Reject, match="ops key expired"):
        ac.verify_registry(doc, late, root=ROOT, now=ac.parse_rfc3339("2027-12-01T00:00:00Z"))
    future = resign(doc, now=NOW + 3600)
    with pytest.raises(Reject, match="from the future"):
        ac.verify_registry(doc, future, root=ROOT, now=NOW)


def test_serial_rules():
    doc, sig = fixture()
    newer = copy.deepcopy(doc)
    newer["components"]["svc:job-service"]["version"] = "2.1.0"
    s2 = resign(newer, serial=2)
    ac.verify_registry(newer, s2, root=ROOT, previous=sig, now=NOW)
    with pytest.raises(Reject, match="rollback"):
        ac.verify_registry(doc, sig, root=ROOT, previous=s2, now=NOW)
    with pytest.raises(Reject, match="serial conflict"):
        ac.verify_registry(newer, resign(newer, serial=1), root=ROOT, previous=sig, now=NOW)
    ac.verify_registry(doc, sig, root=ROOT, previous=sig, now=NOW)  # unchanged is fine


def test_store_pins_and_keeps_the_last_valid(tmp_path):
    doc, sig = fixture()
    store = ac.RegistryStore(tmp_path)
    assert store.current() is None
    store.accept(doc, sig, now=NOW)
    assert ac.same_key(store.pinned_root(), ROOT)
    newer = copy.deepcopy(doc)
    newer["components"].pop("svc:job-service")
    s2 = resign(newer, serial=2)
    assert ac.RegistryStore(tmp_path).accept(newer, s2, now=NOW).serial == 2
    with pytest.raises(Reject, match="rollback"):
        ac.RegistryStore(tmp_path).accept(doc, sig, now=NOW)
    forged = copy.deepcopy(newer)
    forged["owners"] = ac.sign_owners({"ops": doc["owners"]["ops"]}, key("svc:verifier"))
    with pytest.raises(Reject, match="untrusted root"):
        store.accept(forged, resign(forged, serial=3), now=NOW)
    assert ac.RegistryStore(tmp_path).current(now=NOW).serial == 2
    (tmp_path / "registry.json").write_text(json.dumps({**newer, "revoked": []}))
    assert ac.RegistryStore(tmp_path).current(now=NOW) is None  # tampered copy is not trusted


def test_fingerprint_and_ops_keys():
    doc, _ = fixture()
    fp = ac.fingerprint(ROOT)
    assert len(fp.split()) == 8 and fp == ac.fingerprint(ROOT)
    assert len(list(ac.ops_keys(doc, NOW))) == 1
    assert list(ac.ops_keys(doc, ac.parse_rfc3339("2028-01-01T00:00:00Z"))) == []


# --- the owner CLI -----------------------------------------------------------------

FLEET = """\
# a fleet file as edge_server writes it
schema_version: 1
fleet:
  telegram: {operators: [100000001]}
  alert_order: [hub, station]
sites:
  home: {timezone: UTC}
  remote:
nodes:
  hub: {site: home, packaging: host, roles: [nvr], public_key: "%s"}
  station: {site: remote, packaging: ha-addon, roles: [ha], public_key: "%s", lan: 192.168.10.2}
"""


def b64(name):
    return base64.b64encode(base64.urlsafe_b64decode(TEST_KEYS["public"][name][8:] + "=")).decode()


@pytest.fixture
def owner(tmp_path, capsys):
    root, ops = tmp_path / "root.pem", tmp_path / "ops.pem"
    assert cli(["keygen", str(root)]) == 0 and cli(["keygen", str(ops)]) == 0
    assert cli(["keygen", str(root)]) == 2  # never overwritten
    capsys.readouterr()
    return root, ops


def test_cli_migrate_enrol_sign_revoke(tmp_path, owner, capsys, conformance):
    root, ops = owner
    fleet = tmp_path / "fleet.yaml"
    fleet.write_text(FLEET % (b64("cell:home-hub"), b64("cell:field-station")))
    reg = tmp_path / "registry.yaml"
    assert (
        cli(
            [
                "registry",
                "migrate",
                str(fleet),
                str(reg),
                "--root-key",
                str(root),
                "--ops-key",
                str(ops.with_suffix(".pub")),
            ]
        )
        == 0
    )
    manifest = SPEC / "examples" / "job-service" / "autonomy.manifest.json"
    assert (
        cli(
            [
                "registry",
                "add-component",
                str(reg),
                "--manifest",
                str(manifest),
                "--key",
                TEST_KEYS["public"]["svc:job-service"],
                "--cells",
                "hub",
            ]
        )
        == 0
    )
    assert cli(["registry", "sign", str(reg), "--key", str(ops)]) == 0
    doc_json, sig_json = tmp_path / "registry.json", tmp_path / "registry.sig.json"
    doc, sig = load(doc_json), load(sig_json)
    conformance("registry", doc)
    conformance("registry-signature", sig)
    assert sig["serial"] == 1 and sig["signer"] == "owner:ops"
    assert doc["nodes"]["station"]["lan"] == "192.168.10.2" and doc["fleet"]["alert_order"] == [
        "hub",
        "station",
    ]
    assert isinstance(doc["owners"]["ops"][0]["expires"], str)  # YAML timestamps stay strings
    assert doc["components"]["svc:job-service"]["manifest_hash"] == ac.sha256_tagged(
        ac.canonicalize(load(manifest))
    )
    root_pub = (tmp_path / "root.pub").read_text().strip()
    assert cli(["registry", "verify", str(doc_json), "--root", root_pub]) == 0
    assert ac.registry_hash(load(doc_json)) == sig["registry_hash"]

    capsys.readouterr()
    assert cli(["registry", "revoke", str(reg), "svc:job-service", "--reason", "laptop stolen"]) == 0
    assert cli(["registry", "sign", str(reg), "--key", str(ops)]) == 0
    s2 = load(sig_json)
    assert s2["serial"] == 2
    assert (
        cli(
            [
                "registry",
                "verify",
                str(doc_json),
                "--root",
                root_pub,
                "--previous",
                str(tmp_path / "prev.sig.json"),
            ]
        )
        == 2
    )  # missing file
    (tmp_path / "prev.sig.json").write_text(json.dumps(sig))
    assert (
        cli(
            [
                "registry",
                "verify",
                str(doc_json),
                "--root",
                root_pub,
                "--previous",
                str(tmp_path / "prev.sig.json"),
            ]
        )
        == 0
    )
    assert load(doc_json)["revoked"][0]["id"] == "svc:job-service"
    # an older serial is refused at signing time
    assert (
        cli(["registry", "sign", str(reg), "--key", str(ops), "--serial", "1", "--previous", str(sig_json)])
        == 2
    )
    assert "rollback" in capsys.readouterr().err
    # a stranger's root fails verification against the pinned root
    assert cli(["registry", "verify", str(doc_json), "--root", TEST_KEYS["public"]["owner:root"]]) == 1


def test_cli_delegate_needs_root(tmp_path, owner, capsys):
    root, ops = owner
    reg = tmp_path / "r.yaml"
    assert (
        cli(
            ["registry", "init", str(reg), "--root-key", str(root), "--ops-key", str(ops.with_suffix(".pub"))]
        )
        == 0
    )
    assert (
        cli(
            [
                "registry",
                "add-node",
                str(reg),
                "hub",
                "--site",
                "home",
                "--key",
                TEST_KEYS["public"]["cell:home-hub"],
            ]
        )
        == 0
    )
    other = tmp_path / "other.pem"
    cli(["keygen", str(other)])
    assert (
        cli(
            [
                "registry",
                "delegate",
                str(reg),
                "--root-key",
                str(ops),
                "--add",
                str(other.with_suffix(".pub")),
            ]
        )
        == 2
    )
    assert (
        cli(
            [
                "registry",
                "delegate",
                str(reg),
                "--root-key",
                str(root),
                "--add",
                str(other.with_suffix(".pub")),
                "--remove",
                str(ops.with_suffix(".pub")),
            ]
        )
        == 0
    )
    assert cli(["registry", "sign", str(reg), "--key", str(other)]) == 0
    capsys.readouterr()
    assert cli(["registry", "sign", str(reg), "--key", str(ops)]) == 2  # no longer delegated
    assert "unknown signing key" in capsys.readouterr().err
    assert cli(["registry", "show", str(reg)]) == 0


def test_cli_refuses_bad_input(tmp_path, owner, capsys):
    root, ops = owner
    fleet = tmp_path / "fleet.yaml"
    fleet.write_text(FLEET.replace('public_key: "%s", lan', "lan") % b64("cell:home-hub"))
    assert cli(["registry", "migrate", str(fleet), str(tmp_path / "r.yaml"), "--root-key", str(root)]) == 2
    assert "nodes without public_key: station" in capsys.readouterr().err
    reg = tmp_path / "r.yaml"
    cli(["registry", "init", str(reg), "--root-key", str(root)])
    assert (
        cli(
            [
                "registry",
                "add-node",
                str(reg),
                "a--b",
                "--site",
                "x",
                "--key",
                TEST_KEYS["public"]["cell:home-hub"],
            ]
        )
        == 2
    )
    assert cli(["registry", "add-node", str(reg), "hub", "--site", "x", "--key", "nonsense"]) == 2
    cli(
        [
            "registry",
            "add-node",
            str(reg),
            "hub",
            "--site",
            "x",
            "--key",
            TEST_KEYS["public"]["cell:home-hub"],
        ]
    )
    assert (
        cli(
            [
                "registry",
                "add-node",
                str(reg),
                "hub2",
                "--site",
                "x",
                "--key",
                TEST_KEYS["public"]["cell:home-hub"],
            ]
        )
        == 2
    )
    assert "key already used" in capsys.readouterr().err
