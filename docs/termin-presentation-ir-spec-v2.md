# Termin Presentation IR Specification

**Version:** 0.2.0-draft
**Status:** Formative — major revision incorporating feedback

---

## Problem

The current `PageSpec` has 22 fields. Every new Presentation feature requires a new field, updates to the lowering pass, the backend, and the renderer. Nothing composes.

## Solution

Replace `PageSpec` with a **component tree**. Each Presentation declaration becomes a tree of typed, composable UI components.

---

## Component Node Structure

Every component in the IR has this shape:

```json
{
  "type": "string",
  "props": {
    "key": { "value": "any", "is_expr": false },
    "key": { "value": "greeting", "is_expr": true }
  },
  "style": { ... },
  "layout": { ... },
  "children": [ ... ]
}
```

### Props and Expressions

Props are key-value pairs. Each value is an object with `value` and `is_expr`:

```json
{
  "content": { "value": "Hello, World", "is_expr": false },
  "label":   { "value": "SayHelloTo(LoggedInUser.CurrentUser)", "is_expr": true }
}
```

When `is_expr` is `true`, the runtime evaluates the `value` string as CEL. When `false`, the `value` is a literal.

This replaces the `=` prefix convention from v1. The lowering pass converts DSL bracket expressions `[expr]` to `{ "value": "expr", "is_expr": true }` and literal strings to `{ "value": "string", "is_expr": false }`.

**Shorthand for serialization:** When reading IR JSON, props with bare string values (no `is_expr` field) are treated as literals. This keeps simple cases readable:

```json
{
  "content": "Hello, World",
  "label": { "value": "SayHelloTo(LoggedInUser.CurrentUser)", "is_expr": true }
}
```

### Style

The `style` object controls visual appearance. All properties are optional. The DSL does not express style — it's set through the visual editor or deployment themes.

```json
"style": {
  "margin": "16px",
  "margin_top": "8px",
  "margin_bottom": "8px",
  "margin_left": "16px",
  "margin_right": "16px",
  "padding": "12px",
  "padding_top": "4px",
  "padding_bottom": "4px",
  "padding_left": "8px",
  "padding_right": "8px",
  "alignment": "left",
  "vertical_alignment": "top",
  "width": "100%",
  "max_width": "800px",
  "min_height": "200px",
  "background": "#ffffff",
  "border": "1px solid #e0e0e0",
  "border_radius": "4px",
  "font_size": "14px",
  "font_weight": "normal",
  "color": "#333333",
  "opacity": 1.0,
  "gap": "8px"
}
```

Symmetric shorthand: `margin` and `padding` set all four sides. Asymmetric overrides (`margin_top`, etc.) take precedence when present.

Renderers interpret style values according to their platform. The HTML renderer maps these to CSS. A native renderer maps them to platform equivalents. Values use CSS-like syntax for portability.

### Layout (Visual Editor Metadata)

The `layout` object stores visual editor canvas state. It is ignored by application renderers and only consumed by the visual editor.

```json
"layout": {
  "x": 120,
  "y": 340,
  "width": 400,
  "height": 250,
  "collapsed": false,
  "z_index": 1,
  "connector_anchors": {
    "input_top": { "x": 200, "y": 340 },
    "output_bottom": { "x": 200, "y": 590 }
  }
}
```

The visual editor reads and writes `layout`. The DSL ignores it. Round-tripping from visual editor → IR → visual editor preserves positions. Round-tripping from DSL → IR loses layout (the visual editor auto-arranges on first open).

---

## Page Entries

Each user story compiles to a **page entry** in the IR's `pages` array. The page entry contains routing metadata and a component tree.

```json
{
  "name": "Inventory Dashboard",
  "slug": "inventory_dashboard",
  "role": "warehouse clerk",
  "required_scope": "read inventory",
  "children": [
    { "type": "data_table", "props": { ... }, "children": [ ... ] },
    { "type": "aggregation", "props": { ... }, "children": [] }
  ]
}
```

There is no `root` wrapper and no `page` component type. The page entry *is* the page. Its `children` are the components on it. The `name`, `slug`, `role`, and `required_scope` fields are routing metadata — they determine which page a user sees. Everything inside `children` is the component tree.

When multiple page entries share the same `slug` but have different `role` values, the runtime's `selectPresentation(slug, identityContext)` picks the correct one.

---

## Visibility: The Unified `visible_when`

All components that can be conditionally shown or hidden use `visible_when`:

```json
{ "type": "conditional", "props": { "visible_when": { "value": "Identity.reflect.hasScope('admin')", "is_expr": true } }, "children": [...] }
{ "type": "action_button", "props": { "visible_when": { "value": ".state.canTransition('active')", "is_expr": true } }, "children": [] }
{ "type": "section", "props": { "title": "Admin Tools", "visible_when": { "value": "Identity.reflect.hasScope('admin')", "is_expr": true } }, "children": [...] }
```

There is no `when`, no `show_if`, no `condition`. It's always `visible_when`. If `visible_when` is absent, the component is always visible.

---

## Expression Evaluation Lifecycle

Expressions in the component tree evaluate at different times depending on context.

### Static Evaluation

Expressions evaluate **once at initial render** when there is no `subscribe` component in the ancestor chain. The rendered output is fixed until the page is reloaded.

```json
{ "type": "text", "props": { "content": { "value": "'Welcome, ' + Identity.reflect.currentUser.firstName", "is_expr": true } }, "children": [] }
```

### Reactive Evaluation

Expressions **re-evaluate when subscribed content changes**. A `subscribe` component in the tree activates reactivity for its parent and siblings.

```json
{
  "type": "data_table",
  "props": { "source": "tasks" },
  "children": [
    { "type": "subscribe", "props": { "content": "tasks" }, "children": [] }
  ]
}
```

When any `tasks` record changes (created, updated, deleted, transitioned), the Event Bus emits a change event. The renderer receives it and re-walks the `data_table` subtree: re-queries the data, re-evaluates row-level expressions, and re-evaluates `visible_when` on all child components including action buttons.

A State transition on row 5 → event emitted → table re-renders row 5 → action buttons on row 5 re-evaluate `visible_when` → "Start" button disables, "Done" button enables. No page reload.

### Row-Level Evaluation

Inside a `data_table`, expressions prefixed with `.` resolve against the **current row**. These evaluate once per row during rendering.

```json
{ "visible_when": { "value": ".state.canTransition('active')", "is_expr": true } }
{ "condition": { "value": ".quantity <= .reorderThreshold", "is_expr": true } }
```

### On-Demand Evaluation

Form validation expressions evaluate **when triggered by user action** (submit button, field blur, etc.), not continuously.

```json
{ "type": "field_input", "props": { "field": "sku", "validate_unique": true }, "children": [] }
```

### Summary

| Context | Trigger | Example |
|---|---|---|
| Static | Page load | Welcome message with user name |
| Reactive | Content change event via subscribe | Table row updates, button state changes |
| Row-level | Table row render | Per-row action button visibility |
| On-demand | User action (submit, blur) | Form field validation |

---

## Component Type Catalog

### Layout Components

**section** — a labeled group with optional visibility and nesting.

```json
{
  "type": "section",
  "props": {
    "title": "Summary",
    "collapsible": false,
    "visible_when": { "value": "Identity.reflect.hasScope('admin')", "is_expr": true }
  },
  "children": [ ... ]
}
```

Sections nest. A section can contain other sections:

```json
{
  "type": "section",
  "props": { "title": "Reports" },
  "children": [
    { "type": "section", "props": { "title": "Revenue" }, "children": [ ... ] },
    { "type": "section", "props": { "title": "Inventory" }, "children": [ ... ] }
  ]
}
```

**DSL syntax for nested sections:**

```
Section "Reports":
  Section "Revenue":
    Display sum of [orders.total] from orders as currency
  Section "Inventory":
    Display count of products grouped by status
```

**columns** — side-by-side layout.

```json
{
  "type": "columns",
  "props": { "count": 2 },
  "children": [ ... ]
}
```

**conditional** — visibility wrapper. Uses `visible_when` like everything else.

```json
{
  "type": "conditional",
  "props": { "visible_when": { "value": "Identity.reflect.hasScope('admin')", "is_expr": true } },
  "children": [ ... ]
}
```

### Data Display Components

**data_table** — table displaying Content records.

```json
{
  "type": "data_table",
  "props": {
    "source": "products",
    "columns": [
      { "field": "sku", "label": "SKU" },
      { "field": "name", "label": "Name" },
      { "field": "category", "label": "Category" },
      { "field": "status", "label": "Status" }
    ],
    "row_actions": [ ... ]
  },
  "children": [
    { "type": "filter", ... },
    { "type": "search", ... },
    { "type": "highlight", ... },
    { "type": "subscribe", ... },
    { "type": "related", ... }
  ]
}
```

Table sub-components are children. `row_actions` is a prop containing action button definitions because action buttons render *inside* each row, not as separate tree nodes.

**text** — static or dynamic text.

```json
{ "type": "text", "props": { "content": "Hello, World" }, "children": [] }
{ "type": "text", "props": { "content": { "value": "SayHelloTo(LoggedInUser.CurrentUser)", "is_expr": true } }, "children": [] }
```

**aggregation** — a computed summary value with structured definition.

```json
{
  "type": "aggregation",
  "props": {
    "label": "Total Stock Value",
    "agg_type": "sum",
    "source": "stock_levels",
    "expression": { "value": ".quantity * content.lookup('products', .product).unitCost", "is_expr": true },
    "format": "currency"
  },
  "children": []
}
```

**stat_breakdown** — count grouped by a field (typically status).

```json
{
  "type": "stat_breakdown",
  "props": {
    "source": "products",
    "label": "Products by Status",
    "group_by": "status"
  },
  "children": []
}
```

**chart** — data visualization.

```json
{
  "type": "chart",
  "props": {
    "source": "reorder_alerts",
    "chart_type": "line",
    "period_days": 30,
    "label": "Reorder Alerts (30 days)"
  },
  "children": []
}
```

### Input Components

**form** — data entry form targeting a Content schema.

```json
{
  "type": "form",
  "props": {
    "target": "products",
    "create_as": "draft",
    "submit_scope": "write inventory",
    "after_save": "return_to:inventory_dashboard"
  },
  "children": [
    { "type": "field_input", "props": { "field": "sku", "validate_unique": true }, "children": [] },
    { "type": "field_input", "props": { "field": "name" }, "children": [] },
    { "type": "field_input", "props": { "field": "unit_cost" }, "children": [] },
    { "type": "field_input", "props": { "field": "category" }, "children": [] }
  ]
}
```

**field_input** — single form field. The renderer infers the widget from the Content schema's business type:

| Business Type | Widget |
|---|---|
| text | Text input |
| number, whole_number | Number input |
| currency | Number input with step="0.01" and currency symbol |
| percent | Number input with % suffix |
| boolean | Checkbox |
| date | Date picker |
| datetime | Datetime picker |
| enum | Dropdown select |
| reference | Lookup selector (queries referenced Content) |
| list | Multi-value input |

The IR doesn't specify widgets. The renderer decides.

### Action Components

**action_button** — triggers State transitions or navigation. Appears in `row_actions` on data tables or as standalone children.

```json
{
  "type": "action_button",
  "props": {
    "label": "Activate",
    "action": "transition",
    "target_state": "active",
    "visible_when": { "value": ".state.canTransition('active')", "is_expr": true },
    "unavailable_behavior": "disable"
  },
  "children": []
}
```

`unavailable_behavior` determines what happens when `visible_when` evaluates to false:
- `"disable"` — button renders but is grayed out and non-interactive (default)
- `"hide"` — button does not render at all

**DSL syntax:**

```
For each task, show actions:
  "Start" transitions to "in progress" if available
  "Start" transitions to "in progress" if available, disable otherwise
  "Done" transitions to "done" if available, hide otherwise
```

`disable otherwise` is the default when neither is specified.

**nav_link** — inline navigation.

```json
{
  "type": "nav_link",
  "props": {
    "label": "View Details",
    "target_page": "product_detail",
    "params": { "id": { "value": ".id", "is_expr": true } }
  },
  "children": []
}
```

### Data Table Sub-Components

These only appear as children of `data_table`:

**filter** — adds a filter control to the table.

```json
{ "type": "filter", "props": { "field": "category", "mode": "enum" }, "children": [] }
{ "type": "filter", "props": { "field": "status", "mode": "state" }, "children": [] }
{ "type": "filter", "props": { "field": "warehouse", "mode": "distinct" }, "children": [] }
{ "type": "filter", "props": { "field": "sprint", "mode": "reference" }, "children": [] }
```

Filter modes: `enum` (from field's enum values), `state` (from State machine), `distinct` (unique values in data), `reference` (from referenced Content).

**search** — adds a search box.

```json
{ "type": "search", "props": { "fields": ["sku", "name"] }, "children": [] }
```

**highlight** — conditional row highlighting.

```json
{ "type": "highlight", "props": { "condition": { "value": ".quantity <= .reorderThreshold", "is_expr": true } }, "children": [] }
```

**subscribe** — activates reactive evaluation for the table.

```json
{ "type": "subscribe", "props": { "content": "stock_levels" }, "children": [] }
```

**related** — shows related Content grouped by a field.

```json
{ "type": "related", "props": { "content": "stock_levels", "join": "product", "group_by": "warehouse" }, "children": [] }
```

### Error and Reflection Components

**error_feed** — displays errors from primitive error Channels.

```json
{
  "type": "error_feed",
  "props": {
    "sources": ["order_webhook", "vendor_api"],
    "columns": ["source", "kind", "message", "timestamp"],
    "subscribe": true
  },
  "children": [
    { "type": "filter", "props": { "field": "kind", "mode": "enum" }, "children": [] }
  ]
}
```

**channel_health** — Channel state overview.

```json
{
  "type": "channel_health",
  "props": {
    "sources": { "value": "Channel.reflect.channels", "is_expr": true },
    "columns": ["name", "state", "errors", "lastActive"]
  },
  "children": []
}
```

**state_inspector** — State machine visualization.

```json
{
  "type": "state_inspector",
  "props": {
    "source": "products",
    "show_transitions": true,
    "show_history": true
  },
  "children": []
}
```

---

## Structured Aggregation DSL Syntax

Natural language aggregation descriptions like "Display total product count with active vs discontinued breakdown" are fragile. The parser has to guess what "total," "count," "breakdown," and "sum" mean from context. This is the same fragility we eliminated from Compute bodies.

Structured aggregation syntax:

```
Display count of products grouped by status
Display count of tickets
Display sum of [.quantity * .unitCost] from "stock levels" as currency
Display average of [.points] from tasks as number
Display minimum of [.unitCost] from products as currency
Display maximum of [.quantity] from "stock levels" as number
```

The pattern: `Display <agg_function> of <expression-or-field> from <content> as <format>`

For count with grouping: `Display count of <content> grouped by <field>`

For count without grouping: `Display count of <content>`

When `from` is omitted and the expression is a simple field reference, the source is inferred from the page's primary data table.

These parse deterministically:

| DSL | agg_type | source | expression | format |
|---|---|---|---|---|
| `Display count of products` | count | products | — | number |
| `Display count of products grouped by status` | count_by | products | status | number |
| `Display sum of [.quantity * .unitCost] from "stock levels" as currency` | sum | stock_levels | .quantity * .unitCost | currency |
| `Display average of [.points] from tasks` | average | tasks | .points | number |

---

## DSL to IR Examples

### Example 1: Hello World

**DSL:**
```
As anonymous, I want to see a page "Hello" so that I can be greeted:
  Display text "Hello, World"
```

**IR:**
```json
{
  "name": "Hello",
  "slug": "hello",
  "role": "anonymous",
  "required_scope": null,
  "children": [
    { "type": "text", "props": { "content": "Hello, World" }, "children": [] }
  ]
}
```

### Example 2: Role-Scoped Pages with Compute

**DSL:**
```
As Anonymous, I want to see a page "Hello" so that I can be greeted:
  Display text "Anon, Hello!"

As LoggedInUser, I want to see a page "Hello" so that I can be greeted:
  Display text [SayHelloTo(LoggedInUser.CurrentUser)]
```

**IR (two page entries, same slug):**
```json
[
  {
    "name": "Hello",
    "slug": "hello",
    "role": "Anonymous",
    "required_scope": null,
    "children": [
      { "type": "text", "props": { "content": "Anon, Hello!" }, "children": [] }
    ]
  },
  {
    "name": "Hello",
    "slug": "hello",
    "role": "LoggedInUser",
    "required_scope": null,
    "children": [
      { "type": "text", "props": { "content": { "value": "SayHelloTo(LoggedInUser.CurrentUser)", "is_expr": true } }, "children": [] }
    ]
  }
]
```

### Example 3: Inventory Dashboard (Full Data Table)

**DSL:**
```
As a "warehouse clerk", I want to see all products and their current stock levels
  so that I know what we have on hand:
    Show a page called "Inventory Dashboard"
    Display a table of products with columns: SKU, name, category, status
    For each product, show stock levels grouped by warehouse
    Highlight rows where [.quantity <= .reorderThreshold]
    Allow filtering by category, warehouse, status
    Allow searching by SKU or name
    This table subscribes to stock level changes
```

**IR:**
```json
{
  "name": "Inventory Dashboard",
  "slug": "inventory_dashboard",
  "role": "warehouse clerk",
  "required_scope": "read inventory",
  "children": [
    {
      "type": "data_table",
      "props": {
        "source": "products",
        "columns": [
          { "field": "sku", "label": "SKU" },
          { "field": "name", "label": "Name" },
          { "field": "category", "label": "Category" },
          { "field": "status", "label": "Status" }
        ]
      },
      "children": [
        { "type": "related", "props": { "content": "stock_levels", "join": "product", "group_by": "warehouse" }, "children": [] },
        { "type": "highlight", "props": { "condition": { "value": ".quantity <= .reorderThreshold", "is_expr": true } }, "children": [] },
        { "type": "filter", "props": { "field": "category", "mode": "enum" }, "children": [] },
        { "type": "filter", "props": { "field": "warehouse", "mode": "distinct" }, "children": [] },
        { "type": "filter", "props": { "field": "status", "mode": "state" }, "children": [] },
        { "type": "search", "props": { "fields": ["sku", "name"] }, "children": [] },
        { "type": "subscribe", "props": { "content": "stock_levels" }, "children": [] }
      ]
    }
  ]
}
```

### Example 4: Add Product Form

**DSL:**
```
As a "warehouse manager", I want to add new products to the catalog
  so that we can track their inventory:
    Show a page called "Add Product"
    Accept input for SKU, name, description, unit cost, category
    Validate that [SKU.isUnique()] before saving
    Create the product as draft
    After saving, offer to set initial stock levels per warehouse
```

**IR:**
```json
{
  "name": "Add Product",
  "slug": "add_product",
  "role": "warehouse manager",
  "required_scope": "write inventory",
  "children": [
    {
      "type": "form",
      "props": {
        "target": "products",
        "create_as": "draft",
        "submit_scope": "write inventory",
        "after_save": "offer:set initial stock levels per warehouse"
      },
      "children": [
        { "type": "field_input", "props": { "field": "sku", "validate_unique": true }, "children": [] },
        { "type": "field_input", "props": { "field": "name" }, "children": [] },
        { "type": "field_input", "props": { "field": "description" }, "children": [] },
        { "type": "field_input", "props": { "field": "unit_cost" }, "children": [] },
        { "type": "field_input", "props": { "field": "category" }, "children": [] }
      ]
    }
  ]
}
```

### Example 5: Executive Overview (Structured Aggregations)

**DSL:**
```
As an "executive", I want to see an overview of inventory health
  so that I can make purchasing decisions:
    Show a page called "Inventory Overview"
    Display count of products grouped by status
    Display sum of [.quantity * .unitCost] from "stock levels" as currency
    Show chart of "reorder alerts" over past 30 days as line
```

**IR:**
```json
{
  "name": "Inventory Overview",
  "slug": "inventory_overview",
  "role": "executive",
  "required_scope": "read inventory",
  "children": [
    {
      "type": "stat_breakdown",
      "props": {
        "source": "products",
        "label": "Products by Status",
        "group_by": "status"
      },
      "children": []
    },
    {
      "type": "aggregation",
      "props": {
        "label": "Total Stock Value",
        "agg_type": "sum",
        "source": "stock_levels",
        "expression": { "value": ".quantity * content.lookup('products', .product).unitCost", "is_expr": true },
        "format": "currency"
      },
      "children": []
    },
    {
      "type": "chart",
      "props": {
        "source": "reorder_alerts",
        "chart_type": "line",
        "period_days": 30,
        "label": "Reorder Alerts (30 days)"
      },
      "children": []
    }
  ]
}
```

### Example 6: Sprint Board with Action Buttons

**DSL:**
```
As a "developer", I want to see all tasks assigned to my sprint
  so that I know what to work on:
    Show a page called "Sprint Board"
    Display a table of tasks with columns: title, priority, status, assignee, points
    Allow filtering by status, priority, sprint
    Allow searching by title
    This table subscribes to tasks changes
    For each task, show actions:
      "Start" transitions to "in progress" if available
      "Review" transitions to "in review" if available
      "Done" transitions to "done" if available, hide otherwise
```

**IR:**
```json
{
  "name": "Sprint Board",
  "slug": "sprint_board",
  "role": "developer",
  "required_scope": "view projects",
  "children": [
    {
      "type": "data_table",
      "props": {
        "source": "tasks",
        "columns": [
          { "field": "title", "label": "Title" },
          { "field": "priority", "label": "Priority" },
          { "field": "status", "label": "Status" },
          { "field": "assignee", "label": "Assignee" },
          { "field": "points", "label": "Points" }
        ],
        "row_actions": [
          {
            "type": "action_button",
            "props": {
              "label": "Start",
              "action": "transition",
              "target_state": "in progress",
              "visible_when": { "value": ".state.canTransition('in progress')", "is_expr": true },
              "unavailable_behavior": "disable"
            },
            "children": []
          },
          {
            "type": "action_button",
            "props": {
              "label": "Review",
              "action": "transition",
              "target_state": "in review",
              "visible_when": { "value": ".state.canTransition('in review')", "is_expr": true },
              "unavailable_behavior": "disable"
            },
            "children": []
          },
          {
            "type": "action_button",
            "props": {
              "label": "Done",
              "action": "transition",
              "target_state": "done",
              "visible_when": { "value": ".state.canTransition('done')", "is_expr": true },
              "unavailable_behavior": "hide"
            },
            "children": []
          }
        ]
      },
      "children": [
        { "type": "filter", "props": { "field": "status", "mode": "state" }, "children": [] },
        { "type": "filter", "props": { "field": "priority", "mode": "enum" }, "children": [] },
        { "type": "filter", "props": { "field": "sprint", "mode": "reference" }, "children": [] },
        { "type": "search", "props": { "fields": ["title"] }, "children": [] },
        { "type": "subscribe", "props": { "content": "tasks" }, "children": [] }
      ]
    }
  ]
}
```

### Example 7: Nested Sections

**DSL:**
```
As a "project manager", I want to see project health metrics
  so that I can report progress to stakeholders:
    Show a page called "Project Dashboard"
    Section "Work Status":
      Display count of tasks grouped by status
      Display count of tasks
    Section "Time Tracking":
      Display sum of [.hours] from "time logs" as number
    Section "Team":
      Display count of "team members"
```

**IR:**
```json
{
  "name": "Project Dashboard",
  "slug": "project_dashboard",
  "role": "project manager",
  "required_scope": "view projects",
  "children": [
    {
      "type": "section",
      "props": { "title": "Work Status" },
      "children": [
        { "type": "stat_breakdown", "props": { "source": "tasks", "label": "Tasks by Status", "group_by": "status" }, "children": [] },
        { "type": "aggregation", "props": { "label": "Total Tasks", "agg_type": "count", "source": "tasks" }, "children": [] }
      ]
    },
    {
      "type": "section",
      "props": { "title": "Time Tracking" },
      "children": [
        { "type": "aggregation", "props": { "label": "Total Hours", "agg_type": "sum", "source": "time_logs", "expression": { "value": ".hours", "is_expr": true }, "format": "number" }, "children": [] }
      ]
    },
    {
      "type": "section",
      "props": { "title": "Team" },
      "children": [
        { "type": "aggregation", "props": { "label": "Team Members", "agg_type": "count", "source": "team_members" }, "children": [] }
      ]
    }
  ]
}
```

---

## Rendering Contract

**`renderComponent(component, context) → output`**

The renderer walks the tree depth-first:

1. If `visible_when` exists, evaluate it. If false, apply `unavailable_behavior` (skip or disable).
2. Check identity scope — skip if user lacks access.
3. Resolve `is_expr: true` props by evaluating them via the Expression Evaluator.
4. Render the component using the resolved props and `style`.
5. Recursively render `children`.
6. Return assembled output.

### Unknown Component Types

Development: render a placeholder showing the type name and props.
Production: skip silently. This enables forward compatibility.

### Renderer Implementations

All renderers consume the same component tree:

- **HTML renderer:** server-side HTML + Termin.js client runtime
- **JSON API renderer:** headless mode for API-only deployments
- **Visual editor renderer:** interactive canvas with drag/drop
- **Test renderer:** assertion-checkable data structures

---

## Migration from PageSpec

| Old PageSpec Field | New Component |
|---|---|
| `display_table` + `table_columns` | `data_table` with column props |
| `filters` | `filter` children of `data_table` |
| `search_fields` | `search` child of `data_table` |
| `highlight` | `highlight` child of `data_table` |
| `subscribe_stream` | `subscribe` child of `data_table` |
| `related` | `related` child of `data_table` |
| `form_fields` + `form_target_table` | `form` with `field_input` children |
| `create_as_status` | `form` prop `create_as` |
| `validate_unique_field` | `field_input` prop `validate_unique` |
| `after_save_instruction` | `form` prop `after_save` |
| `aggregations` | `aggregation` or `stat_breakdown` |
| `chart` | `chart` component |
| `static_texts` | `text` with literal content |
| `static_expressions` | `text` with `is_expr: true` content |
| `required_scope` | page entry `required_scope` |

---

## Open Questions

1. **Custom component registration.** Developers should be able to register new component types (kanban board, calendar, map view) the same way they register custom Compute. The renderer discovers registered component types at startup. Spec deferred to a future document.

2. **Style themes.** Should Termin support a theming system (light/dark, brand colors, typography scales) that applies across all components? A `theme` field on the application-level IR could provide defaults that individual component `style` objects override.

3. **Responsive layout.** How should components adapt to different screen sizes? Should `style` support breakpoint-specific overrides? Or should the renderer handle responsiveness internally based on component type?

4. **Animation and transitions.** Should the component tree support transition animations (fade, slide) when `visible_when` changes state? Or is that purely a renderer concern?
