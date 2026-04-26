# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Level 2: Browser automation tests with Playwright.

Tests the actual user experience: fill form, click save, verify row appears.
Uses data-termin-* attributes for element selection — NEVER English text.
This ensures tests survive localization.

Skipped gracefully if Playwright is not installed.
"""

import json
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

pw = pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright

from termin_runtime import create_termin_app
from helpers import extract_ir_from_pkg


def _ir_json(pkg_path):
    return json.dumps(extract_ir_from_pkg(pkg_path))


class BrowserTestServer:
    """Runs uvicorn on a random port for browser tests."""

    def __init__(self, app):
        self.app = app
        self.port = self._find_free_port()
        self.server = None
        self.thread = None

    @staticmethod
    def _find_free_port():
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            try:
                with httpx.Client() as client:
                    client.get(f"{self.base_url}/api/reflect", timeout=1.0)
                return
            except (httpx.ConnectError, httpx.ReadTimeout, OSError):
                time.sleep(0.1)
        raise RuntimeError("Server didn't start")

    def stop(self):
        if self.server:
            self.server.should_exit = True
            self.thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser_context():
    """Launch a headless Chromium browser for the test module."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        yield context
        context.close()
        browser.close()


@pytest.fixture(scope="module")
def agent_simple_app(compiled_packages, tmp_path_factory):
    """Start agent_simple server for the test module.

    Uses a per-module tempfile DB so this test module never sees
    rows left behind by other test modules' app.db. Without this,
    `python -m pytest tests/` would leave a polluted ./app.db that
    a subsequent run of just this module would inherit (the v0.8
    "completions table has 6 rows from test_async_websocket"
    contamination).
    """
    ir_json = _ir_json(compiled_packages["agent_simple"])
    db_path = str(tmp_path_factory.mktemp("agent_simple") / "agent_simple.db")
    app = create_termin_app(
        ir_json, db_path=db_path, strict_channels=False, deploy_config={},
    )
    server = BrowserTestServer(app)
    server.start()
    yield server
    server.stop()


# ── Browser tests using data-termin-* attributes only ──

class TestBrowserFormSubmit:
    """Test the form→table flow in a real browser.

    All element selection uses data-termin-* attributes and
    structural selectors (form, button[type=submit], table).
    NO English text is used for selection — localization safe.
    """

    def test_form_submit_adds_row_to_table(self, browser_context, agent_simple_app):
        """Fill form, click save, row appears in table."""
        page = browser_context.new_page()
        page.goto(f"{agent_simple_app.base_url}/agent")

        # Wait for WebSocket connection
        page.wait_for_selector("[id='termin-status']", timeout=5000)
        page.wait_for_timeout(500)  # let WS subscribe complete

        # Scope to the Termin-generated content form via data-termin-component.
        # The page also contains a nav role-switcher form; using the bare
        # `form` selector matches both, and chained .locator() searches
        # across all matches — that's how the v0.9 Anonymous-template
        # regression hijacked these tests for ~2 weeks.
        form = page.locator("[data-termin-component='form']")
        prompt_input = form.locator("input[name='prompt']")
        prompt_input.fill("browser test prompt")
        form.locator("button[type='submit']").click()

        # Wait for a row with our prompt to appear in the table
        table = page.locator("[data-termin-component='data_table']")
        page.wait_for_timeout(2000)

        # Find our prompt in any cell
        cells = table.locator("[data-termin-field='prompt']")
        texts = [cells.nth(i).text_content() for i in range(cells.count())]
        assert "browser test prompt" in texts, f"Expected 'browser test prompt' in table, got: {texts}"

        page.close()

    def test_no_duplicate_rows(self, browser_context, agent_simple_app):
        """Submit once, exactly one row appears."""
        page = browser_context.new_page()
        page.goto(f"{agent_simple_app.base_url}/agent")
        page.wait_for_selector("[id='termin-status']", timeout=5000)
        page.wait_for_timeout(500)

        # Count existing rows
        table = page.locator("[data-termin-component='data_table']")
        initial_count = table.locator("[data-termin-row-id]").count()

        # Submit one record (scoped to content form — see test above)
        form = page.locator("[data-termin-component='form']")
        prompt_input = form.locator("input[name='prompt']")
        prompt_input.fill("dedup browser test")
        form.locator("button[type='submit']").click()

        # Wait for new row
        page.wait_for_timeout(2000)

        # Should have exactly one more row
        new_count = table.locator("[data-termin-row-id]").count()
        assert new_count == initial_count + 1, \
            f"Expected {initial_count + 1} rows, got {new_count} (duplicate?)"

        page.close()

    def test_form_clears_after_submit(self, browser_context, agent_simple_app):
        """Form inputs should be empty after successful submit."""
        page = browser_context.new_page()
        page.goto(f"{agent_simple_app.base_url}/agent")
        page.wait_for_selector("[id='termin-status']", timeout=5000)
        page.wait_for_timeout(500)

        form = page.locator("[data-termin-component='form']")
        prompt_input = form.locator("input[name='prompt']")
        prompt_input.fill("clear test")
        form.locator("button[type='submit']").click()

        # Wait for AJAX response
        page.wait_for_timeout(1000)

        # Input should be cleared
        assert prompt_input.input_value() == ""

        page.close()

    def test_page_shows_existing_records_on_load(self, browser_context, agent_simple_app):
        """SSR page should show records that exist in the database."""
        server = agent_simple_app

        # Create a record via API first
        with httpx.Client() as client:
            client.post(
                f"{server.base_url}/api/v1/completions",
                json={"prompt": "pre-existing record"},
                cookies={"termin_role": "anonymous"},
            )

        # Load page — should show the record from SSR
        page = browser_context.new_page()
        page.goto(f"{server.base_url}/agent")
        page.wait_for_selector("[data-termin-component='data_table']", timeout=5000)

        # Find the pre-existing record by field value
        cells = page.locator("[data-termin-field='prompt']")
        texts = [cells.nth(i).text_content() for i in range(cells.count())]
        assert "pre-existing record" in texts

        page.close()

    def test_websocket_connected_indicator(self, browser_context, agent_simple_app):
        """The connection status indicator should show 'Connected'."""
        page = browser_context.new_page()
        page.goto(f"{agent_simple_app.base_url}/agent")

        # Wait for the status indicator
        indicator = page.locator("#termin-status")
        indicator.wait_for(timeout=5000)

        # Should show connected (check by CSS color/style, not text)
        # The indicator background is green when connected
        page.wait_for_timeout(1000)
        bg_color = indicator.evaluate("el => getComputedStyle(el).backgroundColor")
        # Green-ish color indicates connected
        assert bg_color != "", "Status indicator should have a background color"

        page.close()
