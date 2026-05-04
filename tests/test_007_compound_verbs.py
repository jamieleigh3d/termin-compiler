# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for compound verb parsing in access rules (issue #007).

The PEG grammar's verb_phrase only handled 'create or update' as a compound.
All other compound patterns ('view or create', 'create, update, or delete', etc.)
silently dropped every verb after the first. This is a security bug: apps
declared multi-verb grants but only the first verb was enforced.

TDD: These tests are written RED before the fix.
"""

import pytest
from termin.peg_parser import parse_peg as parse
from termin_core.ir.types import Verb


# ── Parser: compound verb patterns ──

class TestCompoundVerbParsing:
    """Test that the parser preserves all verbs in compound access rules."""

    def _parse_access(self, verb_phrase: str):
        """Parse a minimal content block with the given verb phrase and return verbs list."""
        src = f'''Content called "items":
  Each item has a name which is text
  Anyone with "scope" can {verb_phrase} items'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        return program.contents[0].access_rules[0].verbs

    def test_single_view(self):
        verbs = self._parse_access("view")
        assert verbs == ["view"]

    def test_single_create(self):
        verbs = self._parse_access("create")
        assert verbs == ["create"]

    def test_single_update(self):
        verbs = self._parse_access("update")
        assert verbs == ["update"]

    def test_single_delete(self):
        verbs = self._parse_access("delete")
        assert verbs == ["delete"]

    def test_create_or_update(self):
        """Existing compound — should still work."""
        verbs = self._parse_access("create or update")
        assert sorted(verbs) == ["create", "update"] or verbs == ["create or update"]

    def test_view_or_create(self):
        """agent_chatbot.termin, channel_simple.termin: 'can view or create messages'"""
        verbs = self._parse_access("view or create")
        assert sorted(verbs) == ["create", "view"]

    def test_update_or_delete(self):
        """security_agent.termin: 'can update or delete findings'"""
        verbs = self._parse_access("update or delete")
        assert sorted(verbs) == ["delete", "update"]

    def test_create_update_or_delete(self):
        """hrportal.termin, channel_demo.termin: 'can create, update, or delete departments'"""
        verbs = self._parse_access("create, update, or delete")
        assert sorted(verbs) == ["create", "delete", "update"]

    def test_view_create_or_update(self):
        """agent_simple.termin: 'can view, create, or update completions'"""
        verbs = self._parse_access("view, create, or update")
        assert sorted(verbs) == ["create", "update", "view"]

    def test_view_create_update_or_delete(self):
        """channel_simple.termin: 'can view, create, update, or delete notes'"""
        verbs = self._parse_access("view, create, update, or delete")
        assert sorted(verbs) == ["create", "delete", "update", "view"]


class TestCompoundVerbsMultiWordContent:
    """Compound verbs must work even when content names are multi-word."""

    def _parse_access_rules(self, content_name: str, access_lines: list[str]):
        """Parse a content block with multiple access rules."""
        singular = content_name.rstrip("s") if content_name.endswith("s") else content_name
        fields = f"  Each {singular} has a name which is text\n"
        rules = "\n".join(f"  {line}" for line in access_lines)
        src = f'Content called "{content_name}":\n{fields}{rules}'
        program, errors = parse(src)
        assert errors.ok, errors.format()
        return program.contents[0].access_rules

    def test_view_or_create_multiword_content(self):
        """'can view or create stock levels' — must not confuse 'create stock' as a verb."""
        rules = self._parse_access_rules("stock levels", [
            'Anyone with "read" can view stock levels',
            'Anyone with "write" can view or create stock levels',
        ])
        assert sorted(rules[1].verbs) == ["create", "view"]

    def test_update_or_delete_multiword_content(self):
        rules = self._parse_access_rules("reorder alerts", [
            'Anyone with "admin" can update or delete reorder alerts',
        ])
        assert sorted(rules[0].verbs) == ["delete", "update"]


# ── Lowering: verbs → Verb enum ──

class TestVerbLowering:
    """Test that all parsed verbs are correctly lowered to Verb enum values."""

    def _compile_grants(self, access_lines: list[str]):
        """Parse and lower a content block, return access_grants from AppSpec."""
        from termin.lower import lower
        rules = "\n".join(f"  {line}" for line in access_lines)
        src = f'''Application: Test
  Description: Test

Identity:
  Scopes are "read", "write", and "admin"
  A "user" has "read" and "write"

Content called "items":
  Each item has a name which is text
{rules}'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        spec = lower(program)
        return spec.access_grants

    def test_lower_view_or_create(self):
        grants = self._compile_grants([
            'Anyone with "read" can view or create items',
        ])
        assert len(grants) == 1
        assert set(grants[0].verbs) == {Verb.VIEW, Verb.CREATE}

    def test_lower_create_update_or_delete(self):
        grants = self._compile_grants([
            'Anyone with "admin" can create, update, or delete items',
        ])
        assert len(grants) == 1
        assert set(grants[0].verbs) == {Verb.CREATE, Verb.UPDATE, Verb.DELETE}

    def test_lower_all_four_verbs(self):
        grants = self._compile_grants([
            'Anyone with "admin" can view, create, update, or delete items',
        ])
        assert len(grants) == 1
        assert set(grants[0].verbs) == {Verb.VIEW, Verb.CREATE, Verb.UPDATE, Verb.DELETE}

    def test_lower_no_empty_verbs(self):
        """No grant should ever have an empty verbs set (TERMIN-S031 safety net)."""
        grants = self._compile_grants([
            'Anyone with "read" can view items',
            'Anyone with "write" can create or update items',
            'Anyone with "admin" can delete items',
        ])
        for g in grants:
            assert len(g.verbs) > 0, f"Empty verbs for {g.content}/{g.scope}"

    def test_s031_rejects_unrecognized_verbs(self):
        """TERMIN-S031: lowering raises SemanticError for unrecognized verb strings."""
        from termin.lower import lower
        from termin.errors import SemanticError
        from termin.ast_nodes import AccessRule
        # Manually construct a program with a bad verb to trigger the safety net
        src = '''Application: Test
  Description: Test

Identity:
  Scopes are "read"
  A "user" has "read"

Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items'''
        program, errors = parse(src)
        assert errors.ok
        # Inject a bad verb into the parsed access rules
        program.contents[0].access_rules.append(
            AccessRule(scope="read", verbs=["explode"], line=99)
        )
        with pytest.raises(SemanticError, match="TERMIN-S031"):
            lower(program)


# ── Full example compilation: all affected fixtures ──

class TestAffectedExamples:
    """Verify every example that uses compound verbs compiles with correct grants."""

    def _load_and_check(self, name: str, expected_grants: dict):
        """Compile an example and verify specific grants have the right verbs."""
        from pathlib import Path
        from termin.lower import lower
        src = Path(f"examples/{name}.termin").read_text()
        program, errors = parse(src)
        assert errors.ok, f"{name}: {errors.format()}"
        spec = lower(program)
        grant_map = {(g.content, g.scope): set(g.verbs) for g in spec.access_grants}
        for (content, scope), expected_verbs in expected_grants.items():
            actual = grant_map.get((content, scope))
            assert actual is not None, f"{name}: no grant for {content}/{scope}"
            assert actual == expected_verbs, (
                f"{name}: {content}/{scope} expected {expected_verbs}, got {actual}"
            )

    def test_agent_chatbot_legacy_view_or_create(self):
        # v0.9.2 L11: the canonical agent_chatbot.termin no longer
        # uses the compound `view or create` form (it uses separate
        # `Anyone with X can view`/`can create`/`can append to`
        # lines). The compound form is preserved for testing on the
        # legacy v0.9.1-shape file.
        self._load_and_check("agent_chatbot_legacy", {
            ("messages", "chat.use"): {Verb.VIEW, Verb.CREATE},
        })

    def test_agent_simple_view_create_or_update(self):
        self._load_and_check("agent_simple", {
            ("completions", "agent.use"): {Verb.VIEW, Verb.CREATE, Verb.UPDATE},
        })

    def test_channel_simple_all_verbs(self):
        self._load_and_check("channel_simple", {
            ("notes", "messages.all"): {Verb.VIEW, Verb.CREATE, Verb.UPDATE, Verb.DELETE},
            ("echoes", "messages.all"): {Verb.VIEW, Verb.CREATE},
        })

    def test_security_agent_update_or_delete(self):
        self._load_and_check("security_agent", {
            ("findings", "findings.remediate"): {Verb.UPDATE, Verb.DELETE},
        })

    def test_hrportal_create_update_or_delete(self):
        self._load_and_check("hrportal", {
            ("departments", "hr.manage"): {Verb.CREATE, Verb.UPDATE, Verb.DELETE},
        })

    def test_channel_demo_create_update_or_delete(self):
        self._load_and_check("channel_demo", {
            ("incidents", "incidents.manage"): {Verb.CREATE, Verb.UPDATE, Verb.DELETE},
        })
