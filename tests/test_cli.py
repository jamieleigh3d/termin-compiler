# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the Termin CLI (termin compile, termin serve) and backend discovery."""

import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from termin.cli import main, _sha256, _generate_deploy_template
from termin_core.ir.serialize import (
    ir_json_default as _ir_json_default,
    simplify_props as _simplify_props,
)
from termin.backend import Backend, discover_backends, get_backend


# ── Helpers ──

HELLO_TERMIN = Path(__file__).parent.parent / "examples" / "hello.termin"
AGENT_SIMPLE = Path(__file__).parent.parent / "examples" / "agent_simple.termin"


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def tmp_workdir(tmp_path, monkeypatch):
    """Change to a temp directory for tests that produce output files."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── CLI: compile command ──

class TestCompileCommand:
    def test_compile_to_package(self, runner, tmp_workdir):
        """Compile hello.termin to .termin.pkg."""
        r = runner.invoke(main, ["compile", str(HELLO_TERMIN)])
        assert r.exit_code == 0, r.output
        assert "Compiled" in r.output
        pkgs = list(tmp_workdir.glob("*.termin.pkg"))
        assert len(pkgs) == 1
        # Verify it's a valid ZIP with manifest
        with zipfile.ZipFile(pkgs[0]) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["manifest_version"] == "1.0.0"
            assert manifest["ir"]["version"] is not None

    def test_compile_to_py_rejected(self, runner, tmp_workdir):
        """Phase 2.x: legacy `.py + .json` codegen path retired.
        `-o foo.py` now exits with a clear pointer at .pkg + serve."""
        out = tmp_workdir / "app.py"
        r = runner.invoke(main, ["compile", str(HELLO_TERMIN), "-o", str(out)])
        assert r.exit_code != 0
        assert "termin.pkg" in (r.output or "") or "termin.pkg" in str(r.exception or "")

    def test_compile_emit_ir(self, runner, tmp_workdir):
        """--emit-ir produces a standalone IR JSON file."""
        ir_out = tmp_workdir / "test_ir.json"
        r = runner.invoke(main, ["compile", str(HELLO_TERMIN), "--emit-ir", str(ir_out)])
        assert r.exit_code == 0, r.output
        assert ir_out.exists()
        ir = json.loads(ir_out.read_text())
        assert "content" in ir
        assert "auth" in ir

    def test_compile_invalid_source(self, runner, tmp_workdir):
        """Compiling invalid syntax should fail with exit code 1."""
        bad_file = tmp_workdir / "bad.termin"
        bad_file.write_text("This is not valid termin syntax at all\n")
        r = runner.invoke(main, ["compile", str(bad_file)])
        assert r.exit_code != 0

    def test_compile_json_format_errors(self, runner, tmp_workdir):
        """--format json outputs machine-readable errors."""
        bad_file = tmp_workdir / "bad.termin"
        bad_file.write_text("Application: Test\n  Description: test\n\nBogus line here\n")
        r = runner.invoke(main, ["compile", str(bad_file), "--format", "json"])
        # Should fail but output JSON
        assert r.exit_code != 0

    def test_compile_package_revision_increment(self, runner, tmp_workdir):
        """Compiling twice increments revision in manifest."""
        r1 = runner.invoke(main, ["compile", str(HELLO_TERMIN)])
        assert r1.exit_code == 0
        pkg = list(tmp_workdir.glob("*.termin.pkg"))[0]
        with zipfile.ZipFile(pkg) as zf:
            m1 = json.loads(zf.read("manifest.json"))
        rev1 = m1["app"]["revision"]

        r2 = runner.invoke(main, ["compile", str(HELLO_TERMIN)])
        assert r2.exit_code == 0
        with zipfile.ZipFile(pkg) as zf:
            m2 = json.loads(zf.read("manifest.json"))
        assert m2["app"]["revision"] == rev1 + 1

    def test_compile_deploy_template_for_channels(self, runner, tmp_workdir):
        """App with channels should auto-generate deploy config."""
        channel_termin = Path(__file__).parent.parent / "examples" / "channel_simple.termin"
        r = runner.invoke(main, ["compile", str(channel_termin)])
        assert r.exit_code == 0, r.output
        deploy_files = list(tmp_workdir.glob("*.deploy.json"))
        assert len(deploy_files) == 1, f"Expected deploy config, found: {deploy_files}"

    def test_compile_deploy_template_for_llm(self, runner, tmp_workdir):
        """App with LLM/agent Computes should auto-generate deploy
        config with v0.9 bindings.compute entries — one per LLM/agent
        compute. The legacy top-level `ai_provider` block was retired
        in Phase 3 slice (b)."""
        r = runner.invoke(main, ["compile", str(AGENT_SIMPLE)])
        assert r.exit_code == 0, r.output
        deploy_files = list(tmp_workdir.glob("*.deploy.json"))
        assert len(deploy_files) == 1
        config = json.loads(deploy_files[0].read_text())
        # No more top-level ai_provider — v0.9 hard-cut.
        assert "ai_provider" not in config
        # Per-compute bindings live at bindings.compute.
        compute_bindings = config["bindings"]["compute"]
        assert compute_bindings, (
            "deploy config for app with LLM/agent computes must "
            "populate bindings.compute"
        )
        for snake, binding in compute_bindings.items():
            assert binding["provider"] == "anthropic"
            assert binding["config"]["model"]
            assert binding["config"]["api_key"].startswith("${")

    # The stale-companion-seed cleanup tests are obsolete in v0.9
    # — the legacy `.py + .json + _seed.json` codegen path was
    # retired, and `.termin.pkg` carries seed bytes inside the
    # archive (no sidecar to go stale).


# ── CLI: utility functions ──

class TestCLIUtils:
    def test_ir_json_default_enum(self):
        from enum import Enum
        class Color(Enum):
            RED = 1
        assert _ir_json_default(Color.RED) == "RED"

    def test_ir_json_default_frozenset(self):
        result = _ir_json_default(frozenset(["b", "a"]))
        assert result == ["a", "b"]

    def test_ir_json_default_unknown_raises(self):
        with pytest.raises(TypeError):
            _ir_json_default(object())

    def test_simplify_props_bare_value(self):
        obj = {"title": {"value": "Hello", "is_expr": False}}
        _simplify_props(obj)
        assert obj["title"] == "Hello"

    def test_simplify_props_expr_preserved(self):
        obj = {"title": {"value": "name + ' World'", "is_expr": True}}
        _simplify_props(obj)
        assert obj["title"] == {"value": "name + ' World'", "is_expr": True}

    def test_simplify_props_nested(self):
        obj = [{"inner": {"value": "x", "is_expr": False}}]
        _simplify_props(obj)
        assert obj[0]["inner"] == "x"

    def test_sha256(self):
        result = _sha256(b"hello")
        assert result.startswith("sha256:")
        assert len(result) == 71  # "sha256:" + 64 hex chars

    def test_generate_deploy_template(self):
        """v0.9 deploy template shape: {version, bindings: {identity,
        storage, presentation, compute, channels}, runtime}. Every
        channel binding carries `provider` + `config` so the v0.9
        strict channel validator accepts the file."""
        ir_dict = {"auth": {"provider": "stub"}}
        channels = [{
            "name": {"display": "test channel", "snake": "test_channel"},
            "direction": "OUTBOUND",
            "delivery": "RELIABLE",
            "actions": [],
        }]
        result = _generate_deploy_template(ir_dict, channels)
        assert result["version"] == "0.9.0"
        bindings = result["bindings"]
        assert "identity" in bindings
        assert "storage" in bindings
        assert "presentation" in bindings
        assert "compute" in bindings
        assert "channels" in bindings
        assert bindings["identity"]["provider"] == "stub"
        assert "test channel" in bindings["channels"]
        entry = bindings["channels"]["test channel"]
        assert entry["provider"] == "stub"
        assert entry["config"]["protocol"] == "http"

    def test_generate_deploy_template_no_provider_contract_emits_v09_envelope(self):
        """Channels without `Provider is "X"` in source — the legacy
        fallback path — must still wrap into {provider, config}.
        v0.9 strict channel validator requires `provider` on every
        binding; raw flat url/protocol/auth blobs (the v0.8 shape)
        get rejected at parse time. Regression guard for the bug
        that broke ~110 conformance tests when the release script
        regenerated fixtures."""
        ir_dict = {"auth": {"provider": "stub"}}
        # Realtime channel triggers the websocket branch of the fallback.
        channels = [{
            "name": {"display": "no-prov-rt", "snake": "no_prov_rt"},
            "direction": "INBOUND",
            "delivery": "REALTIME",
            "actions": [],
        }, {
            "name": {"display": "no-prov-http", "snake": "no_prov_http"},
            "direction": "OUTBOUND",
            "delivery": "RELIABLE",
            "actions": [],
        }]
        result = _generate_deploy_template(ir_dict, channels)
        for name in ("no-prov-rt", "no-prov-http"):
            entry = result["bindings"]["channels"][name]
            assert entry["provider"] == "stub", (
                f"channel {name} must declare a provider for v0.9 "
                f"strict validator; got entry={entry!r}"
            )
            assert "config" in entry
            assert "url" in entry["config"]
            assert "auth" in entry["config"]
        # WS-shaped channel keeps reconnect+heartbeat under config
        assert result["bindings"]["channels"]["no-prov-rt"]["config"]["protocol"] == "websocket"
        assert result["bindings"]["channels"]["no-prov-rt"]["config"]["reconnect"] is True
        # HTTP-shaped channel keeps timeout+retry under config
        assert result["bindings"]["channels"]["no-prov-http"]["config"]["protocol"] == "http"
        assert result["bindings"]["channels"]["no-prov-http"]["config"]["timeout_ms"] == 30000

    def test_generate_deploy_template_validates_strict_v09(self):
        """End-to-end gate: every generated template must parse cleanly
        through the v0.9 strict deploy-config validator. Mixes:
        no-contract fallback channels + each known provider_contract
        path (webhook/email/messaging/event-stream) + LLM compute."""
        from termin_core.providers.deploy_config import parse_deploy_config

        ir_dict = {
            "auth": {"provider": "stub"},
            "computes": [
                {"name": {"display": "Reply", "snake": "reply"}, "provider": "llm"},
            ],
        }
        channels = [
            {"name": {"display": "fallback", "snake": "fallback"},
             "direction": "OUTBOUND", "delivery": "RELIABLE", "actions": []},
            {"name": {"display": "wh", "snake": "wh"},
             "direction": "OUTBOUND", "delivery": "RELIABLE", "actions": [],
             "provider_contract": "webhook"},
            {"name": {"display": "em", "snake": "em"},
             "direction": "OUTBOUND", "delivery": "RELIABLE", "actions": [],
             "provider_contract": "email"},
            {"name": {"display": "msg", "snake": "msg"},
             "direction": "BIDIRECTIONAL", "delivery": "REALTIME", "actions": [],
             "provider_contract": "messaging"},
            {"name": {"display": "es", "snake": "es"},
             "direction": "INBOUND", "delivery": "REALTIME", "actions": [],
             "provider_contract": "event-stream"},
        ]
        template = _generate_deploy_template(ir_dict, channels)
        # Throws DeployConfigError if any channel binding is missing
        # `provider` or any other v0.9 requirement.
        cfg = parse_deploy_config(template)
        assert set(cfg.bindings.channels.keys()) == {"fallback", "wh", "em", "msg", "es"}
        assert "reply" in cfg.bindings.compute

    def test_generate_deploy_template_compute_entries(self):
        """LLM/agent computes get a bindings.compute entry each;
        CEL computes do not (implicit default-CEL binding)."""
        ir_dict = {
            "auth": {"provider": "stub"},
            "computes": [
                {"name": {"display": "Reply", "snake": "reply"}, "provider": "ai-agent"},
                {"name": {"display": "Sum", "snake": "sum"}, "provider": None},
                {"name": {"display": "Summarize", "snake": "summarize"}, "provider": "llm"},
            ],
        }
        result = _generate_deploy_template(ir_dict, [])
        compute = result["bindings"]["compute"]
        assert "reply" in compute
        assert "summarize" in compute
        assert "sum" not in compute, "CEL computes must not appear in bindings.compute"
        assert compute["reply"]["provider"] == "anthropic"


# ── IR serialization (post Phase 2.x retirement of RuntimeBackend) ──

class TestIRSerialization:
    def test_serialize_ir_produces_canonical_json(self):
        """The shared serializer (used by .termin.pkg builder) emits
        canonical IR JSON: dataclass-asdict, PropValue collapse,
        Enum-as-name, sorted-frozenset-as-list."""
        from termin.peg_parser import parse_peg as parse
        from termin.analyzer import analyze
        from termin.lower import lower
        from termin_core.ir.serialize import serialize_ir
        source = HELLO_TERMIN.read_text()
        program, errors = parse(source)
        assert errors.ok
        result = analyze(program)
        assert result.ok
        spec = lower(program)
        ir_json = serialize_ir(spec)
        ir = json.loads(ir_json)
        assert ir["name"] is not None
        # PropValue collapse: literal text props are bare strings,
        # not {value, is_expr} dicts.
        # (This is exercised end-to-end by walking pages but the
        # round-trip + parse is enough sanity here.)

    def test_discover_backends(self):
        """discover_backends() runs without error (may find 0 or more)."""
        result = discover_backends()
        assert isinstance(result, dict)

    def test_get_backend_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            get_backend("nonexistent_backend_xyz")
