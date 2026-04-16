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
from .storage import get_db, create_record, get_record, update_record
from .state import do_state_transition
from .ai_provider import AIProviderError, build_output_tool, build_agent_tools
from .confidentiality import (
    check_compute_access, check_taint_integrity, enforce_output_taint,
    check_for_redacted_values,
)
from .errors import TerminError
from .transaction import Transaction, ContentSnapshot
from .boundaries import check_boundary_access


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
    _llm_started = _dt.datetime.utcnow()
    _llm_invocation_id = str(uuid.uuid4())

    if not ctx.ai_provider.is_configured:
        print(f"[Termin] Compute '{comp_name}': AI provider not configured, skipped")
        return

    # Read input fields from record
    input_values = {}
    for content_ref, field_name in comp.get("input_fields", []):
        if field_name in record:
            input_values[field_name] = record[field_name]

    # Build prompts
    directive = comp.get("directive", "You are a helpful assistant.")
    objective = comp.get("objective", "")

    # Interpolate inline expressions in objective
    for fname, fval in input_values.items():
        singular = ctx.singular_lookup.get(
            content_name,
            content_name.rstrip("s") if content_name.endswith("s") else content_name)
        objective = objective.replace(f"{singular}.{fname}", str(fval))

    # Build user message from input fields
    if input_values:
        user_msg = objective + "\n\n" + "\n".join(f"{k}: {v}" for k, v in input_values.items())
    else:
        user_msg = objective

    # Build output tool
    output_fields = comp.get("output_fields", [])
    output_tool = build_output_tool(output_fields, ctx.content_lookup)

    print(f"[Termin] Compute '{comp_name}': calling {ctx.ai_provider.service} (record {record.get('id', '?')})")

    try:
        result = await ctx.ai_provider.complete(directive, user_msg, output_tool)
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
                    sets = ", ".join(f"{k} = ?" for k in update_data)
                    vals = list(update_data.values()) + [record["id"]]
                    await db.execute(f"UPDATE {content_name} SET {sets} WHERE id = ?", tuple(vals))
                    await db.commit()
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
            trace_data["calls"][0]["system_prompt"] = directive
            trace_data["calls"][0]["thinking"] = thinking
        await write_audit_trace(
            ctx, comp, invocation_id=_llm_invocation_id, trigger="event",
            started_at=_llm_started.isoformat() + "Z",
            completed_at=_llm_completed.isoformat() + "Z",
            duration_ms=_llm_duration, outcome="success",
            trace_data=trace_data,
        )
    except AIProviderError as e:
        print(f"[Termin] [ERROR] Compute '{comp_name}': {e}")
        _llm_err_completed = _dt.datetime.utcnow()
        _llm_err_duration = (_llm_err_completed - _llm_started).total_seconds() * 1000
        await write_audit_trace(
            ctx, comp, invocation_id=_llm_invocation_id, trigger="event",
            started_at=_llm_started.isoformat() + "Z",
            completed_at=_llm_err_completed.isoformat() + "Z",
            duration_ms=_llm_err_duration, outcome="error",
            error_message=str(e),
            trace_data={"compute_type": "agent", "error": str(e)},
        )


async def _execute_agent_compute(ctx: RuntimeContext, comp: dict, record: dict,
                                  content_name: str, main_loop=None):
    """Execute a Level 3 Agent Compute — autonomous with tool calls."""
    comp_name = comp["name"]["display"]
    _agent_started = _dt.datetime.utcnow()
    _agent_invocation_id = str(uuid.uuid4())

    if not ctx.ai_provider.is_configured:
        print(f"[Termin] Compute '{comp_name}': AI provider not configured, skipped")
        return

    directive = comp.get("directive", "You are a helpful AI agent.")
    objective = comp.get("objective", "")
    accesses = comp.get("accesses", [])

    # Build tools
    agent_tools = build_agent_tools(accesses, ctx.content_lookup)
    set_output = {
        "name": "set_output",
        "description": "Signal that you have completed the task. Call this when done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "thinking": {"type": "string", "description": "Brief summary of what you did."},
                "summary": {"type": "string", "description": "Result summary."},
            },
            "required": ["thinking"],
        }
    }
    all_tools = agent_tools + [set_output]

    # Build user message with context
    user_msg = f"{objective}\n\nTriggering record:\n{json.dumps(record, indent=2, default=str)}"

    # Tool execution function
    comp_snake = comp["name"]["snake"]

    async def _execute_tool(tool_name: str, tool_input: dict) -> dict:
        db = await get_db(ctx.db_path)
        try:
            if tool_name == "content_query":
                cname = tool_input.get("content_name", "")
                if cname not in accesses:
                    return {"error": f"Access denied: {cname} not in Accesses"}
                bnd_err = check_boundary_access(
                    ctx.boundary_for_compute, ctx.boundary_for_content,
                    comp_snake, cname)
                if bnd_err:
                    return {"error": bnd_err}
                filters = tool_input.get("filters", {})
                if filters:
                    where = " AND ".join(f"{k} = ?" for k in filters)
                    cursor = await db.execute(
                        f"SELECT * FROM {cname} WHERE {where}", tuple(filters.values()))
                else:
                    cursor = await db.execute(f"SELECT * FROM {cname}")
                return [dict(r) for r in await cursor.fetchall()]

            elif tool_name == "content_create":
                cname = tool_input.get("content_name", "")
                if cname not in accesses:
                    return {"error": f"Access denied: {cname} not in Accesses"}
                bnd_err = check_boundary_access(
                    ctx.boundary_for_compute, ctx.boundary_for_content,
                    comp_snake, cname)
                if bnd_err:
                    return {"error": bnd_err}
                data = tool_input.get("data", {})
                sm_info = ctx.sm_lookup.get(cname)
                if sm_info:
                    data["status"] = sm_info.get("initial", "")
                schema = ctx.content_lookup.get(cname, {})
                rec = await create_record(db, cname, data, schema, sm_info,
                                          ctx.terminator, ctx.event_bus)
                return rec

            elif tool_name == "content_update":
                cname = tool_input.get("content_name", "")
                if cname not in accesses:
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
                if cname not in accesses:
                    return {"error": f"Access denied: {cname} not in Accesses"}
                bnd_err = check_boundary_access(
                    ctx.boundary_for_compute, ctx.boundary_for_content,
                    comp_snake, cname)
                if bnd_err:
                    return {"error": bnd_err}
                rid = tool_input.get("record_id")
                target = tool_input.get("target_state")
                result = await do_state_transition(
                    db, cname, rid, target,
                    {"role": "service", "scopes": list(ctx.scope_for_content_verb(cname, "update") or [])},
                    ctx.sm_lookup, ctx.terminator, ctx.event_bus)
                return result

            else:
                return {"error": f"Unknown tool: {tool_name}"}
        finally:
            await db.close()

    print(f"[Termin] Compute '{comp_name}': starting agent loop ({ctx.ai_provider.service})")

    try:
        result = await ctx.ai_provider.agent_loop(directive, user_msg, all_tools, _execute_tool)
        thinking = result.get("thinking", "")
        if thinking:
            print(f"[Termin] Compute '{comp_name}' completed: {thinking[:100]}")

        _agent_completed = _dt.datetime.utcnow()
        _agent_duration = (_agent_completed - _agent_started).total_seconds() * 1000
        audit_level = comp.get("audit_level", "actions")
        trace_data = {"compute_type": "agent", "calls": [{"response": thinking[:200] if thinking else ""}]}
        if audit_level == "debug":
            trace_data["calls"][0]["system_prompt"] = directive
            trace_data["calls"][0]["thinking"] = thinking
        await write_audit_trace(
            ctx, comp, invocation_id=_agent_invocation_id, trigger="event",
            started_at=_agent_started.isoformat() + "Z",
            completed_at=_agent_completed.isoformat() + "Z",
            duration_ms=_agent_duration, outcome="success",
            trace_data=trace_data,
        )
    except AIProviderError as e:
        print(f"[Termin] [ERROR] Compute '{comp_name}': {e}")
        _agent_err_completed = _dt.datetime.utcnow()
        _agent_err_duration = (_agent_err_completed - _agent_started).total_seconds() * 1000
        await write_audit_trace(
            ctx, comp, invocation_id=_agent_invocation_id, trigger="event",
            started_at=_agent_started.isoformat() + "Z",
            completed_at=_agent_err_completed.isoformat() + "Z",
            duration_ms=_agent_err_duration, outcome="error",
            error_message=str(e),
            trace_data={"compute_type": "agent", "error": str(e)},
        )


# ── D-20: Audit trace recording ──

async def write_audit_trace(ctx: RuntimeContext, comp: dict, invocation_id: str,
                            trigger: str, started_at: str, completed_at: str,
                            duration_ms: float, outcome: str,
                            trace_data: dict = None, error_message: str = None,
                            total_input_tokens: int = 0, total_output_tokens: int = 0):
    """Write a trace record to the compute's audit log Content table."""
    audit_level = comp.get("audit_level", "actions")
    audit_ref = comp.get("audit_content_ref")
    if audit_level == "none" or not audit_ref:
        return

    trace_json = json.dumps(trace_data) if trace_data else "{}"
    record_data = {
        "compute_name": comp["name"]["display"],
        "invocation_id": invocation_id,
        "trigger": trigger,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "outcome": outcome,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "trace": trace_json,
        "error_message": error_message or "",
    }

    try:
        db = await get_db(ctx.db_path)
        try:
            columns = list(record_data.keys())
            placeholders = ", ".join("?" for _ in columns)
            cols_sql = ", ".join(columns)
            vals = tuple(record_data[c] for c in columns)
            await db.execute(
                f"INSERT INTO {audit_ref} ({cols_sql}) VALUES ({placeholders})", vals)
            await db.commit()
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
                    cursor = await db.execute(f"SELECT {field_name} FROM {content_name}")
                    rows = await cursor.fetchall()
                    for row in rows:
                        val = str(dict(row).get(field_name, ""))
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
                duration_ms=_audit_err_duration, outcome="error",
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
                cursor = await db.execute(f"SELECT * FROM {content_name}")
                rows = await cursor.fetchall()
                records = [dict(r) for r in rows]
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
            duration_ms=_audit_duration, outcome="success",
            trace_data=trace_data,
        )

        return final_output
