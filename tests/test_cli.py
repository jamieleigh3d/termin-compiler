"""Tests for the Termin CLI (termin compile, termin serve) and backend discovery."""

import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from termin.cli import main, _ir_json_default, _simplify_props, _sha256, _generate_deploy_template
from termin.backend import Backend, discover_backends, get_backend
from termin.backends.runtime import RuntimeBackend


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

    def test_compile_to_legacy_py(self, runner, tmp_workdir):
        """Compile with -o app.py triggers legacy mode."""
        out = tmp_workdir / "app.py"
        r = runner.invoke(main, ["compile", str(HELLO_TERMIN), "-o", str(out)])
        assert r.exit_code == 0, r.output
        assert out.exists()
        code = out.read_text()
        assert "create_termin_app" in code
        # Should also produce companion .json
        assert out.with_suffix(".json").exists()

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
        """App with LLM Computes should auto-generate deploy config with ai_provider."""
        r = runner.invoke(main, ["compile", str(AGENT_SIMPLE)])
        assert r.exit_code == 0, r.output
        deploy_files = list(tmp_workdir.glob("*.deploy.json"))
        assert len(deploy_files) == 1
        config = json.loads(deploy_files[0].read_text())
        assert "ai_provider" in config


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
        ir_dict = {"auth": {"provider": "stub"}}
        channels = [{
            "name": {"display": "test channel", "snake": "test_channel"},
            "direction": "OUTBOUND",
            "delivery": "RELIABLE",
            "actions": [],
        }]
        result = _generate_deploy_template(ir_dict, channels)
        assert "channels" in result
        assert "test channel" in result["channels"]
        assert result["channels"]["test channel"]["protocol"] == "http"


# ── Backend protocol and discovery ──

class TestBackendProtocol:
    def test_runtime_backend_is_backend(self):
        """RuntimeBackend satisfies the Backend protocol."""
        assert isinstance(RuntimeBackend(), Backend)

    def test_runtime_backend_name(self):
        assert RuntimeBackend.name == "runtime"

    def test_runtime_backend_generate(self):
        """Generate produces valid Python with create_termin_app."""
        from termin.peg_parser import parse_peg as parse
        from termin.analyzer import analyze
        from termin.lower import lower
        source = HELLO_TERMIN.read_text()
        program, errors = parse(source)
        assert errors.ok
        result = analyze(program)
        assert result.ok
        spec = lower(program)
        backend = RuntimeBackend()
        code = backend.generate(spec, source_file="hello.termin")
        assert "create_termin_app" in code
        assert "hello.termin" in code
        # Should store IR JSON for companion file
        assert hasattr(backend, '_ir_json')
        ir = json.loads(backend._ir_json)
        assert ir["name"] is not None

    def test_runtime_backend_dependencies(self):
        deps = RuntimeBackend().required_dependencies()
        assert "fastapi>=0.100.0" in deps
        assert "termin-runtime" in deps

    def test_discover_backends(self):
        """discover_backends() runs without error (may find 0 or more)."""
        result = discover_backends()
        assert isinstance(result, dict)

    def test_get_backend_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            get_backend("nonexistent_backend_xyz")
