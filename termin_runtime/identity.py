"""Identity helpers for the Termin runtime.

Provides get_current_user() and require_scope() as functions that
take a ROLES dict as configuration.
"""

from fastapi import Request, HTTPException


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
