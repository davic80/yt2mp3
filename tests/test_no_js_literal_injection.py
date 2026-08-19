"""Guard against XSS via values interpolated into inline event handlers.

An inline handler (`onclick="f('${value}')"`) is compiled as JavaScript *after*
the HTML parser has decoded character references. So HTML-escaping does not
contain a value there: `esc()` turning ' into &#39; is undone by the parser
before the JS is parsed, and the quote still breaks out of the string literal.
Verified in a real browser — escaping the quote as an entity still executed the
payload.

The fix is to keep untrusted values out of generated JavaScript entirely: put
them in data-* attributes and read them back with `dataset` from a delegated
listener, where they are only ever strings.

These tests scan the templates so the pattern cannot come back.
"""
import glob
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Interpolation inside a single-quoted JavaScript string literal.
JS_LITERAL_INTERP = re.compile(r"'\$\{([^}]*)\}")

# Expressions allowed in that position. All are server-generated identifiers
# with no attacker-controllable characters: UUID job ids, UUID share tokens,
# and a hardcoded column key from a literal in the template itself.
ALLOWED_EXPRESSIONS = {
    "c.key",       # admin/users.html — hardcoded sort-column key
    "jobId",       # player.html — UUID
    "t.job_id",    # player.html / mis_descargas.html — UUID
    "token",       # player.html — UUID share token
}



def _template_files():
    patterns = [
        os.path.join(ROOT, "app", "templates", "**", "*.html"),
        os.path.join(ROOT, "static", "*.js"),
    ]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern, recursive=True))
    assert files, "no templates found — check the scan paths"
    return files


def _scan():
    """Yield (file, line_no, expression) for every single-quoted interpolation."""
    patterns = [
        os.path.join(ROOT, "app", "templates", "**", "*.html"),
        os.path.join(ROOT, "static", "*.js"),
    ]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern, recursive=True))
    assert files, "no templates found — check the scan paths"

    for path in files:
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                for match in JS_LITERAL_INTERP.finditer(line):
                    yield rel, line_no, match.group(1).strip()


def test_no_escaped_value_reaches_a_js_string_literal():
    """esc() output in a JS literal is the exact bug that shipped.

    Calling esc() means the value is untrusted. HTML-escaping it and then
    dropping it into generated JavaScript gives false confidence: the HTML
    parser decodes the entity before the JS runs.
    """
    offenders = [
        (f, n, expr) for f, n, expr in _scan() if "esc(" in expr
    ]
    assert not offenders, (
        "HTML-escaped (therefore untrusted) values inside a single-quoted JS "
        "string literal — use a data-* attribute and a delegated listener:\n"
        + "\n".join(f"  {f}:{n} → '${{{e}}}'" for f, n, e in offenders)
    )


def test_only_server_generated_ids_reach_a_js_string_literal():
    """Anything new in that position must be justified, not just escaped."""
    unexpected = [
        (f, n, expr) for f, n, expr in _scan()
        if expr not in ALLOWED_EXPRESSIONS
    ]
    assert not unexpected, (
        "new interpolation inside a single-quoted JS string literal. If the "
        "value is server-generated and cannot contain a quote, add it to "
        "ALLOWED_EXPRESSIONS; otherwise move it to a data-* attribute:\n"
        + "\n".join(f"  {f}:{n} → '${{{e}}}'" for f, n, e in unexpected)
    )


def test_playlist_name_is_not_in_an_inline_handler():
    """The concrete vector: a batch download names the playlist after the
    YouTube playlist title, so a third party controls pl.name."""
    path = os.path.join(ROOT, "app", "templates", "fragments", "player.html")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()

    assert "sharePlaylist(${pl.id},'${esc(pl.name)}')" not in source
    assert 'data-pl-name="${esc(pl.name)}"' in source


@pytest.mark.parametrize("feature", [
    "is_enabled", "is_admin", "lyrics_enabled", "share_enabled",
])
def test_admin_toggles_use_data_attributes(feature):
    path = os.path.join(ROOT, "app", "templates", "admin", "users.html")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()

    assert f"toggleFeature('${{esc(u.email)}}', '{feature}'" not in source
    assert f'data-feature="{feature}"' in source

# ── The blind spot that let a second instance survive ────────────────────────

INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=\s*\"")


def test_no_inline_event_handlers_anywhere():
    """Inline handlers are what force script-src 'unsafe-inline'.

    They are also where interpolated values get compiled as code — in either
    templating language. The first sweep only looked for JavaScript template
    literals (`${esc(x)}`) and so missed the admin table, which built the same
    hole out of Jinja: the rename link interpolated a YouTube video title into
    a single-quoted JS string with `| e`. Jinja escapes the apostrophe to a
    character reference and the HTML parser turns it straight back, so a
    crafted title ran in the admin's session.

    Zero is the whole rule now. There is no safe way to put a value into one.
    """
    offenders = []
    for path in _template_files():
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                if line.lstrip().startswith(("//", "*", "/*", "#")):
                    continue  # prose about the old code, not the old code
                for match in INLINE_HANDLER.finditer(line):
                    offenders.append((rel, line_no, match.group(0).strip()))

    assert not offenders, (
        "inline event handlers are back — use a data-* attribute and a "
        "delegated listener:\n"
        + "\n".join(f"  {f}:{n} {h}" for f, n, h in offenders)
    )


JINJA_IN_JS_LITERAL = re.compile(r"'\{\{")


def test_no_jinja_interpolation_inside_a_js_string_literal():
    """The Jinja twin of the rule above, kept even with no handlers left."""
    offenders = []
    for path in _template_files():
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                if line.lstrip().startswith(("//", "*", "/*", "#")):
                    continue
                if JINJA_IN_JS_LITERAL.search(line):
                    offenders.append((rel, line_no, line.strip()[:90]))

    assert not offenders, (
        "Jinja value inside a single-quoted JS string literal — `| e` does not "
        "protect a JavaScript context:\n"
        + "\n".join(f"  {f}:{n} {t}" for f, n, t in offenders)
    )
