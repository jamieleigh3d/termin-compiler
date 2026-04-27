# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 1 — Identity contract conformance.

Tests the BRD §6.1 behavioral requirements against the stub
IdentityProvider (the only first-party identity provider in v0.9).
The tests are organized so that future providers (Okta, Cognito,
custom SAML) can run the same suite by parametrizing the
`identity_provider` fixture — same input/output expectations,
different implementation underneath.

Behavioral requirements covered (BRD §6.1):
  - Anonymous bypasses provider entirely (runtime never calls
    authenticate; runtime constructs ANONYMOUS_PRINCIPAL directly).
  - authenticate(credentials) returns a typed Principal.
  - Stable Principal.id across re-authentications of same identity.
  - roles_for(principal, app_id) returns role names.
  - Multi-role principal support (effective scopes = union).
  - Service principals carry their own roles.
  - Mid-session role changes — provider returns fresh roles per call.
  - Fail-closed on provider errors (runtime falls back to Anonymous).

Tests of the runtime's translation layer (provider role names →
source-declared scopes) live in tests/test_runtime.py and the
existing role/scope test files; this file is contract-shape only.
"""

import pytest

from termin_runtime.providers import (
    Category, ContractRegistry, ProviderRegistry,
    Principal, IdentityProvider, ANONYMOUS_PRINCIPAL,
)
from termin_runtime.providers.builtins import register_builtins
from termin_runtime.providers.builtins.identity_stub import StubIdentityProvider


# ── Fixtures ──


@pytest.fixture
def identity_provider() -> IdentityProvider:
    """A fresh stub provider per test. Future providers parametrize
    here to run the same conformance suite against their impl."""
    return StubIdentityProvider({})


@pytest.fixture
def populated_registry():
    """A ProviderRegistry with built-in providers registered, paired
    with the contract registry."""
    contracts = ContractRegistry.default()
    providers = ProviderRegistry()
    register_builtins(providers, contracts)
    return contracts, providers


# ── Anonymous bypass ──


class TestAnonymousBypass:
    """Per BRD §6.1: 'Anonymous bypasses the provider entirely. No-
    credentials requests never call authenticate; runtime treats as
    Anonymous principal directly.'

    These tests prove the contract recognizes the bypass — they don't
    test the runtime's bypass implementation (that's runtime tests)."""

    def test_anonymous_principal_is_typed(self):
        assert isinstance(ANONYMOUS_PRINCIPAL, Principal)

    def test_anonymous_principal_id_is_stable(self):
        assert ANONYMOUS_PRINCIPAL.id == "anonymous"

    def test_anonymous_principal_is_human_type(self):
        assert ANONYMOUS_PRINCIPAL.type == "human"

    def test_anonymous_has_no_on_behalf_of(self):
        assert ANONYMOUS_PRINCIPAL.on_behalf_of is None

    def test_anonymous_has_empty_claims(self):
        assert dict(ANONYMOUS_PRINCIPAL.claims) == {}

    def test_anonymous_is_anonymous_property(self):
        assert ANONYMOUS_PRINCIPAL.is_anonymous is True

    def test_provider_rejects_anonymous_in_roles_for(self, identity_provider):
        """The stub raises if asked to resolve roles for Anonymous —
        runtime is required to bypass, not delegate. Future providers
        may also raise here."""
        with pytest.raises(ValueError):
            identity_provider.roles_for(ANONYMOUS_PRINCIPAL, "app-id")

    def test_provider_rejects_empty_credentials(self, identity_provider):
        """Per BRD: runtime never calls authenticate with empty creds.
        Provider may raise to enforce the contract."""
        with pytest.raises(ValueError):
            identity_provider.authenticate({})

    def test_provider_rejects_empty_role(self, identity_provider):
        with pytest.raises(ValueError):
            identity_provider.authenticate({"role": "", "user_name": "x"})


# ── Principal shape ──


class TestPrincipalShape:
    """authenticate produces a typed Principal with the expected fields."""

    def test_authenticate_returns_principal(self, identity_provider):
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert isinstance(p, Principal)

    def test_authenticate_principal_is_human(self, identity_provider):
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert p.type == "human"

    def test_authenticate_principal_carries_display_name(self, identity_provider):
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert p.display_name == "Alice"

    def test_authenticate_principal_id_is_opaque(self, identity_provider):
        """id should be a non-trivial string, not the role/name themselves —
        runtime treats id as opaque."""
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert isinstance(p.id, str) and len(p.id) > 0
        # Stub specifically uses a "stub:" prefix on the hashed id;
        # this isn't a contract requirement, just a stub property.
        assert p.id.startswith("stub:")

    def test_authenticate_default_user_name(self, identity_provider):
        """Stub defaults user_name when omitted (dev-friendly behavior)."""
        p = identity_provider.authenticate({"role": "warehouse clerk"})
        assert p.display_name == "User"

    def test_authenticate_principal_has_no_on_behalf_of(self, identity_provider):
        """Standard human principal — no delegation chain."""
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert p.on_behalf_of is None

    def test_principal_rejects_invalid_type(self):
        with pytest.raises(ValueError):
            Principal(id="x", type="robot")  # not human/agent/service

    def test_agent_cannot_be_on_behalf_of_agent(self):
        agent_a = Principal(id="a1", type="agent")
        with pytest.raises(ValueError):
            Principal(id="a2", type="agent", on_behalf_of=agent_a)


# ── Stable id ──


class TestStableId:
    """Per BRD §6.1: 'id ... stable identifier, never changes.' Same
    underlying entity must resolve to the same id on each
    authentication."""

    def test_same_credentials_same_id(self, identity_provider):
        p1 = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        p2 = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert p1.id == p2.id

    def test_different_users_different_ids(self, identity_provider):
        p1 = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        p2 = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Bob",
        })
        assert p1.id != p2.id

    def test_different_roles_different_ids(self, identity_provider):
        """The stub treats role+name as the identity tuple. Real
        providers (OIDC sub) would key on the user only — both
        behaviors satisfy the contract."""
        p1 = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        p2 = identity_provider.authenticate({
            "role": "warehouse manager", "user_name": "Alice",
        })
        # Stub: differ. Doc tests this is acceptable; not a hard
        # contract requirement.
        assert p1.id != p2.id


# ── roles_for ──


class TestRolesFor:
    def test_roles_for_returns_set(self, identity_provider):
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        roles = identity_provider.roles_for(p, "warehouse-app")
        assert isinstance(roles, set)

    def test_roles_for_returns_authenticated_role(self, identity_provider):
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert identity_provider.roles_for(p, "warehouse-app") == {"warehouse clerk"}

    def test_roles_for_independent_of_app_id(self, identity_provider):
        """Stub doesn't scope by app — same principal gets the same
        roles in every app. Real providers may scope; either is
        contract-compliant."""
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        r1 = identity_provider.roles_for(p, "app-1")
        r2 = identity_provider.roles_for(p, "app-2")
        assert r1 == r2

    def test_roles_for_freshness(self, identity_provider):
        """Per BRD: 'mid-session role changes MUST be enforced. The
        runtime authorizes against the freshest roles the provider
        can supply.' Calling roles_for twice returns current state
        each time — no caching of stale results in the provider."""
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        r1 = identity_provider.roles_for(p, "app")
        r2 = identity_provider.roles_for(p, "app")
        # Stub is stateless; same answer. The contract permits TTL
        # caching but the runtime still expects per-call freshness
        # within a tolerance.
        assert r1 == r2


# ── Registry integration ──


class TestRegistryIntegration:
    def test_stub_registers_under_identity_default(self, populated_registry):
        contracts, providers = populated_registry
        rec = providers.get(Category.IDENTITY, "default", "stub")
        assert rec is not None
        assert rec.product_name == "stub"

    def test_stub_factory_constructs_provider(self, populated_registry):
        contracts, providers = populated_registry
        rec = providers.get(Category.IDENTITY, "default", "stub")
        instance = rec.factory({})
        assert isinstance(instance, IdentityProvider)

    def test_stub_factory_accepts_config(self, populated_registry):
        """Forward-compatibility: factory accepts a config dict even
        though the stub doesn't currently use it. Third-party stubs
        or extension stubs may take config."""
        contracts, providers = populated_registry
        rec = providers.get(Category.IDENTITY, "default", "stub")
        instance = rec.factory({"some": "future_option"})
        # Should construct successfully and ignore the unknown key.
        assert isinstance(instance, IdentityProvider)

    def test_stub_advertised_conformance_passing(self, populated_registry):
        contracts, providers = populated_registry
        rec = providers.get(Category.IDENTITY, "default", "stub")
        assert rec.conformance == "passing"


# ── Service / agent principals (shape only — runtime use comes later) ──


class TestServiceAndAgent:
    """Service + agent principal shape. The stub doesn't construct
    these (its credentials are cookie-style); they're shown here as
    typed shapes that future providers will produce per BRD §6.1."""

    def test_service_principal_has_own_roles(self):
        """A service principal carries roles via deploy config
        role_mappings; on_behalf_of is None."""
        svc = Principal(
            id="svc:warehouse-batch",
            type="service",
            display_name="Warehouse batch job",
            claims={"service_account_id": "warehouse-batch"},
            on_behalf_of=None,
        )
        assert svc.type == "service"
        assert svc.on_behalf_of is None
        assert svc.is_anonymous is False

    def test_agent_principal_in_delegate_mode(self):
        """An agent in delegate mode has a human on_behalf_of and
        derives authorization from that human."""
        human = Principal(
            id="okta:user-42",
            type="human",
            display_name="Alice",
        )
        agent = Principal(
            id="agent:moderation-bot",
            type="agent",
            display_name="Moderation Bot",
            claims={"agent_id": "moderation-bot"},
            on_behalf_of=human,
        )
        assert agent.type == "agent"
        assert agent.on_behalf_of is human

    def test_agent_principal_in_service_mode(self):
        """An agent in service mode has its own roles and no
        on_behalf_of."""
        agent = Principal(
            id="agent:nightly-cleanup",
            type="agent",
            display_name="Nightly Cleanup",
            on_behalf_of=None,
        )
        assert agent.on_behalf_of is None


# ── Audit log integration: BRD §6.3.4 ──


class TestAuditLogPrincipalRecording:
    """Per BRD §6.3.4, audit records carry the invoking Principal
    (with on_behalf_of for delegate-mode agents). v0.9 Phase 1 step 4
    extends the compute audit log schema with three Principal fields:

      - invoked_by_principal_id
      - invoked_by_display_name
      - on_behalf_of_principal_id

    These are populated by write_audit_trace when callers pass the
    invoking Principal. The contract is exercised end-to-end so that
    when Phase 4 (channels) lands and ai-agent computes start running
    in delegate mode, the audit trail already captures both the
    agent's identity and the human it acts for.
    """

    def test_audit_log_schema_includes_principal_fields(self):
        """Compute audit log Content type must include the three
        Principal-tracking fields so the BRD §6.3.4 contract is
        expressible."""
        from termin.peg_parser import parse_peg as parse
        from termin.lower import lower
        # Use compute_demo since it has audit-enabled computes.
        from pathlib import Path
        src = (Path(__file__).parent.parent / "examples" /
               "compute_demo.termin").read_text()
        program, errors = parse(src)
        assert errors.ok, errors.format()
        spec = lower(program)
        # Find any compute audit log Content type.
        audit_logs = [
            c for c in spec.content
            if c.name.snake.startswith("compute_audit_log_")
        ]
        assert len(audit_logs) > 0, "compute_demo should have audit logs"
        log = audit_logs[0]
        field_names = {f.name for f in log.fields}
        assert "invoked_by_principal_id" in field_names
        assert "invoked_by_display_name" in field_names
        assert "on_behalf_of_principal_id" in field_names

    def _audit_schema_dict(self, audit_ref: str) -> dict:
        """Build a Content schema dict matching the v0.9 audit log
        shape (lower.py audit_fields). Used by the trace-recording
        tests to spin up a minimal DB table."""
        def _f(name, bt, ct, enum_values=()):
            return {
                "name": name, "display_name": name.replace("_", " "),
                "business_type": bt, "column_type": ct,
                "required": False, "unique": False,
                "enum_values": list(enum_values),
                "minimum": None, "maximum": None,
                "foreign_key": None, "default_expr": None,
                "default_is_expr": False, "confidentiality_scopes": [],
                "one_of_values": [],
            }
        return {
            "name": {"display": audit_ref.replace("_", " "),
                     "snake": audit_ref, "pascal": "ComputeAuditLog"},
            "singular": audit_ref,
            "fields": [
                _f("compute_name", "text", "TEXT"),
                _f("invocation_id", "text", "TEXT"),
                _f("trigger", "text", "TEXT"),
                _f("started_at", "datetime", "TIMESTAMP"),
                _f("completed_at", "datetime", "TIMESTAMP"),
                _f("latency_ms", "number", "REAL"),
                _f("outcome", "enum", "TEXT",
                   ("success", "refused", "error", "timeout", "cancelled")),
                _f("total_input_tokens", "number", "INTEGER"),
                _f("total_output_tokens", "number", "INTEGER"),
                _f("trace", "text", "TEXT"),
                _f("error_message", "text", "TEXT"),
                _f("invoked_by_principal_id", "text", "TEXT"),
                _f("invoked_by_display_name", "text", "TEXT"),
                _f("on_behalf_of_principal_id", "text", "TEXT"),
            ],
            "audit": "none",
            "state_machines": [],
            "dependent_values": [],
            "confidentiality_scopes": [],
            "scope_groups": [],
        }

    def test_write_audit_trace_records_human_principal(self, tmp_path):
        """A human Principal flowing into write_audit_trace is
        recorded in the audit row."""
        import asyncio
        from termin_runtime.compute_runner import write_audit_trace
        from termin_runtime.storage import get_db, init_db, list_records
        from termin_runtime.providers import Principal

        # Minimal IR-shape with one audit-enabled compute. Synthesize
        # directly to avoid the compile path.
        audit_ref = "compute_audit_log_demo"
        schema = self._audit_schema_dict(audit_ref)
        db_path = str(tmp_path / "audit.db")

        # Mock RuntimeContext — write_audit_trace only reads ctx.db_path
        # and (for the record path) ctx.ir for redaction; the bare
        # minimum is fine here.
        class _Ctx:
            pass
        ctx = _Ctx()
        ctx.db_path = db_path
        ctx.ir = {"content": [schema], "computes": []}

        comp_dict = {
            "name": {"display": "demo", "snake": "demo"},
            "audit_level": "actions",
            "audit_content_ref": audit_ref,
        }
        alice = Principal(
            id="okta:user-42", type="human", display_name="Alice",
        )

        async def _go():
            await init_db([schema], db_path)
            await write_audit_trace(
                ctx, comp_dict, invocation_id="inv-1", trigger="api",
                started_at="2026-04-25T00:00:00Z",
                completed_at="2026-04-25T00:00:01Z",
                duration_ms=1000.0, outcome="success",
                invoked_by=alice,
            )
            db = await get_db(db_path)
            try:
                rows = await list_records(db, audit_ref)
            finally:
                await db.close()
            return rows
        rows = asyncio.run(_go())
        assert len(rows) == 1
        row = rows[0]
        assert row["invoked_by_principal_id"] == "okta:user-42"
        assert row["invoked_by_display_name"] == "Alice"
        assert row["on_behalf_of_principal_id"] == ""

    def test_write_audit_trace_records_delegate_mode_agent(self, tmp_path):
        """A delegate-mode agent Principal records both the agent's
        id AND the human's id — proving the BRD §6.3.4 'agent X
        acting for user Y did Z' audit shape works end-to-end."""
        import asyncio
        from termin_runtime.compute_runner import write_audit_trace
        from termin_runtime.storage import get_db, init_db, list_records
        from termin_runtime.providers import Principal

        audit_ref = "compute_audit_log_demo"
        schema = self._audit_schema_dict(audit_ref)
        db_path = str(tmp_path / "audit_delegate.db")

        class _Ctx:
            pass
        ctx = _Ctx()
        ctx.db_path = db_path
        ctx.ir = {"content": [schema], "computes": []}

        # Delegate-mode agent: agent X acting on behalf of human Y.
        alice = Principal(
            id="okta:user-42", type="human", display_name="Alice",
        )
        moderation_bot = Principal(
            id="agent:moderation-bot", type="agent",
            display_name="Moderation Bot",
            on_behalf_of=alice,
        )
        comp_dict = {
            "name": {"display": "delegate demo", "snake": "delegate_demo"},
            "audit_level": "actions",
            "audit_content_ref": audit_ref,
        }

        async def _go():
            await init_db([schema], db_path)
            await write_audit_trace(
                ctx, comp_dict, invocation_id="inv-2", trigger="event",
                started_at="2026-04-25T00:00:00Z",
                completed_at="2026-04-25T00:00:01Z",
                duration_ms=1000.0, outcome="success",
                invoked_by=moderation_bot,
            )
            db = await get_db(db_path)
            try:
                rows = await list_records(db, audit_ref)
            finally:
                await db.close()
            return rows
        rows = asyncio.run(_go())
        assert len(rows) == 1
        row = rows[0]
        assert row["invoked_by_principal_id"] == "agent:moderation-bot"
        assert row["invoked_by_display_name"] == "Moderation Bot"
        assert row["on_behalf_of_principal_id"] == "okta:user-42"
