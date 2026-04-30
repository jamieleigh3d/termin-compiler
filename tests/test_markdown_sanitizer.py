# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the v0.9 markdown sanitizer (Step Zero).

Per BRD #2 §7.3 + JL's morning resolutions (2026-04-27 Q3, Q4):

  Allowed: bold, italic, strike-through, links (URL allowlist),
           headers, horizontal rules, ordered + unordered lists.
  Stripped: raw HTML, scripts, images, code blocks, code spans,
            tables, blockquotes, underline.
"""

from __future__ import annotations

import pytest

from termin_server.markdown_sanitizer import (
    sanitize_markdown,
    _is_safe_url,
)


# ── Allowed: emphasis ──

def test_bold_renders_as_strong():
    assert "<strong>" in sanitize_markdown("**bold text**")


def test_italic_renders_as_em():
    assert "<em>" in sanitize_markdown("*italic text*")


def test_strikethrough_renders_as_s():
    assert "<s>" in sanitize_markdown("~~struck out~~")


def test_bold_and_italic_combined():
    out = sanitize_markdown("***both***")
    # markdown-it may render this as nested <em><strong> or
    # <strong><em>. Either is fine.
    assert "<em>" in out or "<strong>" in out


# ── Allowed: headers ──

@pytest.mark.parametrize("level,prefix", [
    (1, "#"), (2, "##"), (3, "###"),
    (4, "####"), (5, "#####"), (6, "######"),
])
def test_headers_render(level, prefix):
    out = sanitize_markdown(f"{prefix} Heading {level}")
    assert f"<h{level}>" in out


# ── Allowed: horizontal rules ──

def test_horizontal_rule_renders_as_hr():
    assert "<hr" in sanitize_markdown("---")


# ── Allowed: lists (Q3 resolution) ──

def test_unordered_list_with_dash():
    out = sanitize_markdown("- item 1\n- item 2\n- item 3")
    assert "<ul>" in out
    assert out.count("<li>") == 3


def test_unordered_list_with_asterisk():
    out = sanitize_markdown("* a\n* b")
    assert "<ul>" in out
    assert out.count("<li>") == 2


def test_unordered_list_with_plus():
    out = sanitize_markdown("+ a\n+ b")
    assert "<ul>" in out


def test_ordered_list():
    out = sanitize_markdown("1. first\n2. second\n3. third")
    assert "<ol>" in out
    assert out.count("<li>") == 3


# ── Allowed: links (with URL allowlist) ──

def test_safe_http_link_renders():
    out = sanitize_markdown("[example](http://example.com)")
    assert '<a href="http://example.com">' in out
    assert "example</a>" in out


def test_safe_https_link_renders():
    out = sanitize_markdown("[example](https://example.com)")
    assert '<a href="https://example.com">' in out


def test_safe_mailto_link_renders():
    out = sanitize_markdown("[contact](mailto:foo@bar.com)")
    assert 'href="mailto:foo@bar.com"' in out


def test_relative_link_renders():
    """Relative URLs (no scheme) are safe — can't escape the app."""
    out = sanitize_markdown("[admin](/admin)")
    assert 'href="/admin"' in out


def test_fragment_link_renders():
    out = sanitize_markdown("[anchor](#section-1)")
    assert 'href="#section-1"' in out


# ── Stripped: unsafe URLs ──

def test_javascript_url_stripped():
    out = sanitize_markdown("[click](javascript:alert(1))")
    # The crucial guarantee: no <a> tag with an executable href.
    # markdown-it rejects javascript: URLs at parse time and the
    # link falls back to text — that's safe; there's nothing
    # clickable. The literal text "javascript:" may appear in the
    # text content, which is benign.
    assert 'href="javascript:' not in out.lower()
    assert "<a " not in out
    assert "click" in out  # visible content preserved


def test_data_url_stripped():
    out = sanitize_markdown("[click](data:text/html,<script>alert(1)</script>)")
    assert "<a " not in out
    assert "<script>" not in out
    assert 'href="data:' not in out.lower()


def test_file_url_stripped():
    out = sanitize_markdown("[bad](file:///etc/passwd)")
    assert "<a " not in out
    assert 'href="file:' not in out.lower()


def test_vbscript_url_stripped():
    out = sanitize_markdown("[bad](vbscript:msgbox('x'))")
    assert "<a " not in out
    assert 'href="vbscript:' not in out.lower()


# ── Stripped: code ──

def test_fenced_code_block_disabled():
    out = sanitize_markdown("```python\nprint('hi')\n```")
    # Disabled rule means the fence is treated as text/paragraph,
    # not as a <pre><code>. We accept either: no <code>, or it
    # rendered as paragraph text. The crucial guarantee is no
    # <code> + <pre> wrapper that would let raw output through.
    assert "<code" not in out
    assert "<pre" not in out


def test_indented_code_block_disabled():
    out = sanitize_markdown("    print('hi')\n    print('bye')")
    assert "<code" not in out
    assert "<pre" not in out


def test_inline_code_disabled():
    out = sanitize_markdown("Some `inline code` here")
    assert "<code" not in out


# ── Stripped: HTML ──

def test_raw_html_tags_escaped():
    out = sanitize_markdown("<script>alert(1)</script>")
    # html=False means raw HTML tags are escaped to their text form.
    assert "<script>" not in out
    assert "&lt;script&gt;" in out or "alert" in out


def test_inline_html_escaped():
    out = sanitize_markdown("Click <a href='evil.com'>here</a>")
    assert "<a href='evil.com'" not in out


def test_html_comment_escaped():
    out = sanitize_markdown("<!-- comment -->")
    assert "<!--" not in out


# ── Stripped: images ──

def test_images_disabled():
    out = sanitize_markdown("![alt](http://example.com/img.png)")
    assert "<img" not in out


# ── Stripped: tables ──

def test_tables_disabled():
    """Tables are not in commonmark by default and we don't enable
    them; rendered as paragraph text."""
    src = "| a | b |\n|---|---|\n| 1 | 2 |"
    out = sanitize_markdown(src)
    assert "<table" not in out


# ── Stripped: blockquotes ──

def test_blockquote_disabled():
    out = sanitize_markdown("> a quote")
    assert "<blockquote>" not in out


# ── Stripped: underline (Q4 resolution) ──

def test_underline_double_underscore_renders_as_emphasis():
    """`__text__` is markdown's bold (commonmark spec). We keep that
    behavior — underline is NOT a markdown-standard syntax. Apps
    wanting underline can use a future provider extension."""
    out = sanitize_markdown("__text__")
    # `__bold__` per markdown spec → <strong>, not <u>.
    assert "<u>" not in out
    assert "<strong>" in out


# ── _is_safe_url unit tests ──

@pytest.mark.parametrize("url", [
    "http://example.com",
    "https://example.com/path?q=1",
    "HTTPS://EXAMPLE.COM",  # case-insensitive
    "mailto:foo@bar.com",
    "MAILTO:foo@bar.com",
    "/relative/path",
    "page.html",
    "./local",
    "#fragment",
    "?query=only",
])
def test_is_safe_url_accepts(url):
    assert _is_safe_url(url), f"expected safe: {url!r}"


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "JAVASCRIPT:alert(1)",
    "data:text/html,foo",
    "file:///etc/passwd",
    "vbscript:msgbox('x')",
    "ftp://example.com",   # not on allowlist
    "ssh://user@host",     # not on allowlist
])
def test_is_safe_url_rejects(url):
    assert not _is_safe_url(url), f"expected unsafe: {url!r}"


def test_is_safe_url_handles_empty_and_none():
    assert not _is_safe_url("")
    assert not _is_safe_url("   ")
    assert not _is_safe_url(None)


# ── End-to-end / mixed input ──

def test_mixed_safe_input_renders_cleanly():
    src = (
        "# Header\n\n"
        "Text with **bold** and *italic* and a [link](https://example.com).\n\n"
        "- item 1\n"
        "- item 2\n\n"
        "1. ordered\n"
        "2. list\n\n"
        "---\n"
    )
    out = sanitize_markdown(src)
    assert "<h1>" in out
    assert "<strong>" in out
    assert "<em>" in out
    assert "<a " in out
    assert "<ul>" in out
    assert "<ol>" in out
    assert "<hr" in out


def test_mixed_unsafe_input_strips_unsafe():
    src = (
        "## Title\n\n"
        "**bold** and <script>alert(1)</script>\n\n"
        "[good](https://ok.com) and [bad](javascript:1)\n"
    )
    out = sanitize_markdown(src)
    assert "<h2>" in out
    assert "<strong>" in out
    assert "<script>" not in out
    # The good link survived; the bad link is stripped (no <a> for it).
    assert 'href="https://ok.com"' in out
    assert 'href="javascript:' not in out.lower()


def test_empty_input_returns_empty_string():
    assert sanitize_markdown("") == ""
    assert sanitize_markdown(None) == ""


def test_non_string_input_coerced():
    out = sanitize_markdown(42)
    assert "42" in out
