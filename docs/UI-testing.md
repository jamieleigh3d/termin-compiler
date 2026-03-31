# an AWS-native Termin runtime UI Testing Guide

How to perform ad-hoc browser-based UI testing of a compiled an AWS-native Termin runtime application.

---

## Prerequisites

1. Compile the warehouse example:
   ```bash
   an AWS-native runtime compile examples/warehouse.an AWS-native runtime -o app.py
   ```
2. Start the server:
   ```bash
   pip install fastapi uvicorn aiosqlite jinja2 python-multipart
   python app.py
   ```
3. Open `http://localhost:8000/inventory_dashboard` in a browser.

---

## Test Matrix

### 1. Role Switcher and Navigation Visibility

| Step | Action | Expected | Status |
|------|--------|----------|--------|
| 1.1 | Select "Warehouse Clerk" in role dropdown | Page reloads as clerk | PASS |
| 1.2 | Check nav bar as clerk | Only "Dashboard" visible; "Add Product" and "Overview" hidden | PASS |
| 1.3 | Select "Warehouse Manager" in role dropdown | Page reloads as manager | PASS |
| 1.4 | Check nav bar as manager | "Dashboard" and "Add Product" visible; "Overview" hidden | PASS |
| 1.5 | Select "Executive" in role dropdown | Page reloads as executive | PASS |
| 1.6 | Check nav bar as executive | "Dashboard" and "Overview" visible; "Add Product" hidden | PASS |

### 2. Add Product Form (as Manager)

| Step | Action | Expected | Status |
|------|--------|----------|--------|
| 2.1 | Click "Add Product" in nav | `/add_product` page loads with empty form | PASS |
| 2.2 | Verify form fields | SKU, name, description, unit cost, category (dropdown) | PASS |
| 2.3 | Fill all fields, click Save | 303 redirect back to blank form | PASS |
| 2.4 | Navigate to Dashboard | New product appears in table with status "draft" | PASS |
| 2.5 | Category dropdown values | "raw material", "finished good", "packaging" | PASS |

### 3. Inventory Dashboard

| Step | Action | Expected | Status |
|------|--------|----------|--------|
| 3.1 | Load dashboard | Products table with columns: SKU, name, category, status, stock levels | PASS |
| 3.2 | Filter by category "raw material" | Only raw material products shown; URL updates with `?category=raw+material` | PASS |
| 3.3 | Search for "Widget" | Only products with "Widget" in SKU or name shown; URL updates with `?q=Widget` | PASS |
| 3.4 | Clear filters (navigate to bare URL) | All products shown | PASS |
| 3.5 | Stock levels column | Shows inline stock per warehouse (e.g., "Warehouse A:100") | PASS |
| 3.6 | SSE subscription | `hx-ext="sse"` attribute present on products table, connects to `/api/v1/stream` | PASS |

### 4. Executive Overview

| Step | Action | Expected | Status |
|------|--------|----------|--------|
| 4.1 | Switch to Executive, click "Overview" | `/inventory_overview` page loads | PASS |
| 4.2 | Product count aggregation | Shows total with active/draft/discontinued breakdown | PASS |
| 4.3 | Stock value aggregation | Shows dollar total (sum of quantity * unit cost) | PASS |
| 4.4 | Reorder alerts chart | Chart.js canvas rendered with "reorder alerts" legend | PASS |

### 5. State Transitions via UI

| Step | Action | Expected | Status |
|------|--------|----------|--------|
| 5.1 | Look for Activate/Discontinue buttons on dashboard | No buttons present | GAP |
| 5.2 | State transitions via API | `POST /api/v1/products/{sku}/activate` and `/discontinue` work correctly | PASS (API only) |

### 6. Stock Level Management via UI

| Step | Action | Expected | Status |
|------|--------|----------|--------|
| 6.1 | Look for stock update form on dashboard | No form present | GAP |
| 6.2 | Stock level CRUD via API | `POST /api/v1/stock-levels` and `PUT /api/v1/stock-levels/{id}` work | PASS (API only) |

---

## Identified UI Gaps

These features work at the API level but have no UI in `examples/warehouse.an AWS-native runtime`:

| Gap | Source | Severity | Notes |
|-----|--------|----------|-------|
| No state transition buttons | No user story generates them | Medium | Users cannot activate/discontinue products from the UI; must use API directly |
| No stock level edit form | `AfterSave` directive ("offer to set initial stock levels") parsed but not implemented in codegen | Medium | Stock levels can only be managed via API |
| Highlight rows (untestable) | Dashboard story: "Highlight rows where quantity is at or below reorder threshold" | Unknown | CSS class logic is generated but no low-stock test data existed to visually verify |

### PRFAQ vs. warehouse.an AWS-native runtime

The PRFAQ Appendix B contains a more complete example with additional nav items ("Receive Stock", "Alerts" with badge) and their corresponding user stories. The `examples/warehouse.an AWS-native runtime` that ships with the compiler is a **subset** — it only includes the three user stories (Dashboard, Add Product, Overview) and their matching nav items. The "missing" pages are not compiler bugs; they are features not yet authored in the example `.an AWS-native runtime` file.

### Potential future enhancements

- Add user stories for Receive Stock and Reorder Alerts to `warehouse.an AWS-native runtime` (matching the PRFAQ)
- Generate state transition buttons (Activate/Discontinue) on dashboard rows based on the State machine declaration
- Implement `AfterSave` directive to generate follow-up forms (e.g., initial stock levels)
- Implement nav badge expressions (e.g., `badge: open alert count`)

---

## How to Reproduce This Testing Process

### Manual (browser)

1. Start `python app.py`
2. Walk through each row in the test matrix above
3. Use the role switcher dropdown (top-right) to test each role
4. Check the browser URL bar to confirm filter/search parameters

### Automated (pytest)

```bash
cd an AWS-native runtime
pip install httpx
python -m pytest tests/test_e2e.py -v
```

The 41 automated e2e tests cover all API-level validation from MVP Spec Section 8, plus UI rendering checks (HTML content assertions). They use FastAPI's `TestClient` for in-process testing (no subprocess needed).

### Browser automation (Claude Code + Chrome)

For interactive UI testing via Claude Code with the Chrome MCP extension:

1. Ensure `python app.py` is running on localhost:8000
2. Use `tabs_context_mcp` to get/create a tab
3. Navigate to pages with `navigate`
4. Use `find` to locate elements by purpose (e.g., "role dropdown", "Save button")
5. Use `form_input` to fill fields, `computer` with `left_click` to click buttons
6. Use `screenshot` after each action to verify visual state
7. Use `javascript_tool` to inspect DOM attributes (e.g., HTMX attributes)

---

## Test Data Setup

The warehouse example starts with an empty database. For meaningful UI testing:

```bash
# Create a product via API
curl -X POST http://localhost:8000/api/v1/products \
  -H 'Content-Type: application/json' \
  -d '{"sku":"W-001","name":"Widget","unit_cost":25.00,"category":"finished good"}'

# Activate it
curl -X POST http://localhost:8000/api/v1/products/W-001/activate

# Add stock level
curl -X POST http://localhost:8000/api/v1/stock-levels \
  -H 'Content-Type: application/json' \
  -d '{"product":1,"warehouse":"Warehouse A","quantity":100,"reorder_threshold":20}'

# Trigger a reorder alert (update stock below threshold)
curl -X PUT http://localhost:8000/api/v1/stock-levels/1 \
  -H 'Content-Type: application/json' \
  -d '{"quantity":5,"reorder_threshold":20}'

# Verify alert created
curl http://localhost:8000/api/v1/alerts
```
