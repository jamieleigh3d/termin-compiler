# CEL User-Field Surface Analysis

**Status:** Slice 7.5b prep — analysis for `legacy_user_dict` removal.
**Author:** Claude Anthropic, 2026-04-30 (afternoon).
**Audience:** JL — read this, decide on the rename / remap question for each entry, then I implement.

## Why this exists

Slice 7.2.e of Phase 7 added a `legacy_user_dict: Optional[dict]` field to `TerminRequest` as a transitional carrier. It exists because **CRUD field-default CEL evaluation reads `User.Username` / `User.Role` / `User.Authenticated` etc. via PascalCase keys on a dict shape that pre-dates `AuthContext`**. Pure-core handlers can't synthesize that shape from `AuthContext` alone without lossy guesses, so for v0.9 we passed the original runtime dict through.

Slice 7.5b removes `legacy_user_dict`. To do that cleanly we need to decide, **per CEL context**, what the user-shaped binding looks like and where each leaf comes from. This doc enumerates every CEL evaluation site that touches a user-shaped binding, lists the leaves in scope at that site today, and makes a recommendation for the post-removal source-of-truth.

## TL;DR

There are two parallel user shapes in CEL today:

| Symbol in CEL | PascalCase keys it exposes | Source today | BRD reference |
|---|---|---|---|
| `User.*` | `Name`, `Username`, `FirstName`, `Role`, `Scopes`, `Authenticated` | `_build_user_object` in `termin_server.identity` | None — legacy v0.8 shape |
| `the user.*` (rewrites to `the_user.*` in CEL) | `id`, `display_name`, `is_anonymous`, `is_system`, `scopes`, `preferences` | `_build_the_user_object` in `termin_server.identity` | BRD #3 §4.2 |

`User.*` is the legacy shape. `the_user.*` is the BRD-anchored v0.9 shape.

**Recommendation tree:**

1. **Keep `the user.*` exactly as-is** — it's the BRD-shaped surface, fully derivable from `AuthContext + Principal + role_name`. No rename, no remap. This becomes the canonical v1.0 surface.
2. **Deprecate `User.*` in v0.10** by adding a compiler warning when source uses `User.X`, with a fix-it suggestion mapping each PascalCase leaf to its `the user.X` (or new) equivalent. Keep the runtime binding through v0.9 → v0.10 so existing examples don't break.
3. **For v0.9 / slice 7.5b specifically:** drop `legacy_user_dict` as a *carrier* on `TerminRequest`, and instead build the `User` dict at the boundary from `AuthContext + role_name` with a small builder helper that lives in core (next to the existing `_user_view_for_state` helper). This is option (a) from your earlier message.

The rest of this doc is the supporting evidence. §1 enumerates the CEL contexts. §2 details each `User.*` leaf and where its data lives in `AuthContext`. §3 details `the user.*` similarly. §4 gives the proposed rename / remap matrix. §5 sketches the implementation.

---

## §1 — CEL contexts that bind a user-shaped value

Every CEL evaluation site, by file:

| # | Site | What evaluates | User binding shape |
|---|---|---|---|
| 1 | `validation/dependents.py::evaluate_field_defaults` | Per-field `default_expr` for missing fields on create | `User` (PascalCase) |
| 2 | `routing/crud.py` (transition handler) | Currently builds `_user_view_for_state` from AuthContext for state-machine eval | `the_user` + `scopes` + `role` |
| 3 | `termin_server.transitions::transition_feedback` evaluator | Transition feedback message templating | `User` (PascalCase, via cel_ctx) |
| 4 | `termin_server.pages` page-data CEL | Render-time text expressions in `Display text` | `User` (via `user.get("User", {})`) |
| 5 | `termin_server.compute_runner` | Compute precondition / postcondition / body CEL | `User` (PascalCase) |
| 6 | `termin_server.app::run_event_handlers` | Event-trigger condition CEL | (none — record fields only) |
| 7 | `termin_server.app` event filter `where` clauses | Filter on event subscriptions | (none — record fields only) |
| 8 | `termin_core.errors.router::TerminAtor` condition CEL | Error-condition matchers | (none — error envelope only) |
| 9 | `validation/dependents.py::validate_dependent_values` | Dependent-field `when` clauses | (none — record fields only) |

Sites 6–9 don't touch a user binding, so they're irrelevant to this analysis.

Sites 1, 3, 4, 5 read `User` (PascalCase). **All four currently depend on `legacy_user_dict`'s `"User"` key.** This is the lift.

Site 2 already runs through AuthContext; that's the model the others should follow.

---

## §2 — `User.*` leaves: today's source of truth, post-removal source

`_build_user_object` in `termin_server.identity` constructs the dict the legacy CEL surface reads. Each leaf:

### `User.Name`

- **Today:** `principal.display_name or "User"` (or `"Anonymous"` when anonymous).
- **AuthContext source:** `auth.principal.display_name`.
- **Recommendation:** **Remap, no rename.** The `Name` leaf is the closest analog to `the user.display_name`. Keep `User.Name` as a v0.9 alias for `the user.display_name`. v0.10 deprecation flow.

### `User.Username`

- **Today:** `display_name.lower().replace(" ", "_")` for authenticated, `"anonymous"` for anonymous. Synthesized — not a separate field on Principal.
- **AuthContext source:** Computed from `auth.principal.display_name` the same way.
- **Recommendation:** **Remap, no rename.** Username has no BRD-shaped equivalent on `the user` because the BRD doesn't formalize it. Users have *id* (stable opaque) and *display_name* (text). The Username leaf is a runtime-side kebab/snake lowercase derivation of display_name. Keep the same derivation in the new builder. Possible v0.10 simplification: collapse to `the user.id` if the identity provider's id is human-readable; otherwise keep the derivation.

### `User.FirstName`

- **Today:** `display_name.split()[0]` — first whitespace-separated token.
- **AuthContext source:** Computed the same way from `auth.principal.display_name`.
- **Recommendation:** **Remap, no rename.** Same status as Username — synthetic, derived. Keep the derivation in the new builder. A future BRD could formalize `the user.first_name` if there's demand; not blocking.

### `User.Role`

- **Today:** `role_name` — the canonical role name resolved from cookie / claims by `_resolve_role_key`.
- **AuthContext source:** `auth.role_name` — added in slice 7.2.e for exactly this reason.
- **Recommendation:** **Remap, no rename.** Direct mapping. `User.Role` reads `auth.role_name`.

### `User.Scopes`

- **Today:** `list(scopes)` — flat list of scope strings.
- **AuthContext source:** `list(auth.scopes)` — already a tuple, list-coerced.
- **Recommendation:** **Remap, no rename.** Direct mapping. Note: `the user.scopes` *also* exposes the same thing (intentional — the BRD §4.2 includes scopes on Principal). v0.10+ may pick one of the two as canonical and deprecate the other.

### `User.Authenticated`

- **Today:** `not principal.is_anonymous`.
- **AuthContext source:** `not auth.is_anonymous`.
- **Recommendation:** **Remap, no rename.** Direct mapping. Note: `the user.is_anonymous` (negated) is the BRD-shape spelling. Same v0.10 dedup conversation as Scopes.

### Summary: every `User.*` leaf is derivable from `AuthContext` alone

There are **zero** leaves on `User.*` that require the legacy dict shape. We can drop `legacy_user_dict` and rebuild the `User` binding from `AuthContext + role_name` at every CEL site without information loss.

---

## §3 — `the user.*` leaves: status quo

For completeness — these are the BRD #3 §4.2-shaped fields, all already plumbed through `_build_the_user_object` and the slice 7.2.e `_user_view_for_state` helper. **No changes proposed.**

| Leaf | Source | Notes |
|---|---|---|
| `the user.id` | `auth.principal.id` | The principal's stable opaque id. |
| `the user.display_name` | `auth.principal.display_name` | Empty string for anonymous (per BRD). |
| `the user.is_anonymous` | `auth.is_anonymous` | Convenience property on AuthContext. |
| `the user.is_system` | `auth.is_system` | Same — system principals get `True`. |
| `the user.scopes` | `list(auth.scopes)` | Mirrors `User.Scopes`. |
| `the user.preferences` | `dict(auth.principal.preferences)` | Includes `theme` after Phase 5a hydration. |

`the user` is the canonical v1.0+ surface. Don't rename, don't remap — already correctly anchored.

---

## §4 — Proposed rename / remap matrix

| Source-level CEL | Rename? | Remap from `legacy_user_dict["User"]` to | v0.10 deprecation? |
|---|---|---|---|
| `User.Name` | No | `auth.principal.display_name` (or `"Anonymous"` fallback) | Yes — encourage `the user.display_name` |
| `User.Username` | No | derived from `auth.principal.display_name` (lowercase + snake) | Yes — encourage `the user.id` if it's human-readable |
| `User.FirstName` | No | derived from `auth.principal.display_name` (first token) | No clear deprecation target until BRD formalizes |
| `User.Role` | No | `auth.role_name` | Possibly — consider whether role belongs on `the user` |
| `User.Scopes` | No | `list(auth.scopes)` | Yes — encourage `the user.scopes` |
| `User.Authenticated` | No | `not auth.is_anonymous` | Yes — encourage `the user.is_anonymous` (negated) |
| `the user.*` (all leaves) | No | (already correct — already reads from `AuthContext`) | n/a |

**No renames in v0.9.** Keep the surface stable; just change where the leaves come from. The deprecation column is forward-looking guidance for v0.10 BRD work — out of scope for slice 7.5b.

---

## §5 — Implementation sketch for slice 7.5b

The work has three layers:

### 5.1 — A `build_user_view_for_cel(auth)` helper in core

Lives in `termin_core.routing.auth` next to AuthContext. Pure, no dependencies. Returns a `dict[str, Any]` shaped exactly like the legacy `User` object so existing CEL `User.*` references keep working.

```python
def build_user_view_for_cel(auth: Optional[AuthContext]) -> dict:
    """Build the legacy `User` PascalCase binding from an AuthContext.
    Used by every CEL site that historically read user["User"].
    Slice 7.5b: replaces the legacy_user_dict carrier on TerminRequest.
    """
    if auth is None:
        return {
            "Name": "Anonymous", "Username": "anonymous", "FirstName": "Anonymous",
            "Role": "", "Scopes": [], "Authenticated": False,
        }
    p = auth.principal
    authenticated = not auth.is_anonymous
    display_name = p.display_name or ("Anonymous" if not authenticated else "User")
    return {
        "Name": display_name if authenticated else "Anonymous",
        "Username": display_name.lower().replace(" ", "_") if authenticated else "anonymous",
        "FirstName": display_name.split()[0] if authenticated and display_name else "Anonymous",
        "Role": auth.role_name,
        "Scopes": list(auth.scopes),
        "Authenticated": authenticated,
    }
```

### 5.2 — Update every CEL call site to call the helper

Sites 1, 3, 4, 5 from §1. The pattern is:

```python
# Before:
default_ctx = {"User": user.get("User", {}), ...}

# After:
default_ctx = {"User": build_user_view_for_cel(request.auth), ...}
```

For sites in `termin_server` that don't currently take a `TerminRequest`, plumb `auth` through their existing call sites (most already pass a `user` dict; replace with `auth`).

### 5.3 — Drop `TerminRequest.legacy_user_dict`

Remove the field. Remove every `legacy_user_dict=user` argument at the FastAPI bridge call sites in `routes.py`, `compute_runner.py`. Update `to_termin_request` to not accept the parameter. The 12 bridge call sites become two lines shorter each.

### 5.4 — Test matrix

- One core unit test per User leaf, asserting the `build_user_view_for_cel(auth)` output for: anonymous, authenticated-with-display-name, authenticated-without-display-name, system-principal.
- One integration test confirming a content type with `default_expr: User.Username` populates correctly through the CRUD `create` handler with no `legacy_user_dict`.
- The existing CRUD + transition + page-rendering conformance tests are the regression guard. If any of them touch `User.*` in a fixture's CEL, they'll exercise the new path.

### 5.5 — Estimated size

- ~80 lines in `termin_core/routing/auth.py` (helper + tests)
- ~40 lines deleted across `termin_core/routing/{crud,channels}.py` (drop `legacy_user_dict` parameter and field)
- ~50 lines deleted across `termin_server/{routes,compute_runner,fastapi_adapter}.py` (drop the carrier wiring)
- ~20 lines updated across `termin_server/{pages,compute_runner,transitions}.py` (call the new helper)

Net effect: slimmer `TerminRequest` value type, single source of truth for the legacy User shape, no regression risk because the CEL surface is bit-identical.

---

## §6 — Open questions for JL

1. **Confirm option (a):** rebuild `User` from `AuthContext` (this doc's recommendation) vs option (b): build at the FastAPI bridge and stash on a separate ctx hook. (a) wins on cleanliness; (b) wins on speed if you're worried about correctness risk. My read: (a) is safe because the leaves are mechanical derivations and the conformance suite covers the CEL paths.

2. **`the_user` keyword vs `the user` source spelling.** Source uses `the user.X`. The expression evaluator rewrites to `the_user.X` before CEL eval (because CEL keys can't have spaces). This is `_rewrite_the_user` in `termin_core.expression.cel`. Out of scope for this slice — just flagging it survives the migration unchanged.

3. **v0.10 deprecation wedge.** Do you want me to add a compiler warning for `User.*` references in v0.10 source, with a per-leaf fix-it suggestion? Not blocking 7.5b; ask now or later.

4. **Should `User.Role` migrate to `the user.role`?** BRD #3 §4.2 doesn't list `role` on `the user` — but it's load-bearing on the legacy shape. Two camps: (i) the BRD shape is intentionally minimal so role belongs on a separate `request.role_name` reference instead; (ii) role is intrinsic to "the user in this request" and should be on `the user`. Punt to v0.10 — slice 7.5b just keeps `User.Role` working.

---

*End of analysis.*
