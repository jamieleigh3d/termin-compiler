# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""End-to-end tests for the Project Management Board example.

Exercises features not covered by warehouse or helpdesk:
- 5 content types with cascading FK references (project -> sprint -> task -> time_log)
- Multi-step task lifecycle: backlog -> in sprint -> in progress -> in review -> done
- Rework loop: in review -> in progress (reopen)
- Reference dropdowns in forms for project, sprint, assignee
- Three roles with task-level vs sprint-level vs project-level scopes
- Time logging as a secondary content type
- Aggregation dashboards
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).parent.parent
APP_PY = APP_DIR / "projectboard_app.py"
DB_PATH = APP_DIR / "app.db"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    subprocess.run(
        [sys.executable, "-m", "termin.cli", "compile",
         "examples/projectboard.termin", "-o", "projectboard_app.py"],
        cwd=str(APP_DIR), check=True,
    )
    if DB_PATH.exists():
        DB_PATH.unlink()

    spec = importlib.util.spec_from_file_location("projectboard_app", str(APP_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with TestClient(mod.app) as tc:
        yield tc

    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture(scope="module")
def seeded(client):
    """Seed the database with a project, team, sprint, and tasks."""
    # Project
    r = client.post("/api/v1/projects", json={
        "name": "Termin", "description": "The compiler", "status": "active"
    }, cookies={"termin_role": "project manager"})
    project_id = r.json()["id"]

    # Team members
    for name, email, role in [
        ("Alice", "alice@dev.com", "developer"),
        ("Bob", "bob@dev.com", "qa"),
    ]:
        client.post("/api/v1/team_members", json={
            "name": name, "email": email, "role": role, "project": project_id
        }, cookies={"termin_role": "project manager"})

    # Sprint
    r = client.post("/api/v1/sprints", json={
        "name": "Sprint 1", "project": project_id, "goal": "MVP", "capacity": 20
    }, cookies={"termin_role": "project manager"})
    sprint_id = r.json()["id"]

    # Tasks
    for title, points, priority in [
        ("PEG parser", 5, "high"),
        ("Write tests", 3, "medium"),
        ("Fix bug #42", 2, "critical"),
    ]:
        client.post("/api/v1/tasks", json={
            "title": title, "description": f"Desc for {title}",
            "project": project_id, "sprint": sprint_id,
            "assignee": 1, "points": points, "priority": priority
        }, cookies={"termin_role": "developer"})

    return {"project_id": project_id, "sprint_id": sprint_id}


# ============================================================
# CRUD
# ============================================================

class TestProjectBoardCRUD:
    def test_create_project(self, client):
        r = client.post("/api/v1/projects", json={
            "name": "Test Project", "description": "Testing", "status": "active"
        }, cookies={"termin_role": "project manager"})
        assert r.status_code == 201

    def test_list_projects(self, client):
        r = client.get("/api/v1/projects")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_create_team_member(self, client):
        r = client.post("/api/v1/team_members", json={
            "name": "Charlie", "email": "charlie@dev.com",
            "role": "designer", "project": 1
        }, cookies={"termin_role": "project manager"})
        assert r.status_code == 201

    def test_create_sprint(self, client):
        r = client.post("/api/v1/sprints", json={
            "name": "Test Sprint", "project": 1, "goal": "Testing", "capacity": 10
        }, cookies={"termin_role": "project manager"})
        assert r.status_code == 201

    def test_create_task(self, client, seeded):
        r = client.post("/api/v1/tasks", json={
            "title": "CRUD test task", "description": "Testing CRUD",
            "project": seeded["project_id"], "points": 1, "priority": "low"
        }, cookies={"termin_role": "developer"})
        assert r.status_code == 201
        d = r.json()
        assert d["status"] == "backlog"
        assert d["title"] == "CRUD test task"

    def test_update_task(self, client, seeded):
        r = client.put("/api/v1/tasks/1", json={"points": 8},
                       cookies={"termin_role": "developer"})
        assert r.status_code == 200
        assert r.json()["points"] == 8

    def test_get_task(self, client, seeded):
        r = client.get("/api/v1/tasks/1")
        assert r.status_code == 200
        assert r.json()["title"] == "PEG parser"

    def test_create_time_log(self, client, seeded):
        r = client.post("/api/v1/time_logs", json={
            "task": 1, "team_member": 1, "hours": 2.5
        }, cookies={"termin_role": "developer"})
        assert r.status_code == 201
        assert r.json()["hours"] == 2.5

    def test_list_time_logs(self, client, seeded):
        r = client.get("/api/v1/time_logs")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_invalid_priority(self, client, seeded):
        r = client.post("/api/v1/tasks", json={
            "title": "Bad", "description": "Bad",
            "project": seeded["project_id"], "priority": "MEGA"
        }, cookies={"termin_role": "developer"})
        assert r.status_code == 422

    def test_invalid_role_enum(self, client):
        r = client.post("/api/v1/team_members", json={
            "name": "Bad", "email": "bad@bad.com",
            "role": "astronaut", "project": 1
        }, cookies={"termin_role": "project manager"})
        assert r.status_code == 422


# ============================================================
# Task lifecycle (5-state with rework loop)
# ============================================================

class TestTaskLifecycle:
    def test_full_lifecycle(self, client, seeded):
        """Walk a task through the complete lifecycle: backlog → in sprint → in progress → in review → rework → in progress → in review → done."""
        # Create a fresh task to avoid shared-state issues
        client.cookies.set("termin_role", "project manager")
        r = client.post("/api/v1/tasks", json={
            "title": "Lifecycle Test", "project": 1, "sprint": 1,
            "priority": "high", "points": 3,
        })
        assert r.status_code == 201
        tid = r.json()["id"]
        assert r.json()["status"] == "backlog"

        # D-11: Transition routes use /_transition/{target_state}
        # backlog → in sprint (project manager)
        r = client.post(f"/api/v1/tasks/{tid}/_transition/in sprint",
                        cookies={"termin_role": "project manager"})
        assert r.status_code == 200
        assert r.json()["status"] == "in sprint"

        # in sprint → in progress (developer)
        r = client.post(f"/api/v1/tasks/{tid}/_transition/in progress",
                        cookies={"termin_role": "developer"})
        assert r.status_code == 200
        assert r.json()["status"] == "in progress"

        # in progress → in review (developer)
        r = client.post(f"/api/v1/tasks/{tid}/_transition/in review",
                        cookies={"termin_role": "developer"})
        assert r.status_code == 200
        assert r.json()["status"] == "in review"

        # Rework: in review → in progress
        r = client.post(f"/api/v1/tasks/{tid}/_transition/in progress",
                        cookies={"termin_role": "developer"})
        assert r.status_code == 200
        assert r.json()["status"] == "in progress"

        # Back to review → done
        client.post(f"/api/v1/tasks/{tid}/_transition/in review", cookies={"termin_role": "developer"})
        r = client.post(f"/api/v1/tasks/{tid}/_transition/done",
                        cookies={"termin_role": "developer"})
        assert r.status_code == 200
        assert r.json()["status"] == "done"

    def test_reopen_done_task(self, client, seeded):
        """Done tasks can be sent back to in progress."""
        r = client.post("/api/v1/tasks/1/_transition/in progress",
                        cookies={"termin_role": "developer"})
        assert r.status_code == 200
        assert r.json()["status"] == "in progress"

    def test_cannot_skip_to_done(self, client, seeded):
        """Cannot jump from backlog to done."""
        r = client.post("/api/v1/tasks/2/_transition/done",
                        cookies={"termin_role": "developer"})
        assert r.status_code == 409

    def test_cannot_plan_without_sprint_scope(self, client, seeded):
        """Developer can't move tasks to sprint (needs manage sprints)."""
        # Create a fresh task as project manager
        client.cookies.set("termin_role", "project manager")
        r = client.post("/api/v1/tasks", json={
            "title": "Scope Test", "project": 1, "sprint": 1,
            "priority": "low", "points": 1,
        })
        tid = r.json()["id"]
        # Developer lacks 'manage sprints' → can't plan
        r = client.post(f"/api/v1/tasks/{tid}/_transition/in sprint",
                        cookies={"termin_role": "developer"})
        assert r.status_code == 403


# ============================================================
# Access control
# ============================================================

class TestProjectBoardAccess:
    def test_stakeholder_can_view(self, client, seeded):
        r = client.get("/api/v1/tasks", cookies={"termin_role": "stakeholder"})
        assert r.status_code == 200

    def test_stakeholder_cannot_create_task(self, client, seeded):
        r = client.post("/api/v1/tasks", json={
            "title": "Nope", "description": "Blocked",
            "project": seeded["project_id"], "priority": "low"
        }, cookies={"termin_role": "stakeholder"})
        assert r.status_code == 403

    def test_developer_cannot_create_project(self, client):
        r = client.post("/api/v1/projects", json={
            "name": "Blocked", "status": "active"
        }, cookies={"termin_role": "developer"})
        assert r.status_code == 403

    def test_developer_cannot_delete_task(self, client, seeded):
        r = client.delete("/api/v1/tasks/1",
                          cookies={"termin_role": "developer"})
        assert r.status_code == 403

    def test_pm_can_delete_task(self, client, seeded):
        # Create a throwaway
        r = client.post("/api/v1/tasks", json={
            "title": "Delete me", "description": "x",
            "project": seeded["project_id"], "priority": "low"
        }, cookies={"termin_role": "developer"})
        tid = r.json()["id"]
        r2 = client.delete(f"/api/v1/tasks/{tid}",
                           cookies={"termin_role": "project manager"})
        assert r2.status_code == 200


# ============================================================
# UI pages
# ============================================================

class TestProjectBoardUI:
    def test_sprint_board_renders(self, client, seeded):
        r = client.get("/sprint_board")
        assert r.status_code == 200
        assert "Sprint Board" in r.text
        assert "<table" in r.text

    def test_create_task_page(self, client, seeded):
        r = client.get("/create_task")
        assert r.status_code == 200
        assert "<form" in r.text
        # Project should be a dropdown
        assert '<select name="project"' in r.text

    def test_new_project_page(self, client, seeded):
        r = client.get("/new_project",
                       cookies={"termin_role": "project manager"})
        assert r.status_code == 200
        assert "New Project" in r.text
        assert "<form" in r.text
        assert '<select name="status"' in r.text  # enum dropdown

    def test_new_project_form_creates_project(self, client, seeded):
        r = client.post("/new_project", data={
            "name": "Form Project", "description": "Created via form", "status": "active"
        }, cookies={"termin_role": "project manager"}, follow_redirects=False)
        assert r.status_code == 303
        r2 = client.get("/api/v1/projects")
        names = [p["name"] for p in r2.json()]
        assert "Form Project" in names

    def test_sprint_planning_page(self, client, seeded):
        r = client.get("/sprint_planning",
                       cookies={"termin_role": "project manager"})
        assert r.status_code == 200
        assert "Sprint Planning" in r.text
        assert '<select name="project"' in r.text

    def test_log_time_page(self, client, seeded):
        r = client.get("/log_time", cookies={"termin_role": "developer"})
        assert r.status_code == 200
        # Task should be a reference dropdown
        assert '<select name="task"' in r.text
        # Team member should be a reference dropdown
        assert '<select name="team_member"' in r.text

    def test_project_dashboard(self, client, seeded):
        r = client.get("/project_dashboard",
                       cookies={"termin_role": "project manager"})
        assert r.status_code == 200
        assert "Project Dashboard" in r.text

    def test_team_management_page(self, client, seeded):
        r = client.get("/team_management",
                       cookies={"termin_role": "project manager"})
        assert r.status_code == 200
        assert "Team Management" in r.text
        # Role should be an enum dropdown
        assert '<select name="role"' in r.text

    def test_board_filter_dropdowns(self, client, seeded):
        r = client.get("/sprint_board")
        assert 'data-filter="status"' in r.text
        assert 'data-filter="priority"' in r.text

    def test_board_shows_tasks(self, client, seeded):
        r = client.get("/sprint_board")
        assert "PEG parser" in r.text or "Write tests" in r.text

    def test_nav_developer(self, client, seeded):
        r = client.get("/sprint_board", cookies={"termin_role": "developer"})
        assert "Board" in r.text
        assert "Create Task" in r.text
        assert "Log Time" in r.text
        assert 'href="/sprint_planning"' not in r.text

    def test_nav_stakeholder(self, client, seeded):
        r = client.get("/sprint_board", cookies={"termin_role": "stakeholder"})
        assert "Board" in r.text
        assert "Dashboard" in r.text
        assert 'href="/create_task"' not in r.text

    def test_nav_pm_sees_all(self, client, seeded):
        r = client.get("/sprint_board", cookies={"termin_role": "project manager"})
        assert "Board" in r.text
        assert "Sprint Planning" in r.text
        assert "Team" in r.text
        assert "Dashboard" in r.text
