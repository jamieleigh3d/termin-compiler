# v0.7.0 UAT Plan

**Purpose:** Derisk the v0.7 release by focusing manual testing on the riskiest changes — particularly UX and behavior that automated tests can't verify.

**Ordered by risk:** Critical first, then high, medium, low.

**How to run examples:**
```bash
# Compile first, then serve the package:
termin compile examples/warehouse.termin
termin serve warehouse.termin.pkg

# Or for headless (API-only) apps:
termin compile examples/headless_service.termin
termin serve headless_service.termin.pkg
```

---

## CRITICAL — Test these first (highest regression risk)

### 1. Warehouse: Access control with corrected role model
*Risk: 007 changed how compound verbs compile. Warehouse access model was also corrected — clerks can update but NOT create products. Managers create/delete.*

```bash
termin compile examples/warehouse.termin
termin serve warehouse.termin.pkg
```

**As clerk:**
- Can you see the product list? *(yes — inventory.read)*
- Can you update an existing product? *(yes — inventory.write grants UPDATE)*
- Do you see the "Add Product" nav link? *(no — visible to manager only)*
- Can you receive stock (create stock level)? *(yes — inventory.write grants CREATE on stock levels)*
- Try the API: `curl -X POST localhost:8000/api/v1/products -H 'Cookie: termin_role=warehouse clerk' -H 'Content-Type: application/json' -d '{"sku":"TEST","name":"Test"}'` — should return **403** (clerk can't create products)

**As manager:**
- Can you see "Add Product" nav link? *(yes)*
- Can you create a product via the form? *(yes — inventory.admin grants CREATE)*
- Can you delete a product? *(yes — inventory.admin grants DELETE)*

**As executive:**
- Can you view products? *(yes — inventory.read)* 
- Can you update, create, or delete? *(no — read only)*

### 2. Warehouse: State transitions with toast/banner feedback + live row updates
*Risk: 006 toast/banner, WS push, and client-side button re-evaluation are all new.*

- Log in as **clerk**, find a draft product, click "Activate":
  - **Toast** appears bottom-right with the product name + "is now active"? 
  - Toast auto-dismisses after ~5 seconds?
  - Row status updates to "active" **without page refresh** (via WebSocket)?
  - "Activate" button disables/disappears and "Discontinue" stays disabled (clerk lacks inventory.admin)?
- Log in as **manager**, click "Discontinue" on an active product:
  - Toast shows product name + "has been discontinued"?
  - Row updates live?
  - "Discontinue" disables, "Activate" does NOT appear (discontinued→active requires inventory.admin, which manager has — so "Activate" should re-enable)?

### 3. Agent Chatbot: New chat UI
*Risk: D-09 replaced data_table+form with a chat component. Complete UX change — zero visual test coverage.*

```bash
termin compile examples/agent_chatbot.termin
termin serve agent_chatbot.termin.pkg
```

- **Does it render as a chat?** Scrolling message area, input box at bottom, send button?
- Type a message, hit Send — does it appear as a right-aligned blue bubble?
- Does the AI response appear as a left-aligned gray bubble? (requires AI provider config — may not work without deploy config)
- Does the chat auto-scroll to the bottom?
- Is the input box usable? (placeholder text, clears after send, Enter key works?)

### 4. Headless Service: Pure API (no UI)
*Risk: D-11 created a new app pattern. Never been run manually.*

```bash
termin compile examples/headless_service.termin
termin serve headless_service.termin.pkg
```

- Hitting `localhost:8000/` in browser — what happens? (should redirect or show empty, no crash)
- `curl localhost:8000/api/v1/orders` — returns empty list?
- `curl -X POST localhost:8000/api/v1/orders -H 'Content-Type: application/json' -d '{"customer":"Acme","total":100}'` — creates a record with 201?
- `curl localhost:8000/api/v1/orders/1` — returns the record?
- `curl -X POST localhost:8000/api/v1/orders/1/_transition/confirmed` — transitions to confirmed?
- Missing required `customer` field — returns 422?

---

## HIGH — Test after critical items pass

### 5. Helpdesk: Multi-word states + banner with dismiss timer
*Risk: Multi-word states were a grammar gap. PEG fix tested programmatically, never in a real app.*

```bash
termin compile examples/helpdesk.termin
termin serve helpdesk.termin.pkg
```

- Create a ticket, transition "open" to "in progress" — toast shows?
- Transition "in progress" to "resolved" — **banner** with 10-second auto-dismiss? Countdown works?
- Full multi-word state chain: "waiting on customer" -> "in progress" works?

### 6. Security Agent: Audit logging at debug level
*Risk: D-20's debug-level trace recording. First app with full trace capture.*

```bash
termin compile examples/security_agent.termin
termin serve security_agent.termin.pkg
```

- Trigger the scanner compute
- Check `curl localhost:8000/api/v1/compute_audit_log_scanner` — trace record with all expected fields?
- Is the trace JSON readable? Right level of detail for `debug`?

### 7. Compute Demo: Audit level "none" suppresses traces
*Risk: Negative test — `none` should produce no trace records.*

```bash
termin compile examples/compute_demo.termin
termin serve compute_demo.termin.pkg
```

- Invoke `triage_order` (Audit level: none) — audit log should be empty
- Invoke `calculate_order_total` (Audit level: actions) — trace with tool calls but no thinking/system prompt

---

## MEDIUM — Spot-check these

### 8. HRPortal: Confidential fields + compound verbs

```bash
termin compile examples/hrportal.termin
termin serve hrportal.termin.pkg
```

- Role lacking `salary.access` — salary fields redacted in UI?
- `create, update, or delete departments` works? (was broken before 007)

### 9. Project Board: Deep FK chains + multi-word states

```bash
termin compile examples/projectboard.termin
termin serve projectboard.termin.pkg
```

- Full FK chain: project -> team member -> sprint -> task -> time log
- Task states: "backlog" -> "in sprint" -> "in progress" -> "in review" -> "done"
- Aggregation page shows correct sprint velocity?

### 10. All examples: Compile + serve smoke test
*Risk: Parser fallback code still exists but grammar gaps were closed. Verify no regressions.*

Compile and serve each — starts without errors, home page renders:
```bash
for f in examples/*.termin; do
  echo "=== $(basename $f) ==="
  termin compile "$f" && echo "OK" || echo "FAIL"
done
```

Then serve and hit home page of: `hello`, `hello_user`, `channel_simple`, `channel_demo`, `agent_simple`

---

## LOW — Verify if time permits

### 11. Auto-CRUD API consistency
- Pick any app, compare `/api/v1/{content}` JSON against IR schema
- Every Content type has endpoints (not just ones with user stories)

### 12. WebSocket: Live updates
- Open two browser tabs on warehouse or helpdesk
- Create a record in tab 1 — tab 2 updates without refresh?

---

## Recommendation

Start with 1-4 (critical). If those pass, do 5-7 (high). Items 8-10 are spot-checks. Skip 11-12 if short on time.

**Riskiest single item:** #3 (agent_chatbot chat UI) — complete UX change with zero visual coverage.
