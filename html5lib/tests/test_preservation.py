from __future__ import absolute_import, division, unicode_literals

import pytest

from html5lib import parse, serialize
from html5lib.serializer import HTMLSerializer, escape_text, escape_attribute
from html5lib.treewalkers.base import TreeWalker


class StaticWalker(TreeWalker):
    """TreeWalker fed directly with tokens, used to exercise the serializer
    independently of the parser/tree representation."""

    def __iter__(self):
        for token in self.tree:
            yield token


def start_tag(name, attrs):
    return {"type": "StartTag", "name": name,
            "namespace": "http://www.w3.org/1999/xhtml",
            "data": {(None, key): value for key, value in attrs}}


def end_tag(name):
    return {"type": "EndTag", "name": name,
            "namespace": "http://www.w3.org/1999/xhtml"}


def characters(data):
    return {"type": "Characters", "data": data}


# ---------------------------------------------------------------------------
# Attribute order
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    "<div z='1' a='2' m='3'></div>",
    "<span beta='b' alpha='a' gamma='g' delta='d'></span>",
    "<input type='text' name='x' id='y' value='z'>",
])
def test_attributes_serialized_in_input_order(source):
    document = parse(source)
    result = serialize(document, omit_optional_tags=False,
                       quote_attr_values="always")
    # Everything between the tag name and the closing angle bracket is the
    # attribute section; double quotes are the default quoting character.
    inner = source[source.index(" ") + 1:source.rindex(">")]
    inner = inner.rstrip("/").strip()
    expected_attrs = inner.replace("'", '"')
    assert expected_attrs in result


def test_attribute_order_round_trip_etree():
    source = "<div z='1' a='2' m='3'></div>"
    first = serialize(parse(source), omit_optional_tags=False,
                      quote_attr_values="always")
    second = serialize(parse(first), omit_optional_tags=False,
                       quote_attr_values="always")
    assert first == second


# ---------------------------------------------------------------------------
# Self-closing / void elements
# ---------------------------------------------------------------------------

def test_void_elements_keep_trailing_solidus():
    document = parse("<hr/><br/><img src='x.png'/>")
    result = serialize(document, omit_optional_tags=False,
                       use_trailing_solidus=True,
                       space_before_trailing_solidus=False)
    assert "<hr/>" in result
    assert "<br/>" in result
    assert "<img src=x.png/>" in result


def test_void_elements_space_before_trailing_solidus():
    document = parse("<hr/>")
    result = serialize(document, omit_optional_tags=False,
                       use_trailing_solidus=True)
    assert "<hr />" in result


def test_foreign_empty_elements_keep_self_closing():
    document = parse("<svg><path d='M0 0'/></svg><math><mi/></math>")
    result = serialize(document, omit_optional_tags=False,
                       use_trailing_solidus=True,
                       space_before_trailing_solidus=False)
    assert '<path d="M0 0"/>' in result
    assert "<mi/>" in result


def test_foreign_non_empty_elements_are_not_collapsed():
    document = parse("<svg><g><path/></g></svg>")
    result = serialize(document, omit_optional_tags=False,
                       use_trailing_solidus=True,
                       space_before_trailing_solidus=False)
    assert "<g>" in result
    assert "</g>" in result
    assert "<path/>" in result


def test_multiple_foreign_empty_elements():
    document = parse("<svg><g/><path/><circle/></svg>")
    result = serialize(document, omit_optional_tags=False,
                       use_trailing_solidus=True,
                       space_before_trailing_solidus=False)
    assert "<g/><path/><circle/>" in result
    document = parse("<svg><path/>text<path/></svg>")
    result = serialize(document, omit_optional_tags=False,
                       use_trailing_solidus=True,
                       space_before_trailing_solidus=False)
    assert result.count("<path/>") == 2


def test_foreign_empty_tag_token_gets_solidus():
    svg_ns = "http://www.w3.org/2000/svg"

    class Walker(TreeWalker):
        def __iter__(self):
            for token in self.emptyTag(svg_ns, "path",
                                       {(None, "d"): "M0 0"}):
                yield token

    result = HTMLSerializer(use_trailing_solidus=True,
                            space_before_trailing_solidus=False).render(
        Walker(None))
    assert result == '<path d="M0 0"/>'
    # The option is still opt-in.
    assert HTMLSerializer().render(Walker(None)) == '<path d="M0 0">'


def test_html_empty_elements_are_not_collapsed():
    # In HTML a trailing solidus on a non-void element does not close it,
    # so an empty HTML element must keep its explicit end tag.
    document = parse("<div></div>")
    result = serialize(document, omit_optional_tags=False,
                       use_trailing_solidus=True)
    assert "<div></div>" in result


def test_self_closing_disabled_by_default():
    document = parse("<hr/><svg><path/></svg>")
    result = serialize(document, omit_optional_tags=False)
    assert "<hr>" in result
    assert "<path></path>" in result


# ---------------------------------------------------------------------------
# Entity preservation (no double escaping)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("data, expected", [
    ("a &amp; b", "a &amp; b"),
    ("&lt;tag&gt;", "&lt;tag&gt;"),
    ("&nbsp; &#x2014; &#8212;", "&nbsp; &#x2014; &#8212;"),
    ("lone & ampersand", "lone &amp; ampersand"),
    ("a & b &amp; c", "a &amp; b &amp; c"),
    ("unterminated &amp", "unterminated &amp;amp"),
])
def test_escape_text_preserve_entities(data, expected):
    assert escape_text(data, preserve_entities=True) == expected


def test_escape_text_default_escapes_every_ampersand():
    assert escape_text("a &amp; b") == "a &amp;amp; b"
    assert escape_text("a & b") == "a &amp; b"
    assert escape_text("<tag>") == "&lt;tag&gt;"


@pytest.mark.parametrize("data, expected", [
    ("Tom &amp; Jerry", "Tom &amp; Jerry"),
    ("a & b", "a &amp; b"),
    ("a &lt; b", "a &lt; b"),
])
def test_escape_attribute_preserve_entities(data, expected):
    assert escape_attribute(data, preserve_entities=True) == expected


def test_escape_attribute_escape_lt():
    assert escape_attribute("a < b &amp; c", preserve_entities=True,
                            escape_lt=True) == "a &lt; b &amp; c"


def test_serializer_does_not_double_escape_text():
    tokens = [start_tag("p", []),
              characters("Tom &amp; Jerry &lt;x&gt; &#x2014; & lone"),
              end_tag("p")]
    result = HTMLSerializer(preserve_entities=True,
                            omit_optional_tags=False).render(
        StaticWalker(tokens))
    assert result == "<p>Tom &amp; Jerry &lt;x&gt; &#x2014; &amp; lone</p>"


def test_serializer_does_not_double_escape_attributes():
    tokens = [start_tag("a", [("href", "x?a=1&amp;b=2"),
                              ("title", "Tom &amp; Jerry")])]
    result = HTMLSerializer(preserve_entities=True,
                            quote_attr_values="always").render(
        StaticWalker(tokens))
    assert 'href="x?a=1&amp;b=2"' in result
    assert 'title="Tom &amp; Jerry"' in result


def test_default_serialization_still_double_escapes_pre_escaped_input():
    tokens = [characters("a &amp; b")]
    result = HTMLSerializer().render(StaticWalker(tokens))
    assert result == "a &amp;amp; b"


def test_parse_round_trip_with_preserve_entities():
    # Entities are decoded by the parser and re-encoded exactly once.
    document = parse("<p>Tom &amp; Jerry &lt;tag&gt;</p>")
    result = serialize(document, omit_optional_tags=False,
                       preserve_entities=True)
    assert "Tom &amp; Jerry &lt;tag&gt;" in result


# ---------------------------------------------------------------------------
# Combined round trip
# ---------------------------------------------------------------------------

def test_combined_preservation_round_trip():
    source = ("<div data-z='z' data-a='a'>"
              "<svg><path d='M0 0'/></svg><br/>"
              "Tom &amp; Jerry &lt;x&gt;</div>")
    document = parse(source)
    result = serialize(document, omit_optional_tags=False,
                       use_trailing_solidus=True,
                       space_before_trailing_solidus=False,
                       preserve_entities=True,
                       quote_attr_values="always")
    assert 'data-z="z" data-a="a"' in result
    assert '<path d="M0 0"/>' in result
    assert "<br/>" in result
    assert "Tom &amp; Jerry &lt;x&gt;" in result
    # A second round trip must be stable: no accumulating diff noise.
    assert result == serialize(parse(result), omit_optional_tags=False,
                               use_trailing_solidus=True,
                               space_before_trailing_solidus=False,
                               preserve_entities=True,
                               quote_attr_values="always")


def test_unknown_serializer_option_still_rejected():
    with pytest.raises(TypeError):
        HTMLSerializer(not_a_real_option=True)
