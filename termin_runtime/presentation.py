"""Presentation renderer for the Termin runtime.

Builds Jinja2 templates and page routes dynamically from IR PageSpecs.
"""

from jinja2 import Environment, BaseLoader

jinja_env = Environment(loader=BaseLoader(), autoescape=True)


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
    import jexl from "https://cdn.jsdelivr.net/npm/jexl@2.3.0/+esm";
    var profile = JSON.parse(document.getElementById("termin-user-data").textContent);
    var role = "{{{{ current_role }}}}";
    var ctx = {{ role: role, CurrentUser: profile }};
    ctx[role] = {{ CurrentUser: profile }};
    {{{{ termin_compute_js|safe }}}}
    document.querySelectorAll("[data-termin-expr]").forEach(function(el) {{
        jexl.eval(el.dataset.terminExpr, ctx).then(function(result) {{
            el.textContent = result;
        }}).catch(function(err) {{
            el.textContent = "[Error: " + err.message + "]";
        }});
    }});
    </script>
</body>
</html>'''
    return jinja_env.from_string(template)


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


def build_merged_page_template(pages: list) -> object:
    """Build a role-conditional template for multiple pages sharing a slug."""
    parts = [f'<h1 class="text-2xl font-bold mb-4">{pages[0]["name"]}</h1>']

    for page in pages:
        role = page["role"]
        cond = f'{{% if current_role == "{role}" or current_role|lower == "{role.lower()}" %}}'
        page_parts = _build_page_content_parts(page)
        if page_parts:
            parts.append(cond)
            parts.extend(page_parts)
            parts.append('{% endif %}')

    return jinja_env.from_string("\n".join(parts))


def _build_page_content_parts(page: dict) -> list:
    """Build the content parts for a single page (no heading)."""
    parts = []
    for text in page.get("static_texts", []):
        parts.append(f'<div class="text-lg text-gray-800 mb-4">{text}</div>')
    for expr in page.get("static_expressions", []):
        parts.append(f'<div class="text-lg text-gray-800 mb-4" data-termin-expr="{expr}">...</div>')
    # Add table/form/agg parts here if needed
    if page.get("display_content") and page.get("table_columns"):
        cols = page["table_columns"]
        parts.append('<table class="w-full bg-white shadow rounded overflow-hidden">')
        parts.append('  <thead class="bg-gray-100"><tr>')
        for col in cols:
            parts.append(f'    <th class="px-4 py-2 text-left text-sm font-medium text-gray-600">{col["display"]}</th>')
        parts.append('  </tr></thead><tbody>')
        parts.append('    {% for item in items %}<tr class="border-t">')
        for col in cols:
            parts.append(f'      <td class="px-4 py-2 text-sm">{{{{ item.{col["key"]}|default("") }}}}</td>')
        parts.append('    </tr>{% endfor %}</tbody></table>')
    return parts


def build_page_template(page: dict) -> object:
    """Build a Jinja2 template for a single page from IR PageSpec."""
    parts = [f'<h1 class="text-2xl font-bold mb-4">{page["name"]}</h1>']

    # Static text
    for text in page.get("static_texts", []):
        parts.append(f'<div class="text-lg text-gray-800 mb-4">{text}</div>')

    # Static expressions (client-side jexl)
    for expr in page.get("static_expressions", []):
        parts.append(f'<div class="text-lg text-gray-800 mb-4" data-termin-expr="{expr}">...</div>')

    # Display table
    if page.get("display_content") and page.get("table_columns"):
        cols = page["table_columns"]
        parts.append('<table class="w-full bg-white shadow rounded overflow-hidden">')
        parts.append('  <thead class="bg-gray-100"><tr>')
        for col in cols:
            parts.append(f'    <th class="px-4 py-2 text-left text-sm font-medium text-gray-600">{col["display"]}</th>')
        parts.append('  </tr></thead>')
        parts.append('  <tbody>')
        parts.append('    {% for item in items %}')
        parts.append('    <tr class="border-t">')
        for col in cols:
            parts.append(f'      <td class="px-4 py-2 text-sm">{{{{ item.{col["key"]}|default("") }}}}</td>')
        parts.append('    </tr>')
        parts.append('    {% endfor %}')
        parts.append('  </tbody>')
        parts.append('</table>')

    # Form
    if page.get("form_fields"):
        slug = page.get("slug", "")
        parts.append(f'<form method="post" action="/{slug}" class="bg-white shadow rounded p-6 max-w-lg">')
        for field in page["form_fields"]:
            key = field["key"]
            display = field["display"]
            input_type = field.get("input_type", "text")
            required = " required" if field.get("required") else ""

            parts.append(f'  <div class="mb-4">')
            parts.append(f'    <label class="block text-sm font-medium text-gray-700 mb-1">{display}</label>')

            if input_type == "enum":
                parts.append(f'    <select name="{key}" class="w-full border rounded px-3 py-2">')
                parts.append(f'      <option value="">Select...</option>')
                for val in field.get("enum_values", []):
                    parts.append(f'      <option value="{val}">{val}</option>')
                parts.append(f'    </select>')
            elif input_type == "reference":
                ref = field.get("reference_content", "")
                ref_display = field.get("reference_display_col", "id")
                parts.append(f'    <select name="{key}" class="w-full border rounded px-3 py-2"{required}>')
                parts.append(f'      <option value="">Select...</option>')
                parts.append(f'      {{% for item in {ref}_list %}}')
                parts.append(f'      <option value="{{{{ item.id }}}}">{{{{ item.{ref_display} }}}}</option>')
                parts.append(f'      {{% endfor %}}')
                parts.append(f'    </select>')
            elif input_type in ("number", "currency"):
                step = f' step="{field["step"]}"' if field.get("step") else ""
                min_attr = f' min="{field["minimum"]}"' if field.get("minimum") is not None else ""
                parts.append(f'    <input type="number" name="{key}" class="w-full border rounded px-3 py-2"{step}{min_attr}{required}>')
            else:
                parts.append(f'    <input type="text" name="{key}" class="w-full border rounded px-3 py-2"{required}>')

            parts.append(f'  </div>')

        parts.append('  <input type="hidden" name="edit_id" value="">')
        parts.append('  <button type="submit" class="bg-indigo-600 text-white px-6 py-2 rounded hover:bg-indigo-700">Save</button>')
        parts.append('</form>')

    # Aggregations
    for agg in page.get("aggregations", []):
        key = agg["key"]
        desc = agg["description"]
        parts.append(f'<div class="bg-white shadow rounded p-4 mb-4">')
        parts.append(f'  <div class="text-sm text-gray-600">{desc}</div>')
        parts.append(f'  <div class="text-2xl font-bold mt-1">{{{{ {key} }}}}</div>')
        parts.append(f'</div>')

    return jinja_env.from_string("\n".join(parts))
