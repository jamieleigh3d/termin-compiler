# Retrospective: WebSocket Sync Bug in Agent Simple

**Date:** 2026-04-10
**Duration to fix:** ~2 hours of active debugging across multiple attempts
**Impact:** Records created via UI form didn't appear in the table until page refresh. LLM responses were invisible without manual reload.
**Root cause count:** 5 distinct bugs found during investigation

---

## What happened

User creates a record via the UI form. The record is saved to the database, and (if AI is configured) the LLM fills in the response field a few seconds later. The user should see the row appear immediately and the response fill in via WebSocket push. Instead: nothing appeared until a manual page refresh.

## The five bugs (in order of discovery)

1. **Event loop isolation:** The LLM Compute runs in a background thread with its own `asyncio.new_event_loop()`. Publishing to the event bus (`asyncio.Queue`) from a different loop meant WebSocket subscribers on the main loop never received the update. Fix: `run_coroutine_threadsafe()` to publish on the main loop.

2. **Form redirect killing WebSocket:** The form POST returned 303 redirect, causing a full page reload that dropped the WebSocket connection. Any push events during the reconnection window were missed. Fix: AJAX form submit with `Accept: application/json`, server returns JSON instead of redirect.

3. **Event payload wrapping:** The broadcast extracted `event.get("record")` but `create_record` in storage.py uses `event.get("data")` as the key. The client received `{"channel_id": "...", "data": {...}}` instead of the record directly, so `data.id` was undefined. Fix: try `event.get("data")` first.

4. **Duplicate rows:** Both the AJAX form handler and the WebSocket `created` push added rows to the table. Fix: let WebSocket be the single source of truth; AJAX only clears the form.

5. **Browser JS cache:** `termin.js` was served with `Cache-Control: max-age=3600`. Even after fixing the code, the browser kept running the old JS. Hard-refresh didn't help on JL's Linux browser. Fix: `Cache-Control: no-cache` + content-hash query param on the script tag.

---

## Five Whys: Why did this take so long to debug?

### Why #1: Why didn't the first fix (run_coroutine_threadsafe) solve it?
Because there were 5 bugs stacked on top of each other. Fixing the event loop isolation was necessary but not sufficient — the form redirect, payload wrapping, duplicate rows, and JS caching were all independent failures that masked each other.

### Why #2: Why were there 5 bugs?
Because the feature crossed 4 layers (form handler → event bus → WebSocket forwarder → client JS) and none of these layers had integration tests that verified the end-to-end flow. Each layer was tested in isolation (form returns 200, event bus publishes, WebSocket sends frames) but the integration between them was untested.

### Why #3: Why didn't we have integration tests for the end-to-end flow?
Because the TestClient doesn't exercise the real server path. `TestClient` is synchronous — it processes requests inline, the event loop runs in-process, and WebSocket frames are delivered immediately. The background-thread timing, browser caching, and redirect behavior that cause the bugs in production don't exist in TestClient. **Our tests validated a different execution model than the one users experience.**

### Why #4: Why did the port collision earlier (JL's dev server on 8000) waste an hour?
Because we didn't follow our own process note: "Check for zombie processes before debugging" (`feedback_zombie_processes.md`). The symptom (handler not executing) was identical to a code bug, and we went deep into the code before checking the environment. Process discipline would have caught it in 30 seconds.

### Why #5: Why did the browser cache waste another 30 minutes?
Because `Cache-Control: max-age=3600` was set for production performance but there's no development mode that disables it. And the server has no mechanism to signal "the JS changed" to already-connected browsers. The content-hash fix solves it going forward, but the root issue is: **we had no way to verify the client was running the code we thought it was running.**

---

## Five Whys: Why didn't automated tests find this?

### Why #1: Why didn't the conformance suite catch the sync bug?
Because the conformance suite tests IR structure and HTTP API responses. It doesn't test WebSocket behavior, real-time push timing, or client-side rendering. The conformance suite validates the contract (given this IR, these API calls should work) but not the experience (when I save a form, the table updates).

### Why #2: Why doesn't the conformance suite test WebSocket behavior?
Because WebSocket testing requires a persistent connection, asynchronous event delivery, and timing-sensitive assertions. The current test infrastructure (pytest + TestClient) doesn't model the real browser's event-driven behavior. We'd need something like Playwright or Selenium to test real browser WebSocket behavior.

### Why #3: Why doesn't the test suite catch cross-loop async bugs?
Because `TestClient` runs everything on one event loop in one thread. The background-thread + new-event-loop pattern that causes the bug in production never executes in tests. The TestClient's `websocket_connect` is synchronous — it doesn't model the real async WebSocket lifecycle.

### Why #4: How would a third-party runtime like an AWS-native Termin runtime validate this behavior?
They can't, with the current conformance suite. The conformance suite tests:
- IR structure (does the compiled output have the right fields?)
- API behavior (does POST /api/v1/notes return 201?)
- Access control (does the wrong role get 403?)

It does NOT test:
- Real-time push (does a WebSocket subscriber see the update?)
- Event-triggered Compute (does the LLM fire when a record is created?)
- Client-side rendering (does the table update without a page refresh?)

An AWS-native Termin runtime implementer could build a fully compliant runtime that passes all 467 conformance tests but has the exact same sync bug we just fixed. The conformance suite validates the contract, not the behavior.

### Why #5: What would it take to actually test this?
Three levels of testing we're missing:

**Level 1: Async WebSocket integration tests.** Use `asyncio` test infrastructure (not TestClient) to create a real WebSocket connection, publish events, and verify push delivery. This catches the event loop isolation bug.

**Level 2: Browser automation tests.** Use Playwright or Selenium to load the page, fill in a form, click Save, and assert the table row appears within N seconds. This catches the form redirect, JS cache, and duplicate row bugs.

**Level 3: Timing-sensitive conformance tests.** The conformance suite should specify: "When a record is created, a connected WebSocket subscriber MUST receive a push event within 1 second." This is a behavioral contract that third-party runtimes must honor.

---

## Action items

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| 1 | Add Level 1 async WebSocket tests to the main repo | High | Medium |
| 2 | Add "WebSocket push delivery" to the conformance spec | High | Small |
| 3 | Add browser automation tests (Playwright) for form→table flow | Medium | Large |
| 4 | Add `--dev` flag to `termin serve` that sets no-cache headers and enables debug logging | Medium | Small |
| 5 | Document the TestClient vs live server execution model difference | High | Small |
| 6 | Add a "smoke test" command: `termin test <app.termin.pkg>` that starts the server, creates a record, checks WebSocket push, and reports | Medium | Medium |

---

## Lessons for the process

1. **TestClient is necessary but not sufficient.** It validates the API contract. It does NOT validate the user experience. Any bug that involves timing, threading, browser behavior, or caching is invisible to TestClient.

2. **The conformance suite tests the contract, not the behavior.** This is by design — the conformance suite is portable across runtimes. But it means conforming runtimes can still have UX bugs. We need a separate "experience test" layer.

3. **Five bugs stacked on each other is a sign of insufficient integration testing.** Each bug was trivial to fix. The hard part was finding them because they masked each other. Integration tests that exercise the full path (form → server → event bus → WebSocket → DOM) would have caught all five.

4. **Check the environment before the code.** The port collision and JS cache bugs were environmental, not code bugs. Both wasted significant time because we assumed the code was wrong. Process note: always verify the runtime environment (ports, processes, caches, file versions) before diving into code debugging.

5. **Cache-busting should be the default for development.** Serving static files with long cache TTLs during development is an anti-pattern. The content-hash approach solves it permanently, but we should have had no-cache from the start.
