"""Standalone pytest file for the lint module.

The lint module reads `SENDER_FIRST` from the environment at import time.
We pre-set a placeholder so the test fixtures pass regardless of any local
`.env` the developer has configured.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Force placeholder identity BEFORE the lint module is imported.
os.environ["SENDER_FIRST"] = "Sender"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adaptive.lint import lint_email  # noqa: E402


GOOD = """Subject: Scouting Note on Caveman

Hi Mark,

I'm Sender, scouting builders. Found you through your project Caveman.

Part of what I do is meet people building or planning startups in the next few months. If that's where you're at, we'd welcome a brief virtual call.

If research remains your focus, please reconnect whenever that changes.

Best,
Sender"""


def test_good_email_passes() -> None:
    r = lint_email(GOOD)
    assert r.ok, f"good email should pass; got violations={r.violations}"
    assert r.word_count <= 110, f"good email word count {r.word_count} > 110"


def test_blacklist_phrase_caught() -> None:
    bad = GOOD.replace("Found you through", "Your work is fascinating, I came across")
    r = lint_email(bad)
    assert not r.ok
    assert any("fascinating" in v.lower() or "i came across" in v.lower() for v in r.violations)


def test_em_dash_rejected() -> None:
    bad = GOOD.replace("Hi Mark,", "Hi Mark — quick one,")
    r = lint_email(bad)
    assert not r.ok
    assert any("em" in v.lower() or "—" in v for v in r.violations)


def test_word_count_overflow() -> None:
    bad = GOOD + "\n\n" + (" extra word" * 100)
    r = lint_email(bad)
    assert not r.ok
    assert r.word_count > 110


def test_missing_sender() -> None:
    bad = GOOD.replace("Sender", "Bob")
    r = lint_email(bad)
    assert not r.ok


def test_template_braces_rejected() -> None:
    bad = GOOD.replace("Mark", "{first_name}")
    r = lint_email(bad)
    assert not r.ok
    assert any("template" in v.lower() or "{" in v for v in r.violations)


def test_empty_string() -> None:
    r = lint_email("")
    assert not r.ok


if __name__ == "__main__":
    import inspect
    funcs = [obj for name, obj in globals().items()
             if name.startswith("test_") and inspect.isfunction(obj)]
    failed = []
    for f in funcs:
        try:
            f()
            print(f"  ok  {f.__name__}")
        except AssertionError as e:
            print(f"  FAIL {f.__name__}: {e}")
            failed.append(f.__name__)
    if failed:
        print(f"\n{len(failed)}/{len(funcs)} failed")
        sys.exit(1)
    print(f"\n{len(funcs)}/{len(funcs)} passed")
    sys.exit(0)
