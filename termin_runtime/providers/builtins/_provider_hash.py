# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Provider config hashing — secret-redacted-then-hashed strategy.

Per compute-provider-design.md §3.5 (Q3 resolved), the
`provider_config_hash` audit field hashes the canonicalized config
dict with secret values replaced by their key paths. This gives
"same vs different operational config" without leaking secrets and
without false-positives on key rotation.

Two configs that differ only in their API keys hash equal — what we
want, since the *behavior-affecting* config is the same. Two configs
with different surrounding shape hash differently, even if they share
secret names.

Heuristic for secret detection: any key name matching a fixed
allow-list of secret-shaped substrings. The allow-list is
intentionally narrow; provider authors can extend per-product if they
have unusual secret-key conventions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


# Substrings that mark a config key as secret. Lowercased comparison.
_SECRET_KEY_SUBSTRINGS = (
    "key",            # api_key, api-key, secret_key
    "secret",         # client_secret
    "token",          # bearer_token, access_token
    "password",
    "credential",     # credentials, aws_credentials
    "private",        # private_key, private_token
)


def _is_secret_key(key: str) -> bool:
    """True iff this key name looks like it holds a secret."""
    lk = key.lower()
    return any(sub in lk for sub in _SECRET_KEY_SUBSTRINGS)


def _redact(value: Any, key_path: str) -> Any:
    """Replace secret values with their key path; recurse into
    dicts and lists. Non-secret leaf values pass through."""
    if isinstance(value, Mapping):
        out = {}
        for k, v in value.items():
            sub_path = f"{key_path}.{k}" if key_path else k
            if _is_secret_key(k):
                # Replace this leaf with the key path.
                out[k] = f"<{sub_path}>"
            else:
                out[k] = _redact(v, sub_path)
        return out
    if isinstance(value, list):
        return [_redact(v, f"{key_path}[{i}]") for i, v in enumerate(value)]
    return value


def hash_provider_config(config: Mapping[str, Any]) -> str:
    """Hash the config with secret values redacted.

    Returns a `sha256:<hex64>` prefixed string for forward-compat with
    other digest schemes the audit format may add later. Same shape
    used by Phase 2.x for content_hash etc.
    """
    redacted = _redact(dict(config or {}), "")
    canonical = json.dumps(redacted, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
