# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for SQL injection defense in depth.

Layer 1: Identifier validation — reject IR with unsafe names at load time.
Layer 2: Proper escaping in _q() — embedded quotes can't break out.
Layer 3: No raw SQL outside storage.py — verified by AST scan.
"""

import re
import ast
import pytest
from pathlib import Path


# ── Layer 1: Identifier validation ──

class TestIdentifierValidation:
    """IR identifiers that flow into SQL must match safe patterns."""

    def test_safe_identifier_accepted(self):
        from termin_server.storage import validate_identifier
        assert validate_identifier("orders") is True
        assert validate_identifier("order_items") is True
        assert validate_identifier("compute_audit_log_scanner") is True
        assert validate_identifier("a") is True
        assert validate_identifier("x123") is True

    def test_sql_injection_rejected(self):
        from termin_server.storage import validate_identifier
        assert validate_identifier('orders"; DROP TABLE orders; --') is False
        assert validate_identifier("orders' OR '1'='1") is False
        assert validate_identifier("name; DELETE FROM users") is False

    def test_empty_rejected(self):
        from termin_server.storage import validate_identifier
        assert validate_identifier("") is False

    def test_spaces_rejected(self):
        from termin_server.storage import validate_identifier
        assert validate_identifier("order items") is False
        assert validate_identifier("my table") is False

    def test_special_chars_rejected(self):
        from termin_server.storage import validate_identifier
        assert validate_identifier("table;") is False
        assert validate_identifier('table"') is False
        assert validate_identifier("table'") is False
        assert validate_identifier("table--") is False
        assert validate_identifier("table/*") is False

    def test_uppercase_rejected(self):
        """Only lowercase snake_case allowed — IR should always be lowered."""
        from termin_server.storage import validate_identifier
        assert validate_identifier("Orders") is False
        assert validate_identifier("ORDER_ITEMS") is False

    def test_init_db_rejects_unsafe_table_name(self):
        """init_db should refuse to create tables with unsafe names."""
        import asyncio
        from termin_server.storage import init_db
        malicious_ir = [{"name": {"snake": 'orders"; DROP TABLE users; --'}, "fields": []}]
        with pytest.raises(ValueError, match="(?i)unsafe"):
            asyncio.run(init_db(malicious_ir, db_path=":memory:"))

    def test_init_db_rejects_unsafe_field_name(self):
        """init_db should refuse to create columns with unsafe names."""
        import asyncio
        from termin_server.storage import init_db
        malicious_ir = [{
            "name": {"snake": "orders"},
            "fields": [{"name": 'quantity; DROP TABLE orders; --', "business_type": "number"}],
        }]
        with pytest.raises(ValueError, match="(?i)unsafe"):
            asyncio.run(init_db(malicious_ir, db_path=":memory:"))


# ── Layer 2: Proper escaping ──

class TestIdentifierEscaping:
    """_q() must handle embedded quotes safely."""

    def test_normal_identifier(self):
        from termin_server.storage import _q
        assert _q("orders") == '"orders"'

    def test_embedded_double_quote_escaped(self):
        from termin_server.storage import _q
        # SQLite escapes " by doubling: "name""with""quotes"
        result = _q('name"injection')
        assert '""' in result  # quote is doubled, not raw

    def test_injection_attempt_neutralized(self):
        from termin_server.storage import _q
        malicious = 'orders"; DROP TABLE users; --'
        result = _q(malicious)
        # The result should be a single quoted identifier, not multiple statements
        # Count the number of complete "..." groups — should be exactly 1
        assert result.startswith('"')
        assert result.endswith('"')
        # The inner content should have all " doubled
        inner = result[1:-1]
        assert '"' not in inner.replace('""', '')  # no unescaped quotes inside


# ── Layer 3: No raw SQL outside storage.py ──

class TestNoRawSQL:
    """Runtime modules outside storage.py should not contain raw SQL strings."""

    RUNTIME_DIR = Path(__file__).parent.parent / "termin_runtime"
    # Files that are allowed to have SQL.
    # - storage.py — the legacy generic CRUD layer.
    # - preferences.py — runtime-managed `_termin_principal_preferences`
    #   table (v0.9 Phase 5a.3). The table name is a hard-coded module
    #   constant (PREFERENCES_TABLE); only values are dynamic and use
    #   `?` parameterization. Same risk profile as the
    #   `_termin_idempotency` and `_termin_schema` tables in
    #   storage_sqlite.py (which lives in a subdirectory and so is not
    #   reached by this test's top-level glob).
    ALLOWED_SQL_FILES = {"storage.py", "preferences.py"}
    # SQL patterns that indicate raw query construction (not plain English)
    # Matches: f"SELECT * FROM", f"INSERT INTO", f"UPDATE {", f"DELETE FROM", f"CREATE TABLE"
    SQL_PATTERNS = re.compile(
        r'''f['"](?:SELECT\s+[\*"]|INSERT\s+INTO|UPDATE\s+[{"]|DELETE\s+FROM|CREATE\s+TABLE)'''
    )

    def _get_runtime_py_files(self):
        return [f for f in self.RUNTIME_DIR.glob("*.py")
                if f.name not in self.ALLOWED_SQL_FILES
                and f.name != "__init__.py"]

    def test_no_raw_sql_in_runtime_modules(self):
        """No runtime module (except storage.py) should construct SQL with f-strings."""
        violations = []
        for py_file in self._get_runtime_py_files():
            content = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(content.splitlines(), 1):
                if self.SQL_PATTERNS.search(line):
                    violations.append(f"{py_file.name}:{i}: {line.strip()[:80]}")
        if violations:
            msg = f"Raw SQL found in {len(violations)} location(s) outside storage.py:\n"
            msg += "\n".join(f"  {v}" for v in violations)
            pytest.fail(msg)
