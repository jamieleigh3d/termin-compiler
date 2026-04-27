# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""State machine engine for the Termin runtime.

Takes transition tables as config and provides do_state_transition().

Phase 2.x (d): the transition is now an atomic CAS via the storage
provider's update_if() — the read-and-write window cannot be
interleaved by another concurrent transition. If two callers race
to advance the same record from the same source state, exactly
one wins; the loser sees HTTP 409 with the current (post-winner)
state.
"""

from fastapi import HTTPException

from .errors import TerminError
from .providers.storage_contract import Eq


async def do_state_transition(storage, table: str, record_id: int,
                              machine_name: str, target_state: str,
                              user: dict, state_machines: dict,
                              terminator=None, event_bus=None):
    """Attempt a state transition on a specific state machine.

    Args:
        storage: StorageProvider (typically ctx.storage). The
            transition is applied via storage.update_if so the
            read-and-write is atomic per the contract.
        table: content table name (snake_case)
        record_id: integer primary key
        machine_name: snake_case identifier of the state machine on this
            content. Same value as the SQL column. A content with two
            state machines (e.g. `lifecycle` and `approval_status`) selects
            which machine to drive via this argument.
        target_state: desired target state string
        user: user dict with 'scopes' key
        state_machines: dict of {table_name: list[sm_dict]} where each
            sm_dict has keys {machine_name, column, initial, transitions}.
            The transitions value is a dict of {(from_state, to_state):
            required_scope}.
        terminator: optional TerminAtor for error routing
        event_bus: optional EventBus for publishing events

    Self-transitions (from_state == to_state) are valid when declared in
    the transition table — they write the same value back and still
    publish the WebSocket event.

    Concurrency: if two callers race to advance the same record from
    the same source state, exactly one's update_if wins; the loser
    receives HTTP 409 with the post-winner state in the response.
    """
    if table not in state_machines:
        raise HTTPException(status_code=400, detail=f"No state machine for {table}")

    sm_list = state_machines[table]
    sm = next((s for s in sm_list if s["machine_name"] == machine_name), None)
    if sm is None:
        raise HTTPException(
            status_code=400,
            detail=f"No state machine '{machine_name}' on {table}")

    column = sm["column"]
    record = await storage.read(table, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    current = record.get(column, "")
    key = (current, target_state)
    if key not in sm["transitions"]:
        if terminator:
            terminator.route(TerminError(
                source=f"state:{table}:{machine_name}",
                kind="state",
                message=f"Cannot transition from '{current}' to '{target_state}'",
                context=f"record_id={record_id}",
            ))
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition from '{current}' to '{target_state}'"
        )

    required_scope = sm["transitions"][key]
    if required_scope and required_scope not in user["scopes"]:
        if terminator:
            terminator.route(TerminError(
                source=f"state:{table}:{machine_name}",
                kind="authorization",
                message=f"Transition requires scope: {required_scope}",
                context=f"record_id={record_id}, user_role={user.get('role', '')}",
            ))
        raise HTTPException(
            status_code=403,
            detail=f"Transition requires scope: {required_scope}"
        )

    # Atomic CAS: the update lands only if the column is still
    # `current` at write time. A racing transition from the same
    # source state will hit condition_failed.
    result = await storage.update_if(
        table, record_id,
        condition=Eq(field=column, value=current),
        patch={column: target_state},
    )

    if not result.applied:
        if result.reason == "not_found":
            # Race: record was deleted between our read and CAS.
            raise HTTPException(status_code=404, detail="Record not found")
        # condition_failed: another transition advanced the state
        # between our read and our CAS. Surface the current state
        # so the caller can display "already X" in the UI.
        post_race_state = (result.record or {}).get(column, "")
        if terminator:
            terminator.route(TerminError(
                source=f"state:{table}:{machine_name}",
                kind="state",
                message=(
                    f"Concurrent transition: another caller advanced "
                    f"this record to '{post_race_state}' before our "
                    f"transition from '{current}' could land"
                ),
                context=f"record_id={record_id}",
            ))
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot transition from '{current}' to '{target_state}': "
                f"record is now '{post_race_state}'"
            )
        )

    updated_record = result.record or {"id": record_id, column: target_state}

    if event_bus:
        await event_bus.publish({
            "channel_id": f"content.{table}.updated",
            "data": updated_record,
        })

    return updated_record
