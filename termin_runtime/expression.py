"""JEXL expression evaluator for the Termin runtime."""

from pyjexl import JEXL


class ExpressionEvaluator:
    def __init__(self):
        self.jexl = JEXL()
        self._functions = {}

    def register_function(self, name, fn):
        self._functions[name] = fn

    def evaluate(self, expression, context=None):
        ctx = context or {}
        for name, fn in self._functions.items():
            ctx[name] = fn
        return self.jexl.evaluate(expression, ctx)
