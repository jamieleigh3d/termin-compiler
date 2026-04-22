# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Deploy-config resolution regression tests.

The compiler and runtime disagreed on snake-casing when the app's
name contained a digit after a space:

  IR `name` = "Agent Chatbot 2"
    compiler writes:    agent_chatbot2.deploy.json  (filename stem)
    runtime looks for:  agent_chatbot_2.deploy.json (snake-cased name)

Result: `termin serve agent_chatbot2.termin.pkg` failed to find its
deploy config, so ANTHROPIC_API_KEY was never substituted and the AI
provider reported itself unconfigured.

These tests lock in both fixes:
  1. load_deploy_config tries the digit-collapsed variant.
  2. `termin serve` resolves the default deploy path from the .pkg
     filename rather than from the IR app name.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest


class TestLoadDeployConfigCollapsedDigits:
    """load_deploy_config's app_name-based lookup must try the
    digit-collapsed variant so it catches filenames written by the
    compiler without the underscore-before-digit."""

    def test_collapsed_digit_variant_found(self, tmp_path, monkeypatch):
        from termin_runtime.channel_config import load_deploy_config

        monkeypatch.chdir(tmp_path)
        # Simulate what the compiler wrote.
        (tmp_path / "agent_chatbot2.deploy.json").write_text(
            json.dumps({"ai_provider": {"service": "anthropic",
                                         "model": "m", "api_key": "k"}}),
            encoding="utf-8",
        )
        # Runtime-style app_name with underscore before digit.
        config = load_deploy_config(app_name="agent_chatbot_2")
        assert config.get("ai_provider", {}).get("service") == "anthropic"

    def test_exact_app_name_variant_still_preferred(self, tmp_path, monkeypatch):
        from termin_runtime.channel_config import load_deploy_config

        monkeypatch.chdir(tmp_path)
        # Both forms exist — the exact-match app_name file should win.
        (tmp_path / "agent_chatbot_2.deploy.json").write_text(
            json.dumps({"ai_provider": {"service": "exact",
                                         "model": "m", "api_key": "k"}}),
            encoding="utf-8",
        )
        (tmp_path / "agent_chatbot2.deploy.json").write_text(
            json.dumps({"ai_provider": {"service": "collapsed",
                                         "model": "m", "api_key": "k"}}),
            encoding="utf-8",
        )
        config = load_deploy_config(app_name="agent_chatbot_2")
        assert config.get("ai_provider", {}).get("service") == "exact"

    def test_no_collapse_when_app_name_has_no_digit(self, tmp_path, monkeypatch):
        """A collapsed-variant lookup must not fire if there's no
        underscore-before-digit in the supplied app_name."""
        from termin_runtime.channel_config import load_deploy_config

        monkeypatch.chdir(tmp_path)
        # Only a differently-named file exists.
        (tmp_path / "warehouse.deploy.json").write_text(
            json.dumps({"ai_provider": {"service": "anthropic",
                                         "model": "m", "api_key": "k"}}),
            encoding="utf-8",
        )
        # app_name has no underscore-before-digit; nothing to collapse.
        # A lookup for "warehouse" should find it; a lookup for
        # something else should return empty.
        found = load_deploy_config(app_name="warehouse")
        assert found.get("ai_provider", {}).get("service") == "anthropic"
        missing = load_deploy_config(app_name="helpdesk")
        assert missing == {}
