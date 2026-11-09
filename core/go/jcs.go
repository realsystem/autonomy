// Package autonomycore is the Go implementation of Autonomy core (C02): RFC 8785
// canonical JSON, Ed25519 keys and signatures, envelope building and acceptance,
// params_hash and the audit hash chain, as specified in spec/README.md (spec v0).
//
// JSON values are handled as decoded by encoding/json with UseNumber: nil, bool,
// string, json.Number, float64, integer types, []any and map[string]any.
package autonomycore

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

// MaxSafeInt is the largest integer that survives a round trip through an IEEE 754 double.
const MaxSafeInt = 1<<53 - 1

// ErrCanonical is wrapped by every canonicalization error.
var ErrCanonical = errors.New("cannot canonicalize")

// Decode parses JSON keeping numbers as json.Number, as Canonicalize expects.
func Decode(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	if dec.More() {
		return nil, errors.New("trailing data after JSON value")
	}
	return v, nil
}

// DecodeObject is Decode for a JSON object.
func DecodeObject(data []byte) (map[string]any, error) {
	v, err := Decode(data)
	if err != nil {
		return nil, err
	}
	m, ok := v.(map[string]any)
	if !ok {
		return nil, errors.New("not a JSON object")
	}
	return m, nil
}

// Canonicalize returns the RFC 8785 canonical encoding of a JSON value.
func Canonicalize(v any) ([]byte, error) {
	var b strings.Builder
	if err := writeCanonical(&b, v); err != nil {
		return nil, err
	}
	return []byte(b.String()), nil
}

func writeCanonical(b *strings.Builder, v any) error {
	switch x := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		if x {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case string:
		return writeString(b, x)
	case json.Number:
		return writeNumberLiteral(b, string(x))
	case float64:
		s, err := formatDouble(x)
		if err != nil {
			return err
		}
		b.WriteString(s)
	case float32:
		return writeCanonical(b, float64(x))
	case int:
		return writeInt(b, int64(x))
	case int64:
		return writeInt(b, x)
	case int32:
		return writeInt(b, int64(x))
	case uint32:
		return writeInt(b, int64(x))
	case uint64:
		if x > MaxSafeInt {
			return fmt.Errorf("%w: integer %d outside the safe range", ErrCanonical, x)
		}
		return writeInt(b, int64(x))
	case []any:
		b.WriteByte('[')
		for i, e := range x {
			if i > 0 {
				b.WriteByte(',')
			}
			if err := writeCanonical(b, e); err != nil {
				return err
			}
		}
		b.WriteByte(']')
	case []string:
		items := make([]any, len(x))
		for i, s := range x {
			items[i] = s
		}
		return writeCanonical(b, items)
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Slice(keys, func(i, j int) bool { return lessUTF16(keys[i], keys[j]) })
		b.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				b.WriteByte(',')
			}
			if err := writeString(b, k); err != nil {
				return err
			}
			b.WriteByte(':')
			if err := writeCanonical(b, x[k]); err != nil {
				return err
			}
		}
		b.WriteByte('}')
	case map[string]string:
		m := make(map[string]any, len(x))
		for k, s := range x {
			m[k] = s
		}
		return writeCanonical(b, m)
	default:
		return fmt.Errorf("%w: %T is not a JSON type", ErrCanonical, v)
	}
	return nil
}

func lessUTF16(a, b string) bool {
	ua, ub := utf16.Encode([]rune(a)), utf16.Encode([]rune(b))
	for i := 0; i < len(ua) && i < len(ub); i++ {
		if ua[i] != ub[i] {
			return ua[i] < ub[i]
		}
	}
	return len(ua) < len(ub)
}

func writeInt(b *strings.Builder, n int64) error {
	if n > MaxSafeInt || n < -MaxSafeInt {
		return fmt.Errorf("%w: integer %d outside the safe range", ErrCanonical, n)
	}
	b.WriteString(strconv.FormatInt(n, 10))
	return nil
}

// writeNumberLiteral handles a number as written in JSON. Integer literals beyond the
// safe range are refused, like the Python implementation does for ints.
func writeNumberLiteral(b *strings.Builder, lit string) error {
	if !strings.ContainsAny(lit, ".eE") {
		n, err := strconv.ParseInt(lit, 10, 64)
		if err != nil || n > MaxSafeInt || n < -MaxSafeInt {
			return fmt.Errorf("%w: integer %s outside the safe range", ErrCanonical, lit)
		}
		b.WriteString(strconv.FormatInt(n, 10))
		return nil
	}
	f, err := strconv.ParseFloat(lit, 64)
	if err != nil {
		return fmt.Errorf("%w: number %s: %v", ErrCanonical, lit, err)
	}
	s, err := formatDouble(f)
	if err != nil {
		return err
	}
	b.WriteString(s)
	return nil
}

// formatDouble is ECMAScript Number.prototype.toString for finite doubles.
func formatDouble(x float64) (string, error) {
	if math.IsNaN(x) || math.IsInf(x, 0) {
		return "", fmt.Errorf("%w: NaN and Infinity are not JSON numbers", ErrCanonical)
	}
	if x == 0 {
		return "0", nil
	}
	sign := ""
	if x < 0 {
		sign, x = "-", -x
	}
	// Shortest round-tripping digits: "d.ddde±XX".
	e := strconv.FormatFloat(x, 'e', -1, 64)
	mant, expStr, _ := strings.Cut(e, "e")
	digits := strings.Replace(mant, ".", "", 1)
	digits = strings.TrimRight(digits, "0")
	if digits == "" {
		digits = "0"
	}
	exp, err := strconv.Atoi(expStr)
	if err != nil {
		return "", fmt.Errorf("%w: %v", ErrCanonical, err)
	}
	k, n := len(digits), exp+1 // value = 0.<digits> * 10**n
	var out string
	switch {
	case k <= n && n <= 21:
		out = digits + strings.Repeat("0", n-k)
	case 0 < n && n <= 21:
		out = digits[:n] + "." + digits[n:]
	case -6 < n && n <= 0:
		out = "0." + strings.Repeat("0", -n) + digits
	default:
		e := n - 1
		out = digits[:1]
		if k > 1 {
			out += "." + digits[1:]
		}
		if e >= 0 {
			out += "e+" + strconv.Itoa(e)
		} else {
			out += "e-" + strconv.Itoa(-e)
		}
	}
	return sign + out, nil
}

func writeString(b *strings.Builder, s string) error {
	if !utf8.ValidString(s) {
		return fmt.Errorf("%w: invalid UTF-8", ErrCanonical)
	}
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			if r < 0x20 {
				fmt.Fprintf(b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return nil
}
