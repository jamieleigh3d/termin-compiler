# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""FastAPI <-> termin-core routing adapter.

Slice 7.2.e of Phase 7 (2026-04-30) introduced this module as the
bridge between FastAPI's Request/Response types and termin-core's
framework-agnostic ``TerminRequest`` / ``TerminResponse`` /
``AuthContext`` types. Pure-core handlers can now run under
FastAPI without taking a FastAPI dependency.

The adapter has three responsibilities:

1. **Request unwrap** — pull method, path, query params, headers,
   cookies, body off ``fastapi.Request`` and pack a
   :class:`TerminRequest`.
2. **AuthContext assembly** — translate the legacy
   ``ctx.get_current_user(request)`` dict into an
   :class:`AuthContext` (principal + scopes + role_name).
3. **Response wrap** — translate :class:`TerminResponse` back to
   the appropriate FastAPI response (JSON / redirect / streaming /
   raw bytes).

This module moves to ``termin-server`` in slice 7.3 of Phase 7,
when the broader hosting layer leaves ``termin-compiler``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import (
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from termin_core.providers.identity_contract import (
    ANONYMOUS_PRINCIPAL,
    Principal,
)
from termin_core.routing import (
    AuthContext,
    TerminRequest,
    TerminResponse,
)


def _principal_from_user_dict(user: dict) -> Principal:
    """Build a :class:`Principal` from the legacy user dict shape.

    The legacy ``ctx.get_current_user(request)`` returns::

        {"role": "warehouse manager",
         "scopes": ["orders.read", ...],
         "the_user": {"id": "...", "display_name": "...", ...}}

    The ``the_user`` sub-dict is a Principal-shaped projection (BRD
    #3 §4.2). When it's present and well-formed, we promote it back
    to a :class:`Principal` value type. When absent or malformed,
    we fall back to :data:`ANONYMOUS_PRINCIPAL`.

    This mapping is transitional — slice 7.5 deletes
    ``ctx.get_current_user`` entirely and identity providers
    populate AuthContext directly via the adapter middleware path.
    """
    the_user = user.get("the_user") if isinstance(user, dict) else None
    if not isinstance(the_user, dict):
        return ANONYMOUS_PRINCIPAL
    pid = str(the_user.get("id", "") or "")
    if not pid:
        return ANONYMOUS_PRINCIPAL
    return Principal(
        id=pid,
        type=str(the_user.get("type", "human") or "human"),
        display_name=str(the_user.get("display_name", "") or ""),
        claims=dict(the_user.get("claims", {}) or {}),
        is_system=bool(the_user.get("is_system", False)),
    )


def make_auth_context(user: dict) -> AuthContext:
    """Translate the legacy user dict into an :class:`AuthContext`.

    Used by every FastAPI route handler that delegates to a
    termin-core handler. Once the slice-7.2.e migration is complete
    and every CRUD handler operates on AuthContext, this helper
    becomes the single conversion point — slice 7.5 then refactors
    it into a proper FastAPI middleware that sets ``request.auth``
    once at the boundary, and the per-handler call site goes away.
    """
    if not isinstance(user, dict):
        return AuthContext(principal=ANONYMOUS_PRINCIPAL)
    return AuthContext(
        principal=_principal_from_user_dict(user),
        scopes=tuple(user.get("scopes", []) or []),
        role_name=str(user.get("role", "") or ""),
    )


async def to_termin_request(
    fastapi_req: Request,
    *,
    path_params: dict | None = None,
    auth: AuthContext | None = None,
) -> TerminRequest:
    """Wrap a FastAPI :class:`Request` as a :class:`TerminRequest`.

    Reads the body once; subsequent calls would error because
    Starlette consumes the receive channel. Callers that need the
    body should use ``request.body``,
    ``await request.json()``, ``await request.form()`` on the
    returned :class:`TerminRequest` rather than calling FastAPI's
    parsers a second time.

    ``path_params`` is supplied by the caller because FastAPI's
    decorator extracts them via parameter names; the adapter takes
    the explicit dict so the bridge is independent of the
    FastAPI-decorator-pattern shape.

    ``auth`` is supplied by the caller (typically built via
    :func:`make_auth_context` from
    ``ctx.get_current_user(fastapi_req)``). Slice 7.5 moves the
    AuthContext assembly into a FastAPI middleware so handlers
    receive a fully-populated ``request.auth`` without per-route
    boilerplate.
    """
    body = await fastapi_req.body()
    # parse_qs-shape multi: FastAPI's query_params is a Starlette
    # MultiDict; we collapse the multi shape into a list-per-key
    # dict for query_params_multi and the last-value-wins shape
    # for query_params.
    query_multi: dict[str, list[str]] = {}
    for k in fastapi_req.query_params.keys():
        query_multi[k] = list(fastapi_req.query_params.getlist(k))
    query_single = {
        k: (vals[-1] if vals else "")
        for k, vals in query_multi.items()
    }
    client = None
    if fastapi_req.client is not None:
        client = (fastapi_req.client.host, fastapi_req.client.port)
    return TerminRequest(
        method=fastapi_req.method,
        path=fastapi_req.url.path,
        path_params=dict(path_params or {}),
        query_params=query_single,
        query_params_multi=query_multi,
        headers=dict(fastapi_req.headers),
        cookies=dict(fastapi_req.cookies),
        body=body,
        principal=auth.principal if auth else None,
        auth=auth,
        scheme=fastapi_req.url.scheme,
        client=client,
    )


def to_fastapi_response(resp: TerminResponse) -> Response:
    """Translate a :class:`TerminResponse` back to a FastAPI
    response.

    Resolution rules (first match wins):

    * ``redirect_url`` set → :class:`RedirectResponse` with the
      response's status_code.
    * ``streaming`` set → :class:`StreamingResponse`.
    * ``json_body`` set (or both bodies unset) → :class:`JSONResponse`.
    * ``body`` set → :class:`Response` with the configured headers
      and media_type.
    """
    headers = dict(resp.headers)

    if resp.redirect_url:
        return RedirectResponse(
            url=resp.redirect_url,
            status_code=resp.status_code,
            headers=headers,
        )

    if resp.streaming is not None:
        return StreamingResponse(
            resp.streaming,
            status_code=resp.status_code,
            headers=headers,
            media_type=resp.media_type or "application/octet-stream",
        )

    if resp.json_body is not None:
        return JSONResponse(
            content=resp.json_body,
            status_code=resp.status_code,
            headers=headers,
        )

    if resp.body is not None:
        return Response(
            content=resp.body,
            status_code=resp.status_code,
            headers=headers,
            media_type=resp.media_type,
        )

    # Empty body — typical for 204 No Content or 303 redirects with
    # no extra payload.
    return Response(
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.media_type,
    )


__all__ = [
    "make_auth_context",
    "to_termin_request",
    "to_fastapi_response",
]
