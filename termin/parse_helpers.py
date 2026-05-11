# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""TatSu grammar loading and parser helper functions.

Provides the compiled PEG grammar model, _try_parse for invoking TatSu,
and all the small utility functions for extracting values from parse results.
Also includes type expression parsing.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

import tatsu

from .ast_nodes import TypeExpr

# --- Load TatSu grammar ---
_GRAMMAR_PATH = Path(__file__).parent / "termin.peg"
_model = tatsu.compile(_GRAMMAR_PATH.read_text(encoding="utf-8"))


def _try_parse(line, rule):
    """Try to parse a line with a TatSu rule. Returns None on failure."""
    try: return _model.parse(line, rule_name=rule)
    except Exception: return None


def _rule(result) -> str:
    """Extract the rule name from a TatSu parse result."""
    if hasattr(result, "parseinfo") and result.parseinfo: return result.parseinfo.rule or ""
    if isinstance(result, dict):
        pi = result.get("parseinfo")
        if pi and hasattr(pi, "rule"): return pi.rule or ""
    return ""


def _qs(r) -> str:
    """Extract quoted string content from a TatSu result."""
    if isinstance(r, dict) and "content" in r: return r["content"]
    return str(r) if r is not None else "" if not isinstance(r, str) else r


def _ql(r) -> list[str]:
    """Extract a list of quoted strings from a TatSu result."""
    if r is None: return []
    if isinstance(r, dict) and "val" in r:
        v = r["val"]
        return [_qs(x) for x in v] if isinstance(v, list) else [_qs(v)]
    return [_qs(x) for x in r] if isinstance(r, list) else [_qs(r)]


def _cl(r) -> list[str]:
    """Extract a comma-separated list of items from a TatSu result."""
    if r is None: return []
    items = r.get("item") if isinstance(r, dict) and "item" in r else r if isinstance(r, list) else [r]
    if not isinstance(items, list): items = [items]
    return [str(i).strip() for i in items if i is not None and str(i).strip()]


def _ol(r) -> list[str]:
    """Extract an or-separated list of items from a TatSu result."""
    if r is None: return []
    if isinstance(r, dict) and "item" in r:
        items = r["item"]
        if not isinstance(items, list): items = [items]
        return [str(i).strip() for i in items if i is not None]
    return [str(i).strip() for i in r if i is not None] if isinstance(r, list) else [str(r).strip()]


def _si(val, d=0) -> int:
    """Safe integer conversion with default."""
    if val is None: return d
    try: return int(val)
    except (ValueError, TypeError): return d


def _scal(text: str) -> list[str]:
    """Split comma-and list: 'a, b, and c' -> ['a','b','c']."""
    text = text.strip().rstrip(":")
    result = []
    for part in text.split(","):
        p = part.strip()
        if not p: continue
        if p.startswith("and "): p = p[4:].strip()
        for ap in p.split(" and "):
            if ap.strip(): result.append(ap.strip())
    return result


def _eqs(text: str) -> list[str]:
    """Extract all double-quoted strings from text."""
    result, start = [], 0
    while True:
        q1 = text.find('"', start)
        if q1 < 0: break
        q2 = text.find('"', q1 + 1)
        if q2 < 0: break
        result.append(text[q1 + 1:q2]); start = q2 + 1
    return result


def _fq(text: str) -> str:
    """Extract the first double-quoted string from text."""
    q1 = text.find('"')
    if q1 < 0: return ""
    q2 = text.find('"', q1 + 1)
    return text[q1 + 1:q2] if q2 >= 0 else ""


def _eb(text: str) -> Optional[str]:
    """Extract backtick expression content from text."""
    b1 = text.find("`")
    if b1 < 0:
        # Legacy bracket fallback
        b1 = text.find("[")
        if b1 < 0: return None
        b2 = text.find("]", b1 + 1)
        return text[b1 + 1:b2].strip() if b2 >= 0 else None
    b2 = text.find("`", b1 + 1)
    return text[b1 + 1:b2].strip() if b2 >= 0 else None


def _parse_literal_list(text: str) -> list:
    """Parse a comma/and-separated list of literals (quoted strings or numbers)."""
    result = []
    for m in re.finditer(r'"([^"]*)"|(-?\d+(?:\.\d+)?)', text):
        if m.group(1) is not None:
            result.append(m.group(1))
        elif m.group(2) is not None:
            v = m.group(2)
            try:
                result.append(int(v))
            except ValueError:
                try:
                    result.append(float(v))
                except ValueError:  # pragma: no cover
                    result.append(v)
    return result


# --- Type expression parsing ---

def _parse_type_text(text: str, ln: int = 0) -> TypeExpr:
    """Parse a type expression string into a TypeExpr AST node."""
    expr = TypeExpr(base_type="text", line=ln)

    # Extract "confidentiality is ..." before other constraint stripping
    cm = re.search(r',?\s*confidentiality\s+is\s+("(?:[^"]*)"(?:\s+and\s+"[^"]*")*)', text, re.IGNORECASE)
    if cm:
        scope_str = cm.group(1)
        expr.confidentiality_scopes = [s.strip().strip('"') for s in re.findall(r'"([^"]*)"', scope_str)]
        text = text[:cm.start()] + text[cm.end():]

    # v0.9: cascade declarations on type_text fields. They are not
    # legal here (cascade only makes sense on `references` fields),
    # but recording them lets the analyzer emit a precise S040
    # error rather than silently dropping the violation. We strip
    # the tokens so the rest of the parser sees clean text.
    _record_cascade_modes(expr, text)
    for phrase in (", cascade on delete", ", restrict on delete",
                   "cascade on delete,", "restrict on delete,",
                   "cascade on delete", "restrict on delete"):
        text = text.replace(phrase, "")
    text = text.strip().rstrip(",").strip()

    # D-19: Extract "is one of: val1, val2, ..." constraint
    ioo_match = re.search(r',?\s*is\s+one\s+of:\s*(.+)', text, re.IGNORECASE)
    if ioo_match:
        vals_text = ioo_match.group(1).strip()
        for kw in [", required", ",required", ", unique", ",unique",
                   ", minimum", ",minimum", ", maximum", ",maximum",
                   ", defaults", ",defaults", ", confidentiality", ",confidentiality"]:
            ki = vals_text.lower().find(kw)
            if ki >= 0:
                vals_text = vals_text[:ki].strip()
        one_of_vals = _parse_literal_list(vals_text)
        if one_of_vals:
            expr.one_of_values = one_of_vals
            text = text[:ioo_match.start()] + text[ioo_match.end():]

    # Extract "defaults to `expr`" or "defaults to [expr]" (legacy) or 'defaults to "literal"'
    dm = re.search(r',?\s*defaults\s+to\s+`([^`]+)`', text, re.IGNORECASE)
    if not dm:
        dm = re.search(r',?\s*defaults\s+to\s+\[([^\]]+)\]', text, re.IGNORECASE)
    if dm:
        expr.default_expr = dm.group(1).strip()
        expr.default_is_expr = True
        text = text[:dm.start()] + text[dm.end():]
    else:
        dm = re.search(r',?\s*defaults\s+to\s+"([^"]*)"', text, re.IGNORECASE)
        if dm:
            expr.default_expr = dm.group(1)
            expr.default_is_expr = False
            text = text[:dm.start()] + text[dm.end():]
        else:
            # v0.9.4 (compiler issues #4 + #6): bare default literals.
            # Numbers (positive, negative, integer, decimal) and the
            # bare yes/no tokens. The natural form for
            # `defaults to 300` and `defaults to no`. Matched after
            # the backtick + quoted alternatives so those win when
            # both could apply (a quoted "300" stays a quoted
            # literal). Anchored on word boundary so we don't
            # match the `no` inside `not`, the `yes` inside `yesterday`,
            # etc. The bare number regex requires a digit, so the
            # alternation is unambiguous: either it's a number or
            # it's exactly "yes" / "no".
            dm = re.search(
                r',?\s*defaults\s+to\s+(\-?\d+(?:\.\d+)?|yes|no)\b',
                text, re.IGNORECASE,
            )
            if dm:
                value = dm.group(1)
                expr.default_expr = value
                # v0.9.4 (compiler issue #6): bare numeric defaults
                # mark as expression so the runtime evaluates them as
                # CEL int/float (matching the backtick `300` form)
                # rather than storing them as text strings. yes/no
                # tokens are text literals — same shape as
                # `defaults to "no"`.
                is_numeric = bool(re.fullmatch(r'\-?\d+(?:\.\d+)?', value))
                expr.default_is_expr = is_numeric
                text = text[:dm.start()] + text[dm.end():]

    while True:
        tl = text.lower().rstrip()
        if tl.endswith(", required") or tl.endswith(",required"):
            expr.required = True; text = text[:text.lower().rfind("required")].rstrip().rstrip(",").strip(); continue
        if tl.endswith(", unique") or tl.endswith(",unique"):
            expr.unique = True; text = text[:text.lower().rfind("unique")].rstrip().rstrip(",").strip(); continue
        changed = False
        for mk, attr in [(",maximum ", "maximum"), (", maximum ", "maximum"),
                         (",minimum ", "minimum"), (", minimum ", "minimum")]:
            idx = tl.rfind(mk)
            if idx >= 0:
                vt = text[idx + len(mk):].strip().rstrip(",").strip()
                c = vt.find(",")
                setattr(expr, attr, _si(vt[:c].strip() if c >= 0 else vt))
                text = text[:idx].strip(); changed = True; break
        if not changed: break

    text = text.strip()
    # Inverted-form prefixes: 'required' and/or 'unique' may appear
    # before the base type, in either order, space-separated. The
    # canonical postfix form is processed in the loop above; this
    # handles `required text`, `unique text`, `required unique text`,
    # `unique required text`. Both orderings produce the same TypeExpr
    # as the canonical form. Loop until neither prefix matches so the
    # ordering is symmetric.
    while True:
        if text.startswith("required "):
            expr.required = True
            text = text[len("required "):].strip()
            continue
        if text.startswith("unique "):
            expr.unique = True
            text = text[len("unique "):].strip()
            continue
        break

    TM = {"text": "text", "currency": "currency", "number": "number",
          "percentage": "percentage", "true/false": "boolean", "boolean": "boolean",
          "date and time": "datetime", "date": "date", "automatic": "automatic",
          "a whole number": "whole_number", "whole number": "whole_number",
          "principal": "principal", "structured": "structured",
          "conversation": "conversation"}
    if text in TM: expr.base_type = TM[text]
    elif text.startswith("one of:"):
        expr.base_type = "enum"
        expr.enum_values = [v.strip().strip('"') for v in _scal(text[len("one of:"):])]
    elif text.startswith("list of "):
        expr.base_type = "list"; expr.list_type = text[len("list of "):].strip().strip('"')
    return expr


def _parse_field_type(text: str, ln: int) -> TypeExpr:
    """Parse a field type clause ('is text', 'references X', 'is state:', etc.)."""
    # v0.9: inline state machine field — `which is state:` opens a sub-block.
    stripped = text.strip()
    if stripped in ("is state:", "is state :"):
        return TypeExpr(base_type="state", line=ln)
    if text.startswith("is "): return _parse_type_text(text[len("is "):], ln)
    if text.startswith("references "):
        rt = text[len("references "):].strip(); ci = rt.find(","); ct = ""
        if ci >= 0: ct = rt[ci:]; rt = rt[:ci].strip()
        te = TypeExpr(base_type="reference", references=rt.strip('"'), line=ln)
        if "required" in ct: te.required = True
        if "unique" in ct: te.unique = True
        _record_cascade_modes(te, ct)
        return te
    return TypeExpr(base_type="text", line=ln)


def _record_cascade_modes(te, constraint_tail: str) -> None:
    """v0.9: scan a constraint tail for `cascade on delete` and
    `restrict on delete`. Records declared mode(s) on the TypeExpr.

    The analyzer enforces:
      - S039: every reference field MUST declare exactly one mode.
      - S040: cascade declarations on non-reference fields are
              rejected (this helper runs from both reference and
              type_text paths so the analyzer sees the violation).
      - S041: declaring both modes on the same field is rejected.
              The `_cascade_modes_seen` tuple records every detected
              mode so the analyzer can cite both.
    """
    seen = []
    if "cascade on delete" in constraint_tail:
        seen.append("cascade")
    if "restrict on delete" in constraint_tail:
        seen.append("restrict")
    if seen:
        te.cascade_mode = seen[0]
        te._cascade_modes_seen = tuple(seen)
