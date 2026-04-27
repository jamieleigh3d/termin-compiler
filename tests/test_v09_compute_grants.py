# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 3 slice (c): full access-grant grammar.

Covers:
  - PEG / parser / classifier wiring for `Reads`, `Sends to`, `Emits`,
    and `Invokes` lines on Compute blocks.
  - AST + IR field population.
  - Analyzer rules: TERMIN-S044 (Accesses ∩ Reads = ∅),
    TERMIN-S045/S046/S047 (resolution checks).
  - ToolSurface construction at app startup from ComputeSpec.
  - Tool gate widening: Reads grants content.{query,read} only,
    not content.{create,update,delete} or state.transition.
"""

from __future__ import annotations

import pytest

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower


def _check(src):
    prog, _ = parse(src)
    return analyze(prog), prog


_BASE_SOURCE = '''Application: Slice C Test
  Description: full access-grant grammar coverage

Identity:
  Scopes are "x.write", "x.read"
  An "agent" has "x.write"

Content called "orders":
  Each order has a name which is text, required
  Anyone with "x.write" can view, create, update, or delete orders

Content called "products":
  Each product has a name which is text, required
  Anyone with "x.read" can view products

Content called "customers":
  Each customer has a name which is text, required
  Anyone with "x.read" can view customers

Compute called "shipping":
  Provider is "ai-agent"
  Accesses orders
  Reads products
  Reads customers
  Directive is ```be helpful```
  Objective is ```do work```
  Anyone with "x.write" can execute this
'''


# ── Grammar / AST / IR ──


class TestGrammar:
    def test_reads_parses(self):
        result, prog = _check(_BASE_SOURCE)
        assert result.ok, [str(e) for e in result.errors]
        comp = prog.computes[0]
        assert "products" in comp.reads
        assert "customers" in comp.reads

    def test_accesses_unchanged(self):
        result, prog = _check(_BASE_SOURCE)
        assert result.ok
        assert prog.computes[0].accesses == ["orders"]

    def test_sends_to_parses(self):
        src = _BASE_SOURCE.replace(
            "Reads customers\n",
            'Reads customers\n  Sends to "supplier alerts" channel\n',
        )
        # need to declare the channel
        src = src.replace(
            'Content called "customers":',
            'Channel called "supplier alerts":\n'
            '  Carries orders\n'
            '  Direction: outbound\n'
            '  Delivery: reliable\n'
            '  Requires "x.write" to send\n\n'
            'Content called "customers":'
        )
        prog, _ = parse(src)
        comp = prog.computes[0]
        assert "supplier alerts" in comp.sends_to

    def test_emits_parses(self):
        src = _BASE_SOURCE.replace(
            "Reads customers\n",
            'Reads customers\n  Emits "order.placed"\n',
        )
        prog, _ = parse(src)
        comp = prog.computes[0]
        assert "order.placed" in comp.emits

    def test_invokes_parses(self):
        src = _BASE_SOURCE + '''
Compute called "audit":
  Provider is "ai-agent"
  Accesses orders
  Directive is ```a```
  Objective is ```b```
  Anyone with "x.write" can execute this
'''
        # Make the first compute Invoke "audit"
        src = src.replace(
            'Reads customers\n  Directive',
            'Reads customers\n  Invokes "audit"\n  Directive',
        )
        prog, _ = parse(src)
        comp = prog.computes[0]
        assert "audit" in comp.invokes


# ── Analyzer rules ──


class TestAnalyzerRules:
    def test_dual_grant_s044_overlap(self):
        """Same content in both Accesses and Reads → TERMIN-S044."""
        src = _BASE_SOURCE.replace("Reads products", "Reads orders")
        result, _ = _check(src)
        s044 = [e for e in result.errors if "TERMIN-S044" in str(e)]
        assert len(s044) == 1, [str(e) for e in result.errors]
        assert "orders" in str(s044[0])

    def test_dual_grant_s044_disjoint_passes(self):
        """Disjoint Accesses and Reads → no S044 error."""
        result, _ = _check(_BASE_SOURCE)
        s044 = [e for e in result.errors if "TERMIN-S044" in str(e)]
        assert s044 == [], [str(e) for e in result.errors]

    def test_reads_undefined_content_s045(self):
        """Reads on undefined content → TERMIN-S045."""
        src = _BASE_SOURCE.replace("Reads products", "Reads nonexistent_thing")
        result, _ = _check(src)
        s045 = [e for e in result.errors if "TERMIN-S045" in str(e)]
        assert len(s045) >= 1
        assert "nonexistent_thing" in str(s045[0])

    def test_sends_to_undefined_channel_s046(self):
        src = _BASE_SOURCE.replace(
            "Reads customers\n",
            'Reads customers\n  Sends to "nonexistent" channel\n',
        )
        result, _ = _check(src)
        s046 = [e for e in result.errors if "TERMIN-S046" in str(e)]
        assert len(s046) >= 1

    def test_invokes_undefined_compute_s047(self):
        src = _BASE_SOURCE.replace(
            "Reads customers\n",
            'Reads customers\n  Invokes "ghost"\n',
        )
        result, _ = _check(src)
        s047 = [e for e in result.errors if "TERMIN-S047" in str(e)]
        assert len(s047) >= 1


# ── IR lowering ──


class TestIRLowering:
    def test_compute_spec_carries_reads(self):
        result, prog = _check(_BASE_SOURCE)
        assert result.ok
        spec = lower(prog)
        comp = spec.computes[0]
        assert "products" in comp.reads
        assert "customers" in comp.reads

    def test_compute_spec_empty_when_grant_absent(self):
        """Computes without any of the new grants get empty tuples."""
        # Remove Reads from base source
        src = _BASE_SOURCE.replace("  Reads products\n  Reads customers\n", "")
        result, prog = _check(src)
        assert result.ok
        spec = lower(prog)
        comp = spec.computes[0]
        assert comp.reads == ()
        assert comp.sends_to == ()
        assert comp.emits == ()
        assert comp.invokes == ()


# ── ToolSurface construction at runtime ──


class TestToolSurfaceConstruction:
    def test_runtime_builds_tool_surface_from_ir(self):
        """create_termin_app populates ctx.compute_tool_surfaces from
        the IR's ComputeSpec list."""
        from termin_runtime import create_termin_app
        from termin.lower import lower
        import json

        result, prog = _check(_BASE_SOURCE)
        assert result.ok
        spec = lower(prog)
        # Manually serialize to IR JSON (the same path the package
        # builder uses).
        from termin.ir_serialize import serialize_ir
        ir_json = serialize_ir(spec)

        deploy = {
            "version": "0.1.0",
            "bindings": {
                "identity": {"provider": "stub", "config": {}},
                "storage": {"provider": "sqlite", "config": {}},
                "presentation": {"provider": "default", "config": {}},
                "compute": {
                    "shipping": {
                        "provider": "stub",
                        "config": {
                            "default_script": {
                                "final_outcome": "success",
                                "tool_calls": [],
                            }
                        },
                    }
                },
                "channels": {},
            },
            "runtime": {},
        }
        app = create_termin_app(ir_json, deploy_config=deploy, strict_channels=False)
        # Pull the RuntimeContext from the app's state.
        ctx = app.state.ctx
        assert "shipping" in ctx.compute_tool_surfaces
        surface = ctx.compute_tool_surfaces["shipping"]
        assert "orders" in surface.content_rw
        assert "products" in surface.content_ro
        assert "customers" in surface.content_ro
        # Read-side: both
        assert surface.permits_content_read("orders")
        assert surface.permits_content_read("products")
        # Write-side: only Accesses
        assert surface.permits_content_write("orders")
        assert not surface.permits_content_write("products")
        # State: only Accesses
        assert surface.permits_state_transition("orders")
        assert not surface.permits_state_transition("products")


# ── ToolSurface in compute_contract ──


class TestToolSurfaceContract:
    def test_full_grant_set(self):
        from termin_runtime.providers import ToolSurface
        s = ToolSurface(
            content_rw=("orders",),
            content_ro=("products",),
            channels=("alerts",),
            events=("order.placed",),
            computes=("audit",),
        )
        assert s.permits_content_read("orders")
        assert s.permits_content_read("products")
        assert s.permits_content_write("orders")
        assert not s.permits_content_write("products")
        assert s.permits_channel("alerts")
        assert not s.permits_channel("other")
        assert s.permits_event("order.placed")
        assert s.permits_compute("audit")
