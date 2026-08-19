"""Fragments must not assume I18n is loaded when they first run.

shell.html inlines the initial fragment *above* the shared <script src> tags,
so on a direct page load — a bookmark, a refresh, a typed URL — the fragment's
own script runs before /static/i18n.js has been evaluated. window.I18n is
undefined, the first I.t() throws, and the page renders empty.

Reached through an SPA navigation the modules are already there, which is why
this only ever broke for people who opened the page directly. player.html and
home.html each carried a guard; mis_descargas.html did not, and its table came
up blank.
"""
import glob
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAGMENTS = sorted(glob.glob(os.path.join(ROOT, "app", "templates", "fragments", "*.html")))

USES_I18N = re.compile(r"window\.I18n\b")
# Deliberately narrow. An 'i18n:change' listener does NOT count: every
# fragment has one to re-render on a language switch, and the broken version
# of mis_descargas.html had one too while still calling load() unguarded at
# boot. What matters is that the *bootstrap* asks whether I18n is there yet.
GUARDS = (
    re.compile(r"if\s*\(\s*window\.I18n\s*\)"),   # if (window.I18n) { ... } else { wait }
    re.compile(r"window\.I18n\s*\?\."),            # window.I18n?.t(...)
)


def test_there_are_fragments_to_check():
    assert FRAGMENTS, "no fragments found — check the scan path"


@pytest.mark.parametrize("path", FRAGMENTS, ids=lambda p: os.path.basename(p))
def test_fragment_guards_its_use_of_i18n(path):
    with open(path, encoding="utf-8") as fh:
        source = fh.read()

    if not USES_I18N.search(source):
        pytest.skip("does not use I18n")

    assert any(guard.search(source) for guard in GUARDS), (
        f"{os.path.basename(path)} reads window.I18n but never checks whether it "
        "is there yet. On a direct page load it is not, and the fragment throws "
        "before rendering anything. Guard with `if (window.I18n)` and fall back "
        "to waiting for the 'i18n:change' event, as player.html does."
    )


def test_the_shared_scripts_really_do_come_after_the_fragment():
    """Pin the layout that makes the guard necessary, so that if someone ever
    reorders shell.html the reason these guards exist is not a mystery."""
    with open(os.path.join(ROOT, "app", "templates", "shell.html"), encoding="utf-8") as fh:
        shell = fh.read()

    include_at = shell.find("{% include 'fragments/")
    i18n_at = shell.find('src="/static/i18n.js"')
    assert include_at != -1 and i18n_at != -1
    assert include_at < i18n_at, (
        "shell.html now loads i18n.js before the fragment — the guards in the "
        "fragments are no longer required, and this test should be removed "
        "along with them"
    )
