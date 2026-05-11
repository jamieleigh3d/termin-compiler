# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Canonical transition path shape (closes (4) of termin-core #6).

Background. An alt-runtime adopter who iterates ``build_route_specs(ctx)``
to bind routes received transition paths under
``/api/v1/{plural}/{id}/_transition/{machine}/{target}``. Their conformance
suite (and ``termin-server``'s own ``register_transition_routes``) bind
the canonical shape ``/_transition/{content}/{machine_name}/{record_id}
/{target_state}`` — top-level path, content as first segment, all four
positions as placeholders. The IR-emitted path and the canonical path
were different. 37 state-machine conformance tests 404'd against the
alt adapter because the path it bound never matched the URL the test
client hit.

This test pins the IR's transition route shape to the canonical form so
any adapter using ``build_route_specs`` gets the same path the
conformance suite tests against.

The fix also aligns CRUD placeholder names: the core CRUD handlers
in ``termin_core.routing.crud`` read the row key from
``request.path_params["key"]`` (see ``get_content_handler``,
``update_content_handler``, ``delete_content_handler``,
``transition_content_handler``); the IR used to emit ``{id}`` instead.
Adapters routing through the framework-agnostic handlers had to
manually rename the path param. After this fix the IR uses ``{key}``
consistently and adapters can pass the regex-extracted ``path_params``
straight through to the handlers.
"""

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower
from termin_core.ir.types import RouteKind


_WAREHOUSE_TRANSITIONS = """
Application: Inventory
Description: Stock movement.

Identity:
  Scopes are "warehouse.view" and "warehouse.admin"
  A "viewer" has "warehouse.view"
  An "admin" has "warehouse.view" and "warehouse.admin"

Content called "products":
  Each product has a name which is text, required
  Each product has a stock which is number, required
  Each product has a product lifecycle which is state:
    product lifecycle starts as draft
    product lifecycle can also be active or archived
    draft can become active if the user has warehouse.admin
    active can become archived if the user has warehouse.admin
  Anyone with "warehouse.view" can view products
  Anyone with "warehouse.admin" can update products
"""


class TestCanonicalTransitionPathShape:
    """The IR transition route path must be top-level
    ``/_transition/{plural}/{{machine}}/{{key}}/{{target}}``."""

    def setup_method(self):
        ast, parse_errors = parse(_WAREHOUSE_TRANSITIONS)
        assert parse_errors.ok, f"Parse errors: {parse_errors}"
        analyze(ast)
        self.spec = lower(ast)

    def test_transition_route_is_top_level_not_nested(self):
        """Path must begin with ``/_transition/`` and NOT be nested
        under ``/api/v1/{content}/...``."""
        trans = [r for r in self.spec.routes if r.kind == RouteKind.TRANSITION]
        assert len(trans) >= 1, "Expected at least one transition route"
        for r in trans:
            assert r.path.startswith("/_transition/"), (
                f"Transition path should start with /_transition/, "
                f"got {r.path!r}"
            )
            assert "/api/v1/" not in r.path, (
                f"Transition path should not be nested under /api/v1/, "
                f"got {r.path!r}"
            )

    def test_transition_path_segments_canonical_order(self):
        """Path segments must be
        ``/_transition/<plural>/<machine>/<key-placeholder>/<target>``."""
        trans = [r for r in self.spec.routes if r.kind == RouteKind.TRANSITION
                 and r.content_ref == "products"]
        assert len(trans) >= 1
        path = trans[0].path
        # /_transition/products/...
        segments = path.split("/")
        # Leading "" + "_transition" + "products" + ...
        assert segments[0] == ""
        assert segments[1] == "_transition"
        assert segments[2] == "products", (
            f"Expected plural content second, got {segments[2]!r}"
        )

    def test_transition_uses_placeholder_for_machine_and_target(self):
        """``machine`` and ``target`` must be path placeholders (not
        baked-in literals) so a single route can handle every
        transition for a content."""
        trans = [r for r in self.spec.routes if r.kind == RouteKind.TRANSITION
                 and r.content_ref == "products"]
        assert len(trans) >= 1
        path = trans[0].path
        assert "{machine}" in path, (
            f"Path should contain {{machine}} placeholder, got {path!r}"
        )
        assert "{target}" in path, (
            f"Path should contain {{target}} placeholder, got {path!r}"
        )

    def test_transition_uses_key_placeholder_not_id(self):
        """The row-key placeholder must be ``{key}`` to match the
        ``transition_content_handler`` which reads
        ``request.path_params['key']``."""
        trans = [r for r in self.spec.routes if r.kind == RouteKind.TRANSITION
                 and r.content_ref == "products"]
        assert len(trans) >= 1
        path = trans[0].path
        assert "{key}" in path, (
            f"Path should contain {{key}} placeholder, got {path!r}"
        )

    def test_one_transition_route_per_content_not_per_target(self):
        """Because machine and target are placeholders, the compiler
        emits exactly ONE transition route per content (with state
        machines), not one route per (machine, target) tuple. This
        matches ``termin-server``'s single ``register_transition_routes``
        catch-all and the conformance test path shape."""
        trans = [r for r in self.spec.routes if r.kind == RouteKind.TRANSITION
                 and r.content_ref == "products"]
        assert len(trans) == 1, (
            f"Expected exactly one transition route for 'products', "
            f"got {len(trans)}: {[r.path for r in trans]}"
        )


class TestCanonicalCrudKeyPlaceholder:
    """CRUD routes that take a row key use ``{key}`` placeholder, not
    ``{id}``, matching the per-handler ``request.path_params['key']``
    read."""

    def setup_method(self):
        ast, parse_errors = parse(_WAREHOUSE_TRANSITIONS)
        assert parse_errors.ok, f"Parse errors: {parse_errors}"
        analyze(ast)
        self.spec = lower(ast)

    def test_update_route_uses_key_placeholder(self):
        upd = [r for r in self.spec.routes if r.kind == RouteKind.UPDATE
               and r.content_ref == "products"]
        assert len(upd) >= 1
        assert "{key}" in upd[0].path, (
            f"UPDATE path should use {{key}}, got {upd[0].path!r}"
        )
        assert "{id}" not in upd[0].path

    def test_get_one_route_uses_key_placeholder(self):
        get1 = [r for r in self.spec.routes if r.kind == RouteKind.GET_ONE
                and r.content_ref == "products"]
        assert len(get1) >= 1
        assert "{key}" in get1[0].path, (
            f"GET_ONE path should use {{key}}, got {get1[0].path!r}"
        )
        assert "{id}" not in get1[0].path

    def test_delete_route_uses_key_placeholder(self):
        # No delete is declared in the test fixture, but every state
        # machine path that takes a key should use {key}. We assert on
        # the broader rule: no route in the IR uses {id} as its path
        # parameter for the row key.
        for r in self.spec.routes:
            assert "/{id}" not in r.path, (
                f"Route {r.path!r} uses legacy {{id}}; expected {{key}}"
            )
