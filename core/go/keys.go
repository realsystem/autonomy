package autonomycore

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"strings"
	"sync"
)

const keyPrefix = "ed25519:"

// EncodePublicKey returns "ed25519:<base64url, unpadded>".
func EncodePublicKey(pub ed25519.PublicKey) string {
	return keyPrefix + base64.RawURLEncoding.EncodeToString(pub)
}

// DecodePublicKey accepts "ed25519:<base64url>" or plain standard base64 of the 32 raw
// bytes (the edge_server fleet.yaml form).
func DecodePublicKey(text string) (ed25519.PublicKey, error) {
	text = strings.TrimSpace(text)
	var raw []byte
	var err error
	if strings.HasPrefix(text, keyPrefix) {
		raw, err = base64.RawURLEncoding.Strict().DecodeString(text[len(keyPrefix):])
	} else {
		raw, err = base64.StdEncoding.Strict().DecodeString(text)
	}
	if err != nil || len(raw) != ed25519.PublicKeySize {
		return nil, errors.New("not an Ed25519 public key")
	}
	return ed25519.PublicKey(raw), nil
}

// PrivateKeyFromSeed is for tests and vectors; production keys come from GenerateKey.
func PrivateKeyFromSeed(seed []byte) (ed25519.PrivateKey, error) {
	if len(seed) != ed25519.SeedSize {
		return nil, errors.New("seed must be 32 bytes")
	}
	return ed25519.NewKeyFromSeed(seed), nil
}

// GenerateKey creates a new Ed25519 key pair.
func GenerateKey() (ed25519.PrivateKey, error) {
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	return priv, err
}

// Sign returns "ed25519:<base64url signature>".
func Sign(key ed25519.PrivateKey, data []byte) string {
	return keyPrefix + base64.RawURLEncoding.EncodeToString(ed25519.Sign(key, data))
}

// Verify reports whether signature (in contract encoding) is valid for data.
func Verify(pub ed25519.PublicKey, data []byte, signature string) bool {
	if !strings.HasPrefix(signature, keyPrefix) {
		return false
	}
	raw, err := base64.RawURLEncoding.Strict().DecodeString(signature[len(keyPrefix):])
	if err != nil || len(raw) != ed25519.SignatureSize {
		return false
	}
	return ed25519.Verify(pub, data, raw)
}

// KeyRing holds public keys by component reference. A signer "svc:pdp@field-station"
// is found under that exact reference first, then under "svc:pdp". Safe for concurrent use.
type KeyRing struct {
	mu   sync.RWMutex
	keys map[string]ed25519.PublicKey
}

// NewKeyRing builds a ring from encoded keys.
func NewKeyRing(keys map[string]string) (*KeyRing, error) {
	r := &KeyRing{keys: map[string]ed25519.PublicKey{}}
	for ref, text := range keys {
		pub, err := DecodePublicKey(text)
		if err != nil {
			return nil, errors.New(ref + ": " + err.Error())
		}
		r.keys[ref] = pub
	}
	return r, nil
}

// Add sets the key for ref.
func (r *KeyRing) Add(ref string, pub ed25519.PublicKey) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.keys == nil {
		r.keys = map[string]ed25519.PublicKey{}
	}
	r.keys[ref] = pub
}

// Get finds the key for a signer, or nil.
func (r *KeyRing) Get(signer string) ed25519.PublicKey {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if k, ok := r.keys[signer]; ok {
		return k
	}
	base, _, _ := strings.Cut(signer, "@")
	return r.keys[base]
}
