# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 5c.2: matcher for contract-package source verbs.

Each contract package declares one source-verb template per
contract, e.g. `Show a cosmic orb of <state-ref>`. The matcher's
job: given a source DSL line and a registered template, decide
whether the line instantiates the template and, if so, extract
the placeholder bindings.

Design choices for v0.9:

  * Pure-Python matching, NOT TatSu. The TatSu-context-state-leak
    that bit the access-rule fallback (memory:
    feedback_grammar_peg_authoritative) makes runtime grammar
    extension via TatSu uneven across platforms. Pure-Python
    keeps the surface platform-uniform and dodges the
    fallback-fidelity discipline entirely (there's no TatSu path
    to fall back from).

  * Whitespace-insensitive matching. `Show a cosmic orb of foo`
    and `Show a  cosmic orb  of foo` both match
    `Show a cosmic orb of <state-ref>`.

  * Placeholders match a single content-name token (snake-case,
    bareword). `<state-ref>` against `Show a cosmic orb of
    "long phrase"` does NOT match — content names are bare
    identifiers in source. Future contract templates that need
    multi-word phrases can use a different placeholder shape;
    v0.9 keeps this simple.

  * Greedy left-to-right matching with literal anchors. A
    template's literal segments must appear verbatim (modulo
    whitespace); placeholders fill the gaps.

  * Verb collisions are caught at load time by
    `ContractPackageRegistry.add()`, not here. The matcher
    assumes the registry has already enforced uniqueness across
    loaded packages.

The output of `match_verb` is None (no match) or a dict mapping
placeholder name (without angle brackets) to the matched token.
The two-pass compiler integrates this in `classify_line` —
when the legacy prefix loop returns "unknown" or generic line
shape, the parser consults `match_active_packages(line)` and
routes a successful match to the `package_contract_line`
handler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ── Template parsing ──

_PLACEHOLDER_RE = re.compile(r"<([a-zA-Z][a-zA-Z0-9_-]*)>")


@dataclass(frozen=True)
class _Token:
    """One segment of a parsed verb template — either a literal run
    of words or a placeholder that captures a single bare token."""
    kind: str  # "literal" | "placeholder"
    text: str  # for literals: the literal text; for placeholders: the name


def _tokenize_template(template: str) -> tuple[_Token, ...]:
    """Split a template into alternating literal / placeholder tokens.

    Whitespace inside literals is collapsed to single spaces.
    Empty literals between consecutive placeholders are dropped —
    consecutive placeholders are not supported in v0.9, but the
    matcher's structure tolerates them gracefully (the second
    placeholder eats whatever the first left).
    """
    tokens: list[_Token] = []
    pos = 0
    for m in _PLACEHOLDER_RE.finditer(template):
        before = template[pos:m.start()].strip()
        if before:
            tokens.append(_Token("literal", _normalize_ws(before)))
        tokens.append(_Token("placeholder", m.group(1)))
        pos = m.end()
    tail = template[pos:].strip()
    if tail:
        tokens.append(_Token("literal", _normalize_ws(tail)))
    return tuple(tokens)


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ── Bareword token shape ──

# Content names in Termin are snake-case identifiers (BRD #2 §10
# implies single-token content references in source `Using` clauses
# and verb instantiations). Allow optional surrounding whitespace.
_BAREWORD_RE = re.compile(r"[a-z][a-z0-9_]*")


def match_verb(line: str, template: str) -> Optional[dict[str, str]]:
    """Try to match `line` against `template`. On success return a
    dict of placeholder bindings; on failure return None.

    The match is total — the entire line must be consumed (after
    whitespace normalization). Trailing punctuation is not allowed
    in v0.9; future template forms may relax this.

    Example:

        match_verb(
            "Show a cosmic orb of scenarios",
            "Show a cosmic orb of <state-ref>",
        )  # => {"state-ref": "scenarios"}
    """
    tokens = _tokenize_template(template)
    if not tokens:
        return None
    # Walk the line position by position consuming each token in
    # order. Literals must match verbatim (whitespace-normalized);
    # placeholders match exactly one bareword identifier.
    norm_line = _normalize_ws(line)
    pos = 0
    bindings: dict[str, str] = {}
    for i, tok in enumerate(tokens):
        if tok.kind == "literal":
            # Literal must appear verbatim at current position.
            if not norm_line[pos:].startswith(tok.text):
                return None
            pos += len(tok.text)
            # Consume one whitespace if any (between literal and
            # next token) — allowed but not required at end.
            if pos < len(norm_line) and norm_line[pos] == " ":
                pos += 1
        else:  # placeholder
            # Match a bareword starting at pos.
            m = _BAREWORD_RE.match(norm_line, pos)
            if not m:
                return None
            bindings[tok.text] = m.group(0)
            pos = m.end()
            # Consume one whitespace if any.
            if pos < len(norm_line) and norm_line[pos] == " ":
                pos += 1
    if pos != len(norm_line):
        # Trailing content the template didn't account for.
        return None
    return bindings


# ── Active-registry hook (consulted by the parser) ──

# Module-level state — set by the compiler during parse, cleared
# after. Module-level rather than thread-local because the parser
# is single-threaded; using a context-manager makes the lifecycle
# explicit.
_active_registry = None


def set_active_registry(registry) -> None:
    """Install a `ContractPackageRegistry` for the parser to consult.

    Called by the compile entry point before parsing begins, and
    cleared (set to None) afterward. Idempotent — multiple sets
    of the same registry are fine.
    """
    global _active_registry
    _active_registry = registry


def clear_active_registry() -> None:
    """Tear down the active registry. Call after parsing completes
    so subsequent parses (e.g., from tests in the same process)
    don't see stale state."""
    global _active_registry
    _active_registry = None


def get_active_registry():
    """Return the currently-installed registry, or None."""
    return _active_registry


def match_active_packages(line: str) -> Optional[tuple[str, dict[str, str]]]:
    """Try to match `line` against any source-verb in the active
    registry. On match, return (qualified_contract_name, bindings).
    On no-match (or no active registry), return None.

    The registry's `_verb_owners` dict maps source-verb → qualified
    name; we iterate it. Verb collisions across packages were
    rejected at registry-load time, so iteration order doesn't
    matter — at most one verb matches a given line.
    """
    reg = _active_registry
    if reg is None:
        return None
    # The registry exposes source_verbs() as a tuple of strings; the
    # qualified-name owner is in the private _verb_owners dict.
    # Reach into the private map deliberately — this is the same
    # module's contract surface.
    owners = getattr(reg, "_verb_owners", {})
    for verb, owner in owners.items():
        bindings = match_verb(line, verb)
        if bindings is not None:
            return owner, bindings
    return None


__all__ = [
    "match_verb",
    "set_active_registry",
    "clear_active_registry",
    "get_active_registry",
    "match_active_packages",
]
