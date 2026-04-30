# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 0 — deploy config schema parser.

Parses *.deploy.json files into typed DeployConfig objects. Validates
the v0.9 shape per BRD §7. Phase 0 only parses; Phase 1+ consumes the
parsed structure to bind providers.

Schema invariants tested here:
  - Required top-level keys: version, bindings, runtime
  - Required category bindings: identity, storage, presentation,
    compute, channels (each may be empty {} but must be present)
  - identity / storage / presentation: flat-bind shape
    {provider, config, role_mappings?}
  - compute / channels: keyed-by-name shape
    {<contract-or-channel-name>: {provider, config}}
  - Unknown top-level keys rejected with a clear error
  - Helpful errors on common shape mistakes
"""

import json
import pytest
from pathlib import Path

from termin_server.providers import Category
from termin_server.providers.deploy_config import (
    DeployConfig, parse_deploy_config, DeployConfigError,
)


def _minimal_config_dict() -> dict:
    """The smallest valid v0.9 deploy config — everything bound to stub
    or empty placeholder objects. Used as a base for per-test edits."""
    return {
        "version": "0.9.0",
        "bindings": {
            "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
            "storage": {"provider": "sqlite", "config": {}},
            "presentation": {"provider": "default", "config": {}},
            "compute": {},
            "channels": {},
        },
        "runtime": {},
    }


# ── Shape and required-keys validation ──

class TestRequiredKeys:
    def test_minimal_config_parses(self):
        cfg = parse_deploy_config(_minimal_config_dict())
        assert isinstance(cfg, DeployConfig)
        assert cfg.version == "0.9.0"

    def test_missing_version_raises(self):
        d = _minimal_config_dict()
        del d["version"]
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "version" in str(exc.value).lower()

    def test_missing_bindings_raises(self):
        d = _minimal_config_dict()
        del d["bindings"]
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "bindings" in str(exc.value).lower()

    def test_missing_identity_raises(self):
        d = _minimal_config_dict()
        del d["bindings"]["identity"]
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "identity" in str(exc.value).lower()

    def test_missing_storage_raises(self):
        d = _minimal_config_dict()
        del d["bindings"]["storage"]
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "storage" in str(exc.value).lower()

    def test_missing_presentation_raises(self):
        d = _minimal_config_dict()
        del d["bindings"]["presentation"]
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "presentation" in str(exc.value).lower()

    def test_missing_compute_raises(self):
        d = _minimal_config_dict()
        del d["bindings"]["compute"]
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "compute" in str(exc.value).lower()

    def test_missing_channels_raises(self):
        d = _minimal_config_dict()
        del d["bindings"]["channels"]
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "channels" in str(exc.value).lower()

    def test_missing_runtime_raises(self):
        d = _minimal_config_dict()
        del d["runtime"]
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "runtime" in str(exc.value).lower()


# ── Per-category binding shape ──

class TestFlatBindings:
    """identity / storage / presentation are flat-bound: one product
    per category. The shape is {provider, config[, role_mappings]}."""

    def test_identity_provider_required(self):
        d = _minimal_config_dict()
        d["bindings"]["identity"] = {"config": {}}
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "provider" in str(exc.value).lower()

    def test_storage_provider_required(self):
        d = _minimal_config_dict()
        d["bindings"]["storage"] = {"config": {}}
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "provider" in str(exc.value).lower()

    def test_identity_config_defaults_to_empty(self):
        d = _minimal_config_dict()
        d["bindings"]["identity"] = {"provider": "stub"}
        cfg = parse_deploy_config(d)
        assert cfg.bindings.identity.config == {}

    def test_identity_role_mappings_defaults_to_empty(self):
        d = _minimal_config_dict()
        d["bindings"]["identity"] = {"provider": "stub", "config": {}}
        cfg = parse_deploy_config(d)
        assert cfg.bindings.identity.role_mappings == {}

    def test_identity_role_mappings_carries_through(self):
        d = _minimal_config_dict()
        d["bindings"]["identity"] = {
            "provider": "okta",
            "config": {"tenant": "example.okta.com"},
            "role_mappings": {
                "warehouse clerk": ["okta-clerks"],
                "warehouse manager": ["okta-managers", "okta-leads"],
            },
        }
        cfg = parse_deploy_config(d)
        assert cfg.bindings.identity.provider == "okta"
        assert cfg.bindings.identity.config == {"tenant": "example.okta.com"}
        assert cfg.bindings.identity.role_mappings["warehouse clerk"] == ["okta-clerks"]
        assert len(cfg.bindings.identity.role_mappings["warehouse manager"]) == 2


class TestKeyedBindings:
    """compute / channels are keyed-by-name: source declares contract
    or channel names; deploy config maps each to a provider."""

    def test_compute_keyed_by_contract_name(self):
        d = _minimal_config_dict()
        d["bindings"]["compute"] = {
            "ai-agent": {
                "provider": "anthropic",
                "config": {"model": "claude-haiku-4-5-20251001"},
            },
        }
        cfg = parse_deploy_config(d)
        binding = cfg.bindings.compute["ai-agent"]
        assert binding.provider == "anthropic"
        assert binding.config["model"] == "claude-haiku-4-5-20251001"

    def test_channels_keyed_by_channel_name(self):
        d = _minimal_config_dict()
        d["bindings"]["channels"] = {
            "supplier alerts": {
                "provider": "slack",
                "config": {"target": "supplier-team-prod"},
            },
            "order webhook": {
                "provider": "webhook",
                "config": {"target": "https://hooks.example.com/orders"},
            },
        }
        cfg = parse_deploy_config(d)
        assert cfg.bindings.channels["supplier alerts"].provider == "slack"
        assert cfg.bindings.channels["supplier alerts"].config["target"] == "supplier-team-prod"
        assert cfg.bindings.channels["order webhook"].provider == "webhook"

    def test_compute_entry_provider_required(self):
        d = _minimal_config_dict()
        d["bindings"]["compute"] = {"ai-agent": {"config": {}}}
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "provider" in str(exc.value).lower()
        assert "ai-agent" in str(exc.value)

    def test_channel_entry_provider_required(self):
        d = _minimal_config_dict()
        d["bindings"]["channels"] = {"my channel": {"config": {}}}
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "provider" in str(exc.value).lower()
        assert "my channel" in str(exc.value)


class TestUnknownKeys:
    """Reject typos and accidents loudly. v0.9 has a fixed top-level
    schema; future versions may add keys but unknown ones in 0.9.x
    indicate a mistake."""

    def test_unknown_top_level_key_raises(self):
        d = _minimal_config_dict()
        d["aws_region"] = "us-east-1"
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "aws_region" in str(exc.value)

    def test_unknown_binding_category_raises(self):
        d = _minimal_config_dict()
        d["bindings"]["secrets"] = {"provider": "vault"}
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config(d)
        assert "secrets" in str(exc.value)


# ── Round-trip via JSON file ──

class TestJsonRoundtrip:
    def test_parse_from_json_string(self):
        cfg_dict = _minimal_config_dict()
        cfg = parse_deploy_config(json.dumps(cfg_dict))
        assert cfg.version == "0.9.0"

    def test_parse_from_json_file(self, tmp_path):
        cfg_dict = _minimal_config_dict()
        path = tmp_path / "app.deploy.json"
        path.write_text(json.dumps(cfg_dict), encoding="utf-8")
        cfg = parse_deploy_config(path.read_text(encoding="utf-8"))
        assert cfg.version == "0.9.0"

    def test_invalid_json_raises_helpful_error(self):
        with pytest.raises(DeployConfigError) as exc:
            parse_deploy_config('{"version": "0.9.0", invalid')
        assert "json" in str(exc.value).lower() or "parse" in str(exc.value).lower()


# ── Real-world example from BRD §7.5 ──

class TestBRDExample:
    """The BRD's agent_chatbot.deploy.json example must parse cleanly."""

    def test_brd_example_parses(self):
        d = {
            "version": "0.9.0",
            "bindings": {
                "identity": {"provider": "stub", "config": {}, "role_mappings": {}},
                "storage": {"provider": "sqlite", "config": {}},
                "presentation": {"provider": "default", "config": {}},
                "compute": {
                    "ai-agent": {
                        "provider": "anthropic",
                        "config": {
                            "model": "claude-haiku-4-5-20251001",
                            "api_key": "${ANTHROPIC_API_KEY}",
                        },
                    },
                },
                "channels": {},
            },
            "runtime": {},
        }
        cfg = parse_deploy_config(d)
        assert cfg.bindings.compute["ai-agent"].config["model"] == "claude-haiku-4-5-20251001"
        # Env-var interpolation syntax preserved verbatim — runtime
        # resolves at provider construction time, not at parse time.
        assert "${ANTHROPIC_API_KEY}" in cfg.bindings.compute["ai-agent"].config["api_key"]


# ── Empty defaults ──

class TestDefaults:
    """Categories the app doesn't use have empty objects per BRD §7.4."""

    def test_empty_compute_object_valid(self):
        d = _minimal_config_dict()
        d["bindings"]["compute"] = {}
        cfg = parse_deploy_config(d)
        assert cfg.bindings.compute == {}

    def test_empty_channels_object_valid(self):
        d = _minimal_config_dict()
        d["bindings"]["channels"] = {}
        cfg = parse_deploy_config(d)
        assert cfg.bindings.channels == {}
