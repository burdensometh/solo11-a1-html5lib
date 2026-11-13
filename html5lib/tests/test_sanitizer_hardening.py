"""Tests for the hardened, auditable sanitizer behaviour.

Covers the four requirements that motivated the rewrite:

* legitimate modern content (responsive images, full tables, forms, ARIA and
  ``data-*`` attributes) survives sanitization;
* dangerous content (``javascript:``/``vbscript:`` URIs and their obfuscated
  forms, event handlers, unknown elements, unsafe ``srcset`` entries) is kept
  out;
* every removal is recorded in an audit log with a reason;
* disallowed elements are escaped to visible text instead of deleting the
  content wrapped in them.
"""
from __future__ import absolute_import, division, unicode_literals

import json
import warnings

import pytest

from html5lib import parseFragment, serialize
from html5lib.treewalkers import getTreeWalker

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from html5lib.filters import sanitizer


def sanitize_html(stream, removals=None):
    parsed = parseFragment(stream)
    with pytest.deprecated_call():
        serialized = serialize(parsed,
                               sanitize=True,
                               sanitizer_removals=removals,
                               omit_optional_tags=False,
                               use_trailing_solidus=True,
                               space_before_trailing_solidus=False,
                               quote_attr_values="always",
                               quote_char='"',
                               alphabetical_attributes=True)
    return serialized


def run_filter(html, **kwargs):
    """Drive the filter directly and return (output_tokens, removals)."""
    removals = []
    dom = parseFragment(html)
    walker = getTreeWalker("etree")
    with pytest.deprecated_call():
        stream = sanitizer.Filter(walker(dom), removals=removals, **kwargs)
        tokens = list(stream)
    return tokens, removals


# ---------------------------------------------------------------------------
# 1. Legitimate content must survive
# ---------------------------------------------------------------------------

def test_picture_srcset_responsive_image_survives():
    html = ('<picture><source srcset="/a.webp" type="image/webp">'
            '<img src="/a.png" srcset="/a.png 1x, /a@2x.png 2x" '
            'sizes="100vw" alt="figure" loading="lazy"></picture>')
    out = sanitize_html(html)
    assert "<picture>" in out
    assert 'srcset="/a.webp"' in out
    assert 'srcset="/a.png 1x, /a@2x.png 2x"' in out
    assert 'sizes="100vw"' in out
    assert 'loading="lazy"' in out
    assert 'alt="figure"' in out


def test_full_table_survives():
    html = ('<table><caption>Cap</caption><thead><tr>'
            '<th scope="col">A</th><th scope="col">B</th></tr></thead>'
            '<tbody><tr><td data-row="1" rowspan="2">1</td>'
            '<td colspan="2">2</td></tr></tbody></table>')
    out = sanitize_html(html)
    for fragment in ("<table>", "<caption>", "<thead>", "<tbody>",
                     'scope="col"', 'data-row="1"',
                     'rowspan="2"', 'colspan="2"'):
        assert fragment in out, fragment


def test_aria_role_and_data_attributes_survive():
    html = ('<nav role="navigation" aria-label="主导航" data-track-id="42">'
            '<a href="/x" aria-current="page">x</a></nav>')
    out = sanitize_html(html)
    # Attributes are emitted alphabetically; the parser lower-cases names.
    assert 'role="navigation"' in out
    assert 'aria-label="主导航"' in out
    assert 'data-track-id="42"' in out
    assert 'aria-current="page"' in out
    assert 'href="/x"' in out


def test_form_modern_attributes_survive():
    html = ('<form action="/post" method="post" novalidate>'
            '<input type="email" name="e" required placeholder="email" '
            'minlength="3" maxlength="100" autocomplete="email"></form>')
    out = sanitize_html(html)
    for fragment in ('novalidate', 'placeholder="email"',
                     'minlength="3"', 'maxlength="100"',
                     'autocomplete="email"', 'required'):
        assert fragment in out, fragment


def test_text_level_semantics_survive():
    html = ("<main><mark>hi</mark><ruby>漢<rt>kan</rt><rp>(</rp></ruby>"
            "<bdi>isolated</bdi><time datetime='2026-11-13'>today</time></main>")
    out = sanitize_html(html)
    for tag in ("main", "mark", "ruby", "rt", "rp", "bdi", "time"):
        assert "<%s" % tag in out, tag
    assert 'datetime="2026-11-13"' in out


# ---------------------------------------------------------------------------
# 2. Dangerous content must be rejected, including obfuscated variants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("href", [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "&#106;avascript:alert(1)",
    "  javascript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "vbscript:msgbox(1)",
    "data:text/html,<script>alert(1)</script>",
    "data:image/svg+xml,<svg onload=alert(1)>",
])
def test_dangerous_href_schemes_are_stripped(href):
    out = sanitize_html('<a href="%s">x</a>' % href)
    assert "javascript:" not in out
    assert "vbscript:" not in out
    assert "data:" not in out
    assert out == "<a>x</a>"


def test_formaction_uri_is_checked():
    out = sanitize_html('<button formaction="javascript:alert(1)">go</button>')
    assert "javascript:" not in out
    assert "<button>go</button>" == out


def test_srcset_with_one_dangerous_entry_drops_whole_attribute():
    removals = []
    html = ('<img src="/ok.png" '
            'srcset="/ok.png 1x, javascript:alert(1) 2x" alt="x">')
    out = sanitize_html(html, removals=removals)
    assert "javascript:" not in out
    assert "srcset" not in out
    # The safe sibling attribute and the element itself are kept.
    assert 'src="/ok.png"' in out
    assert 'alt="x"' in out
    assert any(r["reason"] == "disallowed_uri_set" and
               r["target"] == "srcset" for r in removals)


def test_srcset_relative_and_data_image_allowed():
    out = sanitize_html(
        '<img srcset="/a.png, /b.png 2x" '
        'alt="x">')
    assert 'srcset="/a.png, /b.png 2x"' in out
    out = sanitize_html(
        '<img srcset="data:image/png;base64,aGVsbG8= 1x" alt="x">')
    assert "srcset" in out


@pytest.mark.parametrize("srcset", [
    # Dangerous URI in any position, with and without descriptors.
    "/a.png, javascript:alert(1)",
    "javascript:alert(1), /a.png",
    "/a.png 1x, javascript:alert(1) 2x",
    "  javascript:alert(1)",
    # data: URIs with non-image media types are not acceptable content.
    "data:text/html,<script>x</script>",
])
def test_srcset_dangerous_entries_are_never_leaked(srcset):
    removals = []
    out = sanitize_html('<img srcset="%s" src="/ok.png" alt="x">' % srcset,
                        removals=removals)
    assert "javascript:" not in out
    assert "text/html" not in out
    assert "srcset" not in out
    assert 'src="/ok.png"' in out
    assert any(r["reason"] == "disallowed_uri_set" for r in removals)


def test_event_handler_attributes_always_stripped_even_with_data_prefix_rule():
    # Prefix whitelisting must never be broad enough to admit on* handlers.
    removals = []
    out = sanitize_html('<p onclick="steal()" data-x="1" class="c">hi</p>',
                        removals=removals)
    assert "onclick" not in out
    assert 'data-x="1"' in out
    assert 'class="c"' in out
    reasons = {(r["reason"], r["target"]) for r in removals}
    assert ("disallowed_attribute", "onclick") in reasons


def test_unknown_element_is_escaped_not_deleted():
    # The element is neutralised but the wrapped text content survives.
    removals = []
    out = sanitize_html("<custom-element>keep me</custom-element>",
                        removals=removals)
    assert "&lt;custom-element&gt;keep me&lt;/custom-element&gt;" == out
    assert "keep me" in out
    assert any(r["reason"] == "disallowed_element" and
               r["target"] == "custom-element" for r in removals)


def test_script_element_is_escaped_and_content_kept_as_text():
    removals = []
    out = sanitize_html("<script>alert('XSS');</script>", removals=removals)
    assert "<script" not in out
    assert "alert('XSS');" in out
    assert any(r["reason"] == "disallowed_element" and
               r["target"] == "script" for r in removals)


def test_comments_are_dropped_and_audited():
    removals = []
    out = sanitize_html("<p>a</p><!-- secret --><p>b</p>", removals=removals)
    assert "secret" not in out
    assert any(r["reason"] == "disallowed_comment" for r in removals)


def test_dangerous_css_is_audited():
    # The CSS gauntlet is fail-closed: a value containing constructs outside
    # the safe character grammar (here, expression()) blanks the whole style
    # attribute rather than trying to surgically remove one declaration.
    removals = []
    out = sanitize_html(
        '<p style="width: expression(alert(1));">x</p>',
        removals=removals)
    assert "expression" not in out
    assert any(r["reason"] == "disallowed_css_declaration" and
               "expression" in r["target"] for r in removals)


def test_unknown_css_property_is_dropped_but_known_one_kept():
    removals = []
    out = sanitize_html('<p style="color: red; z-index: 5;">x</p>',
                        removals=removals)
    assert 'color: red' in out
    assert "z-index" not in out
    assert any(r["reason"] == "disallowed_css_declaration" and
               "z-index" in r["target"] for r in removals)


# ---------------------------------------------------------------------------
# 3. Audit log behaviour
# ---------------------------------------------------------------------------

def test_audit_log_records_every_removal_with_reason():
    removals = []
    sanitize_html(
        '<div onclick="x()"><script>bad()</script>'
        '<a href="javascript:alert(1)">link</a>'
        '<!-- c --></div>',
        removals=removals)
    reasons = {r["reason"] for r in removals}
    assert "disallowed_attribute" in reasons  # onclick
    assert "disallowed_element" in reasons    # script
    assert "disallowed_uri" in reasons        # javascript href
    assert "disallowed_comment" in reasons    # comment
    # Records must be plain JSON-serialisable data.
    json.dumps(removals)
    for record in removals:
        assert "reason" in record and "target" in record


def test_audit_log_is_opt_in_and_defaults_to_none():
    tokens, removals = run_filter('<script>x</script><p onclick="y()">z</p>')
    # Filter constructed without a sink still works and simply has no log.
    dom = parseFragment('<script>x</script>')
    walker = getTreeWalker("etree")
    with pytest.deprecated_call():
        default_filter = sanitizer.Filter(walker(dom))
    assert default_filter.removals is None
    list(default_filter)  # must not raise
    assert tokens  # stream still produced
    assert removals  # explicit sink was populated


def test_audit_value_is_truncated():
    long_value = "javascript:" + ("a" * 500)
    removals = []
    sanitize_html('<a href="%s">x</a>' % long_value, removals=removals)
    record = next(r for r in removals if r["reason"] == "disallowed_uri")
    assert len(record["value"]) <= 120
    assert record["value"].endswith("...")


def test_allowed_content_produces_no_removals():
    removals = []
    sanitize_html(
        '<article><h1>Title</h1><p>Text with <a href="/x">link</a> '
        'and <img src="/i.png" alt="i">.</p></article>',
        removals=removals)
    assert removals == []


# ---------------------------------------------------------------------------
# 4. Custom prefix configuration
# ---------------------------------------------------------------------------

def test_custom_attribute_prefixes_can_be_configured():
    html = '<p ng-if="x" data-x="1">z</p>'
    # Default: ng-if is unknown, data-* is allowed.
    _, removals = run_filter(html)
    assert any(r["target"] == "ng-if" for r in removals)

    _, removals = run_filter(
        html, allowed_attribute_prefixes=frozenset((
            (None, "data-"),
            (None, "ng-"),
        )))
    assert not any(r["target"] == "ng-if" for r in removals)
