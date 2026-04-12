"""Structured error types with line numbers and error codes for the Termin compiler.

Error code scheme:
  TERMIN-P001+  Parse errors
  TERMIN-S001+  Semantic errors
  TERMIN-X001+  Security invariant errors
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TerminError(Exception):
    message: str
    line: int = 0
    column: int = 0
    source_line: str = ""
    code: Optional[str] = None
    suggestion: Optional[str] = None

    def __str__(self) -> str:
        location = f"line {self.line}" if self.line else "unknown location"
        prefix = self._prefix()
        code_part = f" [{self.code}]" if self.code else ""
        result = f"{prefix}{code_part} at {location}: {self.message}"
        if self.suggestion:
            result += f"\n  hint: {self.suggestion}"
        if self.source_line:
            result += f"\n  | {self.source_line.rstrip()}"
            if self.column > 0:
                result += f"\n  | {' ' * (self.column - 1)}^"
        return result

    def _prefix(self) -> str:
        return "Error"

    def _severity(self) -> str:
        return "error"

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict for --format json output."""
        return {
            "code": self.code,
            "message": self.message,
            "line": self.line,
            "suggestion": self.suggestion,
            "severity": self._severity(),
        }


@dataclass
class ParseError(TerminError):
    def _prefix(self) -> str:
        return "Parse error"

    def _severity(self) -> str:
        return "error"


@dataclass
class SemanticError(TerminError):
    def _prefix(self) -> str:
        return "Semantic error"

    def _severity(self) -> str:
        return "error"


@dataclass
class SecurityError(TerminError):
    def _prefix(self) -> str:
        return "Security invariant violation"

    def _severity(self) -> str:
        return "error"


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

    def to_json_list(self) -> list[dict]:
        """Serialize all errors to JSON-friendly list."""
        return [e.to_dict() for e in self.errors]
