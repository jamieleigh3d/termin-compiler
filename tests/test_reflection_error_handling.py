"""Tests for Phase R6 (Reflection) and Phase R7 (Error Handling DSL)."""

import re
from termin.peg_parser import parse_peg as parse, _classify_line
from termin.analyzer import analyze
from termin.lower import lower
from termin.backends.fastapi import FastApiBackend


# ── Minimal valid base for compilation ──

VALID_BASE = """\
Application: Test App
  Description: Test application

Users authenticate with stub
Scopes are "read items", "write items", and "admin"

A "manager" has "read items", "write items", and "admin"
A "viewer" has "read items"

Content called "items":
  Each item has a name which is text, required
  Each item has a value which is currency
  Anyone with "read items" can view items
  Anyone with "write items" can create or update items

Content called "logs":
  Each log has a message which is text
  Anyone with "admin" can view logs

State for items called "item lifecycle":
  A item starts as "draft"
  A item can also be "active"
  A draft item can become active if the user has "write items"

Compute called "calculate total":
  Reduce: takes items, produces logs
  Anyone with "admin" can execute this

Channel called "item webhook":
  Carries items
  Direction: inbound
  Delivery: reliable
  Endpoint: /webhooks/items
  Requires "write items" to send

Boundary called "item processing":
  Contains items, logs
  Identity inherits from application
"""


# ════════════════════════════════════════════════════════════
# Phase R7: Error Handling DSL — Lexer Tests
# ════════════════════════════════════════════════════════════

class TestErrorHandlingLineClassification:
    """Verify error handling DSL lines classify to correct PEG rules."""

    def test_error_handler_line(self):
        assert _classify_line('On error from "order webhook":') == "error_from_line"

    def test_error_handler_with_where(self):
        assert _classify_line('On error from "order webhook" where [error.kind == "external"]:') == "error_from_line"

    def test_error_catch_all_line(self):
        assert _classify_line('On any error:') == "error_catch_all_line"

    def test_retry_line(self):
        assert _classify_line('Retry 3 times with backoff') == "error_retry_line"

    def test_retry_single_time(self):
        assert _classify_line('Retry 1 time') == "error_retry_line"

    def test_then_disable_line(self):
        assert _classify_line('Then disable channel') == "error_then_line"

    def test_then_escalate_line(self):
        assert _classify_line('Then escalate') == "error_then_line"

    def test_then_notify_line(self):
        assert _classify_line('Then notify "admin" with [error.message]') == "error_then_line"

    def test_then_create_line(self):
        assert _classify_line('Then create "alert" with [error.source]') == "error_then_line"

    def test_then_set_line(self):
        assert _classify_line('Then set [status = "disabled"]') == "error_then_line"


# ════════════════════════════════════════════════════════════
# Phase R7: Error Handling DSL — Parser Tests
# ════════════════════════════════════════════════════════════

class TestErrorHandlingParser:
    def test_parse_simple_error_handler(self):
        source = VALID_BASE + """
On error from "item webhook":
  Retry 3 times with backoff
  Then disable channel
  Log level: ERROR
"""
        program, errors = parse(source)
        assert errors.ok, f"Parse errors: {errors}"
        assert len(program.error_handlers) == 1
        eh = program.error_handlers[0]
        assert eh.source == "item webhook"
        assert not eh.is_catch_all
        assert len(eh.actions) >= 2  # retry + disable (+ optional separate log_level action)
        assert eh.actions[0].kind == "retry"
        assert eh.actions[0].retry_count == 3
        assert eh.actions[0].retry_backoff is True
        disable_action = next(a for a in eh.actions if a.kind == "disable")
        assert disable_action.target == "channel"

    def test_parse_error_handler_with_where(self):
        source = VALID_BASE + """
On error from "item webhook" where [error.kind == "external"]:
  Retry 2 times
  Then escalate
"""
        program, errors = parse(source)
        assert errors.ok, f"Parse errors: {errors}"
        assert len(program.error_handlers) == 1
        eh = program.error_handlers[0]
        assert eh.source == "item webhook"
        assert eh.condition_jexl == 'error.kind == "external"'
        assert len(eh.actions) == 2
        assert eh.actions[0].kind == "retry"
        assert eh.actions[0].retry_count == 2
        assert eh.actions[0].retry_backoff is False
        assert eh.actions[1].kind == "escalate"

    def test_parse_catch_all(self):
        source = VALID_BASE + """
On any error:
  Then escalate
  Log level: ERROR
"""
        program, errors = parse(source)
        assert errors.ok, f"Parse errors: {errors}"
        assert len(program.error_handlers) == 1
        eh = program.error_handlers[0]
        assert eh.is_catch_all is True
        assert eh.source == ""

    def test_parse_retry_with_max_delay(self):
        source = VALID_BASE + """
On error from "item webhook":
  Retry 3 times with backoff, maximum delay 30 seconds
  Then disable channel
"""
        program, errors = parse(source)
        assert errors.ok, f"Parse errors: {errors}"
        eh = program.error_handlers[0]
        assert eh.actions[0].retry_count == 3
        assert eh.actions[0].retry_backoff is True
        assert eh.actions[0].retry_max_delay == "30 seconds"

    def test_parse_then_notify(self):
        source = VALID_BASE + """
On error from "item webhook":
  Then notify "manager" with [error.message]
"""
        program, errors = parse(source)
        assert errors.ok, f"Parse errors: {errors}"
        eh = program.error_handlers[0]
        assert eh.actions[0].kind == "notify"
        assert eh.actions[0].target == "manager"
        assert eh.actions[0].jexl_expr == "error.message"

    def test_parse_multiple_handlers(self):
        source = VALID_BASE + """
On error from "item webhook":
  Retry 2 times
  Then escalate

On error from "calculate total":
  Then notify "manager" with [error.message]
  Log level: WARN

On any error:
  Then escalate
  Log level: ERROR
"""
        program, errors = parse(source)
        assert errors.ok, f"Parse errors: {errors}"
        assert len(program.error_handlers) == 3


# ════════════════════════════════════════════════════════════
# Phase R7: Error Handling DSL — Analyzer Tests
# ════════════════════════════════════════════════════════════

class TestErrorHandlingAnalyzer:
    def test_valid_error_handler_passes(self):
        source = VALID_BASE + """
On error from "item webhook":
  Retry 3 times
  Then disable channel
"""
        program, errors = parse(source)
        assert errors.ok
        result = analyze(program)
        assert result.ok, f"Analyzer errors: {result}"

    def test_undefined_source_fails(self):
        source = VALID_BASE + """
On error from "nonexistent channel":
  Then escalate
"""
        program, errors = parse(source)
        assert errors.ok
        result = analyze(program)
        assert not result.ok
        assert any("nonexistent channel" in str(e) for e in result.errors)

    def test_catch_all_passes(self):
        source = VALID_BASE + """
On any error:
  Then escalate
"""
        program, errors = parse(source)
        assert errors.ok
        result = analyze(program)
        assert result.ok


# ════════════════════════════════════════════════════════════
# Phase R7: Error Handling DSL — IR Tests
# ════════════════════════════════════════════════════════════

class TestErrorHandlingIR:
    def _lower(self, source):
        program, errors = parse(source)
        assert errors.ok, f"Parse errors: {errors}"
        result = analyze(program)
        assert result.ok, f"Analyzer errors: {result}"
        return lower(program)

    def test_error_handlers_lowered(self):
        source = VALID_BASE + """
On error from "item webhook":
  Retry 3 times with backoff
  Then disable channel
  Log level: ERROR
"""
        spec = self._lower(source)
        assert len(spec.error_handlers) == 1
        eh = spec.error_handlers[0]
        assert eh.source == "item webhook"
        assert len(eh.actions) >= 2
        assert eh.actions[0].kind == "retry"
        assert eh.actions[0].retry_count == 3
        assert eh.actions[0].retry_backoff is True
        disable_action = next(a for a in eh.actions if a.kind == "disable")
        assert disable_action is not None

    def test_catch_all_lowered(self):
        source = VALID_BASE + """
On any error:
  Then escalate
  Log level: ERROR
"""
        spec = self._lower(source)
        assert len(spec.error_handlers) == 1
        eh = spec.error_handlers[0]
        assert eh.is_catch_all is True
        assert eh.actions[0].kind == "escalate"

    def test_no_error_handlers(self):
        spec = self._lower(VALID_BASE)
        assert len(spec.error_handlers) == 0


# ════════════════════════════════════════════════════════════
# Phase R6: Reflection — Codegen Tests
# ════════════════════════════════════════════════════════════

class TestReflectionCodegen:
    def _compile_to_code(self, source):
        program, errors = parse(source)
        assert errors.ok, f"Parse errors: {errors}"
        result = analyze(program)
        assert result.ok, f"Analyzer errors: {result}"
        spec = lower(program)
        backend = FastApiBackend()
        return backend.generate(spec)

    def test_reflection_engine_generated(self):
        code = self._compile_to_code(VALID_BASE)
        assert "class ReflectionEngine" in code
        assert "reflection = ReflectionEngine(APP_SPEC_JSON)" in code

    def test_reflection_endpoints_generated(self):
        code = self._compile_to_code(VALID_BASE)
        assert "/api/reflect" in code
        assert "/api/reflect/content" in code
        assert "/api/reflect/compute" in code
        assert "/api/reflect/channels" in code
        assert "/api/reflect/identity" in code
        assert "/api/reflect/boundaries" in code

    def test_reflection_engine_methods(self):
        code = self._compile_to_code(VALID_BASE)
        assert "def content_schemas(self)" in code
        assert "def content_schema(self, name)" in code
        assert "def compute_functions(self)" in code
        assert "def compute_function(self, name)" in code
        assert "def channel_state(self, name)" in code
        assert "def channel_metrics(self, name)" in code
        assert "def identity_context(self, user)" in code
        assert "def boundary_info(self, name)" in code

    def test_expression_evaluator_registration(self):
        code = self._compile_to_code(VALID_BASE)
        assert "expr_eval.register_function('Content'" in code
        assert "expr_eval.register_function('Compute'" in code
        assert "expr_eval.register_function('Channel'" in code
        assert "expr_eval.register_function('Boundary'" in code
        assert "expr_eval.register_function('Identity'" in code

    def test_generated_code_is_valid_python(self):
        code = self._compile_to_code(VALID_BASE)
        compile(code, "<test>", "exec")

    def test_generated_code_with_error_handlers_is_valid_python(self):
        source = VALID_BASE + """
On error from "item webhook":
  Retry 3 times with backoff
  Then disable channel
  Log level: ERROR

On any error:
  Then escalate
  Log level: ERROR
"""
        code = self._compile_to_code(source)
        compile(code, "<test>", "exec")


# ════════════════════════════════════════════════════════════
# Phase R7: Error Handling — Codegen Tests
# ════════════════════════════════════════════════════════════

class TestErrorHandlingCodegen:
    def _compile_to_code(self, source):
        program, errors = parse(source)
        assert errors.ok, f"Parse errors: {errors}"
        result = analyze(program)
        assert result.ok, f"Analyzer errors: {result}"
        spec = lower(program)
        backend = FastApiBackend()
        return backend.generate(spec)

    def test_error_handlers_registered(self):
        source = VALID_BASE + """
On error from "item webhook":
  Retry 3 times with backoff
  Then disable channel
  Log level: ERROR
"""
        code = self._compile_to_code(source)
        assert "terminator.register_handler" in code
        assert '"item webhook"' in code

    def test_terminator_has_handle_error(self):
        code = self._compile_to_code(VALID_BASE)
        assert "def handle_error(self, error" in code

    def test_terminator_has_typed_handlers(self):
        code = self._compile_to_code(VALID_BASE)
        assert "_typed_handlers" in code

    def test_catch_all_handler_registered(self):
        source = VALID_BASE + """
On any error:
  Then escalate
  Log level: ERROR
"""
        code = self._compile_to_code(source)
        assert '"is_catch_all": True' in code

    def test_no_handlers_no_registration_block(self):
        code = self._compile_to_code(VALID_BASE)
        assert "Error Handler Registration" not in code
