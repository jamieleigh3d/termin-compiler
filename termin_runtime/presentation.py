# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

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
        # Emit Jinja call to termin_eval() which is injected into template context
        # This evaluates the CEL expression server-side at render time
        safe_expr = expr.replace('"', '\\"')
        return (f'<div class="text-lg text-gray-800 mb-4" '
                f'data-termin-expr="{safe_expr}">'
                f'{{{{ termin_eval("{safe_expr}")|default("...") }}}}</div>')
    return f'<div class="text-lg text-gray-800 mb-4">{content}</div>'


def _render_data_table(node: dict) -> str:
    props = node.get("props", {})
    cols = props.get("columns", [])
    children = node.get("children", [])

    source = props.get("source", "")
    # v0.8 #6: inline-editable fields (click-to-edit cells).
    inline_editable_fields = set(props.get("inline_editable_fields", []))
    inline_edit_scope = props.get("inline_edit_scope") or ""
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
    # Semantic marks — check for semantic_mark children
    semantic_marks = []
    for child in children:
        if child.get("type") == "semantic_mark":
            props = child.get("props", {})
            cond = props.get("condition", {})
            label = props.get("label", {})
            if isinstance(cond, dict) and cond.get("is_expr"):
                mark_label = label.get("value", "") if isinstance(label, dict) else str(label)
                semantic_marks.append((cond["value"], mark_label))

    # A5: Highlight — check for highlight child with CEL condition
    highlight_expr = None
    # Migrate highlight to semantic_mark with label "highlighted"
    for child in children:
        if child.get("type") == "highlight":
            cond = child.get("props", {}).get("condition", {})
            if isinstance(cond, dict) and cond.get("is_expr"):
                highlight_expr = cond["value"]
                semantic_marks.append((cond["value"], "highlighted"))

    import re

    def _to_jinja_field(expr):
        """Convert CEL row expression to Jinja2."""
        result = []
        parts_split = re.split(r'("[^"]*"|\'[^\']*\')', expr)
        keywords = {'and', 'or', 'not', 'true', 'false', 'none', 'is', 'in', 'item'}
        for i, part in enumerate(parts_split):
            if i % 2 == 1:
                result.append(part)
            else:
                part = re.sub(r'\.([a-zA-Z_]\w*)', r'item.\1', part)
                part = re.sub(
                    r'\b([a-z_][a-zA-Z0-9_]*)\b',
                    lambda m: m.group(0) if m.group(1) in keywords else f'item.{m.group(1)}',
                    part)
                part = part.replace('item.item.', 'item.')
                result.append(part)
        return ''.join(result)

    def _guard_expr(jinja_expr):
        """Add null guards for all item.field references."""
        field_names = re.findall(r'item\.(\w+)', jinja_expr)
        jinja_expr = re.sub(r'item\.(\w+)', r'item.get("\1")', jinja_expr)
        if field_names:
            guards = " and ".join(f'item.get("{f}") is not none' for f in set(field_names))
            return f'{guards} and ({jinja_expr})'
        return jinja_expr

    # Semantic mark label-to-CSS mapping
    MARK_STYLES = {
        "urgent": "bg-red-50 font-semibold",
        "critical": "bg-red-100 font-bold",
        "warning": "bg-amber-50",
        "success": "bg-green-50",
        "highlighted": "bg-red-50 font-semibold",  # legacy highlight compat
    }

    if semantic_marks:
        # Build conditional class and data attributes from all marks
        mark_parts = []
        for expr, label in semantic_marks:
            jinja_expr = _to_jinja_field(expr)
            jinja_expr = jinja_expr.replace("||", " or ").replace("&&", " and ")
            jinja_expr = _guard_expr(jinja_expr)
            css_class = MARK_STYLES.get(label, "")
            mark_parts.append((jinja_expr, css_class, label))

        # Build the row opening with conditional classes and ARIA
        row_classes = "border-t"
        conditions = []
        for jinja_expr, css_class, label in mark_parts:
            if css_class:
                conditions.append(f'{{% if {jinja_expr} %}}{css_class}{{% endif %}}')
        class_str = row_classes + " " + " ".join(conditions) if conditions else row_classes

        # Add data-termin-mark and aria-label for accessibility
        mark_attrs = ""
        for jinja_expr, css_class, label in mark_parts:
            mark_attrs += f' {{% if {jinja_expr} %}}data-termin-mark="{label}" aria-label="{label}"{{% endif %}}'

        parts.append(f'    {{% for item in items %}}<tr class="{class_str}"{mark_attrs} data-termin-row-id="{{{{ item.id }}}}">')
    else:
        parts.append('    {% for item in items %}<tr class="border-t" data-termin-row-id="{{ item.id }}">')
    for col in cols:
        key = col.get("field", "")
        link_tpl = col.get("link_template")
        # v0.8 #6: if this field is marked inline-editable and the caller
        # holds the content's update scope, emit data-termin-inline-editable
        # so the page-level JS click handler wires up editing on this cell.
        # Redacted values skip the marker (Jinja guards the inner content).
        if key in inline_editable_fields and inline_edit_scope:
            inline_attr = (
                f'{{% if "{inline_edit_scope}" in user_scopes %}}'
                f' data-termin-inline-editable'
                f'{{% endif %}}'
            )
        else:
            inline_attr = ""
        # Handle redacted values: show [REDACTED] in gray italic
        if link_tpl:
            # Linked column: wrap value in <a> with interpolated href
            # Convert {field} to Jinja {{ item.field }}
            import re as _re
            jinja_href = _re.sub(r'\{(\w+)\}', r'{{ item.\1 }}', link_tpl)
            parts.append(
                f'      <td class="px-4 py-2 text-sm" data-termin-field="{key}"{inline_attr}>'
                f'{{% if item.{key} is mapping and item.{key}.__redacted %}}'
                f'<span class="text-gray-400 italic">[REDACTED]</span>'
                f'{{% else %}}'
                f'<a href="{jinja_href}" class="text-indigo-600 hover:text-indigo-800 underline">'
                f'{{{{ item.{key}|default("") }}}}</a>'
                f'{{% endif %}}'
                f'</td>'
            )
        else:
            parts.append(
                f'      <td class="px-4 py-2 text-sm" data-termin-field="{key}"{inline_attr}>'
                f'{{% if item.{key} is mapping and item.{key}.__redacted %}}'
                f'<span class="text-gray-400 italic">[REDACTED]</span>'
                f'{{% else %}}'
                f'{{{{ item.{key}|default("") }}}}'
                f'{{% endif %}}'
                f'</td>'
            )

    # Action buttons per row — rendered conditionally based on state + scope
    if row_actions:
        parts.append('      <td class="px-4 py-2 text-sm space-x-1">')
        for action in row_actions:
            ap = action.get("props", {})
            label = ap.get("label", "Action")
            source = props.get("source", "")
            behavior = ap.get("unavailable_behavior", "disable")
            action_kind = ap.get("action", "transition")

            if action_kind == "edit":
                # Edit action: scope-gated, opens a modal dialog
                # pre-populated from the row via fetch(GET /api/v1/…/{id}).
                # Save orchestrates a state-transition request (if status
                # changed) followed by PUT for other fields, so state
                # changes stay on the transition path and do not depend
                # on the v0.8 PUT-backdoor fix landing first.
                required_scope = ap.get("required_scope") or ""
                scope_check = (
                    f'"{required_scope}" in user_scopes'
                    if required_scope else "false"
                )
                js_label = label.replace("'", "\\'").replace('"', '\\"')
                # The onclick calls the page-level opener for this content,
                # which is emitted by _render_edit_modal. It reads this
                # button's data-row-id, fetches current values, populates
                # the form, and shows the modal.
                edit_js = (
                    f"window.terminOpenEditModal_{source}("
                    f"{{{{ item.id|tojson }}}})"
                )
                btn_attrs = (
                    f'data-termin-edit data-content="{source}" '
                    f'data-row-id="{{{{ item.id }}}}" '
                    f'data-behavior="{behavior}" data-label="{js_label}"'
                )
                if behavior == "hide":
                    parts.append(f'        <span {btn_attrs}>')
                    parts.append(f'        {{% if {scope_check} %}}')
                    parts.append(
                        f'        <button type="button" '
                        f'onclick="{edit_js}" '
                        f'class="text-indigo-600 hover:text-indigo-800 text-xs">'
                        f'{label}</button>')
                    parts.append(f'        {{% endif %}}')
                    parts.append(f'        </span>')
                else:
                    parts.append(f'        <span {btn_attrs}>')
                    parts.append(f'        {{% if {scope_check} %}}')
                    parts.append(
                        f'        <button type="button" '
                        f'onclick="{edit_js}" '
                        f'class="text-indigo-600 hover:text-indigo-800 text-xs">'
                        f'{label}</button>')
                    parts.append(f'        {{% else %}}')
                    parts.append(
                        f'        <button disabled '
                        f'class="text-gray-400 text-xs cursor-not-allowed">'
                        f'{label}</button>')
                    parts.append(f'        {{% endif %}}')
                    parts.append(f'        </span>')
                continue

            if action_kind == "delete":
                # Delete action: scope-gated, confirm + fetch(DELETE) to
                # /api/v1/{source}/{id}. No state-machine involvement.
                required_scope = ap.get("required_scope") or ""
                scope_check = (
                    f'"{required_scope}" in user_scopes'
                    if required_scope else "false"
                )
                # Safe attribute-quoted label (avoid breaking markup on
                # labels containing quotes). The label comes from the IR,
                # which is author-controlled, but Jinja autoescape handles
                # runtime values; we escape statically here.
                js_label = label.replace("'", "\\'").replace('"', '\\"')
                confirm_msg = f"Delete this {source.rstrip('s')}?"
                # On failure, read the server's detail message (e.g.
                # "other records reference it") rather than showing
                # just the status code.
                delete_js = (
                    f"if (confirm('{confirm_msg}')) "
                    f"fetch('/api/v1/{source}/' + {{{{ item.id|tojson }}}}, "
                    f"{{method: 'DELETE'}})"
                    f".then(async r => {{ if (r.ok) location.reload(); "
                    f"else {{ const b = await r.json().catch(() => null); "
                    f"alert((b && b.detail) || ('Delete failed: ' + r.status)); }} }});"
                )
                btn_attrs = (
                    f'data-termin-delete data-content="{source}" '
                    f'data-behavior="{behavior}" data-label="{js_label}"'
                )
                if behavior == "hide":
                    parts.append(f'        <span {btn_attrs}>')
                    parts.append(f'        {{% if {scope_check} %}}')
                    parts.append(
                        f'        <button type="button" '
                        f'onclick="{delete_js}" '
                        f'class="text-red-600 hover:text-red-800 text-xs">'
                        f'{label}</button>')
                    parts.append(f'        {{% endif %}}')
                    parts.append(f'        </span>')
                else:
                    parts.append(f'        <span {btn_attrs}>')
                    parts.append(f'        {{% if {scope_check} %}}')
                    parts.append(
                        f'        <button type="button" '
                        f'onclick="{delete_js}" '
                        f'class="text-red-600 hover:text-red-800 text-xs">'
                        f'{label}</button>')
                    parts.append(f'        {{% else %}}')
                    parts.append(
                        f'        <button disabled '
                        f'class="text-gray-400 text-xs cursor-not-allowed">'
                        f'{label}</button>')
                    parts.append(f'        {{% endif %}}')
                    parts.append(f'        </span>')
                continue

            # Transition action (existing behavior).
            target = ap.get("target_state", "")
            safe_target = target.replace(" ", "_")
            # Build Jinja conditions:
            # 1. Is (current_status, target_state) a valid transition?
            # 2. Does the user hold the required scope for this transition?
            # _sm_transitions is a dict of {(from,to): scope} injected into context
            valid_check = f'(item.get("status",""), "{target}") in _sm_transitions'
            scope_check = f'_sm_transitions.get((item.get("status",""), "{target}"), "") in user_scopes or _sm_transitions.get((item.get("status",""), "{target}"), "") == ""'

            # Wrap each button in a span with transition metadata for client-side re-evaluation
            btn_attrs = f'data-termin-transition data-target-state="{target}" data-behavior="{behavior}" data-label="{label}"'
            if behavior == "hide":
                # Hide: don't render the button at all when transition unavailable
                parts.append(f'        <span {btn_attrs}>')
                parts.append(f'        {{% if {valid_check} and ({scope_check}) %}}')
                parts.append(
                    f'        <form method="post" action="/_transition/{source}/{{{{ item.id }}}}/{safe_target}" '
                    f'style="display:inline">'
                    f'<button type="submit" class="text-indigo-600 hover:text-indigo-800 text-xs">{label}</button></form>')
                parts.append(f'        {{% endif %}}')
                parts.append(f'        </span>')
            else:
                # Disable (default): render grayed-out button when unavailable
                parts.append(f'        <span {btn_attrs}>')
                parts.append(f'        {{% if {valid_check} and ({scope_check}) %}}')
                parts.append(
                    f'        <form method="post" action="/_transition/{source}/{{{{ item.id }}}}/{safe_target}" '
                    f'style="display:inline">'
                    f'<button type="submit" class="text-indigo-600 hover:text-indigo-800 text-xs">{label}</button></form>')
                parts.append(f'        {{% else %}}')
                parts.append(
                    f'        <button disabled class="text-gray-400 text-xs cursor-not-allowed">{label}</button>')
                parts.append(f'        {{% endif %}}')
                parts.append(f'        </span>')
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
    # v0.8 #6: inline-edit click handler — attached once per table when
    # there is at least one inline-editable field. Clicks on a cell that
    # carries data-termin-inline-editable replace its text content with
    # an <input>, blur/Enter saves via PUT, Escape or blur-without-change
    # reverts. On failure the server's detail is surfaced via alert and
    # the cell reverts to its prior value.
    if inline_editable_fields:
        result += f'''
<script>
(function() {{
  const TABLE = document.querySelector(
    'table[data-termin-component="data_table"][data-termin-source="{source}"]');
  if (!TABLE) return;
  TABLE.addEventListener("click", function(e) {{
    const cell = e.target.closest("[data-termin-inline-editable]");
    if (!cell) return;
    if (cell.querySelector("input")) return; // already editing
    const row = cell.closest("tr[data-termin-row-id]");
    if (!row) return;
    const rowId = row.dataset.terminRowId;
    const field = cell.dataset.terminField;
    const original = cell.textContent;
    const input = document.createElement("input");
    input.type = "text";
    input.value = original;
    input.setAttribute("data-termin-inline-input", "");
    input.setAttribute("data-termin-field", field);
    input.className = "w-full border rounded px-2 py-1 text-sm";
    cell.textContent = "";
    cell.appendChild(input);
    input.focus();
    input.select();
    let committed = false;
    async function commit() {{
      if (committed) return;
      committed = true;
      const newVal = input.value;
      if (newVal === original) {{ cell.textContent = original; return; }}
      try {{
        const res = await fetch('/api/v1/{source}/' + rowId, {{
          method: "PUT",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{[field]: newVal}}),
        }});
        if (!res.ok) {{
          const err = await res.json().catch(() => null);
          throw new Error((err && err.detail) || ("Update failed: " + res.status));
        }}
        cell.textContent = newVal;
      }} catch (err) {{
        cell.textContent = original;
        alert(err.message);
      }}
    }}
    function revert() {{
      if (committed) return;
      committed = true;
      cell.textContent = original;
    }}
    input.addEventListener("blur", commit);
    input.addEventListener("keydown", function(ev) {{
      if (ev.key === "Enter") {{ ev.preventDefault(); commit(); }}
      else if (ev.key === "Escape") {{ ev.preventDefault(); revert(); input.blur(); }}
    }});
  }});
}})();
</script>'''
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
    # data-termin-field attribute on every input so behavioral tests can
    # select form inputs via DOM without relying on English labels.
    termin_attr = f' data-termin-field="{key}"'

    parts = [f'  <div class="mb-4">']
    parts.append(f'    <label class="block text-sm font-medium text-gray-700 mb-1">{label}</label>')

    if input_type == "enum":
        parts.append(f'    <select name="{key}"{termin_attr} class="w-full border rounded px-3 py-2"{required}>')
        parts.append(f'      <option value="">Select...</option>')
        for val in props.get("enum_values", []):
            parts.append(f'      <option value="{val}">{val}</option>')
        parts.append(f'    </select>')
    elif input_type == "state":
        # State-machine field. Render a select with ALL states from the
        # state machine (embedded in props at lowering time). JS filters
        # the visible options at modal-open time based on the row's
        # current state (valid transition targets) and the user's scopes.
        # The current state is always included so the user can save
        # without changing state.
        parts.append(
            f'    <select name="{key}"{termin_attr} '
            f'class="w-full border rounded px-3 py-2"{required}>')
        for state_name in props.get("all_states", []):
            parts.append(f'      <option value="{state_name}">{state_name}</option>')
        parts.append(f'    </select>')
    elif input_type == "reference":
        ref = props.get("reference_content", "")
        ref_display = props.get("reference_display_col", "id")
        parts.append(f'    <select name="{key}"{termin_attr} class="w-full border rounded px-3 py-2"{required}>')
        parts.append(f'      <option value="">Select...</option>')
        parts.append(f'      {{% for item in {ref}_list %}}')
        parts.append(f'      <option value="{{{{ item.id }}}}">{{{{ item.{ref_display} }}}}</option>')
        parts.append(f'      {{% endfor %}}')
        parts.append(f'    </select>')
    elif input_type in ("number", "currency", "whole_number"):
        step = f' step="{props["step"]}"' if props.get("step") else ""
        min_attr = f' min="{props["minimum"]}"' if props.get("minimum") is not None else ""
        parts.append(f'    <input type="number" name="{key}"{termin_attr} class="w-full border rounded px-3 py-2"{step}{min_attr}{required}>')
    else:
        parts.append(f'    <input type="text" name="{key}"{termin_attr} class="w-full border rounded px-3 py-2"{required}{unique_attr}>')

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


def _render_chat(node: dict) -> str:
    """Render a chat component — scrolling message list with input area."""
    props = node.get("props", {})
    source = props.get("source", "")
    role_field = props.get("role_field", "role")
    content_field = props.get("content_field", "content")
    children = node.get("children", [])

    # Check for subscribe child
    subscribe_attr = ""
    for child in children:
        if child.get("type") == "subscribe":
            sub_content = child.get("props", {}).get("content", source)
            subscribe_attr = f' data-termin-subscribe="{sub_content}"'

    parts = [
        f'<div class="flex flex-col h-[600px] bg-white shadow rounded overflow-hidden"'
        f' data-termin-chat data-termin-source="{source}"{subscribe_attr}>',
        f'  <div class="flex-1 overflow-y-auto p-4 space-y-3" data-termin-chat-messages>',
        f'    {{% for item in items %}}',
        f'    <div class="flex {{% if item.{role_field} == "user" %}}justify-end{{% else %}}justify-start{{% endif %}}"'
        f' data-termin-chat-message data-termin-role="{{{{ item.{role_field} }}}}">',
        f'      <div class="{{% if item.{role_field} == "user" %}}bg-blue-500 text-white{{% else %}}bg-gray-200 text-gray-800{{% endif %}}'
        f' rounded-lg px-4 py-2 max-w-[70%]">',
        f'        <div class="text-xs opacity-70 mb-1">{{{{ item.{role_field} }}}}</div>',
        f'        <div>{{{{ item.{content_field}|default("") }}}}</div>',
        f'      </div>',
        f'    </div>',
        f'    {{% endfor %}}',
        f'  </div>',
        f'  <div class="border-t p-3" data-termin-chat-input>',
        f'    <form method="post" action="/api/v1/{source}" class="flex space-x-2" data-termin-chat-form>',
        f'      <input type="text" name="{content_field}" placeholder="Type a message..."'
        f' class="flex-1 border rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"'
        f' autocomplete="off">',
        f'      <button type="submit"'
        f' class="bg-blue-500 text-white px-6 py-2 rounded-lg hover:bg-blue-600 transition-colors">Send</button>',
        f'    </form>',
        f'  </div>',
        f'</div>',
    ]
    return '\n'.join(parts)


def _render_action_button(node: dict) -> str:
    props = node.get("props", {})
    label = props.get("label", "Action")
    return f'<button class="bg-indigo-600 text-white px-4 py-2 rounded text-sm hover:bg-indigo-700">{label}</button>'


def _render_edit_modal(node: dict, content_schemas: dict = None) -> str:
    """Render the edit_modal ComponentNode as an HTML5 <dialog> with a
    form containing the content's editable fields, plus the JS opener
    and submit orchestrator.

    The opener is attached to window as terminOpenEditModal_{content}
    so each row's Edit button onclick can call it with the row id.
    On Save, the form fires a state-transition POST for status changes
    (if status changed), then a PUT for the other fields. This keeps
    state changes on the already-scoped transition path and is
    independent of the v0.8 PUT-backdoor fix.
    """
    props = node.get("props", {})
    content = props.get("content", "")
    singular = props.get("singular", content[:-1] if content.endswith("s") else content)
    modal_id = f"termin-edit-modal-{content}"

    # Check if the form includes the state field — controls how the
    # submit handler splits the body between transition and PUT.
    has_state_field = any(
        (child.get("props", {}) or {}).get("input_type") == "state"
        for child in node.get("children", [])
    )

    # user_scopes + per-content transitions are embedded as data
    # attributes so the JS is self-contained and doesn't need window
    # globals. They're filled in by Jinja at render time.
    user_scopes_attr = "{{ (user_scopes|list)|tojson }}"
    transitions_attr = (
        "{{ (_sm_transitions_by_content.get('" + content +
        "', []))|tojson }}"
    )
    parts = [
        f'<dialog id="{modal_id}" data-termin-edit-modal data-content="{content}" '
        f"data-user-scopes='{user_scopes_attr}' "
        f"data-sm-transitions='{transitions_attr}' "
        f'class="rounded-lg shadow-xl p-0 bg-white" style="min-width:28rem;max-width:36rem;">',
        f'  <form data-content="{content}" class="p-6">',
        f'    <h2 class="text-lg font-semibold mb-4">Edit {singular}</h2>',
    ]
    # Render each field_input child using the existing renderer.
    for child in node.get("children", []):
        if child.get("type") == "field_input":
            parts.append(_render_field_input(child, content_schemas))
    parts.extend([
        f'    <div class="flex justify-end gap-2 mt-4">',
        f'      <button type="button" data-termin-action="cancel" '
        f'onclick="this.closest(\'dialog\').close()" '
        f'class="px-4 py-2 rounded border border-gray-300 text-gray-700 hover:bg-gray-50">Cancel</button>',
        f'      <button type="submit" data-termin-action="save" '
        f'class="px-4 py-2 rounded bg-indigo-600 text-white hover:bg-indigo-700">Save</button>',
        f'    </div>',
        f'  </form>',
        f'</dialog>',
    ])

    # Page-level script: opener function, submit handler, state filtering.
    # Wrapped in an IIFE so we don't leak locals. Exposes
    # terminOpenEditModal_{content} as the entry point the per-row
    # Edit button onclick calls.
    state_js = "true" if has_state_field else "false"
    script = f'''
<script>
(function() {{
  const MODAL_ID = "{modal_id}";
  const CONTENT = "{content}";
  const HAS_STATE_FIELD = {state_js};

  function getModal() {{ return document.getElementById(MODAL_ID); }}

  async function openEdit(rowId) {{
    const modal = getModal();
    if (!modal) return;
    const form = modal.querySelector("form");
    // Fetch the row's current values.
    let row;
    try {{
      const res = await fetch(`/api/v1/${{CONTENT}}/${{rowId}}`);
      if (!res.ok) throw new Error("Failed to load record: " + res.status);
      row = await res.json();
    }} catch (err) {{ alert(err.message); return; }}
    form.dataset.rowId = rowId;
    form.dataset.origStatus = row.status || "";
    // Populate inputs by matching data-termin-field.
    form.querySelectorAll("[data-termin-field]").forEach(input => {{
      const k = input.dataset.terminField;
      if (k in row) input.value = row[k] == null ? "" : row[k];
    }});
    // Filter state dropdown options if present — only valid transitions
    // from the current state that the user has scope for (plus the
    // current state itself, always, so the user can save without
    // changing state).
    if (HAS_STATE_FIELD) {{
      const sel = form.querySelector('[data-termin-field="status"]');
      if (sel) {{
        const cur = row.status;
        // Read user scopes + transitions from data attributes on this
        // dialog (Jinja-rendered). Self-contained; no window globals.
        let userScopes = [];
        let transitions = [];
        try {{ userScopes = JSON.parse(modal.dataset.userScopes || "[]"); }} catch (e) {{}}
        try {{ transitions = JSON.parse(modal.dataset.smTransitions || "[]"); }} catch (e) {{}}
        const validTargets = new Set([cur]);
        for (const t of transitions) {{
          if (t.from === cur && (!t.scope || userScopes.indexOf(t.scope) !== -1)) {{
            validTargets.add(t.to);
          }}
        }}
        Array.from(sel.options).forEach(opt => {{
          const ok = validTargets.has(opt.value);
          opt.disabled = !ok;
          opt.hidden = !ok;
        }});
        sel.value = cur;
      }}
    }}
    if (typeof modal.showModal === "function") modal.showModal();
    else modal.setAttribute("open", "");
  }}

  // Expose the opener under a stable name the per-row Edit button onclick calls.
  window["terminOpenEditModal_" + CONTENT] = openEdit;

  // Submit handler: orchestrate state transition (if status changed)
  // followed by PUT for other fields.
  document.addEventListener("DOMContentLoaded", function init() {{
    const modal = getModal();
    if (!modal) return;
    const form = modal.querySelector("form");
    form.addEventListener("submit", async (e) => {{
      e.preventDefault();
      const rowId = form.dataset.rowId;
      if (!rowId) return;
      const fd = new FormData(form);
      const body = {{}};
      fd.forEach((v, k) => {{ body[k] = v; }});
      const origStatus = form.dataset.origStatus || "";
      const newStatus = body.status || "";
      // Always strip status from the PUT body — state changes route
      // through /_transition/ to respect transition rules + scopes.
      delete body.status;
      try {{
        if (HAS_STATE_FIELD && newStatus && newStatus !== origStatus) {{
          const stateRes = await fetch(
            `/_transition/${{CONTENT}}/${{rowId}}/${{encodeURIComponent(newStatus)}}`,
            {{method: "POST"}});
          if (!stateRes.ok) {{
            const err = await stateRes.json().catch(() => null);
            throw new Error((err && err.detail) || ("State change failed: " + stateRes.status));
          }}
        }}
        if (Object.keys(body).length > 0) {{
          const putRes = await fetch(`/api/v1/${{CONTENT}}/${{rowId}}`, {{
            method: "PUT",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify(body),
          }});
          if (!putRes.ok) {{
            const err = await putRes.json().catch(() => null);
            throw new Error((err && err.detail) || ("Save failed: " + putRes.status));
          }}
        }}
        modal.close();
        location.reload();
      }} catch (err) {{ alert(err.message); }}
    }});
  }});
}})();
</script>
'''
    return '\n'.join(parts) + script


def _render_unknown(node: dict) -> str:
    comp_type = node.get("type", "unknown")
    return f'<div class="text-gray-400 text-sm">[{comp_type} component]</div>'


# ── Renderer dispatch ──

RENDERERS = {
    "text": _render_text,
    "data_table": _render_data_table,
    "chat": _render_chat,
    "form": _render_form,
    "field_input": _render_field_input,
    "aggregation": _render_aggregation,
    "stat_breakdown": _render_stat_breakdown,
    "chart": _render_chart,
    "section": _render_section,
    "action_button": _render_action_button,
    "edit_modal": _render_edit_modal,
    # Sub-components rendered inline by their parent:
    # "filter", "search", "highlight", "subscribe", "related"
}


def render_component(node: dict) -> str:
    """Render a single component node to a Jinja2 template fragment."""
    renderer = RENDERERS.get(node.get("type", ""), _render_unknown)
    return renderer(node)


# ── Page template builders ──

def build_page_template(page: dict) -> object:
    """Build a Jinja2 template for a page from its component tree.

    Text component expressions emit {{ termin_eval("expr") }} calls
    that are evaluated server-side at render time via the template context.
    """
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

def _js_version_hash():
    """Hash the termin.js file for cache busting."""
    import hashlib
    from pathlib import Path
    js_path = Path(__file__).parent / "static" / "termin.js"
    if js_path.exists():
        return hashlib.md5(js_path.read_bytes()).hexdigest()[:8]
    return "0"

def _css_version_hash():
    """Hash the termin.css file for cache busting."""
    import hashlib
    from pathlib import Path
    css_path = Path(__file__).parent / "static" / "termin.css"
    if css_path.exists():
        return hashlib.md5(css_path.read_bytes()).hexdigest()[:8]
    return "0"

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
    <!-- Termin runtime stylesheet: loads after Tailwind Play CDN so its
         tokens and overrides win on the cascade. -->
    <link rel="stylesheet" href="/runtime/termin.css?v={_css_version_hash()}">
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
        {{% if flash_msg %}}
        {{% if flash_style == "banner" %}}
        <div data-termin-banner role="alert" data-level="{{{{ flash_level }}}}"
             class="mb-4 p-4 rounded-lg border {{% if flash_level == 'error' %}}bg-red-50 border-red-200 text-red-800{{% else %}}bg-green-50 border-green-200 text-green-800{{% endif %}}"
             {{% if flash_dismiss %}}data-dismiss="{{{{ flash_dismiss }}}}" {{% endif %}}>
            <div class="flex items-center justify-between">
                <span>{{{{ flash_msg }}}}</span>
                <button onclick="this.parentElement.parentElement.remove()" class="ml-4 text-lg font-bold opacity-50 hover:opacity-100">&times;</button>
            </div>
        </div>
        {{% else %}}
        <div data-termin-toast role="status" data-level="{{{{ flash_level }}}}"
             class="fixed bottom-4 right-4 z-50 p-4 rounded-lg shadow-lg {{% if flash_level == 'error' %}}bg-red-600 text-white{{% else %}}bg-green-600 text-white{{% endif %}}"
             data-dismiss="{{{{ flash_dismiss or 5 }}}}">
            {{{{ flash_msg }}}}
        </div>
        {{% endif %}}
        {{% endif %}}
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
    <script>
    // Auto-dismiss toast/banner notifications
    document.querySelectorAll("[data-termin-toast], [data-termin-banner]").forEach(function(el) {{
        var dismiss = parseInt(el.dataset.dismiss);
        if (dismiss > 0) {{
            setTimeout(function() {{ el.style.transition = "opacity 0.3s"; el.style.opacity = "0"; setTimeout(function() {{ el.remove(); }}, 300); }}, dismiss * 1000);
        }}
    }});
    // Clean _flash params from URL to prevent re-showing on refresh
    if (window.location.search.includes("_flash")) {{
        var url = new URL(window.location);
        url.searchParams.delete("_flash");
        url.searchParams.delete("_flash_style");
        url.searchParams.delete("_flash_level");
        url.searchParams.delete("_flash_dismiss");
        window.history.replaceState({{}}, "", url);
    }}
    </script>
    <script type="module" src="/runtime/termin.js?v={_js_version_hash()}"></script>
</body>
</html>'''
    return jinja_env.from_string(template)
