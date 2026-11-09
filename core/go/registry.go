package autonomycore

import (
	"bytes"
	"crypto/ed25519"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"time"
)

// RegistryFormat is the registry document format this module understands.
const RegistryFormat = 1

var cellName = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)

// Registry is a verified registry (spec registry + registry-signature).
type Registry struct {
	Doc        map[string]any
	Signature  map[string]any
	Hash       string
	Serial     int64
	Root       string // encoded owners.root
	Signer     string
	CellKeys   map[string]ed25519.PublicKey
	Components map[string]RegisteredComponent
	revoked    [][]byte
}

// RegisteredComponent is one entry of the registry's components section.
type RegisteredComponent struct {
	ID      string
	Key     ed25519.PublicKey
	Version string
	Cells   []string
}

// KeyRing returns the keys of every cell (cell:<name>) and of the components; with a
// non-empty cell, only the components that list it.
func (r *Registry) KeyRing(cell string) *KeyRing {
	ring := &KeyRing{}
	for name, k := range r.CellKeys {
		ring.Add("cell:"+name, k)
	}
	for _, c := range r.Components {
		if cell == "" || contains(c.Cells, cell) {
			ring.Add(c.ID, c.Key)
		}
	}
	return ring
}

// IsRevoked reports whether key is on the revoked list.
func (r *Registry) IsRevoked(key ed25519.PublicKey) bool {
	for _, k := range r.revoked {
		if bytes.Equal(k, key) {
			return true
		}
	}
	return false
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// RegistryHash is sha256 of the canonical JSON of the registry document.
func RegistryHash(doc map[string]any) (string, error) {
	data, err := Canonicalize(doc)
	if err != nil {
		return "", err
	}
	return SHA256Tagged(data), nil
}

func sameKey(a, b string) bool {
	ka, err1 := DecodePublicKey(a)
	kb, err2 := DecodePublicKey(b)
	return err1 == nil && err2 == nil && bytes.Equal(ka, kb)
}

// CheckRegistryDocument lists consistency problems (signatures are checked by VerifyRegistry).
func CheckRegistryDocument(doc map[string]any) []string {
	if f, _ := asInt(doc["registry_format"]); f != RegistryFormat {
		return []string{fmt.Sprintf("not a registry (registry_format %d expected)", RegistryFormat)}
	}
	owners, ok1 := doc["owners"].(map[string]any)
	nodes, ok2 := doc["nodes"].(map[string]any)
	if !ok1 || !ok2 {
		return []string{"owners and nodes must be objects"}
	}
	ops, _ := owners["ops"].([]any)
	components, _ := doc["components"].(map[string]any)
	revoked, _ := doc["revoked"].([]any)
	var problems []string
	seen := map[string]string{}
	claim := func(text any, who string) {
		s, _ := text.(string)
		k, err := DecodePublicKey(s)
		if err != nil {
			problems = append(problems, who+": not an Ed25519 public key")
			return
		}
		if prev, dup := seen[string(k)]; dup {
			problems = append(problems, who+": key already used by "+prev)
			return
		}
		seen[string(k)] = who
	}
	claim(owners["root"], "owner:root")
	for _, o := range ops {
		op, _ := o.(map[string]any)
		claim(op["key"], "owner:ops")
		if s, _ := op["expires"].(string); s == "" {
			problems = append(problems, "owner:ops: expires must be an RFC 3339 UTC time")
		} else if _, err := ParseTime(s); err != nil {
			problems = append(problems, "owner:ops: expires must be an RFC 3339 UTC time")
		}
	}
	sites, hasSites := doc["sites"].(map[string]any)
	for _, name := range sortedKeys(nodes) {
		node, _ := nodes[name].(map[string]any)
		if !cellName.MatchString(name) {
			problems = append(problems, "node "+name+": names use lowercase letters, digits and single hyphens")
		}
		claim(node["public_key"], "cell:"+name)
		if hasSites {
			if site, _ := node["site"].(string); site == "" || !hasKey(sites, site) {
				problems = append(problems, fmt.Sprintf("node %s: site %v is not in sites", name, node["site"]))
			}
		}
	}
	for _, id := range sortedKeys(components) {
		comp, _ := components[id].(map[string]any)
		claim(comp["public_key"], id)
		cells, _ := comp["cells"].([]any)
		if len(cells) == 0 {
			problems = append(problems, id+": cells must list at least one node")
		}
		for _, c := range cells {
			if s, _ := c.(string); !hasKey(nodes, s) {
				problems = append(problems, fmt.Sprintf("%s: %v is not a node of this registry", id, c))
			}
		}
	}
	for _, rv := range revoked {
		rev, _ := rv.(map[string]any)
		id, _ := rev["id"].(string)
		base, _, _ := strings.Cut(id, "@")
		if hasKey(components, base) || (strings.HasPrefix(base, "cell:") && hasKey(nodes, base[5:])) {
			problems = append(problems, id+" is revoked but still listed")
		}
		s, _ := rev["key"].(string)
		k, err := DecodePublicKey(s)
		if err != nil {
			problems = append(problems, "revoked "+id+": not an Ed25519 public key")
		} else if who, used := seen[string(k)]; used {
			problems = append(problems, "revoked key of "+id+" is still used by "+who)
		}
	}
	return problems
}

func hasKey(m map[string]any, k string) bool { _, ok := m[k]; return ok }

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// VerifyRegistry verifies a registry and its detached signature. root is the pinned root
// key ("" accepts the document's own root: first use only); previous is the signature
// document of the registry currently held (nil if none). Errors are *Reject with the same
// reasons as the Python library.
func VerifyRegistry(doc, sig map[string]any, root string, previous map[string]any, now time.Time) (*Registry, error) {
	for _, k := range []string{"registry_hash", "serial", "signed_at", "signer", "key", "signature"} {
		if _, ok := sig[k]; !ok {
			return nil, reject("malformed", "signature has no "+k)
		}
	}
	serial, ok := asInt(sig["serial"])
	if !ok || serial < 1 {
		return nil, reject("malformed", "serial")
	}
	if p := CheckRegistryDocument(doc); len(p) > 0 {
		return nil, reject("invalid registry", strings.Join(p, "; "))
	}
	digest, err := RegistryHash(doc)
	if err != nil {
		return nil, reject("malformed", err.Error())
	}
	if sig["registry_hash"] != digest {
		return nil, reject("registry hash mismatch", "the signature is for another document")
	}
	owners := doc["owners"].(map[string]any)
	rootText, _ := owners["root"].(string)
	rootKey, _ := DecodePublicKey(rootText)
	if root != "" && !sameKey(root, rootText) {
		return nil, reject("untrusted root", "owners.root is not the pinned root key")
	}
	body, err := Canonicalize(without(owners, "signature"))
	if err != nil {
		return nil, reject("malformed", err.Error())
	}
	if s, _ := owners["signature"].(string); !Verify(rootKey, body, s) {
		return nil, reject("bad owners signature", "owners must be signed by root")
	}
	signedAtText, _ := sig["signed_at"].(string)
	signedAt, err := ParseTime(signedAtText)
	if err != nil {
		return nil, reject("malformed", "signed_at")
	}
	if signedAt.After(now.Add(300 * time.Second)) {
		return nil, reject("from the future", "signed_at")
	}
	signer, _ := sig["signer"].(string)
	keyText, _ := sig["key"].(string)
	switch signer {
	case "owner:root":
		if !sameKey(keyText, rootText) {
			return nil, reject("unknown signing key", "owner:root signature not made with owners.root")
		}
	case "owner:ops":
		var match map[string]any
		for _, o := range owners["ops"].([]any) {
			op, _ := o.(map[string]any)
			if s, _ := op["key"].(string); sameKey(keyText, s) {
				match = op
				break
			}
		}
		if match == nil {
			return nil, reject("unknown signing key", "not a delegated ops key")
		}
		exp, _ := ParseTime(match["expires"].(string))
		if !exp.After(signedAt) {
			return nil, reject("ops key expired", fmt.Sprintf("expired %v", match["expires"]))
		}
	default:
		return nil, reject("malformed", "signer")
	}
	signingKey, err := DecodePublicKey(keyText)
	if err != nil {
		return nil, reject("invalid registry", "signature key: not an Ed25519 public key")
	}
	sigBody, err := Canonicalize(without(sig, "signature"))
	if err != nil {
		return nil, reject("malformed", err.Error())
	}
	if s, _ := sig["signature"].(string); !Verify(signingKey, sigBody, s) {
		return nil, reject("bad signature", signer)
	}
	if previous != nil {
		if prev, ok := asInt(previous["serial"]); ok {
			if serial < prev {
				return nil, reject("rollback", fmt.Sprintf("serial %d is older than %d", serial, prev))
			}
			if serial == prev && previous["registry_hash"] != digest {
				return nil, reject("serial conflict", fmt.Sprintf("another registry already has serial %d", serial))
			}
		}
	}
	reg := &Registry{
		Doc: doc, Signature: sig, Hash: digest, Serial: serial, Root: EncodePublicKey(rootKey), Signer: signer,
		CellKeys: map[string]ed25519.PublicKey{}, Components: map[string]RegisteredComponent{},
	}
	for name, n := range doc["nodes"].(map[string]any) {
		s, _ := n.(map[string]any)["public_key"].(string)
		reg.CellKeys[name], _ = DecodePublicKey(s)
	}
	comps, _ := doc["components"].(map[string]any)
	for id, c := range comps {
		cm := c.(map[string]any)
		s, _ := cm["public_key"].(string)
		k, _ := DecodePublicKey(s)
		version, _ := cm["version"].(string)
		var cells []string
		for _, x := range cm["cells"].([]any) {
			cells = append(cells, x.(string))
		}
		reg.Components[id] = RegisteredComponent{ID: id, Key: k, Version: version, Cells: cells}
	}
	revoked, _ := doc["revoked"].([]any)
	for _, rv := range revoked {
		s, _ := rv.(map[string]any)["key"].(string)
		if k, err := DecodePublicKey(s); err == nil {
			reg.revoked = append(reg.revoked, k)
		}
	}
	return reg, nil
}
