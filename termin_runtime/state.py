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

    if event_bus:
        await event_bus.publish({"type": f"{table}_updated", "id": record_id, "status": target_state})

    return {"id": record_id, "status": target_state}
