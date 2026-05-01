# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 4 — Channel provider compiler tests.

Tests that the compiler correctly:
- Parses `Provider is "X"` in Channel blocks
- Parses `Failure mode is "X"` in Channel blocks
- Populates provider_contract / failure_mode in the IR
- Emits SemanticError for outbound channels without a provider
- Emits SemanticError for unknown provider contracts
- Emits SemanticError for invalid failure modes
- Validates action vocabulary against declared contract

TDD: written to drive the compiler changes in Slice 4b.
"""

import pytest

from termin.peg_parser import parse_peg
from termin.analyzer import analyze
from termin.lower import lower
from termin.errors import SemanticError


# ── Fixtures ──

def _parse_and_lower(source: str):
    """Parse, analyze, and lower a .termin source fragment.

    Returns (spec, analysis_errors). Caller decides whether to assert
    on errors or on the IR.
    """
    prog, parse_errors = parse_peg(source)
    assert parse_errors.ok, f"Parse failed: {parse_errors.format()}"
    analysis_errors = analyze(prog)
    if not analysis_errors.ok:
        return None, analysis_errors
    spec = lower(prog)
    return spec, analysis_errors


_BASE = """
Application: Test App
  Description: Tests
Id: 00000000-0000-0000-0000-000000000001

Identity:
  Scopes are "admin"
  A "admin" has "admin"

Content called "items":
  Each item has a name which is text
  Anyone with "admin" can view items
  Anyone with "admin" can create items

"""

_WEBHOOK_CHANNEL = _BASE + """
Channel called "alerts":
  Provider is "webhook"
  Direction: outbound
  Carries items
  Requires "admin" to send
"""

_EMAIL_CHANNEL = _BASE + """
Channel called "digests":
  Provider is "email"
  Direction: outbound
  Carries items
  Requires "admin" to send
"""

_MESSAGING_CHANNEL = _BASE + """
Channel called "team chat":
  Provider is "messaging"
  Direction: outbound
  Carries items
  Requires "admin" to send
"""

_INTERNAL_CHANNEL = _BASE + """
Channel called "live feed":
  Direction: internal
  Carries items
"""

_OUTBOUND_NO_PROVIDER = _BASE + """
Channel called "no-provider":
  Direction: outbound
  Carries items
  Requires "admin" to send
"""

_BIDIRECTIONAL_NO_PROVIDER = _BASE + """
Channel called "bidi":
  Direction: bidirectional
  Carries items
  Requires "admin" to send
  Requires "admin" to receive
"""

_BAD_CONTRACT = _BASE + """
Channel called "bad":
  Provider is "carrier-pigeon"
  Direction: outbound
  Carries items
  Requires "admin" to send
"""

_FAILURE_MODE = _BASE + """
Channel called "strict alerts":
  Provider is "webhook"
  Direction: outbound
  Carries items
  Failure mode is surface-as-error
  Requires "admin" to send
"""

_BAD_FAILURE_MODE = _BASE + """
Channel called "bad mode":
  Provider is "webhook"
  Direction: outbound
  Carries items
  Failure mode is just-drop-it
  Requires "admin" to send
"""

_INBOUND_NO_PROVIDER = _BASE + """
Channel called "inbound":
  Direction: inbound
  Carries items
  Requires "admin" to receive
"""


# ── Parser: provider_contract populates IR field ──

class TestChannelProviderContractParsed:
    def test_webhook_contract_in_ir(self):
        spec, errors = _parse_and_lower(_WEBHOOK_CHANNEL)
        assert errors.ok
        ch = next(c for c in spec.channels if c.name.display == "alerts")
        assert ch.provider_contract == "webhook"

    def test_email_contract_in_ir(self):
        spec, errors = _parse_and_lower(_EMAIL_CHANNEL)
        assert errors.ok
        ch = next(c for c in spec.channels if c.name.display == "digests")
        assert ch.provider_contract == "email"

    def test_messaging_contract_in_ir(self):
        spec, errors = _parse_and_lower(_MESSAGING_CHANNEL)
        assert errors.ok
        ch = next(c for c in spec.channels if c.name.display == "team chat")
        assert ch.provider_contract == "messaging"

    def test_internal_channel_no_provider_required(self):
        spec, errors = _parse_and_lower(_INTERNAL_CHANNEL)
        assert errors.ok
        ch = next(c for c in spec.channels if c.name.display == "live feed")
        assert ch.provider_contract is None

    def test_no_provider_defaults_to_none(self):
        # Inbound channels are exempt from the provider requirement
        spec, errors = _parse_and_lower(_INBOUND_NO_PROVIDER)
        assert errors.ok
        ch = next(c for c in spec.channels if c.name.display == "inbound")
        assert ch.provider_contract is None


# ── Parser: failure_mode populates IR field ──

class TestChannelFailureModeParsed:
    def test_default_failure_mode(self):
        spec, errors = _parse_and_lower(_WEBHOOK_CHANNEL)
        assert errors.ok
        ch = next(c for c in spec.channels if c.name.display == "alerts")
        assert ch.failure_mode == "log-and-drop"

    def test_explicit_failure_mode(self):
        spec, errors = _parse_and_lower(_FAILURE_MODE)
        assert errors.ok
        ch = next(c for c in spec.channels if c.name.display == "strict alerts")
        assert ch.failure_mode == "surface-as-error"


# ── Analyzer: SemanticError for missing provider ──

class TestOutboundRequiresProvider:
    def test_outbound_without_provider_is_error(self):
        _, errors = _parse_and_lower(_OUTBOUND_NO_PROVIDER)
        assert not errors.ok
        codes = [e.code for e in errors.errors]
        assert "TERMIN-S026" in codes

    def test_outbound_error_mentions_provider(self):
        _, errors = _parse_and_lower(_OUTBOUND_NO_PROVIDER)
        msg = next(
            e.message for e in errors.errors if e.code == "TERMIN-S026"
        )
        assert "Provider is" in msg or "provider" in msg.lower()

    def test_bidirectional_without_provider_is_error(self):
        _, errors = _parse_and_lower(_BIDIRECTIONAL_NO_PROVIDER)
        assert not errors.ok
        codes = [e.code for e in errors.errors]
        assert "TERMIN-S026" in codes

    def test_internal_without_provider_is_ok(self):
        _, errors = _parse_and_lower(_INTERNAL_CHANNEL)
        assert errors.ok

    def test_inbound_without_provider_is_ok(self):
        _, errors = _parse_and_lower(_INBOUND_NO_PROVIDER)
        assert errors.ok


# ── Analyzer: SemanticError for bad contract name ──

class TestUnknownProviderContract:
    def test_unknown_contract_is_error(self):
        _, errors = _parse_and_lower(_BAD_CONTRACT)
        assert not errors.ok
        codes = [e.code for e in errors.errors]
        assert "TERMIN-S027" in codes

    def test_error_mentions_contract_name(self):
        _, errors = _parse_and_lower(_BAD_CONTRACT)
        msg = next(
            e.message for e in errors.errors if e.code == "TERMIN-S027"
        )
        assert "carrier-pigeon" in msg


# ── Analyzer: SemanticError for bad failure mode ──

class TestInvalidFailureMode:
    def test_invalid_failure_mode_is_error(self):
        _, errors = _parse_and_lower(_BAD_FAILURE_MODE)
        assert not errors.ok
        codes = [e.code for e in errors.errors]
        assert "TERMIN-S028" in codes

    def test_valid_failure_modes_do_not_error(self):
        for mode in ("surface-as-error", "queue-and-retry", "log-and-drop"):
            src = _BASE + f"""
Channel called "ch":
  Provider is "webhook"
  Direction: outbound
  Carries items
  Failure mode is {mode}
  Requires "admin" to send
"""
            _, errors = _parse_and_lower(src)
            assert errors.ok, f"Mode '{mode}' should be valid but got: {errors.format()}"


# ── Analyzer: action vocabulary validation ──

class TestActionVocabularyValidation:
    def test_valid_messaging_action_no_error(self):
        src = _BASE + """
Channel called "msg":
  Provider is "messaging"
  Direction: outbound
  Requires "admin" to invoke

  Action called "Send a message alert":
    Takes payload which is text
    Requires "admin" to invoke
"""
        _, errors = _parse_and_lower(src)
        assert errors.ok, f"Expected no errors but got: {errors.format()}"

    def test_invalid_action_verb_for_messaging(self):
        src = _BASE + """
Channel called "msg":
  Provider is "messaging"
  Direction: outbound
  Requires "admin" to invoke

  Action called "Dispatch carrier pigeon":
    Takes payload which is text
    Requires "admin" to invoke
"""
        _, errors = _parse_and_lower(src)
        assert not errors.ok
        codes = [e.code for e in errors.errors]
        assert "TERMIN-S029" in codes

    def test_vocab_error_mentions_action_name(self):
        src = _BASE + """
Channel called "msg":
  Provider is "messaging"
  Direction: outbound
  Requires "admin" to invoke

  Action called "Dispatch carrier pigeon":
    Takes payload which is text
    Requires "admin" to invoke
"""
        _, errors = _parse_and_lower(src)
        msg = next(e.message for e in errors.errors if e.code == "TERMIN-S029")
        assert "Dispatch carrier pigeon" in msg or "messaging" in msg

    def test_webhook_any_action_name_is_valid(self):
        """webhook channels have no action vocab restriction — any name is valid."""
        for name in ("Post alert", "restart-service", "scale-up", "Invoke operation"):
            src = _BASE + f"""
Channel called "wh":
  Provider is "webhook"
  Direction: outbound
  Requires "admin" to invoke

  Action called "{name}":
    Takes payload which is text
    Requires "admin" to invoke
"""
            _, errors = _parse_and_lower(src)
            assert errors.ok, f"Expected webhook action '{name}' to be valid, got: {errors.format()}"

    def test_invalid_email_action_verb(self):
        """Email channels require actions to start with recognized email vocab prefixes."""
        src = _BASE + """
Channel called "mail":
  Provider is "email"
  Direction: outbound
  Requires "admin" to invoke

  Action called "Dispatch carrier pigeon":
    Takes payload which is text
    Requires "admin" to invoke
"""
        _, errors = _parse_and_lower(src)
        assert not errors.ok
        codes = [e.code for e in errors.errors]
        assert "TERMIN-S029" in codes

    def test_no_actions_on_data_channel_skips_vocab_check(self):
        # Data channels (Carries, no Actions) don't need action vocab validation
        _, errors = _parse_and_lower(_WEBHOOK_CHANNEL)
        assert errors.ok
