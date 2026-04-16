# v0.7.0 UAT Plan

**Purpose:** Derisk the v0.7 release by focusing manual testing on the riskiest changes — particularly UX and behavior that automated tests can't verify.

**Ordered by risk:** Critical first, then high, medium, low.

---

## CRITICAL — Test these first (highest regression risk)

### 1. Warehouse: Access control with fixed verbs
*Risk: 007 changed how every compound verb compiles. If wrong, permissions are broken.*

- `termin serve examples/warehouse.termin`
- Log in as **clerk** — Can you create a product? Update a product? *(should work — `create or update`)*
- Log in as **executive** — Can you view products? *(yes)* Can you create? *(no — 403 or no button)*
- Log in as **manager** — Can you delete a product? *(yes — admin scope)*
- Try the API: `curl -X POST /api/v1/products` with clerk cookie — returns 201?

### 2. Warehouse: State transitions with toast/banner feedback
*Risk: 006 added new DSL syntax feeding through parser -> IR -> runtime. Toast rendering is new.*

- Create a draft product, click "Activate" — **do you see a toast** bottom-right with the product name + "is now active"? Does it auto-dismiss after ~5 seconds?
- Try activating an already-active product — **do you see a banner** saying "Could not activate product"? Does it persist until dismissed?
- Does the URL clean up after the toast shows? (no `_flash` params lingering)

### 3. Agent Chatbot: New chat UI
*Risk: D-09 replaced data_table+form with a chat component. Complete UX change — zero visual test coverage.*

- `termin serve examples/agent_chatbot.termin`
- **Does it render as a chat?** Scrolling message area, input box at bottom, send button?
- Type a message, hit Send — does it appear as a right-aligned blue bubble?
- Does the AI response appear as a left-aligned gray bubble?
- Does the chat auto-scroll to the bottom?
- Is the input box usable? (placeholder text, clears after send, Enter key works?)

### 4. Headless Service: Pure API (no UI)
*Risk: D-11 created a new app pattern. Never been run manually.*

- `termin serve examples/headless_service.termin`
- Hitting `/` in browser — what happens? (should redirect or show empty, no crash)
- `curl /api/v1/orders` — returns empty list?
- `curl -X POST /api/v1/orders -d '{"customer":"Acme","total":100}'` — creates a record?
- `curl /api/v1/orders/1` — returns the record?
- `curl /api/v1/orders/1/_transition/confirmed` — transitions work?
- Missing required `customer` field — returns 422?

---

## HIGH — Test after critical items pass

### 5. Helpdesk: Multi-word states + banner with dismiss timer
*Risk: Multi-word states were a grammar gap. PEG fix tested programmatically, never in a real app.*

- `termin serve examples/helpdesk.termin`
- Create a ticket, transition "open" to "in progress" — toast shows?
- Transition "in progress" to "resolved" — **banner** with 10-second auto-dismiss? Countdown works?
- Full multi-word state chain: "waiting on customer" -> "in progress" works?

### 6. Security Agent: Audit logging at debug level
*Risk: D-20's debug-level trace recording. First app with full trace capture.*

- `termin serve examples/security_agent.termin`
- Trigger the scanner compute
- Check `/api/v1/compute_audit_log_scanner` — trace record with all expected fields?
- Is the trace JSON readable? Right level of detail for `debug`?

### 7. Compute Demo: Audit level "none" suppresses traces
*Risk: Negative test — `none` should produce no trace records.*

- `termin serve examples/compute_demo.termin`
- Invoke `triage_order` (Audit level: none) — audit log should be empty
- Invoke `calculate_order_total` (Audit level: actions) — trace with tool calls but no thinking/system prompt

---

## MEDIUM — Spot-check these

### 8. HRPortal: Confidential fields + compound verbs
- Role lacking `salary.access` — salary fields redacted in UI?
- `create, update, or delete departments` works? (was broken before 007)

### 9. Project Board: Deep FK chains + multi-word states
- Full FK chain: project -> team member -> sprint -> task -> time log
- Task states: "backlog" -> "in sprint" -> "in progress" -> "in review" -> "done"
- Aggregation page shows correct sprint velocity?

### 10. All examples: Compile + serve smoke test
*Risk: Parser fallbacks removed. Any line TatSu can't handle crashes instead of guessing.*

- Run each with `termin serve` — starts without errors:
  - `hello`, `hello_user`, `channel_simple`, `channel_demo`, `agent_simple`
- Hit home page of each — renders without 500 errors?

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
