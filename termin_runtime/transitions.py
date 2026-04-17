"""State transition feedback — toast/banner notifications for transitions.

Thread 006: Flash/toast notification primitives with CEL interpolation.
Handles the generic transition endpoint used by presentation action buttons.
"""

import urllib.parse

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from .context import RuntimeContext
from .storage import get_db, get_record_by_id
from .state import do_state_transition


def build_transition_feedback(ir: dict) -> dict:
    """Build transition feedback lookup from IR state machines.

    Returns: {(content_ref, from_state, to_state): [feedback_specs]}
    """
    feedback = {}
    for sm in ir.get("state_machines", []):
        for t in sm.get("transitions", []):
            fb_list = t.get("feedback", [])
            if fb_list:
                key = (sm["content_ref"], t["from_state"], t["to_state"])
                feedback[key] = fb_list
    return feedback


def get_feedback(transition_feedback: dict, content: str,
                 from_state: str, to_state: str, trigger: str) -> list:
    """Look up transition feedback specs for a given trigger (success/error)."""
    specs = transition_feedback.get((content, from_state, to_state), [])
    return [fb for fb in specs if fb["trigger"] == trigger]


def eval_feedback_message(ctx: RuntimeContext, fb: dict, record: dict = None,
                          from_state: str = "", to_state: str = "",
                          content_name: str = "") -> str:
    """Evaluate a feedback message — CEL expression or literal string."""
    if not fb.get("is_expr"):
        return fb["message"]
    try:
        cel_ctx = dict(record) if record else {}
        cel_ctx["from_state"] = from_state
        cel_ctx["to_state"] = to_state
        singular = content_name.rstrip("s") if content_name.endswith("s") else content_name
        for ct in ctx.ir.get("content", []):
            if ct["name"]["snake"] == content_name:
                singular = ct.get("singular", singular)
                break
        if record:
            cel_ctx[singular] = dict(record)
        return str(ctx.expr_eval.evaluate(fb["message"], cel_ctx))
    except Exception:
        return fb["message"]


def append_flash_params(ctx: RuntimeContext, url: str, feedback_specs: list,
                        record: dict = None, from_state: str = "",
                        to_state: str = "", content_name: str = "") -> str:
    """Append _flash query params to a URL for feedback rendering."""
    if not feedback_specs:
        return url
    fb = feedback_specs[0]
    msg = eval_feedback_message(ctx, fb, record, from_state, to_state, content_name)
    separator = "&" if "?" in url else "?"
    params = urllib.parse.urlencode({
        "_flash": msg,
        "_flash_style": fb["style"],
        "_flash_level": fb["trigger"],
    })
    if fb.get("dismiss_seconds") is not None:
        params += f"&_flash_dismiss={fb['dismiss_seconds']}"
    return url + separator + params


def register_transition_routes(app, ctx: RuntimeContext):
    """Register the generic transition endpoint."""

    @app.post("/_transition/{content}/{record_id}/{target_state}")
    async def generic_transition(content: str, record_id: int, target_state: str,
                                 request: Request):
        """Presentation-layer transition by record ID. Converts underscores in
        target_state back to spaces for multi-word states."""
        # Reject unknown content types immediately (don't leak SQL errors)
        if content not in ctx.content_lookup:
            raise HTTPException(status_code=404, detail=f"Unknown content: {content}")
        target = target_state.replace("_", " ")
        user = ctx.get_current_user(request)
        db = await get_db(ctx.db_path)
        try:
            # Get full record before transition for feedback CEL evaluation
            record = await get_record_by_id(db, content, record_id) or {}
            from_state = record.get("status")

            result = await do_state_transition(db, content, record_id, target, user,
                                               ctx.sm_lookup, ctx.terminator, ctx.event_bus)
            is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

            # Build feedback message
            feedback_msg = None
            feedback_spec = None
            if from_state:
                success_fb = get_feedback(ctx.transition_feedback, content,
                                          from_state, target, "success")
                if success_fb:
                    feedback_spec = success_fb[0]
                    feedback_msg = eval_feedback_message(
                        ctx, feedback_spec, record, from_state, target, content)

            accept = request.headers.get("accept", "")
            has_referer = bool(request.headers.get("referer"))
            is_browser_form = has_referer and "text/html" in accept and not is_ajax

            if is_ajax:
                response = {"id": record_id, "status": target}
                if feedback_msg:
                    response["_flash"] = feedback_msg
                    response["_flash_style"] = feedback_spec["style"]
                    response["_flash_level"] = "success"
                    if feedback_spec.get("dismiss_seconds") is not None:
                        response["_flash_dismiss"] = feedback_spec["dismiss_seconds"]
                return response
            elif is_browser_form:
                referer = request.headers.get("referer", "/")
                if from_state:
                    referer = append_flash_params(
                        ctx, referer, success_fb or [], record, from_state, target, content)
                return RedirectResponse(url=referer, status_code=303)
            else:
                # API client — return the record
                return result

        except HTTPException as exc:
            is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
            accept = request.headers.get("accept", "")
            has_referer = bool(request.headers.get("referer"))
            # API clients (curl, conformance tests) have no Referer and accept JSON.
            # Browser form submits have a Referer and we redirect with flash params.
            is_browser_form = has_referer and "text/html" in accept and not is_ajax

            if is_ajax:
                error_response = {"detail": exc.detail}
                if from_state:
                    error_fb = get_feedback(ctx.transition_feedback, content,
                                            from_state, target, "error")
                    if error_fb:
                        fb = error_fb[0]
                        error_response["_flash"] = eval_feedback_message(
                            ctx, fb, record, from_state, target, content)
                        error_response["_flash_style"] = fb["style"]
                        error_response["_flash_level"] = "error"
                        if fb.get("dismiss_seconds") is not None:
                            error_response["_flash_dismiss"] = fb["dismiss_seconds"]
                raise HTTPException(status_code=exc.status_code, detail=error_response)
            elif is_browser_form:
                referer = request.headers.get("referer", "/")
                if from_state:
                    error_fb = get_feedback(ctx.transition_feedback, content,
                                            from_state, target, "error")
                    referer = append_flash_params(
                        ctx, referer, error_fb, record, from_state, target, content)
                return RedirectResponse(url=referer, status_code=303)
            else:
                # API client — return the actual error status
                raise
        finally:
            await db.close()
