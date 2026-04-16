"""State machine engine for the Termin runtime.

Takes transition tables as config and provides do_state_transition().
"""

from fastapi import HTTPException

from .errors import TerminError


async def do_state_transition(db, table: str, record_id: int, target_state: str,
                              user: dict, state_machines: dict,
                              terminator=None, event_bus=None):
    """Attempt a state transition. Raises HTTPException on invalid transition.

    Args:
        db: aiosqlite connection
        table: content table name (snake_case)
        record_id: integer primary key
        target_state: desired target state string
        user: user dict with 'scopes' key
        state_machines: dict of {table_name: {"initial": str, "transitions": {(from, to): scope}}}
        terminator: optional TerminAtor for error routing
        event_bus: optional EventBus for publishing events
    """
    if table not in state_machines:
        raise HTTPException(status_code=400, detail=f"No state machine for {table}")

    sm = state_machines[table]
    cursor = await db.execute(f"SELECT status FROM {table} WHERE id = ?", (record_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")

    current = row["status"]
    key = (current, target_state)
    if key not in sm["transitions"]:
        if terminator:
            terminator.route(TerminError(
                source=f"state:{table}",
                kind="state",
                message=f"Cannot transition from '{current}' to '{target_state}'",
                context=f"record_id={record_id}",
            ))
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition from '{current}' to '{target_state}'"
        )

    required_scope = sm["transitions"][key]
    if required_scope not in user["scopes"]:
        if terminator:
            terminator.route(TerminError(
                source=f"state:{table}",
                kind="authorization",
                message=f"Transition requires scope: {required_scope}",
                context=f"record_id={record_id}, user_role={user.get('role', '')}",
            ))
        raise HTTPException(
            status_code=403,
            detail=f"Transition requires scope: {required_scope}"
        )

    await db.execute(f"UPDATE {table} SET status = ? WHERE id = ?", (target_state, record_id))
    await db.commit()

    # Fetch the full updated record for WebSocket push
    cursor = await db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,))
    updated_row = await cursor.fetchone()
    updated_record = dict(updated_row) if updated_row else {"id": record_id, "status": target_state}

    if event_bus:
        await event_bus.publish({
            "channel_id": f"content.{table}.updated",
            "data": updated_record,
        })

    return updated_record
