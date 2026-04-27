# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Identity helpers for the Termin runtime.

v0.9 Phase 1: this module is now a thin wrapper over the registered
IdentityProvider. The runtime constructs a provider from the app's
deploy config (defaulting to the first-party stub) and passes it
into make_get_current_user / make_get_user_from_websocket. The
helpers extract credentials from the request, route through the
provider for non-Anonymous principals, and translate provider role
names into effective scopes using the source's Identity-block
role-to-scope mapping.

Per BRD §6.1, Anonymous bypasses the provider entirely — no
credentials in the request means no provider call.

The returned User dict shape is unchanged from v0.8 so existing
templates and CEL expressions (User.Name, User.Authenticated,
User.Scopes, etc.) continue to work without modification. The dict
also carries the new "Principal" key (the typed Principal object)
for code that wants to consume the contract directly.
"""

from dataclasses import replace
import sqlite3

from fastapi import Request, HTTPException, WebSocket

from .providers.identity_contract import (
    ANONYMOUS_PRINCIPAL, Principal, IdentityProvider,
)


def _hydrate_principal_preferences(
    principal: Principal,
    db_path: str | None,
    theme_default: str | None,
    theme_locked: str | None,
) -> Principal:
    """v0.9 Phase 5a.3: enrich Principal.preferences with the
    runtime-managed `_termin_principal_preferences` table.

    Anonymous principals are not hydrated from the DB (their session-
    scoped storage is cookie-backed, read in the endpoint handler).

    `theme_locked` wins over the stored value at hydration time so
    CEL expressions see the same effective theme that the GET endpoint
    returns. The unlocked stored value still persists in the DB —
    only the in-memory Principal carries the masked value.
    """
    if principal.is_anonymous or not db_path:
        return principal
    try:
        from .preferences import get_all_preferences
        conn = sqlite3.connect(db_path)
        try:
            stored = dict(get_all_preferences(conn, principal.id))
        finally:
            conn.close()
    except Exception:
        # Hydration is non-critical. Failing closed here would break
        # every authenticated request whenever the prefs table is
        # mid-migration; degrade to the default instead.
        stored = {}
    # Lock-aware projection: GET endpoint returns theme_locked when
    # set, so CEL must see the same. theme_default backs absent values.
    if theme_locked is not None:
        stored["theme"] = theme_locked
    elif "theme" not in stored and theme_default is not None:
        stored["theme"] = theme_default
    merged = dict(principal.preferences)
    merged.update(stored)
    return replace(principal, preferences=merged)


def _build_user_object(principal: Principal, role_name: str, scopes: list) -> dict:
    """Build the standard User object available in CEL expressions.

    The User object is the identity contract between auth providers
    and the runtime. Any auth provider must produce a Principal that
    flows through this builder. CEL expressions use PascalCase:
    User.Name, User.Username, User.Role.
    """
    authenticated = not principal.is_anonymous
    display_name = principal.display_name or ("Anonymous" if not authenticated else "User")
    return {
        "Username": display_name.lower().replace(" ", "_") if authenticated else "anonymous",
        "Name": display_name if authenticated else "Anonymous",
        "FirstName": display_name.split()[0] if authenticated and display_name else "Anonymous",
        "Role": role_name,
        "Scopes": list(scopes),
        "Authenticated": authenticated,
    }


def _resolve_role_key(roles_to_scopes: dict, candidate: str | None) -> str:
    """Resolve a role-cookie value to a canonical role name in the
    source's Identity-block role-to-scope mapping.

    Tries exact match first, then case-insensitive. Falls back to the
    first role in the dict — typically Anonymous if the source
    declares one. The case-insensitive path lets historical cookies
    like `termin_role=anonymous` continue to work after v0.9
    canonicalized the role to `Anonymous` (capitalized).
    """
    keys = list(roles_to_scopes.keys())
    if not keys:
        return ""  # caller decides what to do with no roles defined
    if candidate is None:
        return keys[0]
    if candidate in roles_to_scopes:
        return candidate
    cl = candidate.lower()
    for k in keys:
        if k.lower() == cl:
            return k
    return keys[0]


def _build_the_user_object(
    principal: Principal,
    scopes: list,
) -> dict:
    """v0.9 Phase 6a.4: Build the BRD #3 §4.2-shaped `the user` object.

    Distinct from the legacy `User` object (PascalCase fields) — this
    is the structure CEL expressions referencing `the user.X` will see.
    Fields per BRD §4.2:

      id            : principal id (text storage; principal-typed at the
                      business layer)
      display_name  : human-readable name or empty string for anonymous
      is_anonymous  : True iff Anonymous principal
      is_system     : True iff synthetic system principal
      scopes        : list of scope strings the principal holds in this
                      request (mirrors `User.Scopes` for now)
      preferences   : per-principal key-value store (e.g., `theme`)
    """
    return {
        "id": principal.id,
        "display_name": principal.display_name or "",
        "is_anonymous": principal.is_anonymous,
        "is_system": principal.is_system,
        "scopes": list(scopes),
        "preferences": dict(principal.preferences),
    }


def _build_user_dict(
    principal: Principal,
    role_name: str,
    scopes: list,
) -> dict:
    """Build the runtime's user dict that gets passed to handlers.

    Shape preserved from v0.8: keys role, scopes, profile, User. The
    new v0.9 key 'Principal' carries the typed Principal. v0.9 Phase
    6a.4 adds 'the_user' — the BRD #3 §4.2-shaped dict that CEL sees
    when source uses the `the user.X` symbol (rewritten by
    expression.py to `the_user.X` before compile).
    """
    authenticated = not principal.is_anonymous
    display_name = principal.display_name or ("Anonymous" if not authenticated else "User")
    profile = {
        "FirstName": display_name if authenticated else "Anonymous",
        "DisplayName": display_name if authenticated else "Anonymous",
    }
    user_obj = _build_user_object(principal, role_name, scopes)
    the_user = _build_the_user_object(principal, scopes)
    return {
        "role": role_name,
        "scopes": scopes,
        "profile": profile,
        "User": user_obj,
        "Principal": principal,
        "the_user": the_user,
    }


def _resolve_principal_and_scopes(
    cookie_role: str | None,
    cookie_name: str | None,
    roles_to_scopes: dict,
    identity_provider: IdentityProvider,
    app_id: str,
) -> tuple[Principal, str, list]:
    """Resolve credentials → (Principal, canonical_role_name, scopes).

    Per BRD §6.1: Anonymous bypasses the provider entirely — when the
    runtime determines a request is Anonymous, it constructs the
    Anonymous Principal directly without calling authenticate.

    The runtime determines a request is Anonymous when the resolved
    role name (after case-insensitive normalization + first-role
    fallback) is "Anonymous" / "anonymous". This means:
      - Explicit `termin_role=Anonymous` cookie → Anonymous bypass.
      - No cookie + first source role is Anonymous → Anonymous bypass.
      - No cookie + first source role is something else (e.g.,
        "warehouse clerk") → that role is assumed (the stub
        provider's dev-friendly default; real providers would
        reject no-credentials requests).

    Non-Anonymous resolutions route through the provider:
      - The cookie value is normalized to the canonical source role
        name before authenticate is called.
      - provider.authenticate({role, user_name}) → Principal.
      - provider.roles_for(principal, app_id) → role names.
      - Effective scopes = union of source scopes for each role.
    """
    canonical_role = _resolve_role_key(roles_to_scopes, cookie_role)
    if canonical_role.lower() == "anonymous":
        scopes = list(roles_to_scopes.get(canonical_role, []))
        return ANONYMOUS_PRINCIPAL, canonical_role, scopes
    user_name = cookie_name or "User"
    try:
        principal = identity_provider.authenticate({
            "role": canonical_role,
            "user_name": user_name,
        })
        provider_roles = identity_provider.roles_for(principal, app_id)
    except Exception:
        # Fail-closed per BRD §6.1: provider failure → Anonymous.
        # Logging is the deploy operator's concern; runtime doesn't
        # raise so the request proceeds with limited scopes.
        anon_key = _resolve_role_key(roles_to_scopes, "anonymous")
        scopes = list(roles_to_scopes.get(anon_key, []))
        return ANONYMOUS_PRINCIPAL, anon_key, scopes

    # Effective scopes = union over all returned roles. Map provider
    # role names (which the stub gets from cookies, may be in any
    # case) back to source canonical names before lookup.
    effective_scopes: set = set()
    chosen_role: str = canonical_role
    for r in provider_roles:
        canonical = _resolve_role_key(roles_to_scopes, r)
        effective_scopes.update(roles_to_scopes.get(canonical, []))
        # If the provider returns the principal's primary role first,
        # use it; otherwise stick with the cookie-canonical role.
        if r == canonical_role or canonical == canonical_role:
            chosen_role = canonical
    return principal, chosen_role, list(effective_scopes)


def make_get_current_user(
    roles_to_scopes: dict,
    identity_provider: IdentityProvider,
    app_id: str = "",
    ctx=None,
):
    """Create a get_current_user dependency bound to a specific
    role-to-scope mapping and IdentityProvider.

    `ctx` (RuntimeContext) optional — when supplied, Principal.preferences
    is hydrated from the runtime-managed preferences table on every
    request. Required for `the_user.preferences.theme` to resolve in
    CEL (v0.9 Phase 5a.3).
    """
    def get_current_user(request: Request) -> dict:
        cookie_role = request.cookies.get("termin_role")
        cookie_name = request.cookies.get("termin_user_name")
        principal, role_name, scopes = _resolve_principal_and_scopes(
            cookie_role, cookie_name, roles_to_scopes,
            identity_provider, app_id,
        )
        if ctx is not None:
            principal = _hydrate_principal_preferences(
                principal,
                getattr(ctx, "db_path", None),
                getattr(ctx, "theme_default", None),
                getattr(ctx, "theme_locked", None),
            )
        return _build_user_dict(principal, role_name, scopes)
    return get_current_user


def make_get_user_from_websocket(
    roles_to_scopes: dict,
    identity_provider: IdentityProvider,
    app_id: str = "",
    ctx=None,
):
    """Create a WebSocket auth function bound to a provider.

    `ctx` (RuntimeContext) optional — same hydration story as
    make_get_current_user.
    """
    def get_user_from_websocket(ws: WebSocket) -> dict:
        # Token query param reserved for future production auth (JWT,
        # session token); for now fall through to cookie auth.
        token = ws.query_params.get("token")
        if token:
            pass  # not validated in v0.9

        cookie_role = ws.cookies.get("termin_role")
        cookie_name = ws.cookies.get("termin_user_name")
        principal, role_name, scopes = _resolve_principal_and_scopes(
            cookie_role, cookie_name, roles_to_scopes,
            identity_provider, app_id,
        )
        if ctx is not None:
            principal = _hydrate_principal_preferences(
                principal,
                getattr(ctx, "db_path", None),
                getattr(ctx, "theme_default", None),
                getattr(ctx, "theme_locked", None),
            )
        return _build_user_dict(principal, role_name, scopes)
    return get_user_from_websocket


def make_require_scope(get_current_user_fn):
    """Create a require_scope factory bound to a specific
    get_current_user."""
    def require_scope(scope: str):
        """FastAPI dependency that checks the user has a required scope."""
        def checker(request: Request):
            user = get_current_user_fn(request)
            if scope not in user["scopes"]:
                raise HTTPException(
                    status_code=403, detail=f"Requires scope: {scope}"
                )
            return user
        return checker
    return require_scope
