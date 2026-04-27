# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""End-to-end tests for the Help Desk Ticket Tracker example.

Validates a second Termin application that exercises different features:
- Multi-word state names ("in progress", "waiting on customer")
- 5-state ticket lifecycle with multiple transition paths
- Comments as related content referencing tickets
- Three roles with overlapping scopes
- Filter dropdowns for status, priority, and category
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).parent.parent
HELPDESK_PY = APP_DIR / "helpdesk_app.py"
DB_PATH = APP_DIR / "app.db"


@pytest.fixture(scope="module")
def client(compiled_packages, tmp_path_factory):
    """Phase 2.x: legacy `compile -o app.py` + importlib pattern
    retired; consume the same .termin.pkg artifacts production
    uses."""
    from fastapi.testclient import TestClient
    from helpers import make_app_from_pkg

    db_path = str(tmp_path_factory.mktemp("helpdesk") / "app.db")
    app = make_app_from_pkg(compiled_packages["helpdesk"], db_path)
    with TestClient(app) as tc:
        yield tc


# ============================================================
# Schema and CRUD
# ============================================================

class TestHelpdeskCRUD:
    def test_create_ticket(self, client):
        r = client.post("/api/v1/tickets", json={
            "title": "Login broken",
            "description": "Can't log in after password reset",
            "priority": "high",
            "category": "bug",
            "submitted_by": "alice@example.com"
        })
        assert r.status_code == 201
        d = r.json()
        assert d["title"] == "Login broken"
        assert d["ticket_lifecycle"] == "open"
        assert d["priority"] == "high"

    def test_list_tickets(self, client):
        r = client.get("/api/v1/tickets")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_get_ticket(self, client):
        r = client.get("/api/v1/tickets/1")
        assert r.status_code == 200
        assert r.json()["title"] == "Login broken"

    def test_update_ticket(self, client):
        r = client.put("/api/v1/tickets/1", json={"assigned_to": "bob@support.com"},
                       cookies={"termin_role": "support agent"})
        assert r.status_code == 200
        assert r.json()["assigned_to"] == "bob@support.com"

    def test_create_comment(self, client):
        r = client.post("/api/v1/comments", json={
            "ticket": 1,
            "author": "bob@support.com",
            "body": "Looking into this now"
        })
        assert r.status_code == 201
        assert r.json()["body"] == "Looking into this now"

    def test_list_comments(self, client):
        r = client.get("/api/v1/comments")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_invalid_priority_rejected(self, client):
        r = client.post("/api/v1/tickets", json={
            "title": "Bad", "description": "Bad priority",
            "priority": "ULTRA", "category": "bug",
            "submitted_by": "x@x.com"
        })
        assert r.status_code == 422

    def test_invalid_category_rejected(self, client):
        r = client.post("/api/v1/tickets", json={
            "title": "Bad", "description": "Bad category",
            "priority": "low", "category": "INVALID",
            "submitted_by": "x@x.com"
        })
        assert r.status_code == 422


# ============================================================
# Multi-word state transitions
# ============================================================

class TestHelpdeskStateTransitions:
    def test_open_to_in_progress(self, client):
        client.post("/api/v1/tickets", json={
            "title": "State test 1", "description": "Test",
            "priority": "low", "category": "question",
            "submitted_by": "user@test.com"
        })
        # D-11: Transition routes use /_transition/{target_state}
        r = client.post("/api/v1/tickets/2/_transition/ticket_lifecycle/in progress",
                        cookies={"termin_role": "support agent"})
        assert r.status_code == 200
        assert r.json()["ticket_lifecycle"] == "in progress"

    def test_in_progress_to_waiting(self, client):
        r = client.post("/api/v1/tickets/2/_transition/ticket_lifecycle/waiting on customer",
                        cookies={"termin_role": "support agent"})
        assert r.status_code == 200
        assert r.json()["ticket_lifecycle"] == "waiting on customer"

    def test_waiting_to_in_progress(self, client):
        """Customer responds, ticket goes back to in progress."""
        r = client.post("/api/v1/tickets/2/_transition/ticket_lifecycle/in progress",
                        cookies={"termin_role": "customer"})
        assert r.status_code == 200
        assert r.json()["ticket_lifecycle"] == "in progress"

    def test_in_progress_to_resolved(self, client):
        r = client.post("/api/v1/tickets/2/_transition/ticket_lifecycle/resolved",
                        cookies={"termin_role": "support agent"})
        assert r.status_code == 200
        assert r.json()["ticket_lifecycle"] == "resolved"

    def test_resolved_to_closed(self, client):
        r = client.post("/api/v1/tickets/2/_transition/ticket_lifecycle/closed",
                        cookies={"termin_role": "support manager"})
        assert r.status_code == 200
        assert r.json()["ticket_lifecycle"] == "closed"

    def test_cannot_close_open_ticket(self, client):
        """No direct open -> closed transition."""
        client.post("/api/v1/tickets", json={
            "title": "Blocker", "description": "Test",
            "priority": "critical", "category": "bug",
            "submitted_by": "user@test.com"
        })
        r = client.post("/api/v1/tickets/3/_transition/ticket_lifecycle/closed",
                        cookies={"termin_role": "support manager"})
        assert r.status_code == 409

    def test_customer_cannot_resolve(self, client):
        """Customer lacks manage tickets scope."""
        client.post("/api/v1/tickets", json={
            "title": "Scope test", "description": "Test",
            "priority": "low", "category": "question",
            "submitted_by": "user@test.com"
        })
        client.post("/api/v1/tickets/4/_transition/ticket_lifecycle/in progress",
                    cookies={"termin_role": "support agent"})
        r = client.post("/api/v1/tickets/4/_transition/ticket_lifecycle/resolved",
                        cookies={"termin_role": "customer"})
        assert r.status_code == 403


# ============================================================
# Access control
# ============================================================

class TestHelpdeskAccessControl:
    def test_customer_can_create(self, client):
        r = client.post("/api/v1/tickets", json={
            "title": "Help", "description": "Need help",
            "priority": "medium", "category": "question",
            "submitted_by": "customer@test.com"
        }, cookies={"termin_role": "customer"})
        assert r.status_code == 201

    def test_customer_can_view(self, client):
        r = client.get("/api/v1/tickets", cookies={"termin_role": "customer"})
        assert r.status_code == 200

    def test_customer_cannot_update(self, client):
        r = client.put("/api/v1/tickets/1", json={"assigned_to": "hacker"},
                       cookies={"termin_role": "customer"})
        assert r.status_code == 403

    def test_customer_cannot_delete(self, client):
        r = client.delete("/api/v1/tickets/1", cookies={"termin_role": "customer"})
        assert r.status_code == 403

    def test_agent_cannot_delete(self, client):
        r = client.delete("/api/v1/tickets/1", cookies={"termin_role": "support agent"})
        assert r.status_code == 403

    def test_manager_can_delete(self, client):
        # Create a throwaway ticket
        client.post("/api/v1/tickets", json={
            "title": "Delete me", "description": "Test",
            "priority": "low", "category": "question",
            "submitted_by": "tmp@test.com"
        })
        r = client.delete("/api/v1/tickets/6",
                          cookies={"termin_role": "support manager"})
        assert r.status_code == 200


# ============================================================
# UI pages
# ============================================================

class TestHelpdeskUI:
    def test_ticket_queue_renders(self, client):
        r = client.get("/ticket_queue")
        assert r.status_code == 200
        assert "Ticket Queue" in r.text
        assert "<table" in r.text

    def test_submit_ticket_renders(self, client):
        r = client.get("/submit_ticket")
        assert r.status_code == 200
        assert "Submit Ticket" in r.text
        assert "<form" in r.text

    def test_support_dashboard_renders(self, client):
        r = client.get("/support_dashboard")
        assert r.status_code == 200
        assert "Support Dashboard" in r.text

    def test_filter_dropdowns_present(self, client):
        r = client.get("/ticket_queue")
        html = r.text
        assert 'data-filter="ticket_lifecycle"' in html
        assert 'data-filter="priority"' in html
        assert 'data-filter="category"' in html

    def test_status_filter_has_multi_word_states(self, client):
        r = client.get("/ticket_queue")
        assert "in progress" in r.text
        assert "waiting on customer" in r.text

    def test_submit_form_has_priority_dropdown(self, client):
        r = client.get("/submit_ticket")
        assert '<select name="priority"' in r.text
        assert "critical" in r.text

    def test_nav_visibility_customer(self, client):
        r = client.get("/submit_ticket", cookies={"termin_role": "customer"})
        assert "Submit" in r.text
        assert 'href="/ticket_queue"' not in r.text

    def test_nav_visibility_agent(self, client):
        r = client.get("/ticket_queue", cookies={"termin_role": "support agent"})
        assert "Queue" in r.text
        assert "Submit" in r.text
        assert 'href="/support_dashboard"' not in r.text

    def test_nav_visibility_manager(self, client):
        r = client.get("/ticket_queue", cookies={"termin_role": "support manager"})
        assert "Queue" in r.text
        assert "Dashboard" in r.text
