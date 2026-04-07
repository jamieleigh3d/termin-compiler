"""JEXL expression evaluator for the Termin runtime.

Provides system-defined JEXL transforms and context variables available
in any expression without declaration.

JEXL uses pipe syntax for transforms: value|transform(args)
  items|sum, name|uppercase, 150|clamp(0, 100), date|daysUntil

Zero-argument "functions" are injected as context variables:
  now, today (evaluated fresh on each expression evaluation)
"""

import math
from datetime import datetime, date, timedelta

from pyjexl import JEXL


def _register_system_transforms(jexl: JEXL):
    """Register all system-defined transforms on a JEXL instance."""

    # ── Aggregation ──
    jexl.add_transform("sum", lambda arr: sum(arr) if arr else 0)
    jexl.add_transform("avg", lambda arr: (sum(arr) / len(arr)) if arr else 0)
    jexl.add_transform("min", lambda arr: min(arr) if arr else None)
    jexl.add_transform("max", lambda arr: max(arr) if arr else None)
    jexl.add_transform("count", lambda arr: len(arr) if isinstance(arr, (list, tuple)) else 0)

    # ── Collection ──
    jexl.add_transform("flatten", lambda arr: [x for sub in arr for x in (sub if isinstance(sub, list) else [sub])])
    jexl.add_transform("unique", lambda arr: list(dict.fromkeys(arr)))
    jexl.add_transform("first", lambda arr: arr[0] if arr else None)
    jexl.add_transform("last", lambda arr: arr[-1] if arr else None)
    jexl.add_transform("sort", lambda arr: sorted(arr) if arr else [])

    # ── Temporal ──
    def _days_between(d1, d2):
        try:
            a = date.fromisoformat(str(d1)[:10])
            b = date.fromisoformat(str(d2)[:10])
            return abs((b - a).days)
        except (ValueError, TypeError):
            return 0

    def _days_until(d):
        try:
            target = date.fromisoformat(str(d)[:10])
            return (target - date.today()).days
        except (ValueError, TypeError):
            return 0

    def _add_days(d, n):
        try:
            base = date.fromisoformat(str(d)[:10])
            return (base + timedelta(days=int(n))).isoformat()
        except (ValueError, TypeError):
            return d

    jexl.add_transform("daysBetween", _days_between)
    jexl.add_transform("daysUntil", _days_until)
    jexl.add_transform("addDays", _add_days)

    # ── String ──
    jexl.add_transform("uppercase", lambda s: str(s).upper() if s else "")
    jexl.add_transform("lowercase", lambda s: str(s).lower() if s else "")
    jexl.add_transform("trim", lambda s: str(s).strip() if s else "")
    jexl.add_transform("contains", lambda s, sub: str(sub) in str(s) if s else False)
    jexl.add_transform("startsWith", lambda s, pre: str(s).startswith(str(pre)) if s else False)
    jexl.add_transform("endsWith", lambda s, suf: str(s).endswith(str(suf)) if s else False)
    jexl.add_transform("replace", lambda s, old, new: str(s).replace(str(old), str(new)) if s else "")
    jexl.add_transform("length", lambda s: len(s) if s else 0)

    # ── Math ──
    jexl.add_transform("round", lambda n, d=0: round(float(n), int(d)) if n is not None else 0)
    jexl.add_transform("floor", lambda n: math.floor(float(n)) if n is not None else 0)
    jexl.add_transform("ceil", lambda n: math.ceil(float(n)) if n is not None else 0)
    jexl.add_transform("abs", lambda n: abs(float(n)) if n is not None else 0)
    jexl.add_transform("clamp", lambda n, lo, hi: max(float(lo), min(float(n), float(hi))))


def _make_dynamic_context():
    """Build context variables that are evaluated fresh each call."""
    return {
        "now": datetime.utcnow().isoformat() + "Z",
        "today": date.today().isoformat(),
    }


class ExpressionEvaluator:
    def __init__(self):
        self.jexl = JEXL()
        self._functions = {}
        _register_system_transforms(self.jexl)

    def register_function(self, name, fn):
        self._functions[name] = fn

    def evaluate(self, expression, context=None):
        ctx = _make_dynamic_context()   # temporal context, refreshed per call
        if context:
            ctx.update(context)         # user context overrides
        for name, fn in self._functions.items():
            ctx[name] = fn              # registered functions override
        return self.jexl.evaluate(expression, ctx)
