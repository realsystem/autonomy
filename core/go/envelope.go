package autonomycore

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"math/big"
	"regexp"
	"strings"
	"sync"
	"time"
)

// SpecVersion is the contracts version this module implements.
const SpecVersion = "0.1.0"

// Required envelope attributes (spec/schemas/v0/envelope.schema.json).
var Required = []string{
	"specversion", "id", "source", "type", "time", "seq", "correlationid", "traceparent",
	"principal", "trust", "privacy", "autonomysig",
}

var (
	componentRef = regexp.MustCompile(`^(?:(?:cell|svc|agent|adapter|app):[a-z0-9]+(?:-[a-z0-9]+)*(?:@[a-z0-9]+(?:-[a-z0-9]+)*)?|owner:(?:root|ops))$`)
	rfc3339UTC   = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$`)
	trustValues  = map[string]bool{"trusted": true, "owner": true, "untrusted": true}
	privValues   = map[string]bool{"public": true, "site": true, "private": true, "secret-adjacent": true}
)

const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

// IsComponentRef reports whether s is <kind>:<name>[@<cell>] or owner:root|ops.
func IsComponentRef(s string) bool { return len(s) <= 192 && componentRef.MatchString(s) }

// NewULID returns a ULID for time t: 48-bit milliseconds, 80 random bits.
func NewULID(t time.Time) string {
	var buf [16]byte
	binary.BigEndian.PutUint64(buf[:8], uint64(t.UnixMilli())<<16)
	if _, err := rand.Read(buf[6:]); err != nil {
		panic(err) // crypto/rand does not fail on supported platforms
	}
	n := new(big.Int).SetBytes(buf[:])
	out := make([]byte, 26)
	mask := big.NewInt(31)
	for i := 25; i >= 0; i-- {
		out[i] = crockford[new(big.Int).And(n, mask).Int64()]
		n.Rsh(n, 5)
	}
	return string(out)
}

// NewTraceparent returns a fresh W3C traceparent (version 00, sampled).
func NewTraceparent() string {
	var b [24]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return "00-" + hex.EncodeToString(b[:16]) + "-" + hex.EncodeToString(b[16:]) + "-01"
}

// FormatTime renders t in UTC with a Z suffix; milliseconds only when t has a fraction.
func FormatTime(t time.Time) string {
	t = t.UTC()
	if t.Nanosecond()/1e6 != 0 {
		return t.Format("2006-01-02T15:04:05.000Z")
	}
	return t.Format("2006-01-02T15:04:05Z")
}

// ParseTime parses an RFC 3339 UTC time with a Z suffix.
func ParseTime(s string) (time.Time, error) {
	if !rfc3339UTC.MatchString(s) {
		return time.Time{}, reject("malformed", "time must be RFC 3339 UTC with a Z suffix")
	}
	return time.Parse(time.RFC3339Nano, s)
}

// EnvelopeParams are the inputs to NewEnvelope. Zero values get defaults: Now = time.Now,
// CorrelationID and Traceparent fresh, Principal = Source, Trust "trusted", Privacy "site".
type EnvelopeParams struct {
	Source, Type  string
	Data          any
	Seq           int64
	Now           time.Time
	TTL           time.Duration // > 0 sets expires
	Subject       string
	DataSchema    string
	CorrelationID string
	CausationID   string
	Traceparent   string
	Principal     string
	Trust         string
	Privacy       string
}

// NewEnvelope builds and signs an envelope.
func NewEnvelope(key ed25519.PrivateKey, p EnvelopeParams) (map[string]any, error) {
	now := p.Now
	if now.IsZero() {
		now = time.Now()
	}
	or := func(v, d string) string {
		if v == "" {
			return d
		}
		return v
	}
	env := map[string]any{
		"specversion":     "1.0",
		"id":              NewULID(now),
		"source":          p.Source,
		"type":            p.Type,
		"time":            FormatTime(now),
		"datacontenttype": "application/json",
		"data":            p.Data,
		"seq":             p.Seq,
		"correlationid":   or(p.CorrelationID, "corr_"+NewULID(now)),
		"traceparent":     or(p.Traceparent, NewTraceparent()),
		"principal":       or(p.Principal, p.Source),
		"trust":           or(p.Trust, "trusted"),
		"privacy":         or(p.Privacy, "site"),
	}
	if p.Subject != "" {
		env["subject"] = p.Subject
	}
	if p.DataSchema != "" {
		env["dataschema"] = p.DataSchema
	}
	if p.CausationID != "" {
		env["causationid"] = p.CausationID
	}
	if p.TTL > 0 {
		env["expires"] = FormatTime(now.Add(p.TTL))
	}
	if err := SignObject(env, "envelope", key); err != nil {
		return nil, err
	}
	return env, nil
}

// Freshness bounds what a receiver accepts. MaxLifetime 0 means no limit; MaxAge applies
// to envelopes without expires (default 10 minutes); FutureSlack defaults to 2 minutes.
type Freshness struct {
	MaxLifetime    time.Duration
	FutureSlack    time.Duration
	MaxAge         time.Duration
	RequireExpires bool
}

func (f Freshness) withDefaults() Freshness {
	if f.FutureSlack == 0 {
		f.FutureSlack = 120 * time.Second
	}
	if f.MaxAge == 0 {
		f.MaxAge = 600 * time.Second
	}
	return f
}

// Seen remembers accepted envelope ids for replay protection.
type Seen interface {
	// Add returns true if id is new (and now remembered until until).
	Add(id string, until, now time.Time) (bool, error)
}

// MemorySeen is an in-memory Seen with a capacity limit.
type MemorySeen struct {
	mu  sync.Mutex
	ids map[string]time.Time
	Max int // default 10000
}

// Add implements Seen.
func (m *MemorySeen) Add(id string, until, now time.Time) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.ids == nil {
		m.ids = map[string]time.Time{}
	}
	for k, e := range m.ids {
		if !e.After(now) {
			delete(m.ids, k)
		}
	}
	if _, ok := m.ids[id]; ok {
		return false, nil
	}
	max := m.Max
	if max == 0 {
		max = 10000
	}
	if len(m.ids) >= max {
		return false, reject("too many messages in flight", "")
	}
	m.ids[id] = until
	return true, nil
}

func asInt(v any) (int64, bool) {
	switch x := v.(type) {
	case json.Number:
		if strings.ContainsAny(string(x), ".eE") {
			return 0, false
		}
		n, err := x.Int64()
		return n, err == nil
	case int64:
		return x, true
	case int:
		return int64(x), true
	case float64:
		if x == float64(int64(x)) {
			return int64(x), true
		}
	}
	return 0, false
}

// EnvelopeTimes returns time and expires (zero if absent).
func EnvelopeTimes(env map[string]any) (sent, expires time.Time, err error) {
	ts, ok := env["time"].(string)
	if !ok {
		return sent, expires, reject("malformed", "time or expires")
	}
	if sent, err = ParseTime(ts); err != nil {
		return sent, expires, reject("malformed", "time or expires")
	}
	if raw, present := env["expires"]; present {
		es, ok := raw.(string)
		if !ok {
			return sent, expires, reject("malformed", "time or expires")
		}
		if expires, err = ParseTime(es); err != nil {
			return sent, expires, reject("malformed", "time or expires")
		}
	}
	return sent, expires, nil
}

// CheckStructure performs the checks a receiver needs before trusting any field.
func CheckStructure(env map[string]any) error {
	var missing []string
	for _, k := range Required {
		if _, ok := env[k]; !ok {
			missing = append(missing, k)
		}
	}
	if len(missing) > 0 {
		return reject("malformed", "missing "+strings.Join(missing, ", "))
	}
	if env["specversion"] != "1.0" {
		return reject("unknown protocol version", "")
	}
	for _, k := range []string{"id", "type", "correlationid", "traceparent", "autonomysig"} {
		if s, ok := env[k].(string); !ok || s == "" {
			return reject("malformed", k)
		}
	}
	for _, k := range []string{"source", "principal"} {
		if s, ok := env[k].(string); !ok || !IsComponentRef(s) {
			return reject("malformed", k)
		}
	}
	if seq, ok := asInt(env["seq"]); !ok || seq < 0 || seq > MaxSafeInt {
		return reject("malformed", "seq")
	}
	if s, _ := env["trust"].(string); !trustValues[s] {
		return reject("malformed", "trust or privacy")
	}
	if s, _ := env["privacy"].(string); !privValues[s] {
		return reject("malformed", "trust or privacy")
	}
	if _, ok := env["data_base64"]; ok {
		return reject("malformed", "data_base64 is not supported")
	}
	sent, expires, err := EnvelopeTimes(env)
	if err != nil {
		return err
	}
	if !expires.IsZero() && !expires.After(sent) {
		return reject("malformed", "expires is not after time")
	}
	return nil
}

// CheckFresh returns the time until which the envelope must be remembered, or a Reject.
func CheckFresh(env map[string]any, now time.Time, f Freshness) (time.Time, error) {
	f = f.withDefaults()
	sent, expires, err := EnvelopeTimes(env)
	if err != nil {
		return time.Time{}, err
	}
	if sent.After(now.Add(f.FutureSlack)) {
		return time.Time{}, reject("from the future", "clock difference?")
	}
	if expires.IsZero() {
		if f.RequireExpires {
			return time.Time{}, reject("malformed", "expires is required")
		}
		until := sent.Add(f.MaxAge)
		if !until.After(now) {
			return time.Time{}, reject("expired", "")
		}
		return until, nil
	}
	if !expires.After(sent) {
		return time.Time{}, reject("malformed", "expires is not after time")
	}
	if !expires.After(now) {
		return time.Time{}, reject("expired", "")
	}
	if f.MaxLifetime > 0 && expires.Sub(sent) > f.MaxLifetime {
		return time.Time{}, reject("lifetime too long", "")
	}
	return expires, nil
}

// VerifyEnvelope accepts or rejects an envelope and returns its source. Order:
// structure, signature, freshness, replay (when seen is not nil).
func VerifyEnvelope(env map[string]any, ring *KeyRing, now time.Time, f Freshness, seen Seen) (string, error) {
	if err := CheckStructure(env); err != nil {
		return "", err
	}
	source, err := VerifyObject(env, "envelope", ring)
	if err != nil {
		return "", err
	}
	until, err := CheckFresh(env, now, f)
	if err != nil {
		return "", err
	}
	if seen != nil {
		fresh, err := seen.Add(source+" "+env["id"].(string), until, now)
		if err != nil {
			return "", err
		}
		if !fresh {
			return "", reject("replayed", "")
		}
	}
	return source, nil
}
