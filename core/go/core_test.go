package autonomycore

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

var spec = filepath.Join("..", "..", "spec")

func readObject(t *testing.T, path string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	obj, err := DecodeObject(data)
	if err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	return obj
}

func glob(t *testing.T, pattern string) []string {
	t.Helper()
	files, err := filepath.Glob(filepath.Join(spec, pattern))
	if err != nil || len(files) == 0 {
		t.Fatalf("no files for %s", pattern)
	}
	sort.Strings(files)
	return files
}

func testKeys(t *testing.T) (*KeyRing, map[string]any) {
	t.Helper()
	doc := readObject(t, filepath.Join(spec, "fixtures", "keys", "test-keys.json"))
	pub := map[string]string{}
	for k, v := range doc["public"].(map[string]any) {
		pub[k] = v.(string)
	}
	ring, err := NewKeyRing(pub)
	if err != nil {
		t.Fatal(err)
	}
	return ring, doc["private_seed_hex"].(map[string]any)
}

func keyFor(t *testing.T, seeds map[string]any, name string) ed25519.PrivateKey {
	t.Helper()
	seed, err := hex.DecodeString(seeds[name].(string))
	if err != nil {
		t.Fatal(err)
	}
	k, err := PrivateKeyFromSeed(seed)
	if err != nil {
		t.Fatal(err)
	}
	return k
}

func reason(err error) string {
	var r *Reject
	if errors.As(err, &r) {
		return r.Reason
	}
	if err == nil {
		return ""
	}
	return "other: " + err.Error()
}

func TestJCSVectors(t *testing.T) {
	for _, path := range glob(t, "vectors/jcs/*.json") {
		v := readObject(t, path)
		got, err := Canonicalize(v["input"])
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		if string(got) != v["canonical"] {
			t.Errorf("%s:\n got %s\nwant %s", filepath.Base(path), got, v["canonical"])
		}
	}
}

func TestJCSRejects(t *testing.T) {
	for _, raw := range []string{`9007199254740992`, `{"a":-9007199254740993}`} {
		v, _ := Decode([]byte(raw))
		if _, err := Canonicalize(v); !errors.Is(err, ErrCanonical) {
			t.Errorf("%s: want ErrCanonical, got %v", raw, err)
		}
	}
	if _, err := Canonicalize(struct{}{}); err == nil {
		t.Error("struct accepted")
	}
}

func TestFormatDouble(t *testing.T) {
	cases := map[float64]string{
		1: "1", 0.5: "0.5", 1e21: "1e+21", 1e20: "100000000000000000000", 1e-7: "1e-7",
		0.000001: "0.000001", -1.5e-300: "-1.5e-300", 123.456: "123.456",
	}
	for f, want := range cases {
		if got, _ := formatDouble(f); got != want {
			t.Errorf("%v: got %s want %s", f, got, want)
		}
	}
}

func TestHashVectors(t *testing.T) {
	v := readObject(t, filepath.Join(spec, "vectors", "hash", "params-hash.json"))
	if got, _ := ParamsHash(v["action"].(map[string]any)); got != v["params_hash"] {
		t.Errorf("params_hash %s want %s", got, v["params_hash"])
	}
	v = readObject(t, filepath.Join(spec, "vectors", "hash", "audit-hash.json"))
	if got, _ := AuditHash(v["record"].(map[string]any)); got != v["hash"] {
		t.Errorf("audit hash %s want %s", got, v["hash"])
	}
}

func TestSignatureVectorBothDirections(t *testing.T) {
	v := readObject(t, filepath.Join(spec, "vectors", "signature", "envelope.json"))
	seed, _ := hex.DecodeString(v["private_seed_hex"].(string))
	key, _ := PrivateKeyFromSeed(seed)
	if got := EncodePublicKey(key.Public().(ed25519.PublicKey)); got != v["public_key"] {
		t.Fatalf("public key %s want %s", got, v["public_key"])
	}
	env := v["envelope"].(map[string]any)
	in, _ := SigningInput(env, "envelope")
	if string(in) != v["signing_input"] {
		t.Fatalf("signing input differs:\n got %s\nwant %s", in, v["signing_input"])
	}
	copyEnv := map[string]any{}
	for k, x := range env {
		copyEnv[k] = x
	}
	if err := SignObject(copyEnv, "envelope", key); err != nil {
		t.Fatal(err)
	}
	if copyEnv["autonomysig"] != v["autonomysig"] {
		t.Fatalf("Go signature differs from the Python vector")
	}
	ring, _ := NewKeyRing(map[string]string{v["signer"].(string): v["public_key"].(string)})
	if _, err := VerifyObject(env, "envelope", ring); err != nil {
		t.Fatal(err)
	}
}

func TestEnvelopeFixtures(t *testing.T) {
	ring, _ := testKeys(t)
	for _, path := range glob(t, "fixtures/v0/envelope/valid/*.json") {
		env := readObject(t, path)
		now, _ := ParseTime(env["time"].(string))
		if _, err := VerifyEnvelope(env, ring, now, Freshness{}, nil); err != nil {
			t.Errorf("%s rejected: %v", filepath.Base(path), err)
		}
	}
	now, _ := ParseTime("2026-11-06T18:20:11Z")
	want := map[string]string{
		"missing-signature": "malformed", "wrong-specversion": "unknown protocol version",
		"tampered-data": "bad signature", "uppercase-source": "malformed",
		"data-base64": "malformed", "expires-before-time": "malformed",
	}
	for name, r := range want {
		env := readObject(t, filepath.Join(spec, "fixtures", "v0", "envelope", "invalid", name+".json"))["instance"].(map[string]any)
		if _, err := VerifyEnvelope(env, ring, now, Freshness{}, nil); reason(err) != r {
			t.Errorf("%s: got %q want %q", name, reason(err), r)
		}
	}
}

func TestSignedFixtures(t *testing.T) {
	ring, _ := testKeys(t)
	for _, kind := range []string{"grant", "decision", "approval", "execution-record", "verification"} {
		for _, path := range glob(t, "fixtures/v0/"+kind+"/valid/*.json") {
			obj := readObject(t, path)
			if _, err := VerifyObject(obj, kind, ring); err != nil {
				t.Errorf("%s/%s: %v", kind, filepath.Base(path), err)
			}
			obj["signature"] = strings.Repeat("A", 4) + obj["signature"].(string)[4:]
			if _, err := VerifyObject(obj, kind, ring); reason(err) != "bad signature" {
				t.Errorf("%s/%s tampered: %v", kind, filepath.Base(path), err)
			}
		}
	}
}

func TestAuditChainFixture(t *testing.T) {
	ring, _ := testKeys(t)
	data, err := os.ReadFile(filepath.Join(spec, "fixtures", "v0", "audit-record", "valid", "chain.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var chain []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		rec, err := DecodeObject([]byte(line))
		if err != nil {
			t.Fatal(err)
		}
		chain = append(chain, rec)
	}
	if p := VerifyChain(chain, ring); len(p) != 0 {
		t.Fatalf("intact chain: %v", p)
	}
	if p := VerifyChain([]map[string]any{chain[0], chain[2]}, ring); len(p) == 0 {
		t.Fatal("gap not detected")
	}
}

func TestNewEnvelopeRoundTrip(t *testing.T) {
	ring, seeds := testKeys(t)
	key := keyFor(t, seeds, "cell:field-station")
	now := time.Date(2027, 1, 15, 8, 0, 0, 0, time.UTC)
	env, err := NewEnvelope(key, EnvelopeParams{
		Source: "cell:field-station", Type: "health.heartbeat", Data: map[string]any{"uptime": 1},
		Seq: 7, Now: now, TTL: 3 * time.Minute,
	})
	if err != nil {
		t.Fatal(err)
	}
	if env["expires"] != "2027-01-15T08:03:00Z" || len(env["id"].(string)) != 26 {
		t.Fatalf("unexpected %v", env)
	}
	seen := &MemorySeen{}
	if src, err := VerifyEnvelope(env, ring, now, Freshness{}, seen); err != nil || src != "cell:field-station" {
		t.Fatalf("%s %v", src, err)
	}
	if _, err := VerifyEnvelope(env, ring, now, Freshness{}, seen); reason(err) != "replayed" {
		t.Fatalf("replay: %v", err)
	}
	checks := []struct {
		now  time.Time
		f    Freshness
		want string
	}{
		{now.Add(3 * time.Minute), Freshness{}, "expired"},
		{now.Add(-121 * time.Second), Freshness{}, "from the future"},
		{now, Freshness{MaxLifetime: time.Minute}, "lifetime too long"},
	}
	for _, c := range checks {
		if _, err := VerifyEnvelope(env, ring, c.now, c.f, nil); reason(err) != c.want {
			t.Errorf("got %q want %q", reason(err), c.want)
		}
	}
	env["seq"] = int64(8)
	if _, err := VerifyEnvelope(env, ring, now, Freshness{}, nil); reason(err) != "bad signature" {
		t.Errorf("tamper: %v", err)
	}
}

func TestKeyEncodings(t *testing.T) {
	key, _ := GenerateKey()
	pub := key.Public().(ed25519.PublicKey)
	text := EncodePublicKey(pub)
	legacy := base64.StdEncoding.EncodeToString(pub) // edge_server fleet.yaml form
	for _, s := range []string{text, legacy} {
		got, err := DecodePublicKey(s)
		if err != nil || EncodePublicKey(got) != text {
			t.Errorf("%s: %v", s, err)
		}
	}
	for _, bad := range []string{"", "ed25519:abc", "AAAA"} {
		if _, err := DecodePublicKey(bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
	ring, _ := NewKeyRing(map[string]string{"svc:pdp": text})
	if ring.Get("svc:pdp@field-station") == nil || ring.Get("svc:other") != nil {
		t.Error("key ring lookup")
	}
}

func TestTimesAndIDs(t *testing.T) {
	ts := time.Date(2027, 1, 15, 8, 0, 0, 250e6, time.UTC)
	if FormatTime(ts) != "2027-01-15T08:00:00.250Z" || FormatTime(ts.Truncate(time.Second)) != "2027-01-15T08:00:00Z" {
		t.Error(FormatTime(ts))
	}
	for _, bad := range []string{"2027-01-15T08:00:00", "2027-01-15T08:00:00+00:00"} {
		if _, err := ParseTime(bad); err == nil {
			t.Errorf("%s accepted", bad)
		}
	}
	a, b := NewULID(ts), NewULID(ts.Add(time.Millisecond))
	if len(a) != 26 || a[:10] >= b[:10] || strings.ContainsAny(a, "ILOU") {
		t.Errorf("ulid %s %s", a, b)
	}
	if !IsComponentRef("svc:pdp@field-station") || IsComponentRef("owner:bob") {
		t.Error("component ref")
	}
	if tp := NewTraceparent(); len(tp) != 55 || !strings.HasPrefix(tp, "00-") {
		t.Error(tp)
	}
}

func TestRegistryFixtures(t *testing.T) {
	_, seeds := testKeys(t)
	keys := readObject(t, filepath.Join(spec, "fixtures", "keys", "test-keys.json"))["public"].(map[string]any)
	root := keys["owner:root"].(string)
	doc := readObject(t, filepath.Join(spec, "fixtures", "v0", "registry", "valid", "two-site.json"))
	sig := readObject(t, filepath.Join(spec, "fixtures", "v0", "registry-signature", "valid", "ops.json"))
	now, _ := ParseTime("2026-11-08T12:30:00Z")
	reg, err := VerifyRegistry(doc, sig, root, nil, now)
	if err != nil {
		t.Fatal(err)
	}
	if reg.Serial != 1 || reg.Signer != "owner:ops" || len(reg.CellKeys) != 2 || len(reg.Components) != 2 {
		t.Fatalf("unexpected %+v", reg)
	}
	ring := reg.KeyRing("field-station")
	if ring.Get("agent:field-station") == nil || ring.Get("svc:job-service") != nil || ring.Get("cell:home-hub") == nil {
		t.Error("key ring by cell")
	}
	if _, err := VerifyRegistry(doc, sig, keys["owner:ops"].(string), nil, now); reason(err) != "untrusted root" {
		t.Errorf("pinned root: %v", err)
	}
	if _, err := VerifyRegistry(doc, sig, root, map[string]any{"serial": json.Number("2")}, now); reason(err) != "rollback" {
		t.Errorf("rollback: %v", err)
	}
	ops := keyFor(t, seeds, "owner:ops")
	for name, want := range map[string]string{
		"ops-added-without-root": "bad owners signature", "shared-key": "invalid registry",
		"unknown-cell": "invalid registry", "revoked-still-listed": "invalid registry",
		"unknown-site": "invalid registry", "double-hyphen-name": "invalid registry",
	} {
		bad := readObject(t, filepath.Join(spec, "fixtures", "v0", "registry", "invalid", name+".json"))["instance"].(map[string]any)
		h, _ := RegistryHash(bad)
		s := map[string]any{"registry_hash": h, "serial": int64(1), "signed_at": "2026-11-08T12:00:00Z", "signer": "owner:ops", "key": EncodePublicKey(ops.Public().(ed25519.PublicKey))}
		if err := SignObject(s, "registry-signature", ops); err != nil {
			t.Fatal(err)
		}
		if _, err := VerifyRegistry(bad, s, root, nil, now); reason(err) != want {
			t.Errorf("%s: got %q want %q", name, reason(err), want)
		}
	}
	tampered := readObject(t, filepath.Join(spec, "fixtures", "v0", "registry-signature", "invalid", "tampered-serial.json"))["instance"].(map[string]any)
	if _, err := VerifyRegistry(doc, tampered, root, nil, now); reason(err) != "bad signature" {
		t.Errorf("tampered: %v", err)
	}
}
