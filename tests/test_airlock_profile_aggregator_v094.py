# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""End-to-end smoke for the v0.9.4 A4 B7 wiring of the airlock app.

The airlock.termin source declares a When-rule on
`sessions.lifecycle.complete.entered` that uses the new owner-keyed
Update grammar to project session.scores into the player's profile.
This test compiles the actual airlock.termin source, loads it into
termin-server, manually populates session.scores, transitions the
session to complete, and verifies the profile reflects the
projection.

It's a smoke test, not a behavioral conformance test — the
behavioral contract for owner-keyed Update + state-entered When-rule
lives in `termin-conformance/tests/test_v094_owner_keyed_update.py`.
This test exists to catch regressions in the airlock-specific
wiring (the .termin source compiles, the IR shape is right, the
runtime fires the right rule, the profile updates as expected).
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
SERVER_ROOT = REPO_ROOT.parent / "termin-server"
sys.path.insert(0, str(SERVER_ROOT))

from termin.peg_parser import parse_peg  # noqa: E402
from termin.analyzer import analyze  # noqa: E402
from termin.lower import lower  # noqa: E402
from termin_core.ir.serialize import serialize_ir  # noqa: E402

from termin_server import create_termin_app  # noqa: E402


AIRLOCK_SRC = REPO_ROOT / "examples-dev" / "airlock.termin"


@pytest.fixture(scope="module")
def airlock_ir_json():
    """Compile the actual airlock.termin source and return its IR
    as JSON. Module-scoped because the compile is mildly expensive
    and pure (deterministic for a fixed source)."""
    source = AIRLOCK_SRC.read_text(encoding="utf-8")
    program, errors = parse_peg(source)
    assert errors.ok, f"airlock.termin parse errors: {errors.format()}"
    result = analyze(program)
    assert result.ok, f"airlock.termin analyzer errors: {result.format()}"
    spec = lower(program)
    return serialize_ir(spec)


AIRLOCK_DEPLOY_CONFIG = REPO_ROOT / "examples-dev" / "airlock.deploy.json"


@pytest.fixture
def airlock_client(airlock_ir_json, tmp_path):
    """Spin up the airlock app in TestClient with an empty DB so
    each test starts clean. Loads the airlock.deploy.json so the
    channel + compute bindings (stub providers — no Anthropic
    tokens burned) satisfy the runtime's startup gate."""
    db_path = str(tmp_path / "airlock.db")
    deploy_config = json.loads(
        AIRLOCK_DEPLOY_CONFIG.read_text(encoding="utf-8")
    )
    app = create_termin_app(
        airlock_ir_json,
        db_path=db_path,
        deploy_config=deploy_config,
    )
    with TestClient(app) as client:
        client.cookies.set("termin_role", "anonymous")
        client.cookies.set("termin_user_name", "smoke_alice")
        yield client


class TestAirlockProfileAggregatorV094:
    """The B7 wiring: lifecycle.complete.entered fires the new
    owner-keyed Update action; the player's profile reflects the
    projected scores."""

    def test_profile_increments_on_session_complete(self, airlock_client):
        """Walk a session through all lifecycle states. The
        airlock lifecycle is `survey → scenario → scoring → complete`,
        and the When-rule fires on the `complete` entry."""
        client = airlock_client
        # Create a session.
        r = client.post("/api/v1/sessions", json={"timer_seconds": 300})
        assert r.status_code == 201, r.text
        session_id = r.json()["id"]

        # Walk the lifecycle to scoring. The airlock lifecycle gates
        # transitions on `hatch_unlocked == "yes"` for survey →
        # scoring; flip that flag before the transition (the real
        # gameplay path writes it when ARIA confirms the correct
        # fix). The smoke test takes the shortcut.
        r = client.post(
            f"/_transition/sessions/lifecycle/{session_id}/scenario",
        )
        assert r.status_code == 200, r.text
        r = client.put(
            f"/api/v1/sessions/{session_id}",
            json={"hatch_unlocked": "yes"},
        )
        assert r.status_code == 200, r.text
        r = client.post(
            f"/_transition/sessions/lifecycle/{session_id}/scoring",
        )
        assert r.status_code == 200, r.text

        # Manually populate session.scores (the evaluator stub
        # doesn't write real scores; this simulates what the real
        # evaluator would land before transitioning to complete).
        scores = {
            "of_level": 3,
            "of_evidence": ["caught the flawed diagnosis"],
            "of_next": "Direct ARIA more proactively",
            "gc_level": "self",
            "gc_evidence": [],
            "gc_next": "engage if Reeves shows up",
            "bf_level": "compliant",
            "bf_evidence": [],
            "bf_next": "try probing tools",
            "badges": ["diagnostician"],
            "calibration": "rated 7, scored 3 — well-calibrated",
            "summary": "Solid run.",
        }
        r = client.put(
            f"/api/v1/sessions/{session_id}",
            json={"scores": scores},
        )
        assert r.status_code == 200, r.text

        # Transition to complete — this fires the When-rule.
        r = client.post(
            f"/_transition/sessions/lifecycle/{session_id}/complete",
        )
        assert r.status_code == 200, r.text

        # The When-rule should have upserted the player's profile:
        # best_of_level = max(0 default, 3 from scores) = 3
        # total_attempts = 0 + 1 = 1
        r = client.get("/api/v1/profiles")
        assert r.status_code == 200
        profiles = r.json()
        assert len(profiles) == 1, (
            f"Expected one profile after first session, got {len(profiles)}: "
            f"{profiles}"
        )
        prof = profiles[0]
        assert prof["best_of_level"] == 3, (
            f"Expected best_of_level=3 after complete, got {prof}"
        )
        assert prof["total_attempts"] == 1, (
            f"Expected total_attempts=1 after first session, got {prof}"
        )
