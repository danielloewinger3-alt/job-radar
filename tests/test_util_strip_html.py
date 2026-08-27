"""Regression + security-characterization coverage for backend.util.strip_html."""

import pytest

from backend.util import strip_html


# --------------------------------------------------------------------------- #
# Normal HTML
# --------------------------------------------------------------------------- #
def test_strips_inline_tags_keeps_text():
    assert strip_html("<p>Hello <strong>world</strong></p>") == "Hello world"


def test_block_level_tags_become_newlines():
    assert strip_html("<div>Line one</div><div>Line two</div>") == "Line one\nLine two"
    assert strip_html("First<br/>Second") == "First\nSecond"
    assert strip_html("<li>a</li><li>b</li>") == "a\nb"


def test_collapses_runs_of_blank_lines():
    assert strip_html("<p>a</p><br><br><br><br><p>b</p>") == "a\n\nb"


def test_resolves_html_entities():
    assert strip_html("Tom &amp; Jerry") == "Tom & Jerry"


def test_resolves_double_encoded_entities():
    # Greenhouse double-encodes: "&amp;lt;p&amp;gt;" -> "<p>" after two unescapes.
    assert strip_html("text &amp;amp; more &amp;lt;p&amp;gt;done") == "text & more done"


# --------------------------------------------------------------------------- #
# Script / style removal (raw tags)
# --------------------------------------------------------------------------- #
def test_removes_script_tag_and_contents():
    assert strip_html("before<script>alert('x')</script>after") == "before after"
    assert "alert" not in strip_html("a<script>var x = 1;</script>b")


def test_removes_style_tag_and_contents():
    assert strip_html("a<style>.x{color:red}</style>b") == "a b"
    assert "color:red" not in strip_html("a<style>.x{color:red}</style>b")


def test_removes_script_case_insensitively():
    assert strip_html("<SCRIPT>evil()</SCRIPT>keep") == "keep"


def test_removes_noscript_block():
    assert strip_html("x<noscript>fallback</noscript>y") == "x y"


# --------------------------------------------------------------------------- #
# Malformed / edge input
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_like_input_returns_empty_string(value):
    assert strip_html(value) == ""


def test_unclosed_tags_are_still_stripped():
    assert strip_html("<p>unclosed <b>bold") == "unclosed bold"


def test_bare_angle_brackets_are_treated_as_a_tag():
    # Current behaviour: `<[^>]+>` greedily spans from the first "<" to the first
    # ">", so "< 3 and <div>" is removed wholesale. Pinned, not endorsed.
    assert strip_html("2 < 3 and <div>x") == "2 x"


# --------------------------------------------------------------------------- #
# Security characterization: entity-encoded <script> delimiters
# --------------------------------------------------------------------------- #
@pytest.mark.xfail(
    reason=(
        "SECURITY GAP: strip_html unescapes entities AFTER removing <script> blocks, "
        "so when the tag delimiters arrive entity-encoded (&lt;script&gt;...&lt;/script&gt;) "
        "the surrounding <script> tags are stripped but the JS *body* leaks into the "
        "output. Desired behaviour: script contents are removed regardless of encoding. "
        "A fix that removes 'stealCookies()' makes this test xPASS - which is fine."
    ),
    strict=False,
)
def test_entity_encoded_script_contents_are_removed():
    out = strip_html("Hello a &lt;script&gt;stealCookies()&lt;/script&gt; b world")
    assert "stealCookies" not in out
    assert "Hello" in out and "world" in out
