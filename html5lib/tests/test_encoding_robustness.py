"""Tests for robust handling of mislabelled and BOM-marked documents.

Covers:
* a page that declares UTF-8 but is really GBK being read correctly,
* the audit trail explaining every encoding decision,
* decode-error accounting when a declaration has to be kept,
* BOMs (including UTF-32, which is outside the WHATWG Encoding Standard)
  being detected and stripped,
* authoritative signals (BOM / headers / override) always winning.
"""
from __future__ import absolute_import, division, unicode_literals

import codecs

import pytest

from html5lib import HTMLParser
from html5lib._inputstream import (HTMLBinaryInputStream, lookupEncoding,
                                   lookupEncodingWithFallback)


GBK_TEXT = "中文页面：编码声明与实际不符的测试内容，足够多一点的中文让检测器有把握。"
GBK_PAGE = ("<html><head><meta charset='utf-8'></head>"
            "<body><p>%s</p></body></html>" % GBK_TEXT).encode("gbk")


def drain(stream):
    from html5lib.constants import EOF
    chars = []
    while True:
        char = stream.char()
        if char == EOF:
            return "".join(chars)
        chars.append(char)


# ---------------------------------------------------------------------------
# Misdeclared UTF-8 (actually GBK)
# ---------------------------------------------------------------------------

def test_misdeclared_utf8_is_corrected_when_sniffer_available():
    stream = HTMLBinaryInputStream(GBK_PAGE)
    # The false UTF-8 declaration must not be used; chardet finds a Chinese
    # encoding (gb18030 is a superset of gbk) which decodes cleanly.
    assert stream.encoding in ("gb18030", "gbk", "gb2312")
    assert stream.decodeErrors == 0
    assert GBK_TEXT in drain(stream)
    assert stream.decodeErrors == 0


def test_misdeclared_utf8_records_rejection_evidence():
    stream = HTMLBinaryInputStream(GBK_PAGE)
    trail = stream.encodingHistory()

    rejected = [entry for entry in trail
                if entry["source"] == "meta" and entry["action"] == "rejected"]
    assert len(rejected) == 1
    entry = rejected[0]
    assert entry["encoding"] == "utf-8"
    assert entry["confidence"] == "tentative"
    # The reason must point at the concrete byte that broke UTF-8.
    assert "position" in entry["reason"]

    selected = [entry for entry in trail if entry["action"] == "selected"]
    assert selected[-1]["encoding"] == stream.encoding


def test_misdeclared_utf8_without_sniffer_keeps_label_but_reports_conflict():
    stream = HTMLBinaryInputStream(GBK_PAGE, useChardet=False)
    assert stream.encoding == "utf-8"
    trail = stream.encodingHistory()
    # The contradiction is visible in the audit trail even though, with no
    # sniffer, there is nothing better to switch to.
    assert any("does not match the actual bytes" in entry["reason"]
               for entry in trail)
    drain(stream)
    # Reading produced replacement characters, one per undecodable byte run.
    assert stream.decodeErrors > 0


def test_strict_mode_keeps_spec_behaviour():
    stream = HTMLBinaryInputStream(GBK_PAGE,
                                   overrideMisdeclaredEncoding=False)
    assert stream.encoding == "utf-8"
    assert all(entry["action"] != "rejected"
               for entry in stream.encodingHistory())


def test_transport_header_is_authoritative_even_when_wrong():
    stream = HTMLBinaryInputStream(GBK_PAGE, transport_encoding="utf-8")
    assert stream.encoding == "utf-8"
    assert stream.encodingConfidence == "certain"
    entry = stream.encodingHistory()[0]
    assert entry["source"] == "transport"
    assert entry["action"] == "selected"
    # But the mismatch is still recorded for diagnostics.
    assert "does not match the actual bytes" in entry["reason"]


def test_override_encoding_is_authoritative():
    stream = HTMLBinaryInputStream(GBK_PAGE, override_encoding="utf-8")
    assert stream.encoding == "utf-8"
    assert stream.encodingConfidence == "certain"


def test_parser_exposes_encoding_audit():
    parser = HTMLParser(namespaceHTMLElements=False)
    assert parser.documentEncoding is None
    assert parser.encodingTrail is None
    assert parser.decodeErrors is None

    doc = parser.parse(GBK_PAGE)
    assert parser.documentEncoding in ("gb18030", "gbk", "gb2312")
    assert parser.documentEncodingConfidence == "tentative"
    assert parser.decodeErrors == 0
    assert doc.find(".//p").text == GBK_TEXT
    assert any(entry["source"] == "meta"
               for entry in parser.encodingTrail)


# ---------------------------------------------------------------------------
# Correctly labelled pages keep working, with zero decode errors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,codec", [
    ("utf-8", "utf-8"),
    ("gbk", "gbk"),
    ("big5", "big5"),
    ("euc-jp", "euc-jp"),
    ("shift_jis", "shift_jis"),
    ("euc-kr", "euc-kr"),
    ("windows-1252", "windows-1252"),
])
def test_correctly_labelled_pages(label, codec):
    text = {"utf-8": "通用字符集测试",
            "gbk": "中文简体网页",
            "big5": "繁體中文網頁",
            "euc-jp": "これは日本語",
            "shift_jis": "日本語テスト",
            "euc-kr": "한국어 테스트",
            "windows-1252": "café €"}[label]
    data = ("<meta charset='%s'><p>%s</p>" % (label, text)).encode(codec)
    parser = HTMLParser(namespaceHTMLElements=False)
    doc = parser.parse(data)
    assert doc.find(".//p").text == text
    assert parser.decodeErrors == 0


# ---------------------------------------------------------------------------
# BOM handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bom,codec,expected_name", [
    (codecs.BOM_UTF8, "utf-8", "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le", "utf-16le"),
    (codecs.BOM_UTF16_BE, "utf-16-be", "utf-16be"),
    (codecs.BOM_UTF32_LE, "utf-32-le", "utf-32le"),
    (codecs.BOM_UTF32_BE, "utf-32-be", "utf-32be"),
])
def test_bom_is_detected_and_stripped(bom, codec, expected_name):
    payload = bom + "你好abc".encode(codec)
    stream = HTMLBinaryInputStream(payload, useChardet=False)
    assert stream.encoding == expected_name
    # The BOM itself must never appear in the decoded text.
    text = drain(stream)
    assert not text.startswith("﻿")
    assert text.startswith("你好")
    assert stream.decodeErrors == 0


def test_utf32_bom_not_misdetected_as_utf16():
    # Regression: the UTF-32 LE BOM starts with the UTF-16 LE BOM bytes.
    data = codecs.BOM_UTF32_LE + "x".encode("utf-32-le")
    stream = HTMLBinaryInputStream(data, useChardet=False)
    assert stream.encoding == "utf-32le"
    assert drain(stream) == "x"


def test_utf32_uses_stdlib_fallback_codec():
    # webencodings deliberately does not know UTF-32.
    assert lookupEncoding("utf-32le") is None
    encoding = lookupEncodingWithFallback("utf-32le")
    assert encoding is not None
    assert encoding.name == "utf-32le"
    assert encoding.codec_info is not None
    assert lookupEncodingWithFallback("totally-bogus") is None


def test_bom_beats_override_and_meta():
    data = codecs.BOM_UTF8 + "<meta charset='iso-8859-2'>abc".encode("utf-8")
    stream = HTMLBinaryInputStream(data, override_encoding="iso-8859-2",
                                   useChardet=False)
    assert stream.encoding == "utf-8"
    assert stream.encodingConfidence == "certain"


# ---------------------------------------------------------------------------
# Sniffed / default encodings are validated before being used
# ---------------------------------------------------------------------------

def test_chardet_guess_that_does_not_decode_is_rejected():
    # Pure ASCII always decodes; instead force the default path with a codec
    # that fails on the bytes and check the trail records the rejection.
    data = b"<p>\xff\xfe valid windows-1252 bytes \xe9</p>"
    # utf-8 cannot decode 0xff: the configured default is rejected and the
    # single-byte fallback is used (which cannot fail).
    stream = HTMLBinaryInputStream(data, useChardet=False,
                                   default_encoding="utf-8")
    assert stream.encoding == "windows-1252"
    trail = stream.encodingHistory()
    assert any(entry["source"] == "default" and entry["action"] == "rejected"
               for entry in trail)
    assert any(entry["source"] == "fallback" and entry["action"] == "selected"
               for entry in trail)
    assert stream.decodeErrors == 0


def test_trail_entry_shape():
    stream = HTMLBinaryInputStream(GBK_PAGE, useChardet=False)
    for entry in stream.encodingHistory():
        assert set(entry) == {"source", "encoding", "confidence",
                              "action", "reason"}
        assert entry["action"] in ("selected", "rejected", "ignored",
                                   "upgraded")


def test_indocument_meta_switch_to_contradicted_utf8_is_rejected():
    # The tokenizer re-finds the meta charset while tokenizing. Switching to
    # UTF-8 would corrupt GBK bytes, so changeEncoding must refuse it and keep
    # the working sniffed encoding.
    parser = HTMLParser(namespaceHTMLElements=False)
    doc = parser.parse(GBK_PAGE)
    assert parser.documentEncoding in ("gb18030", "gbk", "gb2312")
    assert doc.find(".//p").text == GBK_TEXT
    meta_changes = [entry for entry in parser.encodingTrail
                    if entry["source"] == "meta-change"]
    assert any(entry["action"] == "rejected" for entry in meta_changes)


def test_indocument_meta_switch_that_decodes_is_accepted():
    # No encoding in the prescan window (padded past 1024 bytes); a later,
    # correct UTF-8 meta declaration must still trigger the normal reparse.
    data = ("<title>Café</title><!--" + ("a" * 10240) +
            "--><meta charset='utf-8'>").encode("utf-8")
    parser = HTMLParser(namespaceHTMLElements=False)
    doc = parser.parse(data, useChardet=False)
    assert parser.documentEncoding == "utf-8"
    assert doc.find(".//title").text == "Café"
    meta_changes = [entry for entry in parser.encodingTrail
                    if entry["source"] == "meta-change"]
    assert any(entry["action"] == "selected" for entry in meta_changes)
