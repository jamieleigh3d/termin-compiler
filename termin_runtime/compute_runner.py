# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Compute execution — LLM, Agent, and CEL compute invocation + audit traces.

Handles Level 1 LLM (field-to-field), Level 3 Agent (autonomous tool use),
and CEL server-side Compute execution. D-20 audit trace writing and redaction.
"""

import asyncio
import datetime as _dt
import json
import threading
import uuid

from fastapi import HTTPException, Request

from .context import RuntimeContext
from .storage import (
    get_db, create_record, get_record, update_record,
    list_records, filtered_query, update_fields, insert_raw, select_column,
)
from .state import do_state_transition
from .ai_provider import AIProviderError, build_output_tool, build_agent_tools
from .confidentiality import (
    check_compute_access, check_taint_integrity, enforce_output_taint,
    check_for_redacted_values,
)
from .errors import TerminError
from .transaction import Transaction, ContentSnapshot
from .boundaries import check_boundary_access


# ── Prompt building (testable, pure functions) ──

def _resolve_directive_at_invocation(comp: dict, record: dict) -> tuple[str, str]:
    """v0.9 Phase 6c (BRD #3 §6.2-§6.3): resolve field-ref Directive
    and Objective text from the triggering record at each invocation.

    For computes with `directive_source.kind == "field"`, returns
    `record[<field>]` as the directive text. Same for objective.
    Deploy-config-sourced directives have already been resolved at
    app startup (see `app._resolve_directive_sources`); for those
    forms the resolved text already lives in `comp["directive"]`.

    A record missing the named field resolves to empty rather than
    raising — keeps the prompt-build path forgiving for partial
    data, same as inline-empty behavior.

    Returns: (directive_text, objective_text). Either may be empty.
    """
    directive = comp.get("directive", "") or ""
    objective = comp.get("objective", "") or ""

    d_src = comp.get("directive_source")
    if isinstance(d_src, dict) and d_src.get("kind") == "field":
        directive = str(record.get(d_src.get("field", ""), "") or "")

    o_src = comp.get("objective_source")
    if isinstance(o_src, dict) and o_src.get("kind") == "field":
        objective = str(record.get(o_src.get("field", ""), "") or "")

    return directive, objective


def _build_llm_prompts(comp: dict, record: dict, content_name: str,
                       singular_lookup: dict) -> tuple[str, str]:
    """Build system and user messages for Level 1 LLM compute.

    Fix 009.1: system = directive + objective (objective was wrongly in user turn).
    Fix 009.2: No default directive injected when only objective is present.

    Returns: (system_message, user_message)
    """
    # v0.9 Phase 6c: field-ref Directive/Objective resolve from the
    # triggering record. No-op for inline / deploy-config forms.
    directive, objective = _resolve_directive_at_invocation(comp, record)

    # Read input fields from record
    input_values = {}
    for content_ref, field_name in comp.get("input_fields", []):
        if field_name in record:
            input_values[field_name] = record[field_name]

    # Interpolate inline expressions in objective (field references)
    if objective:
        singular = singular_lookup.get(
            content_name,
            content_name.rstrip("s") if content_name.endswith("s") else content_name)
        for fname, fval in input_values.items():
            objective = objective.replace(f"{singular}.{fname}", str(fval))

    # System message: directive + objective (both optional, no defaults)
    system_parts = []
    if directive:
        system_parts.append(directive)
    if objective:
        system_parts.append(objective)
    system_msg = "\n\n".join(system_parts) if system_parts else ""

    # User message: input field values ONLY (no objective)
    if input_values:
        user_msg = "\n".join(f"{k}: {v}" for k, v in input_values.items())
    else:
        user_msg = ""

    return system_msg, user_msg


def _build_agent_prompts(comp: dict, record: dict) -> tuple[str, str]:
    """Build system and user messages for Level 3 Agent compute.

    Fix 009.2: No default directive injected when only objective is present.

    Returns: (system_message, user_message)
    """
    # v0.9 Phase 6c: field-ref Directive/Objective resolve from the
    # triggering record. No-op for inline / deploy-config forms.
    directive, objective = _resolve_directive_at_invocation(comp, record)

    # System message: directive + objective
    system_parts = []
    if directive:
        system_parts.append(directive)
    if objective:
        system_parts.append(objective)
    system_msg = "\n\n".join(system_parts) if system_parts else ""

    # User message: triggering record context
    user_msg = f"Triggering record:\n{json.dumps(record, indent=2, default=str)}"

    return system_msg, user_msg


def _build_agent_set_output(comp: dict, content_lookup: dict) -> dict:
    """Build the set_output tool for agent computes.

    Fix 009.3: Only includes 'thinking' if the compute's output schema declares it.
    Always includes 'summary' for completion signal.
    """
    properties = {
        "summary": {"type": "string", "description": "Result summary."},
    }
    required = ["summary"]

    # Add output fields from the compute's declaration
    for content_ref, field_name in comp.get("output_fields", []):
        schema = None
        for name, s in content_lookup.items():
            singular = s.get("singular", "")
            if name == content_ref or singular == content_ref:
                schema = s
                break
        if schema:
            field_def = None
            for f in schema.get("fields", []):
                if f.get("name", "") == field_name:
                    field_def = f
                    break
            if field_def:
                prop = {"description": f"Field: {content_ref}.{field_name}"}
                if field_def.get("column_type") in ("INTEGER", "REAL"):
                    prop["type"] = "number"
                else:
                    prop["type"] = "string"
                properties[field_name] = prop
                required.append(field_name)
                continue
        # Fallback
        properties[field_name] = {"type": "string", "description": f"Field: {content_ref}.{field_name}"}
        required.append(field_name)

    return {
        "name": "set_output",
        "description": "Signal that you have completed the task. Call this when done.",
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    }


def _build_llm_audit_metadata(
    ctx: RuntimeContext, comp_snake: str,
    system_msg: str, user_msg: str, result: dict | None,
    *, error_str: str | None = None,
) -> dict:
    """Build the BRD §6.3.4 audit metadata dict for an LLM call.

    Slice (d) reads the deploy binding for provider_product, the
    provider instance for provider_config_hash + model_identifier,
    and the result dict for cost. The output dict shape matches what
    write_audit_trace expects under audit_metadata.

    Slice (e) extends this for refusal and structured tool_calls.
    """
    provider_inst = ctx.compute_providers.get(comp_snake)
    # Resolve provider_product from the deploy binding if available.
    bindings = (
        getattr(ctx, "_deploy_bindings", None)
        or {}
    )
    provider_product = ""
    if hasattr(ctx, "compute_providers") and provider_inst is not None:
        provider_product = getattr(provider_inst, "service", "") or ""
    config_hash = getattr(provider_inst, "_config_hash", "") if provider_inst else ""
    model_id = getattr(provider_inst, "model", "") or ""
    prompt_as_sent = f"<system>\n{system_msg}\n</system>\n{user_msg}"
    cost_units = 0
    if result and isinstance(result, dict):
        usage = result.get("_termin_usage") or {}
        if isinstance(usage, dict):
            cost_units = int(usage.get("total_tokens") or 0)
    return {
        "provider_product": provider_product,
        "model_identifier": model_id,
        "provider_config_hash": config_hash,
        "prompt_as_sent": prompt_as_sent,
        "sampling_params_json": "{}",
        "tool_calls_json": "[]",
        "refusal_reason": None,
        "cost_units": cost_units,
        "cost_unit_type": "tokens" if cost_units else "",
        "cost_currency_amount": "",
    }


async def _write_refusal_sidecar(
    ctx: RuntimeContext, comp: dict, invocation_id: str,
    reason: str, refused_at: str, invoked_by=None,
) -> None:
    """v0.9 Phase 3 slice (e): write a record to the runtime-managed
    `compute_refusals` Content type. Linked to the per-compute audit
    log by invocation_id. Schema generated by lower.py for any app
    with at least one ai-agent compute.

    The sidecar table may not exist if the compiler was older than
    slice (e) — best-effort write with a warn-on-failure log.
    """
    invoked_by_id = ""
    on_behalf_of_id = ""
    if invoked_by is not None:
        invoked_by_id = getattr(invoked_by, "id", "") or ""
        obo = getattr(invoked_by, "on_behalf_of", None)
        if obo is not None:
            on_behalf_of_id = getattr(obo, "id", "") or ""
    record_data = {
        "compute_name": comp["name"]["display"],
        "invocation_id": invocation_id,
        "reason": reason,
        "refused_at": refused_at,
        "invoked_by_principal_id": invoked_by_id,
        "on_behalf_of_principal_id": on_behalf_of_id,
    }
    try:
        db = await get_db(ctx.db_path)
        try:
            await insert_raw(db, "compute_refusals", record_data)
        finally:
            await db.close()
    except Exception as e:
        print(
            f"[Termin] [WARN] Failed to write compute_refusals "
            f"record for {comp['name']['display']!r}: {e}"
        )


def _build_agent_audit_metadata(
    ctx: RuntimeContext, comp_snake: str,
    system_msg: str, user_msg: str, tool_calls_log: list,
    *, refusal_reason: str | None = None,
) -> dict:
    """Build the BRD §6.3.4 audit metadata dict for an ai-agent call.

    tool_calls_log is a list of {tool, args, result, is_error,
    latency_ms} dicts capturing every tool call the agent made
    during the invocation. Persisted as JSON in the audit
    `tool_calls` column.
    """
    provider_inst = ctx.compute_providers.get(comp_snake)
    provider_product = ""
    if provider_inst is not None:
        provider_product = getattr(provider_inst, "service", "") or ""
    config_hash = getattr(provider_inst, "_config_hash", "") if provider_inst else ""
    model_id = getattr(provider_inst, "model", "") or ""
    prompt_as_sent = f"<system>\n{system_msg}\n</system>\n{user_msg}"
    return {
        "provider_product": provider_product,
        "model_identifier": model_id,
        "provider_config_hash": config_hash,
        "prompt_as_sent": prompt_as_sent,
        "sampling_params_json": "{}",
        "tool_calls_json": json.dumps(tool_calls_log) if tool_calls_log else "[]",
        "refusal_reason": refusal_reason,
        "cost_units": 0,
        "cost_unit_type": "",
        "cost_currency_amount": "",
    }


async def execute_compute(ctx: RuntimeContext, comp: dict, record: dict,
                          content_name: str, main_loop=None):
    """Execute a Compute triggered by an event."""
    comp_name = comp["name"]["display"]
    provider = comp.get("provider", "cel")

    if provider == "llm":
        await _execute_llm_compute(ctx, comp, record, content_name, main_loop)
    elif provider == "ai-agent":
        await _execute_agent_compute(ctx, comp, record, content_name, main_loop)
    else:
        print(f"[Termin] Compute '{comp_name}': provider '{provider}' not supported for event triggers")


async def _execute_llm_compute(ctx: RuntimeContext, comp: dict, record: dict,
                                content_name: str, main_loop=None):
    """Execute a Level 1 LLM Compute — field-to-field completion."""
    comp_name = comp["name"]["display"]
    comp_snake = comp["name"]["snake"]
    _llm_started = _dt.datetime.utcnow()
    _llm_invocation_id = str(uuid.uuid4())

    # v0.9 Phase 3: per-compute provider lookup. Slice (b) routes
    # through `provider.legacy` (the embedded AIProvider) for SDK
    # calls so prompt building, tool_use forcing, and streaming
    # behavior are byte-identical with v0.8. Slice (d) ports the
    # legacy methods into the contract surface and deletes .legacy.
    provider = ctx.compute_providers.get(comp_snake)
    if provider is None or not getattr(provider, "is_configured", False):
        print(f"[Termin] Compute '{comp_name}': no provider bound, skipped")
        return

    # Build prompts (Fix 009.1 + 009.2)
    system_msg, user_msg = _build_llm_prompts(comp, record, content_name, ctx.singular_lookup)

    # Build output tool
    output_fields = comp.get("output_fields", [])
    output_tool = build_output_tool(output_fields, ctx.content_lookup)

    print(f"[Termin] Compute '{comp_name}': calling {provider.service} (record {record.get('id', '?')})")

    # v0.8.1: LLM-path streaming. When the provider supports
    # stream_agent_response, route the call through it and publish
    # each field_delta / field_done / done event onto the event bus
    # so any component rendering the target field (data_table cells,
    # chat bubbles, detail views) can render tokens as they arrive.
    # Falls back to non-streaming complete() for providers that don't
    # implement the streaming path.
    #
    # Events carry content_name + record_id so the general client
    # hydrator can target `[data-termin-row-id=<id>]
    # [data-termin-field=<field>]` without knowing the component type.
    _llm_stream_base = f"compute.stream.{_llm_invocation_id}"
    _llm_record_id = record.get("id")

    async def _on_llm_stream_event(event):
        if ctx.event_bus is None:
            return
        etype = event.get("type")
        if etype == "field_delta":
            field = event.get("field", "")
            await ctx.event_bus.publish({
                "channel_id": f"{_llm_stream_base}.field.{field}",
                "data": {
                    "invocation_id": _llm_invocation_id,
                    "compute": comp_snake,
                    "mode": "tool_use",
                    "tool": event.get("tool", "set_output"),
                    "content_name": content_name,
                    "record_id": _llm_record_id,
                    "field": field,
                    "delta": event.get("delta", ""),
                    "done": False,
                },
            })
        elif etype == "field_done":
            field = event.get("field", "")
            await ctx.event_bus.publish({
                "channel_id": f"{_llm_stream_base}.field.{field}",
                "data": {
                    "invocation_id": _llm_invocation_id,
                    "compute": comp_snake,
                    "mode": "tool_use",
                    "tool": event.get("tool", "set_output"),
                    "content_name": content_name,
                    "record_id": _llm_record_id,
                    "field": field,
                    "done": True,
                    "value": event.get("value"),
                },
            })
        elif etype == "done":
            await ctx.event_bus.publish({
                "channel_id": _llm_stream_base,
                "data": {
                    "invocation_id": _llm_invocation_id,
                    "compute": comp_snake,
                    "mode": "tool_use",
                    "tool": "set_output",
                    "content_name": content_name,
                    "record_id": _llm_record_id,
                    "done": True,
                    "output": event.get("output") or {},
                },
            })

    try:
        # Slice (b): legacy methods on `provider.legacy` (an internal
        # AIProvider). Same call shape as v0.8 — no behavior change.
        legacy = provider.legacy
        use_streaming = (
            ctx.event_bus is not None
            and hasattr(legacy, "stream_agent_response")
            and provider.service == "anthropic"
        )
        if use_streaming:
            result = {}
            async for event in legacy.stream_agent_response(
                    system_msg, user_msg, output_tool):
                if event.get("type") == "done":
                    result = event.get("output") or {}
                await _on_llm_stream_event(event)
        else:
            result = await legacy.complete(system_msg, user_msg, output_tool)
        thinking = result.pop("thinking", "")
        if thinking:
            print(f"[Termin] Compute '{comp_name}' thinking: {thinking[:100]}")

        # Write output fields back to the record
        if output_fields and record.get("id"):
            update_data = {}
            for content_ref, field_name in output_fields:
                if field_name in result:
                    update_data[field_name] = result[field_name]
            if update_data:
                db = await get_db(ctx.db_path)
                try:
                    await update_fields(db, content_name, record["id"], update_data)
                    print(f"[Termin] Compute '{comp_name}': updated record {record['id']}")
                    updated_record = dict(record)
                    updated_record.update(update_data)
                    event_data = {
                        "channel_id": f"content.{content_name}.updated",
                        "data": updated_record,
                    }
                    if main_loop and main_loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            ctx.event_bus.publish(event_data), main_loop)
                    else:
                        await ctx.event_bus.publish(event_data)
                finally:
                    await db.close()

        # D-20: Audit trace on success
        _llm_completed = _dt.datetime.utcnow()
        _llm_duration = (_llm_completed - _llm_started).total_seconds() * 1000
        audit_level = comp.get("audit_level", "actions")
        trace_data = {"compute_type": "agent", "calls": [{"response": thinking[:200] if thinking else ""}]}
        if audit_level == "debug":
            trace_data["calls"][0]["system_prompt"] = system_msg
            trace_data["calls"][0]["thinking"] = thinking
        # v0.9 Phase 3 slice (d): BRD §6.3.4 audit_metadata.
        audit_metadata = _build_llm_audit_metadata(
            ctx, comp_snake, system_msg, user_msg, result,
        )
        await write_audit_trace(
            ctx, comp, invocation_id=_llm_invocation_id, trigger="event",
            started_at=_llm_started.isoformat() + "Z",
            completed_at=_llm_completed.isoformat() + "Z",
            latency_ms=_llm_duration, outcome="success",
            trace_data=trace_data,
            audit_metadata=audit_metadata,
        )
    except AIProviderError as e:
        print(f"[Termin] [ERROR] Compute '{comp_name}': {e}")
        _llm_err_completed = _dt.datetime.utcnow()
        _llm_err_duration = (_llm_err_completed - _llm_started).total_seconds() * 1000
        audit_metadata = _build_llm_audit_metadata(
            ctx, comp_snake, system_msg, user_msg, None, error_str=str(e),
        )
        await write_audit_trace(
            ctx, comp, invocation_id=_llm_invocation_id, trigger="event",
            started_at=_llm_started.isoformat() + "Z",
            completed_at=_llm_err_completed.isoformat() + "Z",
            latency_ms=_llm_err_duration, outcome="error",
            error_message=str(e),
            trace_data={"compute_type": "agent", "error": str(e)},
            audit_metadata=audit_metadata,
        )


async def _execute_agent_compute(ctx: RuntimeContext, comp: dict, record: dict,
                                  content_name: str, main_loop=None):
    """Execute a Level 3 Agent Compute — autonomous with tool calls."""
    comp_name = comp["name"]["display"]
    comp_snake = comp["name"]["snake"]
    _agent_started = _dt.datetime.utcnow()
    _agent_invocation_id = str(uuid.uuid4())

    # v0.9 Phase 3: per-compute provider lookup (slice b interim
    # via provider.legacy — see _execute_llm_compute for rationale).
    provider = ctx.compute_providers.get(comp_snake)
    if provider is None or not getattr(provider, "is_configured", False):
        print(f"[Termin] Compute '{comp_name}': no provider bound, skipped")
        return

    accesses = comp.get("accesses", [])
    # v0.9 Phase 3 slice (c): Reads grants read-only content access.
    # The agent's tool surface includes content_query / content_get
    # for these types but not content_create / update / delete.
    # State tools (state_transition) come from accesses only.
    reads = comp.get("reads", [])
    # readable = anything in Accesses or Reads; writable = Accesses only.
    readable_set = set(accesses) | set(reads)
    writable_set = set(accesses)

    # Build prompts (Fix 009.1 + 009.2)
    system_msg, user_msg = _build_agent_prompts(comp, record)

    # Build tools
    agent_tools = build_agent_tools(accesses, ctx.content_lookup)
    set_output = _build_agent_set_output(comp, ctx.content_lookup)
    all_tools = agent_tools + [set_output]

    # v0.9 Phase 3 slice (e): refusal capture state. Mutated by
    # _execute_tool when the agent calls system_refuse; consulted
    # post-loop to convert the outcome to "refused" and write the
    # compute_refusals sidecar record.
    refusal_state: dict = {}

    async def _execute_tool(tool_name: str, tool_input: dict) -> dict:
        db = await get_db(ctx.db_path)
        try:
            # v0.9 Phase 3 slice (e): system_refuse capture. Recorded
            # in refusal_state; the legacy agent loop continues to
            # call other tools until it hits set_output or max_turns,
            # but post-loop we override the outcome to "refused" and
            # discard any output. Slice (f)/v1.0 may add a
            # halt-on-refuse semantics to the contract methods so the
            # loop terminates immediately.
            if tool_name == "system_refuse":
                if not refusal_state:
                    refusal_state["reason"] = str(
                        tool_input.get("reason", "")
                    ).strip()
                return {"acknowledged": True}

            if tool_name == "content_query":
                cname = tool_input.get("content_name", "")
                # v0.9 Phase 3 slice (c): read tools accept either
                # Accesses or Reads as the source-side grant.
                if cname not in readable_set:
                    return {"error": (
                        f"Access denied: {cname} not in Accesses or "
                        f"Reads"
                    )}
                bnd_err = check_boundary_access(
                    ctx.boundary_for_compute, ctx.boundary_for_content,
                    comp_snake, cname)
                if bnd_err:
                    return {"error": bnd_err}
                filters = tool_input.get("filters", {})
                return await filtered_query(db, cname, filters or None)

            elif tool_name == "content_create":
                cname = tool_input.get("content_name", "")
                if cname not in writable_set:
                    return {"error": f"Access denied: {cname} not in Accesses"}
                bnd_err = check_boundary_access(
                    ctx.boundary_for_compute, ctx.boundary_for_content,
                    comp_snake, cname)
                if bnd_err:
                    return {"error": bnd_err}
                data = tool_input.get("data", {})
                # v0.9 multi-SM: sm_info is the list of state-machine specs
                # for this content. create_record() seeds initial values
                # for each machine's column from that list.
                sm_info = ctx.sm_lookup.get(cname, [])
                schema = ctx.content_lookup.get(cname, {})
                rec = await create_record(db, cname, data, schema, sm_info,
                                          ctx.terminator, ctx.event_bus)
                return rec

            elif tool_name == "content_update":
                cname = tool_input.get("content_name", "")
                if cname not in writable_set:
                    return {"error": f"Access denied: {cname} not in Accesses"}
                bnd_err = check_boundary_access(
                    ctx.boundary_for_compute, ctx.boundary_for_content,
                    comp_snake, cname)
                if bnd_err:
                    return {"error": bnd_err}
                rid = tool_input.get("record_id")
                data = tool_input.get("data", {})
                await update_record(db, cname, rid, data, "id",
                                    ctx.terminator, ctx.event_bus)
                return {"ok": True, "id": rid}

            elif tool_name == "state_transition":
                cname = tool_input.get("content_name", "")
                # State tools come from Accesses only — Reads grants
                # do not include state.transition. BRD §6.3.3 explicit.
                if cname not in writable_set:
                    return {"error": f"Access denied: {cname} not in Accesses"}
                bnd_err = check_boundary_access(
                    ctx.boundary_for_compute, ctx.boundary_for_content,
                    comp_snake, cname)
                if bnd_err:
                    return {"error": bnd_err}
                rid = tool_input.get("record_id")
                target = tool_input.get("target_state")
                # v0.9: machine_name is required when content has multiple
                # state machines. Fall back to the single machine when one
                # exists; raise when ambiguous.
                machine = tool_input.get("machine_name", "")
                sm_list = ctx.sm_lookup.get(cname, [])
                if not machine:
                    if len(sm_list) == 1:
                        machine = sm_list[0]["machine_name"]
                    else:
                        return {"error": (
                            f"machine_name is required for state_transition on "
                            f"'{cname}' (has {len(sm_list)} state machines)")}
                # Phase 2.x (d): transitions go through ctx.storage
                # for atomic CAS — same path as the human transition
                # endpoint.
                result = await do_state_transition(
                    ctx.storage, cname, rid, machine, target,
                    {"role": "service", "scopes": list(ctx.scope_for_content_verb(cname, "update") or [])},
                    ctx.sm_lookup, ctx.terminator, ctx.event_bus)
                return result

            else:
                return {"error": f"Unknown tool: {tool_name}"}
        finally:
            await db.close()

    print(f"[Termin] Compute '{comp_name}': starting agent loop ({provider.service})")

    # v0.8 #7: stream set_output field deltas to the compute.stream.*
    # channel family so connected clients (chat UI) can render
    # token-by-token. Event-bus publication is cheap and no-op when
    # nobody is subscribed, so we always go through the streaming path
    # when an event bus is available. Fallback to the non-streaming
    # agent_loop only if the bus is unavailable (defensive).
    _stream_base_channel = f"compute.stream.{_agent_invocation_id}"
    _agent_record_id = record.get("id") if record else None

    async def _on_stream_event(event):
        """Push each agent-stream event onto the event bus on the
        appropriate channel per the v0.8 streaming protocol.

        content_name + record_id (when known) are included so the
        general client-side hydrator can target DOM elements keyed by
        (row_id, field_name) — the same shape as `content.*.updated`
        events but streamed. This keeps streaming orthogonal to the
        presentation component type (data_table, chat, detail view).
        """
        if ctx.event_bus is None:
            return
        etype = event.get("type")
        tool_name = event.get("tool", "set_output")
        if etype == "field_delta":
            field = event.get("field", "")
            await ctx.event_bus.publish({
                "channel_id": f"{_stream_base_channel}.field.{field}",
                "data": {
                    "invocation_id": _agent_invocation_id,
                    "compute": comp_snake,
                    "mode": "tool_use",
                    "tool": tool_name,
                    "content_name": content_name,
                    "record_id": _agent_record_id,
                    "field": field,
                    "delta": event.get("delta", ""),
                    "done": False,
                },
            })
        elif etype == "field_done":
            field = event.get("field", "")
            await ctx.event_bus.publish({
                "channel_id": f"{_stream_base_channel}.field.{field}",
                "data": {
                    "invocation_id": _agent_invocation_id,
                    "compute": comp_snake,
                    "mode": "tool_use",
                    "tool": tool_name,
                    "content_name": content_name,
                    "record_id": _agent_record_id,
                    "field": field,
                    "done": True,
                    "value": event.get("value"),
                },
            })
        elif etype == "done":
            await ctx.event_bus.publish({
                "channel_id": _stream_base_channel,
                "data": {
                    "invocation_id": _agent_invocation_id,
                    "compute": comp_snake,
                    "mode": "tool_use",
                    "tool": "set_output",
                    "content_name": content_name,
                    "record_id": _agent_record_id,
                    "done": True,
                    "output": event.get("output") or {},
                },
            })

    try:
        legacy = provider.legacy
        if ctx.event_bus is not None and hasattr(
                legacy, "agent_loop_streaming"):
            result = await legacy.agent_loop_streaming(
                system_msg, user_msg, all_tools, _execute_tool,
                on_event=_on_stream_event)
        else:
            result = await legacy.agent_loop(
                system_msg, user_msg, all_tools, _execute_tool)
        thinking = result.get("thinking", "")
        if thinking:
            print(f"[Termin] Compute '{comp_name}' completed: {thinking[:100]}")

        _agent_completed = _dt.datetime.utcnow()
        _agent_duration = (_agent_completed - _agent_started).total_seconds() * 1000
        audit_level = comp.get("audit_level", "actions")

        # v0.9 Phase 3 slice (e): if the agent invoked system_refuse
        # during the loop, override the outcome to "refused" and
        # write the compute_refusals sidecar record. Refusal is
        # logged unconditionally regardless of audit_level (BRD
        # contract invariant).
        if refusal_state.get("reason"):
            await _write_refusal_sidecar(
                ctx, comp, _agent_invocation_id,
                refusal_state["reason"],
                _agent_completed.isoformat() + "Z",
            )
            agent_audit_metadata = _build_agent_audit_metadata(
                ctx, comp_snake, system_msg, user_msg, tool_calls_log=[],
                refusal_reason=refusal_state["reason"],
            )
            await write_audit_trace(
                ctx, comp, invocation_id=_agent_invocation_id,
                trigger="event",
                started_at=_agent_started.isoformat() + "Z",
                completed_at=_agent_completed.isoformat() + "Z",
                latency_ms=_agent_duration, outcome="refused",
                trace_data={
                    "compute_type": "agent",
                    "refused": True,
                    "reason": refusal_state["reason"],
                },
                audit_metadata=agent_audit_metadata,
            )
            # Refusal event for downstream subscribers (UI surfacing,
            # ops alerts, etc.).
            if ctx.event_bus is not None:
                await ctx.event_bus.publish({
                    "channel_id": f"compute.{comp_snake}.refused",
                    "data": {
                        "invocation_id": _agent_invocation_id,
                        "compute": comp_snake,
                        "reason": refusal_state["reason"],
                    },
                })
            return

        trace_data = {"compute_type": "agent", "calls": [{"response": thinking[:200] if thinking else ""}]}
        if audit_level == "debug":
            trace_data["calls"][0]["system_prompt"] = system_msg
            trace_data["calls"][0]["thinking"] = thinking
        # v0.9 Phase 3 slice (d): BRD §6.3.4 audit_metadata. The
        # legacy AIProvider doesn't yet expose a structured tool-calls
        # list back to the runner, so we pass the empty list here.
        agent_audit_metadata = _build_agent_audit_metadata(
            ctx, comp_snake, system_msg, user_msg, tool_calls_log=[],
        )
        await write_audit_trace(
            ctx, comp, invocation_id=_agent_invocation_id, trigger="event",
            started_at=_agent_started.isoformat() + "Z",
            completed_at=_agent_completed.isoformat() + "Z",
            latency_ms=_agent_duration, outcome="success",
            trace_data=trace_data,
            audit_metadata=agent_audit_metadata,
        )
    except AIProviderError as e:
        print(f"[Termin] [ERROR] Compute '{comp_name}': {e}")
        _agent_err_completed = _dt.datetime.utcnow()
        _agent_err_duration = (_agent_err_completed - _agent_started).total_seconds() * 1000
        agent_audit_metadata = _build_agent_audit_metadata(
            ctx, comp_snake, system_msg, user_msg, tool_calls_log=[],
        )
        await write_audit_trace(
            ctx, comp, invocation_id=_agent_invocation_id, trigger="event",
            started_at=_agent_started.isoformat() + "Z",
            completed_at=_agent_err_completed.isoformat() + "Z",
            latency_ms=_agent_err_duration, outcome="error",
            error_message=str(e),
            trace_data={"compute_type": "agent", "error": str(e)},
            audit_metadata=agent_audit_metadata,
        )


# ── D-20: Audit trace recording ──

async def write_audit_trace(ctx: RuntimeContext, comp: dict, invocation_id: str,
                            trigger: str, started_at: str, completed_at: str,
                            latency_ms: float = 0.0, outcome: str = "success",
                            trace_data: dict = None, error_message: str = None,
                            total_input_tokens: int = 0, total_output_tokens: int = 0,
                            invoked_by=None,
                            audit_metadata: dict = None):
    """Write a trace record to the compute's audit log Content table.

    Per BRD §6.3.4 (v0.9 Phase 3 slice (d)):
      - `latency_ms` is the canonical column name (renamed from
        `duration_ms` in v0.8). The transitional `duration_ms=` kwarg
        was dropped along with the back-compat shim before Phase 7.
      - `audit_metadata` carries the BRD §6.3.4 reproducibility-grade
        fields for LLM/agent invocations: provider_product,
        model_identifier, provider_config_hash, prompt_as_sent,
        sampling_params (JSON), tool_calls (JSON), refusal_reason,
        cost_{units,unit_type,currency_amount}. Missing keys default
        to safe values (empty strings / 0 / null). CEL computes pass
        None.

    invoked_by: optional Principal who triggered the compute. For
        event-triggered computes this is the principal who caused
        the upstream event; for system-triggered computes (scheduler,
        startup hooks) it's None and the audit fields are empty.
        For delegate-mode agent principals, on_behalf_of is also
        recorded so the audit trail captures 'agent X acting for
        user Y did Z'.
    """
    audit_level = comp.get("audit_level", "actions")
    audit_ref = comp.get("audit_content_ref")
    if audit_level == "none" or not audit_ref:
        return

    # Per BRD §6.3.4, principal info on the audit record.
    invoked_by_id = ""
    invoked_by_name = ""
    on_behalf_of_id = ""
    if invoked_by is not None:
        invoked_by_id = getattr(invoked_by, "id", "") or ""
        invoked_by_name = getattr(invoked_by, "display_name", "") or ""
        obo = getattr(invoked_by, "on_behalf_of", None)
        if obo is not None:
            on_behalf_of_id = getattr(obo, "id", "") or ""

    trace_json = json.dumps(trace_data) if trace_data else "{}"
    record_data = {
        "compute_name": comp["name"]["display"],
        "invocation_id": invocation_id,
        "trigger": trigger,
        "started_at": started_at,
        "completed_at": completed_at,
        "latency_ms": latency_ms,
        "outcome": outcome,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "trace": trace_json,
        "error_message": error_message or "",
        "invoked_by_principal_id": invoked_by_id,
        "invoked_by_display_name": invoked_by_name,
        "on_behalf_of_principal_id": on_behalf_of_id,
    }

    # v0.9 Phase 3 slice (d): LLM/agent invocations carry the
    # reproducibility-grade audit columns from BRD §6.3.4. CEL
    # computes don't get these columns in their audit table — the
    # writer only populates them when the schema includes them
    # (provider in {"llm", "ai-agent"}).
    if comp.get("provider") in ("llm", "ai-agent"):
        m = audit_metadata or {}
        record_data.update({
            "provider_product": m.get("provider_product", ""),
            "model_identifier": m.get("model_identifier", ""),
            "provider_config_hash": m.get("provider_config_hash", ""),
            "prompt_as_sent": m.get("prompt_as_sent", ""),
            "sampling_params": m.get("sampling_params_json", "{}"),
            "tool_calls": m.get("tool_calls_json", "[]"),
            "refusal_reason": m.get("refusal_reason") or "",
            "cost_units": m.get("cost_units") or 0,
            "cost_unit_type": m.get("cost_unit_type", "") or "",
            "cost_currency_amount": m.get("cost_currency_amount", "") or "",
        })

    try:
        db = await get_db(ctx.db_path)
        try:
            await insert_raw(db, audit_ref, record_data)
        finally:
            await db.close()
    except Exception as e:
        print(f"[Termin] [WARN] Failed to write audit trace for '{comp['name']['display']}': {e}")


async def redact_audit_traces(ctx: RuntimeContext, records: list,
                              audit_table_name: str, user_scopes: set) -> list:
    """Apply redaction to audit trace records based on caller scopes."""
    comp = None
    for c in ctx.ir.get("computes", []):
        if c.get("audit_content_ref") == audit_table_name:
            comp = c
            break
    if not comp:
        return records

    all_content_refs = set(
        comp.get("input_content", []) + comp.get("output_content", []) + comp.get("accesses", []))
    redact_fields = []
    for cr in all_content_refs:
        schema = ctx.content_lookup.get(cr, {})
        for field_def in schema.get("fields", []):
            conf_scopes = tuple(field_def.get("confidentiality_scopes", []))
            if conf_scopes and not all(s in user_scopes for s in conf_scopes):
                redact_fields.append((cr, field_def["name"]))

    if not redact_fields:
        return records

    redact_values = []
    try:
        db = await get_db(ctx.db_path)
        try:
            for content_name, field_name in redact_fields:
                try:
                    col_values = await select_column(db, content_name, field_name)
                    for val_raw in col_values:
                        val = str(val_raw) if val_raw is not None else ""
                        if len(val) >= 4:
                            redact_values.append((val, field_name))
                except Exception:
                    pass
        finally:
            await db.close()
    except Exception:
        return records

    if not redact_values:
        return records

    for rec in records:
        trace_val = rec.get("trace", "")
        if trace_val:
            for val, fname in redact_values:
                trace_val = trace_val.replace(val, f"[REDACTED:{fname}]")
            rec["trace"] = trace_val
        err_val = rec.get("error_message", "")
        if err_val:
            for val, fname in redact_values:
                err_val = err_val.replace(val, f"[REDACTED:{fname}]")
            rec["error_message"] = err_val

    return records


def register_compute_endpoint(app, ctx: RuntimeContext):
    """Register the server-side Compute invocation endpoint."""

    @app.post("/api/v1/compute/{compute_name}")
    async def invoke_compute(compute_name: str, request: Request):
        """Execute a Compute server-side with confidentiality checks (Checks 1-4)."""
        comp = ctx.compute_lookup.get(compute_name)
        if not comp:
            raise HTTPException(status_code=404, detail=f"Compute '{compute_name}' not found")

        user = ctx.get_current_user(request)
        user_scopes = set(user.get("scopes", []))
        body = await request.json()
        input_data = body.get("input", {})

        # Check execution permission
        req_scope = comp.get("required_scope")
        if req_scope and req_scope not in user_scopes:
            raise HTTPException(status_code=403, detail=f"Requires scope '{req_scope}' to execute")

        # Check 1: Confidentiality gate
        gate_err = check_compute_access(comp, user_scopes)
        if gate_err:
            ctx.terminator.route(TerminError(
                source=comp["name"]["display"], kind="confidentiality_gate_rejected",
                message=gate_err))
            raise HTTPException(status_code=403, detail=gate_err)

        # Check 2: Taint integrity
        if isinstance(input_data, list) and comp.get("identity_mode") == "service":
            for input_content_name in comp.get("input_content", []):
                schema = ctx.content_lookup.get(input_content_name, {})
                taint_err = check_taint_integrity(input_data, schema, user_scopes)
                if taint_err:
                    ctx.terminator.route(TerminError(
                        source="confidentiality", kind="taint_violation",
                        message=taint_err))
                    raise HTTPException(status_code=500, detail=taint_err)

        # D-20: Audit timing
        _audit_started = _dt.datetime.utcnow()
        _audit_started_str = _audit_started.isoformat() + "Z"

        tx = Transaction()

        compute_ctx = {
            "Compute": {
                "Name": comp["name"]["display"],
                "Provider": comp.get("provider") or "cel",
                "IdentityMode": comp.get("identity_mode", "delegate"),
                "Scopes": list(user_scopes),
                "ExecutionId": tx.id,
                "Trigger": "api",
                "StartedAt": tx.started_at,
            },
            "User": user.get("User", {}),
        }

        # Evaluate preconditions
        for i, precond in enumerate(comp.get("preconditions", [])):
            try:
                result = ctx.expr_eval.evaluate(precond, compute_ctx)
                if not result:
                    tx.rollback()
                    detail = f"Precondition {i+1} failed: {precond}"
                    ctx.terminator.route(TerminError(
                        source=comp["name"]["display"], kind="precondition_failed",
                        message=detail))
                    raise HTTPException(status_code=412, detail=detail)
            except HTTPException:
                raise
            except Exception as e:
                tx.rollback()
                raise HTTPException(status_code=500, detail=f"Precondition evaluation error: {e}")

        # Block C: Boundary enforcement
        comp_snake_name = comp["name"]["snake"]
        for acc_content in comp.get("accesses", []):
            bnd_err = check_boundary_access(
                ctx.boundary_for_compute, ctx.boundary_for_content,
                comp_snake_name, acc_content)
            if bnd_err:
                tx.rollback()
                raise HTTPException(status_code=403, detail=bnd_err)

        # Execute the CEL body
        body_lines = comp.get("body_lines", [])
        if not body_lines:
            raise HTTPException(status_code=400, detail="Compute has no body to execute")

        cel_body = body_lines[0]
        try:
            eval_ctx = dict(compute_ctx)
            if isinstance(input_data, dict):
                eval_ctx.update(input_data)
            elif isinstance(input_data, list):
                for input_name in comp.get("input_content", []):
                    eval_ctx[input_name] = input_data

            # Check 3: CEL redaction guard
            redacted_err = check_for_redacted_values(eval_ctx)
            if redacted_err:
                tx.rollback()
                ctx.terminator.route(TerminError(
                    source="expression", kind="redacted_field_access",
                    message=redacted_err))
                raise HTTPException(status_code=500, detail=redacted_err)

            result = ctx.expr_eval.evaluate(cel_body, eval_ctx)
        except HTTPException:
            raise
        except Exception as e:
            tx.rollback()
            _audit_err_completed = _dt.datetime.utcnow()
            _audit_err_duration = (_audit_err_completed - _audit_started).total_seconds() * 1000
            await write_audit_trace(
                ctx, comp, invocation_id=tx.id, trigger="api",
                started_at=_audit_started_str,
                completed_at=_audit_err_completed.isoformat() + "Z",
                latency_ms=_audit_err_duration, outcome="error",
                error_message=str(e),
                trace_data={"compute_type": "cel", "expression": cel_body, "error": str(e)},
            )
            raise HTTPException(status_code=500, detail=f"Compute evaluation failed: {e}")

        output = {"result": result, "transaction_id": tx.id}

        # Before/After snapshots for postconditions
        before_data = {"result": None}
        after_data = {"result": result}

        try:
            db = await get_db(ctx.db_path)
            all_content_refs = set(
                comp.get("input_content", []) + comp.get("output_content", [])
                + comp.get("accesses", []))
            for content_name in all_content_refs:
                records = await list_records(db, content_name)
                before_data[content_name] = records
                after_data[content_name] = await tx.read_all(content_name, records)
            await db.close()
        except Exception:
            pass

        before_snapshot_obj = ContentSnapshot(
            {k: v for k, v in before_data.items() if k != "result"}, result=None)
        after_snapshot_obj = ContentSnapshot(
            {k: v for k, v in after_data.items() if k != "result"}, result=result)

        # Evaluate postconditions
        post_ctx = dict(compute_ctx)
        post_ctx["After"] = after_data
        post_ctx["Before"] = before_data
        for i, postcond in enumerate(comp.get("postconditions", [])):
            try:
                check = ctx.expr_eval.evaluate(postcond, post_ctx)
                if not check:
                    tx.rollback()
                    detail = f"Postcondition {i+1} failed: {postcond}"
                    ctx.terminator.route(TerminError(
                        source=comp["name"]["display"], kind="postcondition_failed",
                        message=detail))
                    raise HTTPException(status_code=409, detail=detail)
            except HTTPException:
                raise
            except Exception:
                pass

        # Check 4: Output taint enforcement
        final_output, taint_err = enforce_output_taint(output, comp, user_scopes)
        if taint_err:
            tx.rollback()
            ctx.terminator.route(TerminError(
                source=comp["name"]["display"], kind="output_taint_blocked",
                message=taint_err))
            raise HTTPException(status_code=403, detail=taint_err)

        # D-20: Audit trace on success
        _audit_completed = _dt.datetime.utcnow()
        _audit_duration = (_audit_completed - _audit_started).total_seconds() * 1000
        audit_level = comp.get("audit_level", "actions")
        trace_data = {"compute_type": "cel", "expression": cel_body, "output": result}
        if audit_level == "debug":
            trace_data["input"] = input_data
        await write_audit_trace(
            ctx, comp, invocation_id=tx.id, trigger="api",
            started_at=_audit_started_str,
            completed_at=_audit_completed.isoformat() + "Z",
            latency_ms=_audit_duration, outcome="success",
            trace_data=trace_data,
        )

        return final_output

    # Slice 7.2.x: bridge POST /api/v1/compute/{compute_name}/trigger
    # to the pure trigger_compute_handler in termin-core. The handler
    # reads check_compute_access through ctx; stash it on first
    # registration so the bridge is the only place this binding
    # lives.
    if not hasattr(ctx, "check_compute_access"):
        ctx.check_compute_access = check_compute_access

    from termin_core.routing import trigger_compute_handler
    from .fastapi_adapter import (
        make_auth_context,
        to_fastapi_response,
        to_termin_request,
    )

    @app.post("/api/v1/compute/{compute_name}/trigger")
    async def trigger_compute(compute_name: str, request: Request):
        """Manually trigger any Compute regardless of declared trigger type.

        The sibling endpoint POST /api/v1/compute/{compute_name} runs CEL
        computes synchronously and returns the result. This endpoint is
        for the provider types whose normal trigger is an event or a
        schedule — llm and ai-agent — so they can be invoked on demand
        for testing, dev-loop iteration, or "re-run on this record"
        workflows.

        Bridge to termin_core.routing.trigger_compute_handler.
        """
        user = ctx.get_current_user(request)
        auth = make_auth_context(user)
        termin_req = await to_termin_request(
            request,
            path_params={"compute_name": compute_name},
            auth=auth,
            legacy_user_dict=user,
        )
        response = await trigger_compute_handler(termin_req, ctx)
        return to_fastapi_response(response)


# ── LLM streaming support (v0.8 #7) ──
#
# Two publishers, one for each streaming mode:
#   publish_stream_deltas        — text streaming (stream_complete)
#   publish_agent_stream_events  — tool-use streaming (stream_agent_response)
#
# See docs/termin-streaming-protocol.md for the full protocol.


async def publish_agent_stream_events(event_bus, invocation_id: str,
                                       compute_name: str, stream,
                                       tool_name: str = "set_output"):
    """Pump tool-use stream events from stream_agent_response onto the
    event bus on the tool-use channels described in the protocol:

      compute.stream.<invocation_id>                   (done event)
      compute.stream.<invocation_id>.field.<name>      (field_delta/done)

    Returns the final output dict from the agent's set_output call so
    the caller can persist the result.

    Args:
        event_bus: runtime EventBus.
        invocation_id: UUID assigned at invocation start.
        compute_name: Compute's snake_name.
        stream: async generator yielding event dicts from
            AIProvider.stream_agent_response — shapes:
              {"type":"field_delta","field":<name>,"delta":<text>}
              {"type":"field_done","field":<name>,"value":<final>}
              {"type":"done","output":<dict>}
        tool_name: the tool whose input is being streamed (default
            "set_output").
    """
    base_channel = f"compute.stream.{invocation_id}"
    output = {}
    async for ev in stream:
        etype = ev.get("type")
        if etype == "field_delta":
            field = ev.get("field", "")
            await event_bus.publish({
                "channel_id": f"{base_channel}.field.{field}",
                "data": {
                    "invocation_id": invocation_id,
                    "compute": compute_name,
                    "mode": "tool_use",
                    "tool": tool_name,
                    "field": field,
                    "delta": ev.get("delta", ""),
                    "done": False,
                },
            })
        elif etype == "field_done":
            field = ev.get("field", "")
            value = ev.get("value")
            output[field] = value
            await event_bus.publish({
                "channel_id": f"{base_channel}.field.{field}",
                "data": {
                    "invocation_id": invocation_id,
                    "compute": compute_name,
                    "mode": "tool_use",
                    "tool": tool_name,
                    "field": field,
                    "done": True,
                    "value": value,
                },
            })
        elif etype == "done":
            provider_output = ev.get("output") or {}
            final_output = {**output, **provider_output}
            await event_bus.publish({
                "channel_id": base_channel,
                "data": {
                    "invocation_id": invocation_id,
                    "compute": compute_name,
                    "mode": "tool_use",
                    "tool": tool_name,
                    "done": True,
                    "output": final_output,
                },
            })
            return final_output
    # Stream exited without a top-level done event — emit one.
    await event_bus.publish({
        "channel_id": base_channel,
        "data": {
            "invocation_id": invocation_id,
            "compute": compute_name,
            "mode": "tool_use",
            "tool": tool_name,
            "done": True,
            "output": output,
        },
    })
    return output


async def publish_stream_deltas(event_bus, invocation_id: str,
                                compute_name: str, stream):
    """Iterate the stream generator, publishing each delta to the event
    bus, and return the concatenated final text. Used for text-mode
    streaming (stream_complete).

    Args:
        event_bus: runtime EventBus.
        invocation_id: UUID assigned at invocation start.
        compute_name: the Compute's snake_name (used in event payloads).
        stream: async generator yielding (delta: str, done: bool).

    Returns:
        The concatenated final_text.
    """
    channel = f"compute.stream.{invocation_id}"
    parts = []
    async for delta, done in stream:
        if done:
            # Terminal event: include final_text for latecomers.
            parts.append(delta)
            final_text = "".join(parts)
            await event_bus.publish({
                "channel_id": channel,
                "data": {
                    "invocation_id": invocation_id,
                    "compute": compute_name,
                    "delta": delta,
                    "done": True,
                    "final_text": final_text,
                },
            })
            return final_text
        parts.append(delta)
        await event_bus.publish({
            "channel_id": channel,
            "data": {
                "invocation_id": invocation_id,
                "compute": compute_name,
                "delta": delta,
                "done": False,
            },
        })
    # Stream exited without a done=True signal — treat as terminal.
    final_text = "".join(parts)
    await event_bus.publish({
        "channel_id": channel,
        "data": {
            "invocation_id": invocation_id,
            "compute": compute_name,
            "delta": "",
            "done": True,
            "final_text": final_text,
        },
    })
    return final_text
