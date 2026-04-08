"""Presentation renderer for the Termin runtime.

Walks a component tree (Presentation IR v2) and emits Jinja2 template
fragments. Each component type has a dedicated renderer function.
"""

from jinja2 import Environment, BaseLoader

jinja_env = Environment(loader=BaseLoader(), autoescape=True)


# ── Component renderers ──

def _render_text(node: dict) -> str:
    content = node.get("props", {}).get("content", "")
    if isinstance(content, dict) and content.get("is_expr"):
        expr = content["value"]
        return f'<div class="text-lg text-gray-800 mb-4" data-termin-expr="{expr}">...</div>'
    return f'<div class="text-lg text-gray-800 mb-4">{content}</div>'


def _render_data_table(node: dict) -> str:
    props = node.get("props", {})
    cols = props.get("columns", [])
    children = node.get("children", [])

    source = props.get("source", "")
    parts = [f'<table class="w-full bg-white shadow rounded overflow-hidden" data-termin-component="data_table" data-termin-source="{source}">']
    parts.append('  <thead class="bg-gray-100"><tr>')
    for col in cols:
        label = col.get("label", col.get("field", ""))
        parts.append(f'    <th class="px-4 py-2 text-left text-sm font-medium text-gray-600">{label}</th>')

    # Action column header if row_actions exist
    row_actions = props.get("row_actions", [])
    if row_actions:
        parts.append('    <th class="px-4 py-2 text-left text-sm font-medium text-gray-600">Actions</th>')

    parts.append('  </tr></thead><tbody>')
    # A5: Highlight — check for highlight child with JEXL condition
    highlight_expr = None
    for child in children:
        if child.get("type") == "highlight":
            cond = child.get("props", {}).get("condition", {})
            if isinstance(cond, dict) and cond.get("is_expr"):
                highlight_expr = cond["value"]

    if highlight_expr:
        # Convert JEXL field references to Jinja item.field references
        import re
        def _to_jinja_field(expr):
            """Convert JEXL row expression to Jinja2.
            Prefixes bare identifiers with 'item.' while preserving
            string literals and operators."""
            result = []
            # Split on quoted strings to avoid replacing inside them
            parts_split = re.split(r'("[^"]*"|\'[^\']*\')', expr)
            keywords = {'and', 'or', 'not', 'true', 'false', 'none', 'is', 'in', 'item'}
            for i, part in enumerate(parts_split):
                if i % 2 == 1:
                    # Quoted string — keep as-is
                    result.append(part)
                else:
                    # Replace .field -> item.field
                    part = re.sub(r'\.([a-zA-Z_]\w*)', r'item.\1', part)
                    # Replace bare identifiers -> item.identifier
                    part = re.sub(
                        r'\b([a-z_][a-zA-Z0-9_]*)\b',
                        lambda m: m.group(0) if m.group(1) in keywords else f'item.{m.group(1)}',
                        part)
                    # Deduplicate item.item. -> item.
                    part = part.replace('item.item.', 'item.')
                    result.append(part)
            return ''.join(result)
        jinja_expr = _to_jinja_field(highlight_expr)
        # Replace JEXL operators with Jinja equivalents
        jinja_expr = jinja_expr.replace("||", " or ").replace("&&", " and ")
        # Collect all field names referenced in the expression
        field_names = re.findall(r'item\.(\w+)', jinja_expr)
        # Use .get() with None default so missing fields → None → comparison fails
        jinja_expr = re.sub(r'item\.(\w+)', r'item.get("\1")', jinja_expr)
        # Guard: only evaluate when ALL referenced fields exist on the row
        if field_names:
            guards = " and ".join(f'item.get("{f}") is not none' for f in set(field_names))
            jinja_expr = f'{guards} and ({jinja_expr})'
        parts.append(f'    {{% for item in items %}}<tr class="border-t {{% if {jinja_expr} %}}bg-red-50 font-semibold{{% endif %}}" data-termin-row-id="{{{{ item.id }}}}">')
    else:
        parts.append('    {% for item in items %}<tr class="border-t" data-termin-row-id="{{ item.id }}">')
    for col in cols:
        key = col.get("field", "")
        parts.append(f'      <td class="px-4 py-2 text-sm" data-termin-field="{key}">{{{{ item.{key}|default("") }}}}</td>')

    # Action buttons per row — rendered conditionally based on state + scope
    if row_actions:
        parts.append('      <td class="px-4 py-2 text-sm space-x-1">')
        for action in row_actions:
            ap = action.get("props", {})
            label = ap.get("label", "Action")
            target = ap.get("target_state", "")
            source = props.get("source", "")
            safe_target = target.replace(" ", "_")
            behavior = ap.get("unavailable_behavior", "disable")

            # Build Jinja conditions:
            # 1. Is (current_status, target_state) a valid transition?
            # 2. Does the user hold the required scope for this transition?
            # _sm_transitions is a dict of {(from,to): scope} injected into context
            valid_check = f'(item.get("status",""), "{target}") in _sm_transitions'
            scope_check = f'_sm_transitions.get((item.get("status",""), "{target}"), "") in user_scopes or _sm_transitions.get((item.get("status",""), "{target}"), "") == ""'

            if behavior == "hide":
                # Hide: don't render the button at all when transition unavailable
                parts.append(f'        {{% if {valid_check} and ({scope_check}) %}}')
                parts.append(
                    f'        <form method="post" action="/_transition/{source}/{{{{ item.id }}}}/{safe_target}" '
                    f'style="display:inline">'
                    f'<button type="submit" class="text-indigo-600 hover:text-indigo-800 text-xs">{label}</button></form>')
                parts.append(f'        {{% endif %}}')
            else:
                # Disable (default): render grayed-out button when unavailable
                parts.append(f'        {{% if {valid_check} and ({scope_check}) %}}')
                parts.append(
                    f'        <form method="post" action="/_transition/{source}/{{{{ item.id }}}}/{safe_target}" '
                    f'style="display:inline">'
                    f'<button type="submit" class="text-indigo-600 hover:text-indigo-800 text-xs">{label}</button></form>')
                parts.append(f'        {{% else %}}')
                parts.append(
                    f'        <button disabled class="text-gray-400 text-xs cursor-not-allowed">{label}</button>')
                parts.append(f'        {{% endif %}}')
        parts.append('      </td>')

    parts.append('    </tr>{% endfor %}</tbody></table>')

    # Render sub-components (filter UI, search box, etc.) above or below the table
    sub_parts = []
    for child in children:
        rendered = _render_filter(child) if child.get("type") == "filter" else ""
        if rendered:
            sub_parts.append(rendered)
        if child.get("type") == "search":
            sub_parts.append(_render_search(child))
    # A6: Related data — render a related data section below the table
    related_parts = []
    for child in children:
        if child.get("type") == "related":
            rp = child.get("props", {})
            rel_content = rp.get("content", "")
            join_col = rp.get("join", "")
            related_parts.append(
                f'<div class="mt-4 bg-gray-50 rounded p-3 text-sm" '
                f'data-termin-component="related" data-termin-source="{rel_content}" '
                f'data-termin-join="{join_col}">'
                f'<div class="font-medium text-gray-700 mb-2">Related: {rel_content}</div>'
                f'<div class="text-gray-500">[Related {rel_content} by {join_col} loaded dynamically]</div>'
                f'</div>'
            )

    result = '\n'.join(sub_parts) + '\n' + '\n'.join(parts) if sub_parts else '\n'.join(parts)
    if related_parts:
        result += '\n' + '\n'.join(related_parts)
    return result


def _render_filter(node: dict) -> str:
    """Render a filter dropdown (displayed above the table)."""
    props = node.get("props", {})
    field = props.get("field", "")
    mode = props.get("mode", "text")
    options = props.get("options", [])
    if not options and mode not in ("enum", "state", "distinct", "reference"):
        return ""
    parts = [f'<div class="inline-block mr-4 mb-2">']
    parts.append(f'  <label class="text-sm text-gray-600">{field}:</label>')
    parts.append(f'  <select class="text-sm border rounded px-2 py-1" data-filter="{field}">')
    parts.append(f'    <option value="">All</option>')
    for opt in options:
        parts.append(f'    <option value="{opt}">{opt}</option>')
    parts.append(f'  </select>')
    parts.append(f'</div>')
    return '\n'.join(parts)


def _render_search(node: dict) -> str:
    fields = node.get("props", {}).get("fields", [])
    placeholder = f"Search by {', '.join(fields)}"
    return (f'<input type="text" placeholder="{placeholder}" '
            f'class="border rounded px-3 py-1 mb-2 text-sm" data-search="true">')


def _render_form(node: dict, content_schemas: dict = None) -> str:
    props = node.get("props", {})
    target = props.get("target", "")
    # Infer slug from context — form posts to the current page slug
    target = props.get("target", "")
    parts = [f'<form method="post" class="bg-white shadow rounded p-6 max-w-lg" data-termin-component="form" data-termin-target="{target}">']

    for child in node.get("children", []):
        if child.get("type") == "field_input":
            parts.append(_render_field_input(child, content_schemas))

    parts.append('  <input type="hidden" name="edit_id" value="">')
    parts.append('  <button type="submit" class="bg-indigo-600 text-white px-6 py-2 rounded hover:bg-indigo-700">Save</button>')
    parts.append('</form>')
    return '\n'.join(parts)


def _render_field_input(node: dict, content_schemas: dict = None) -> str:
    props = node.get("props", {})
    key = props.get("field", "")
    label = props.get("label", key)
    input_type = props.get("input_type", "text")
    required = ' required' if props.get("required") else ''
    unique_attr = ' data-validate-unique="true"' if props.get("validate_unique") else ''

    parts = [f'  <div class="mb-4">']
    parts.append(f'    <label class="block text-sm font-medium text-gray-700 mb-1">{label}</label>')

    if input_type == "enum":
        parts.append(f'    <select name="{key}" class="w-full border rounded px-3 py-2"{required}>')
        parts.append(f'      <option value="">Select...</option>')
        for val in props.get("enum_values", []):
            parts.append(f'      <option value="{val}">{val}</option>')
        parts.append(f'    </select>')
    elif input_type == "reference":
        ref = props.get("reference_content", "")
        ref_display = props.get("reference_display_col", "id")
        parts.append(f'    <select name="{key}" class="w-full border rounded px-3 py-2"{required}>')
        parts.append(f'      <option value="">Select...</option>')
        parts.append(f'      {{% for item in {ref}_list %}}')
        parts.append(f'      <option value="{{{{ item.id }}}}">{{{{ item.{ref_display} }}}}</option>')
        parts.append(f'      {{% endfor %}}')
        parts.append(f'    </select>')
    elif input_type in ("number", "currency", "whole_number"):
        step = f' step="{props["step"]}"' if props.get("step") else ""
        min_attr = f' min="{props["minimum"]}"' if props.get("minimum") is not None else ""
        parts.append(f'    <input type="number" name="{key}" class="w-full border rounded px-3 py-2"{step}{min_attr}{required}>')
    else:
        parts.append(f'    <input type="text" name="{key}" class="w-full border rounded px-3 py-2"{required}{unique_attr}>')

    parts.append(f'  </div>')
    return '\n'.join(parts)


def _render_aggregation(node: dict) -> str:
    props = node.get("props", {})
    label = props.get("label", "Metric")
    agg_type = props.get("agg_type", "count")
    source = props.get("source", "")
    key = label.lower().replace(" ", "_")[:30]
    return (f'<div class="bg-white shadow rounded p-4 mb-4" data-termin-component="aggregation" data-termin-source="{source}">\n'
            f'  <div class="text-sm text-gray-600">{label}</div>\n'
            f'  <div class="text-2xl font-bold mt-1" data-termin-agg="{agg_type}">...</div>\n'
            f'</div>')


def _render_stat_breakdown(node: dict) -> str:
    props = node.get("props", {})
    label = props.get("label", "Breakdown")
    source = props.get("source", "")
    group_by = props.get("group_by", "status")
    return (f'<div class="bg-white shadow rounded p-4 mb-4" data-termin-component="stat_breakdown" data-termin-source="{source}">\n'
            f'  <div class="text-sm text-gray-600">{label}</div>\n'
            f'  <div class="text-2xl font-bold mt-1" data-termin-agg="count_by" '
            f'data-group="{group_by}">...</div>\n'
            f'</div>')


def _render_chart(node: dict) -> str:
    props = node.get("props", {})
    source = props.get("source", "")
    chart_type = props.get("chart_type", "line")
    days = props.get("period_days", 30)
    label = props.get("label", f"{source} chart")
    return (f'<div class="bg-white shadow rounded p-4 mb-4">\n'
            f'  <div class="text-sm text-gray-600 mb-2">{label}</div>\n'
            f'  <canvas id="chart_{source}" data-chart-type="{chart_type}" '
            f'data-source="{source}" data-days="{days}" height="200"></canvas>\n'
            f'</div>')


def _render_section(node: dict) -> str:
    props = node.get("props", {})
    title = props.get("title", "")
    parts = [f'<div class="mb-6">']
    if title:
        parts.append(f'  <h2 class="text-xl font-semibold text-gray-800 mb-3">{title}</h2>')
    for child in node.get("children", []):
        parts.append(f'  {render_component(child)}')
    parts.append('</div>')
    return '\n'.join(parts)


def _render_action_button(node: dict) -> str:
    props = node.get("props", {})
    label = props.get("label", "Action")
    return f'<button class="bg-indigo-600 text-white px-4 py-2 rounded text-sm hover:bg-indigo-700">{label}</button>'


def _render_unknown(node: dict) -> str:
    comp_type = node.get("type", "unknown")
    return f'<div class="text-gray-400 text-sm">[{comp_type} component]</div>'


# ── Renderer dispatch ──

RENDERERS = {
    "text": _render_text,
    "data_table": _render_data_table,
    "form": _render_form,
    "field_input": _render_field_input,
    "aggregation": _render_aggregation,
    "stat_breakdown": _render_stat_breakdown,
    "chart": _render_chart,
    "section": _render_section,
    "action_button": _render_action_button,
    # Sub-components rendered inline by their parent:
    # "filter", "search", "highlight", "subscribe", "related"
}


def render_component(node: dict) -> str:
    """Render a single component node to a Jinja2 template fragment."""
    renderer = RENDERERS.get(node.get("type", ""), _render_unknown)
    return renderer(node)


# ── Page template builders ──

def build_page_template(page: dict) -> object:
    """Build a Jinja2 template for a page from its component tree."""
    parts = [f'<h1 class="text-2xl font-bold mb-4">{page["name"]}</h1>']
    for child in page.get("children", []):
        parts.append(render_component(child))
    return jinja_env.from_string("\n".join(parts))


def build_merged_page_template(pages: list) -> object:
    """Build a role-conditional template for multiple pages sharing a slug."""
    parts = [f'<h1 class="text-2xl font-bold mb-4">{pages[0]["name"]}</h1>']
    for page in pages:
        role = page["role"]
        cond = f'{{% if current_role == "{role}" or current_role|lower == "{role.lower()}" %}}'
        page_content = []
        for child in page.get("children", []):
            page_content.append(render_component(child))
        if page_content:
            parts.append(cond)
            parts.extend(page_content)
            parts.append('{% endif %}')
    return jinja_env.from_string("\n".join(parts))


# ── Navigation ──

def build_nav_html(nav_items: list, roles: list) -> str:
    """Build navigation HTML from IR nav items."""
    parts = []
    for item in nav_items:
        slug = item.get("page_slug", "")
        label = item.get("label", "")
        visible = item.get("visible_to", [])

        if "all" in visible:
            parts.append(f'<a href="/{slug}" class="text-gray-700 hover:text-indigo-600">{label}</a>')
        elif visible:
            checks = " or ".join(f'"{r}" in current_role' for r in visible)
            parts.append(f'{{% if {checks} %}}<a href="/{slug}" class="text-gray-700 hover:text-indigo-600">{label}</a>{{% endif %}}')

    return "\n                ".join(parts)


# ── Base template ──

def build_base_template(app_name: str, nav_html: str) -> object:
    """Build the base HTML template with nav bar and termin.js runtime."""
    template = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{app_name} - {{{{ page_title }}}}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-gray-50 min-h-screen">
    <nav class="bg-white shadow mb-6">
        <div class="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
            <div class="flex items-center space-x-6">
                <span class="text-lg font-bold text-indigo-600">{app_name}</span>
                {nav_html}
            </div>
            <form method="post" action="/set-role" class="flex items-center space-x-2">
                <label class="text-sm text-gray-600">Role:</label>
                <select name="role" onchange="this.form.submit()" class="text-sm border rounded px-2 py-1">
                    {{% for rname in roles %}}
                    <option value="{{{{ rname }}}}" {{% if rname == current_role %}}selected{{% endif %}}>{{{{ rname|title }}}}</option>
                    {{% endfor %}}
                </select>
                {{% if current_role != "anonymous" %}}
                <label class="text-sm text-gray-600 ml-2">Name:</label>
                <input type="text" name="user_name" value="{{{{ current_user_name }}}}"
                       placeholder="Display name" class="text-sm border rounded px-2 py-1 w-28"
                       onchange="this.form.submit()">
                {{% endif %}}
            </form>
        </div>
    </nav>
    <main class="max-w-7xl mx-auto px-4">
        {{{{ content|safe }}}}
    </main>
    <script id="termin-user-data" type="application/json">{{{{ user_profile_json|safe }}}}</script>
    <script type="module">
    import {{ evaluate }} from "https://cdn.jsdelivr.net/npm/@marcbachmann/cel-js/+esm";
    var profile = JSON.parse(document.getElementById("termin-user-data").textContent);
    var role = "{{{{ current_role }}}}";
    var ctx = {{ role: role, CurrentUser: profile, User: profile }};
    ctx[role] = {{ CurrentUser: profile }};
    {{{{ termin_compute_js|safe }}}}
    document.querySelectorAll("[data-termin-expr]").forEach(function(el) {{
        try {{
            var result = evaluate(el.dataset.terminExpr, ctx);
            el.textContent = result;
        }} catch(err) {{
            el.textContent = "[Error: " + err.message + "]";
        }}
    }});
    </script>
    <script type="module" src="/runtime/termin.js"></script>
</body>
</html>'''
    return jinja_env.from_string(template)
