# Termin UI Testing Guide

How to perform ad-hoc browser-based UI testing of a compiled Termin application.

---

## Prerequisites

1. Compile the warehouse example:
   ```bash
   termin compile examples/warehouse.termin -o app.py --backend runtime
   ```
2. Start the server:
   ```bash
   pip install -e .
   python app.py
   ```
3. Open `http://localhost:8000/` in a browser.

---

## Test Matrix

### 1. Role Switcher and Navigation Visibility

| Step | Action | Expected |
|------|--------|----------|
| 1.1 | Select "Warehouse Clerk" in role dropdown | Page reloads as clerk |
| 1.2 | Check nav bar as clerk | "Dashboard", "Receive Stock", "Alerts" visible |
| 1.3 | Select "Warehouse Manager" in role dropdown | Page reloads as manager |
| 1.4 | Check nav bar as manager | "Dashboard" and "Add Product" visible |
| 1.5 | Select "Executive" in role dropdown | Page reloads as executive |
| 1.6 | Check nav bar as executive | "Dashboard" and "Overview" visible |

### 2. WebSocket Connection

| Step | Action | Expected |
|------|--------|----------|
| 2.1 | Open browser console | No WebSocket errors |
| 2.2 | Check bottom-right indicator | "Connected" (green, fading) |
| 2.3 | Open `/runtime/registry` in new tab | JSON with boundaries and WebSocket URL |
| 2.4 | Open `/runtime/bootstrap` in new tab | JSON with identity, pages, schemas |

### 3. Real-Time Updates

| Step | Action | Expected |
|------|--------|----------|
| 3.1 | Open Dashboard in Tab A | Products table visible |
| 3.2 | Open Add Product in Tab B | Form visible |
| 3.3 | Add a product in Tab B, save | Tab B redirects |
| 3.4 | Check Tab A (no refresh) | New product row appears with yellow flash |

### 4. Seed Data

If using seed data:
```bash
termin compile examples/warehouse.termin -o app.py --backend runtime
# Seed file auto-copied if examples/warehouse_seed.json exists
rm app.db  # fresh database
python app.py
```

The warehouse seed provides 6 products and 6 stock levels.

---

## Automated Testing

```bash
# Full test suite
python -m pytest tests/ -v

# Runtime-only tests (faster)
python -m pytest tests/test_runtime.py -v

# E2E tests (compiles + runs via TestClient)
python -m pytest tests/test_e2e.py tests/test_helpdesk.py tests/test_projectboard.py -v
```
