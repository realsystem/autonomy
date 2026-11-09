"""autonomy-core (C02): the one implementation of Autonomy envelopes, signatures and the
state behind them. Implements spec v0 (spec/README.md)."""

from .audit import AuditLog, verify_chain
from .envelope import (
    Freshness,
    check_fresh,
    check_structure,
    envelope_times,
    is_envelope,
    make_envelope,
    verify_envelope,
)
from .errors import Reject
from .ids import (
    child_traceparent,
    is_component_ref,
    is_name,
    new_correlation_id,
    new_traceparent,
    new_ulid,
    parse_rfc3339,
    prefixed_id,
    to_rfc3339,
)
from .jcs import CanonicalizationError, canonicalize
from .keys import (
    KeyRing,
    decode_public_key,
    encode_public_key,
    generate_private_key,
    load_private_key,
    private_key_from_seed,
    save_private_key,
    sign,
    verify,
)
from .outbox import Outbox
from .registry import (
    Component,
    Registry,
    RegistryStore,
    check_document,
    fingerprint,
    ops_keys,
    registry_hash,
    same_key,
    sign_owners,
    sign_registry,
    verify_registry,
)
from .replay import Counter, SeenStore
from .signing import (
    GENESIS_HASH,
    audit_hash,
    params_hash,
    sha256_tagged,
    sign_object,
    signing_input,
    verify_object,
)

__version__ = "0.2.0"
SPEC_VERSION = "0.2.0"  # the contracts version implemented

__all__ = [
    "AuditLog",
    "CanonicalizationError",
    "Component",
    "Counter",
    "Freshness",
    "GENESIS_HASH",
    "KeyRing",
    "Outbox",
    "Registry",
    "RegistryStore",
    "Reject",
    "SPEC_VERSION",
    "SeenStore",
    "audit_hash",
    "canonicalize",
    "check_document",
    "check_fresh",
    "check_structure",
    "child_traceparent",
    "decode_public_key",
    "encode_public_key",
    "envelope_times",
    "fingerprint",
    "generate_private_key",
    "is_component_ref",
    "is_envelope",
    "is_name",
    "load_private_key",
    "make_envelope",
    "new_correlation_id",
    "new_traceparent",
    "new_ulid",
    "ops_keys",
    "params_hash",
    "parse_rfc3339",
    "prefixed_id",
    "private_key_from_seed",
    "registry_hash",
    "same_key",
    "save_private_key",
    "sha256_tagged",
    "sign",
    "sign_object",
    "sign_owners",
    "sign_registry",
    "signing_input",
    "to_rfc3339",
    "verify",
    "verify_chain",
    "verify_envelope",
    "verify_object",
    "verify_registry",
]
