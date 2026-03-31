"""Structured error types with line numbers for the an AWS-native Termin runtime compiler."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TerminError(Exception):
    message: str
    line: int = 0
    column: int = 0
    source_line: str = ""

    def __str__(self) -> str:
        location = f"line {self.line}" if self.line else "unknown location"
        prefix = self._prefix()
        result = f"{prefix} at {location}: {self.message}"
        if self.source_line:
            result += f"\n  | {self.source_line.rstrip()}"
            if self.column > 0:
                result += f"\n  | {' ' * (self.column - 1)}^"
        return result

    def _prefix(self) -> str:
        return "Error"


@dataclass
class ParseError(TerminError):
    def _prefix(self) -> str:
        return "Parse error"


@dataclass
class SemanticError(TerminError):
    def _prefix(self) -> str:
        return "Semantic error"


@dataclass
class SecurityError(TerminError):
    def _prefix(self) -> str:
        return "Security invariant violation"


@dataclass
class CompileResult:
    """Collects errors during compilation without stopping at the first one."""
    errors: list[TerminError] = field(default_factory=list)

    def add(self, error: TerminError) -> None:
        self.errors.append(error)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    @property
    def has_security_errors(self) -> bool:
        return any(isinstance(e, SecurityError) for e in self.errors)

    def format(self) -> str:
        if self.ok:
            return "No errors."
        lines = [f"Found {len(self.errors)} error(s):\n"]
        for e in self.errors:
            lines.append(f"  {e}\n")
        return "".join(lines)
