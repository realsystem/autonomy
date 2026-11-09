package autonomycore

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
)

// GenesisHash is the prev_hash of the first audit record of a chain.
var GenesisHash = "sha256:" + strings.Repeat("0", 64)

// SignedFields maps an object kind to its (signature field, signer field).
var SignedFields = map[string][2]string{
	"envelope":         {"autonomysig", "source"},
	"grant":            {"signature", "issued_by"},
	"decision":         {"signature", "issuer"},
	"approval":         {"signature", "approver"},
	"execution-record": {"signature", "executor"},
	"verification":     {"signature", "verifier"},
	"audit-record":     {"signature", "actor"},
	// the signer field names the owner role; the key used is in "key"
	"registry-signature": {"signature", "signer"},
	"control-order":      {"signature", "issued_by"},
}

// Attested lists the kinds an approval service (C05) may sign on the owner's behalf:
// when attested_by is present it names the signer.
var Attested = map[string]bool{"approval": true, "control-order": true}

// Reject is the error for a message or record that must not be acted on. Reason is a
// short, stable phrase identical to the Python library's ("bad signature", "expired", ...).
type Reject struct {
	Reason string
	Detail string
}

func (r *Reject) Error() string {
	if r.Detail == "" {
		return r.Reason
	}
	return r.Reason + ": " + r.Detail
}

func reject(reason, detail string) *Reject { return &Reject{Reason: reason, Detail: detail} }

// SHA256Tagged returns "sha256:<hex>".
func SHA256Tagged(data []byte) string {
	sum := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(sum[:])
}

func without(obj map[string]any, fields ...string) map[string]any {
	out := make(map[string]any, len(obj))
outer:
	for k, v := range obj {
		for _, f := range fields {
			if k == f {
				continue outer
			}
		}
		out[k] = v
	}
	return out
}

func fieldsFor(kind string) ([2]string, error) {
	f, ok := SignedFields[kind]
	if !ok {
		return f, fmt.Errorf("unknown signed kind %q", kind)
	}
	return f, nil
}

// SigningInput is the canonical JSON of obj without its signature field.
func SigningInput(obj map[string]any, kind string) ([]byte, error) {
	f, err := fieldsFor(kind)
	if err != nil {
		return nil, err
	}
	return Canonicalize(without(obj, f[0]))
}

// SignObject sets the signature field of obj in place.
func SignObject(obj map[string]any, kind string, key ed25519.PrivateKey) error {
	f, err := fieldsFor(kind)
	if err != nil {
		return err
	}
	delete(obj, f[0])
	data, err := Canonicalize(obj)
	if err != nil {
		return err
	}
	obj[f[0]] = Sign(key, data)
	return nil
}

// VerifyObject checks the signature and returns the signer reference.
func VerifyObject(obj map[string]any, kind string, ring *KeyRing) (string, error) {
	f, err := fieldsFor(kind)
	if err != nil {
		return "", err
	}
	signerField := f[1]
	if att, isStr := obj["attested_by"].(string); isStr && Attested[kind] && att != "" {
		signerField = "attested_by"
	}
	signer, ok := obj[signerField].(string)
	if !ok {
		return "", reject("malformed", signerField+" missing")
	}
	pub := ring.Get(signer)
	if pub == nil {
		return "", reject("unknown signer", signer)
	}
	sig, _ := obj[f[0]].(string)
	data, err := SigningInput(obj, kind)
	if err != nil {
		return "", reject("malformed", err.Error())
	}
	if !Verify(pub, data, sig) {
		return "", reject("bad signature", signer)
	}
	return signer, nil
}

// ParamsHash is sha256 of the canonical proposal.action {"type", "target", "params"}.
func ParamsHash(action map[string]any) (string, error) {
	data, err := Canonicalize(action)
	if err != nil {
		return "", err
	}
	return SHA256Tagged(data), nil
}

// AuditHash is sha256(UTF-8(prev_hash) || canonical(record without hash and signature)).
func AuditHash(record map[string]any) (string, error) {
	prev, ok := record["prev_hash"].(string)
	if !ok {
		return "", reject("malformed", "prev_hash missing")
	}
	body, err := Canonicalize(without(record, "hash", "signature"))
	if err != nil {
		return "", err
	}
	return SHA256Tagged(append([]byte(prev), body...)), nil
}

// VerifyChain lists the problems in an audit chain (none: intact). With a ring it also
// checks every signature.
func VerifyChain(records []map[string]any, ring *KeyRing) []string {
	var problems []string
	var prev map[string]any
	for i, rec := range records {
		where := fmt.Sprintf("record %d (seq %v)", i, rec["seq"])
		h, err := AuditHash(rec)
		if err != nil || rec["hash"] != h {
			problems = append(problems, where+": hash does not match")
		}
		seq, _ := asInt(rec["seq"])
		if prev == nil {
			if seq != 0 || rec["prev_hash"] != GenesisHash {
				problems = append(problems, where+": chain does not start at seq 0 with the genesis hash")
			}
		} else {
			pseq, _ := asInt(prev["seq"])
			if seq != pseq+1 {
				problems = append(problems, fmt.Sprintf("%s: seq does not follow %d", where, pseq))
			}
			if rec["prev_hash"] != prev["hash"] {
				problems = append(problems, where+": prev_hash does not link to the previous record")
			}
			if rec["cell"] != prev["cell"] {
				problems = append(problems, where+": chain mixes cells")
			}
		}
		if ring != nil {
			if _, err := VerifyObject(rec, "audit-record", ring); err != nil {
				problems = append(problems, where+": "+err.Error())
			}
		}
		prev = rec
	}
	return problems
}
