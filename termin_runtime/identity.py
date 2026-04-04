"""Identity helpers for the Termin runtime.

Provides get_current_user() and require_scope() as functions that
take a ROLES dict as configuration. Supports both HTTP requests
(cookie-based) and WebSocket connections (handshake auth).
"""

from fastapi import Request, HTTPException, WebSocket


def make_get_current_user(roles: dict):
    """Create a get_current_user dependency bound to a specific ROLES dict."""
    def get_current_user(request: Request) -> dict:
        """Get current user from cookie or default to first role."""
        role = request.cookies.get("termin_role", list(roles.keys())[0])
        if role not in roles:
            role = list(roles.keys())[0]
        display_name = request.cookies.get("termin_user_name", "User")
        profile = {"FirstName": display_name, "DisplayName": display_name}
        if role == "anonymous":
            profile = {"FirstName": "Anonymous", "DisplayName": "Anonymous"}
        return {"role": role, "scopes": roles[role], "profile": profile}
    return get_current_user


def make_get_user_from_websocket(roles: dict):
    """Create a WebSocket auth function. Reads role from cookies or query params."""
    def get_user_from_websocket(ws: WebSocket) -> dict:
        # Try token query param first (for production auth)
        token = ws.query_params.get("token")
        if token:
            # Future: validate JWT/session token
            # For now, fall through to cookie auth
            pass

        # Fall back to cookie auth (dev mode)
        role = ws.cookies.get("termin_role", list(roles.keys())[0])
        if role not in roles:
            role = list(roles.keys())[0]
        display_name = ws.cookies.get("termin_user_name", "User")
        profile = {"FirstName": display_name, "DisplayName": display_name}
        if role == "anonymous":
            profile = {"FirstName": "Anonymous", "DisplayName": "Anonymous"}
        return {"role": role, "scopes": roles[role], "profile": profile}
    return get_user_from_websocket


def make_require_scope(get_current_user_fn):
    """Create a require_scope factory bound to a specific get_current_user."""
    def require_scope(scope: str):
        """FastAPI dependency that checks the user has a required scope."""
        def checker(request: Request):
            user = get_current_user_fn(request)
            if scope not in user["scopes"]:
                raise HTTPException(status_code=403, detail=f"Requires scope: {scope}")
            return user
        return checker
    return require_scope
