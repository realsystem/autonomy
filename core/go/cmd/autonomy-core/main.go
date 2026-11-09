// Command autonomy-core exposes the Go library on the command line, mainly for
// cross-language tests against the Python library.
//
//	autonomy-core canonicalize < value.json
//	autonomy-core make --seed HEX --source cell:x --type health.heartbeat --data '{}' --seq 1 [--ttl 180] [--now T]
//	autonomy-core verify --keys keys.json [--now T] < envelope.json   # prints the source, or "reject: <reason>" and exits 1
package main

import (
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"time"

	core "github.com/realsystem/autonomy/core/go"
)

func main() {
	if len(os.Args) < 2 {
		fail("usage: autonomy-core canonicalize|make|verify [flags]")
	}
	switch os.Args[1] {
	case "canonicalize":
		v, err := core.Decode(readStdin())
		check(err)
		out, err := core.Canonicalize(v)
		check(err)
		os.Stdout.Write(out)
	case "make":
		fs := flag.NewFlagSet("make", flag.ExitOnError)
		seed := fs.String("seed", "", "hex private key seed")
		source := fs.String("source", "", "")
		typ := fs.String("type", "", "")
		data := fs.String("data", "null", "JSON")
		seq := fs.Int64("seq", 0, "")
		ttl := fs.Int("ttl", 0, "seconds")
		now := fs.String("now", "", "RFC 3339")
		check(fs.Parse(os.Args[2:]))
		raw, err := hex.DecodeString(*seed)
		check(err)
		key, err := core.PrivateKeyFromSeed(raw)
		check(err)
		d, err := core.Decode([]byte(*data))
		check(err)
		env, err := core.NewEnvelope(key, core.EnvelopeParams{
			Source: *source, Type: *typ, Data: d, Seq: *seq, Now: parseNow(*now),
			TTL: time.Duration(*ttl) * time.Second,
		})
		check(err)
		check(json.NewEncoder(os.Stdout).Encode(env))
	case "verify":
		fs := flag.NewFlagSet("verify", flag.ExitOnError)
		keys := fs.String("keys", "", `JSON {"ref": "ed25519:..."} or {"public": {...}}`)
		now := fs.String("now", "", "RFC 3339")
		check(fs.Parse(os.Args[2:]))
		ring := loadKeys(*keys)
		env, err := core.DecodeObject(readStdin())
		check(err)
		src, err := core.VerifyEnvelope(env, ring, parseNow(*now), core.Freshness{}, nil)
		if err != nil {
			fmt.Println("reject:", err)
			os.Exit(1)
		}
		fmt.Println(src)
	default:
		fail("unknown command " + os.Args[1])
	}
}

func loadKeys(path string) *core.KeyRing {
	data, err := os.ReadFile(path)
	check(err)
	var doc map[string]json.RawMessage
	check(json.Unmarshal(data, &doc))
	if pub, ok := doc["public"]; ok {
		data = pub
	}
	var m map[string]string
	check(json.Unmarshal(data, &m))
	ring, err := core.NewKeyRing(m)
	check(err)
	return ring
}

func parseNow(s string) time.Time {
	if s == "" {
		return time.Now()
	}
	t, err := core.ParseTime(s)
	check(err)
	return t
}

func readStdin() []byte {
	b, err := io.ReadAll(os.Stdin)
	check(err)
	return b
}

func check(err error) {
	if err != nil {
		fail(err.Error())
	}
}

func fail(msg string) {
	fmt.Fprintln(os.Stderr, msg)
	os.Exit(2)
}
