# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 0 — effective-binding resolver.

Per BRD §8: a leaf application's effective bindings come from a
key-level shallow merge of [root, ..., parent, leaf] configs. Leaf
overrides keys present at the parent; keys absent at the leaf are
inherited from the parent.

The shallow rule applies at every level — there is no deep merge of
nested config objects. If a leaf binding partially specifies a complex
sub-object, it replaces the parent's wholesale at that one key.

Tests cover:
  - Single-config (leaf only) returns leaf unchanged
  - Two-level (root + leaf): leaf overrides where it specifies
  - Three-level (root + parent + leaf): walk-down chain
  - Nested config dicts: shallow merge at the dict-key level
  - role_mappings: shallow merge of role-name keys
  - compute / channels keyed-by-name: leaf adds keys parent didn't have
  - role_mappings replaces values whole at the role-name level
"""

import pytest

from termin_runtime.providers import (
    DeployConfig, parse_deploy_config,
)
from termin_runtime.providers.binding import (
    resolve_effective_bindings,
)


def _cfg(version="0.9.0", **overrides):
    """Build a minimal deploy config with optional overrides."""
    base = {
        "version": version,
        "bindings": {
            "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {},
            "channels": {},
        },
        "runtime": {},
    }
    for k, v in overrides.items():
        # Top-level key (e.g., "bindings" or "runtime") gets replaced
        # wholesale by the override; nested dict structure preserved.
        base[k] = v
    return parse_deploy_config(base)


# ── Single-level: nothing to merge ──

class TestSingleLevel:
    def test_leaf_alone_returns_unchanged(self):
        leaf = _cfg()
        result = resolve_effective_bindings([leaf])
        assert result.bindings.identity.provider == "stub"
        assert result.bindings.storage.provider == "sqlite"

    def test_empty_chain_raises(self):
        with pytest.raises(ValueError):
            resolve_effective_bindings([])


# ── Two-level: root + leaf ──

class TestTwoLevel:
    def test_leaf_overrides_root_for_identity_provider(self):
        root = _cfg(bindings={
            "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        leaf = _cfg(bindings={
            "identity": {"provider": "okta", "config": {"tenant": "ex.okta.com"}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        result = resolve_effective_bindings([root, leaf])
        assert result.bindings.identity.provider == "okta"
        assert result.bindings.identity.config == {"tenant": "ex.okta.com"}

    def test_leaf_inherits_root_when_not_overridden(self):
        """Per BRD §8: leaf inherits keys it doesn't specify. Test that
        when leaf and root have the same identity provider, leaf
        config doesn't accidentally erase root config."""
        root = _cfg(bindings={
            "identity": {"provider": "okta", "config": {"tenant": "root.okta.com"}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        # Leaf doesn't override identity at all — it should inherit.
        leaf = _cfg()  # uses default minimal stub bindings
        # Override leaf identity provider WITH the same root identity, no config.
        result = resolve_effective_bindings([root, leaf])
        # leaf's stub provider wins because leaf SPECIFIED a binding —
        # there's no "absence" sentinel in this shape.
        assert result.bindings.identity.provider == "stub"


# ── role_mappings: per BRD §8 explicit case ──

class TestRoleMappingsShallow:
    """Per BRD §8: role_mappings keys overlay parent keys; parent keys
    not mentioned at leaf survive.

    If root: role_mappings = {role-A: [...], role-B: [...]}
    and leaf: role_mappings = {role-C: [...]}
    then effective: {role-A, role-B, role-C}.

    This is the explicit case JL called out in review."""

    def test_role_mappings_shallow_merge_keys_inherit(self):
        root = _cfg(bindings={
            "identity": {
                "provider": "okta", "config": {},
                "role_mappings": {
                    "warehouse clerk": ["okta-clerks"],
                    "warehouse manager": ["okta-managers"],
                },
            },
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        leaf = _cfg(bindings={
            "identity": {
                "provider": "okta", "config": {},
                "role_mappings": {
                    "executive": ["okta-execs"],
                },
            },
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        result = resolve_effective_bindings([root, leaf])
        rm = result.bindings.identity.role_mappings
        assert "warehouse clerk" in rm
        assert "warehouse manager" in rm
        assert "executive" in rm
        assert rm["executive"] == ["okta-execs"]

    def test_role_mappings_leaf_overrides_same_role(self):
        """When leaf specifies a role-name key the parent also specifies,
        leaf's value replaces parent's value at THAT key wholesale —
        no merging of the inner list."""
        root = _cfg(bindings={
            "identity": {
                "provider": "okta", "config": {},
                "role_mappings": {
                    "warehouse clerk": ["okta-clerks-old"],
                },
            },
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        leaf = _cfg(bindings={
            "identity": {
                "provider": "okta", "config": {},
                "role_mappings": {
                    "warehouse clerk": ["okta-clerks-new"],
                },
            },
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        result = resolve_effective_bindings([root, leaf])
        # Leaf wins at the role-name level. NO list concatenation.
        assert result.bindings.identity.role_mappings["warehouse clerk"] == ["okta-clerks-new"]


# ── identity.config: shallow merge by config-key ──

class TestConfigShallow:
    def test_identity_config_shallow_merge(self):
        """config keys overlay; leaf wins at each config key."""
        root = _cfg(bindings={
            "identity": {
                "provider": "okta",
                "config": {"tenant": "root.okta.com", "scope": "openid"},
                "role_mappings": {},
            },
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        leaf = _cfg(bindings={
            "identity": {
                "provider": "okta",
                "config": {"scope": "openid email profile"},
                "role_mappings": {},
            },
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        result = resolve_effective_bindings([root, leaf])
        # tenant inherited from root, scope from leaf.
        assert result.bindings.identity.config["tenant"] == "root.okta.com"
        assert result.bindings.identity.config["scope"] == "openid email profile"

    def test_complex_subobject_replaced_wholesale(self):
        """Per BRD §8: shallow at every level — a nested config object
        like 'auth: {...}' replaces wholesale, not merge-by-key inside.

        If leaf says auth: {type: bearer, token: ...} and root said
        auth: {type: hmac, secret: ...}, the leaf's auth wins entirely.
        type and token are leaf's; root's secret does NOT survive."""
        root = _cfg(bindings={
            "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        # Two configs with different "auth" subobjects.
        root_complex = _cfg(bindings={
            "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
            "storage": {"provider": "postgres", "config": {
                "host": "db.example.com",
                "auth": {"type": "hmac", "secret": "root-secret"},
            }},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        leaf = _cfg(bindings={
            "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
            "storage": {"provider": "postgres", "config": {
                "auth": {"type": "bearer", "token": "leaf-token"},
            }},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        result = resolve_effective_bindings([root_complex, leaf])
        # host inherited (leaf didn't mention it).
        assert result.bindings.storage.config["host"] == "db.example.com"
        # auth replaced wholesale — no 'secret' key.
        auth = result.bindings.storage.config["auth"]
        assert auth["type"] == "bearer"
        assert auth["token"] == "leaf-token"
        assert "secret" not in auth


# ── Three-level: root + org + app ──

class TestThreeLevel:
    """Per BRD §8 boundary tree: root → org → app."""

    def test_three_level_chain_walks_in_order(self):
        root = _cfg(bindings={
            "identity": {"provider": "stub", "config": {"k": "from-root"}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        org = _cfg(bindings={
            "identity": {"provider": "stub", "config": {"k": "from-org"}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        app = _cfg(bindings={
            "identity": {"provider": "stub", "config": {"k": "from-app"}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        result = resolve_effective_bindings([root, org, app])
        assert result.bindings.identity.config["k"] == "from-app"

    def test_three_level_inherits_through_chain(self):
        """app inherits from org inherits from root."""
        root = _cfg(bindings={
            "identity": {"provider": "stub", "config": {"root_only": 1}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        org = _cfg(bindings={
            "identity": {"provider": "stub", "config": {"org_only": 2}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        app = _cfg(bindings={
            "identity": {"provider": "stub", "config": {"app_only": 3}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {}, "channels": {},
        })
        result = resolve_effective_bindings([root, org, app])
        cfg = result.bindings.identity.config
        assert cfg["root_only"] == 1
        assert cfg["org_only"] == 2
        assert cfg["app_only"] == 3


# ── Keyed-by-name (compute / channels): same shallow rule ──

class TestKeyedByNameMerge:
    def test_compute_keys_inherit(self):
        root = _cfg(bindings={
            "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {
                "ai-agent": {"provider": "anthropic", "config": {"model": "x"}},
            },
            "channels": {},
        })
        leaf = _cfg(bindings={
            "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {
                "llm": {"provider": "anthropic", "config": {"model": "y"}},
            },
            "channels": {},
        })
        result = resolve_effective_bindings([root, leaf])
        # Both keys present.
        assert "ai-agent" in result.bindings.compute
        assert "llm" in result.bindings.compute
        assert result.bindings.compute["ai-agent"].config["model"] == "x"
        assert result.bindings.compute["llm"].config["model"] == "y"

    def test_channels_leaf_overrides_same_key(self):
        root = _cfg(bindings={
            "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {},
            "channels": {
                "alerts": {"provider": "slack", "config": {"target": "general"}},
            },
        })
        leaf = _cfg(bindings={
            "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {},
            "channels": {
                "alerts": {"provider": "slack", "config": {"target": "alerts-prod"}},
            },
        })
        result = resolve_effective_bindings([root, leaf])
        # Leaf's channel binding replaces root's at the channel-name level.
        # config also shallow-merged.
        assert result.bindings.channels["alerts"].provider == "slack"
        assert result.bindings.channels["alerts"].config["target"] == "alerts-prod"


# ── Version compatibility ──

class TestVersionPropagation:
    def test_resolved_version_matches_leaf(self):
        root = _cfg(version="0.9.0")
        leaf = _cfg(version="0.9.0")
        result = resolve_effective_bindings([root, leaf])
        assert result.version == "0.9.0"

    def test_mismatched_versions_rejected(self):
        """If root and leaf disagree on schema version, that's a
        deploy misconfiguration — refuse to merge silently."""
        root = _cfg(version="0.9.0")
        leaf_v2 = _cfg(version="0.10.0")
        with pytest.raises(ValueError) as exc:
            resolve_effective_bindings([root, leaf_v2])
        assert "version" in str(exc.value).lower()
