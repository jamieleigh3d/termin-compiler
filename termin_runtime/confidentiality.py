"""Confidentiality enforcement for the Termin runtime.

Provides field-level and content-level redaction of confidential data.
Records crossing API boundaries have restricted fields replaced with
redaction markers. The record itself is never omitted — even if every
field is redacted, the record remains present with its id and markers.

Redaction marker format: {"__redacted": True, "scope": "scope_name"}
"""


def effective_scopes(field_ir: dict, content_ir: dict) -> set[str]:
    """Return the set of scopes required to see a field (AND semantics).

    Combines content-level scopes with field-level scopes. The caller
    must hold ALL scopes in the set to see the field unredacted.
    """
    scopes = set()
    scopes.update(content_ir.get("confidentiality_scopes") or [])
    scopes.update(field_ir.get("confidentiality_scopes") or [])
    return scopes


def redact_record(record: dict, content_ir: dict, caller_scopes: set[str]) -> dict:
    """Replace restricted field values with redaction markers.

    System fields (id, status for state machines) pass through unless they
    have explicit confidentiality scopes. Fields not in the schema also
    pass through (e.g., auto-generated id).
    """
    fields_by_name = {f["name"]: f for f in content_ir.get("fields", [])}
    result = {}
    for key, value in record.items():
        field_ir = fields_by_name.get(key)
        if field_ir is None:
            # System field (id) — always passes through
            result[key] = value
            continue
        required = effective_scopes(field_ir, content_ir)
        if required and not required.issubset(caller_scopes):
            missing = sorted(required - caller_scopes)
            result[key] = {"__redacted": True, "scope": missing[0]}
        else:
            result[key] = value
    return result


def redact_records(records: list[dict], content_ir: dict, caller_scopes: set[str]) -> list[dict]:
    """Redact a list of records."""
    return [redact_record(r, content_ir, caller_scopes) for r in records]


def is_redacted(value) -> bool:
    """Check if a value is a redaction marker."""
    return isinstance(value, dict) and value.get("__redacted") is True


def check_write_access(fields_to_write: dict, content_ir: dict, caller_scopes: set[str]) -> str | None:
    """Check if the caller can write to all fields in the payload.

    Returns None if OK, or an error message if a restricted field is being written.
    """
    fields_by_name = {f["name"]: f for f in content_ir.get("fields", [])}
    for key in fields_to_write:
        field_ir = fields_by_name.get(key)
        if field_ir is None:
            continue  # unknown fields handled elsewhere
        required = effective_scopes(field_ir, content_ir)
        if required and not required.issubset(caller_scopes):
            missing = sorted(required - caller_scopes)
            return f"Cannot write to field '{key}' — requires scope '{missing[0]}'"
    return None
