"""Lint gate for cold-email variants.

Rejects AI-slop output before it gets shown or sent. Pure stdlib.

Public API:
    lint_email(body: str, subject: str = "") -> LintResult

Run self-test:
    python -m adaptive.lint --selftest
    python -m adaptive.lint
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, asdict
from typing import List

# Only SENDER_FIRST flows from env. No other identity fields are read.
# Default placeholder "Sender" lets tests run without a configured .env.
_SENDER_FIRST = (os.getenv("SENDER_FIRST", "Sender") or "Sender").strip()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_WORDS: int = 110

BLACKLIST_PHRASES: List[str] = [
    "caught my attention",
    "I came across",
    "I was particularly impressed",
    "Your work is fascinating",
    "fascinating",
    "impressive",
    "the core approach",
    "I wanted to reach out",
    "I hope this email finds you well",
    "exactly the kind of",
    "exactly what we look for",
    "state-of-the-art",
    "game changer",
    "solves a real pain point",
    "real bottleneck",
    "consistent thread",
    "is serious work",
    "puts you in direct competition",
    "grabbed my attention",
    "resonated with me",
    "I'd love to learn more",
    "your innovative approach",
    "innovative",
    "quick question",
    "quick q",
    "this resonated",
]

REQUIRED_SUBJECT_TOKEN: str = "Scouting Note on"
TEMPLATE_ARTIFACT_CHARS: List[str] = ["{", "}", "[", "]", "<", ">"]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class LintResult:
    ok: bool
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    word_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_subject(body: str, subject: str) -> tuple[str, str]:
    """If body starts with a 'Subject:' prefix line, extract it.

    Returns (effective_subject, body_without_subject_line).
    """
    eff_subject = subject
    lines = body.splitlines()
    # Skip leading blank lines to find first non-empty
    idx = 0
    while idx < len(lines) and lines[idx].strip() == "":
        idx += 1
    if idx < len(lines) and lines[idx].lstrip().lower().startswith("subject:"):
        extracted = lines[idx].split(":", 1)[1].strip()
        if not eff_subject:
            eff_subject = extracted
        # Drop the subject line plus a trailing blank line if present
        new_lines = lines[:idx] + lines[idx + 1 :]
        if idx < len(new_lines) and new_lines[idx].strip() == "":
            new_lines = new_lines[:idx] + new_lines[idx + 1 :]
        body = "\n".join(new_lines)
    return eff_subject, body


def _has_bullet_lines(body: str) -> bool:
    for raw in body.splitlines():
        stripped = raw.lstrip()
        if not stripped:
            continue
        first = stripped[0]
        if first in ("-", "*", "•"):
            # Allow words like "- " only if it's literally at the start of a line
            # The rule says lines starting with these chars are bullets.
            # Need to allow the sign-off "Best,\nSender Name" — none start with these.
            return True
    return False


def _first_paragraph(body: str) -> str:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    p = paragraphs[0]
    # Skip an opening greeting like "Hi Mark," — use the next paragraph if so
    greet_lower = p.lower()
    if (
        greet_lower.startswith("hi ")
        or greet_lower.startswith("hello ")
        or greet_lower.startswith("hey ")
        or greet_lower.startswith("dear ")
    ) and len(p.split()) <= 5:
        return paragraphs[1] if len(paragraphs) > 1 else p
    return p


def _sentence_count(text: str) -> int:
    count = 0
    for ch in text:
        if ch in ".!?":
            count += 1
    return max(count, 1)


# ---------------------------------------------------------------------------
# Main lint function
# ---------------------------------------------------------------------------

def lint_email(body: str, subject: str = "") -> LintResult:
    violations: List[str] = []
    warnings: List[str] = []

    if body is None or not body.strip():
        return LintResult(ok=False, violations=["empty body"], warnings=[], word_count=0)

    eff_subject, body = _split_subject(body, subject)

    word_count = len(body.split())

    # Rule 1: word count
    if word_count > MAX_WORDS:
        violations.append(f"word count {word_count} > {MAX_WORDS}")

    # Rule 2: blacklist phrases (case-insensitive substring)
    body_lower = body.lower()
    for phrase in BLACKLIST_PHRASES:
        if phrase.lower() in body_lower:
            violations.append(f"blacklist: '{phrase}'")

    # Rule 3: forbidden punctuation/structure — applies to BOTH body and subject.
    # (Codex review P2: previously only body was checked, so a subject like
    # "Scouting Note on {anchor}" passed despite forbidden braces.)
    body_and_subject = body + "\n" + eff_subject
    if "—" in body_and_subject:  # em dash
        violations.append("em dash present")
    if "–" in body_and_subject:  # en dash
        violations.append("en dash present")
    if "..." in body_and_subject:
        violations.append("trailing '...' present")
    if "…" in body_and_subject:  # unicode ellipsis
        violations.append("trailing '...' present")
    if _has_bullet_lines(body):
        violations.append("bullet point line present")
    for ch in TEMPLATE_ARTIFACT_CHARS:
        if ch in body_and_subject:
            violations.append(f"template artifact '{ch}' present")

    # Rule 4: required structural elements
    sender_first_lower = _SENDER_FIRST.lower()
    if sender_first_lower and sender_first_lower not in body_lower:
        violations.append(f"missing sender identity '{_SENDER_FIRST}'")
    if REQUIRED_SUBJECT_TOKEN.lower() not in eff_subject.lower():
        violations.append(f"subject missing '{REQUIRED_SUBJECT_TOKEN}'")

    # Rule 5: soft warning — first paragraph should be a single short sentence
    p1 = _first_paragraph(body)
    if p1:
        p1_words = len(p1.split())
        p1_sentences = _sentence_count(p1)
        if p1_sentences > 1 or p1_words > 30:
            warnings.append(
                f"paragraph 1 should be a single short sentence "
                f"(got {p1_sentences} sentences, {p1_words} words)"
            )

    return LintResult(
        ok=len(violations) == 0,
        violations=violations,
        warnings=warnings,
        word_count=word_count,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

GOOD_1 = """Subject: Scouting Note on Caveman

Hi Mark,

I'm Sender, scouting builders. Found you through your project Caveman.

Part of what I do is meet people building or planning startups in the next few months. If that's where you're at, we'd welcome a brief virtual call.

If research remains your focus, please reconnect whenever that changes.

Best,
Sender
"""

GOOD_2 = """Subject: Scouting Note on Telos

Hi Tom,

I'm Sender, scouting builders. Found you through Telos.

My work is meeting people building or planning startups in the next few months. If that fits, we'd welcome a brief virtual call.

If studies remain your focus, please reconnect whenever that changes.

Best,
Sender
"""

GOOD_3 = """Subject: Scouting Note on Auteur

Hi Ishan,

I'm Sender, scouting builders. Found you through Auteur.

My work is meeting people who are starting companies in the next quarter. If that's you, we'd welcome a brief Zoom call.

Best,
Sender
"""

GOOD_4 = """Subject: Scouting Note on BoostT1D

Hi Aaron,

I'm Sender, scouting builders. Found you through BoostT1D.

My job is meeting builders planning startups soon. If that's where you're at, we'd welcome a quick virtual call.

Best,
Sender
"""

GOOD_5 = """Subject: Scouting Note on your discrete diffusion paper

Hi Oussama,

I'm Sender, scouting researchers. Found you through your discrete diffusion paper.

Part of what I do is meet researchers planning to start companies in the next few months. If that fits, we'd welcome a brief virtual call.

If research remains your focus, please reconnect whenever that changes.

Best,
Sender
"""

# BAD samples — each must trigger at least one specific violation

BAD_BLACKLIST = """Subject: Scouting Note on Caveman

Hi Mark,

I'm Sender. Your work caught my attention and I find it truly fascinating.

Brief call?

Best,
Sender
"""

BAD_EMDASH = """Subject: Scouting Note on Telos

Hi Tom,

I'm Sender — scouting builders. Found you through Telos.

Brief call?

Best,
Sender
"""

BAD_MISSING_SUBJECT = """Hi Mark,

I'm Sender, scouting builders. Found you through Caveman.

Brief call?

Best,
Sender
"""

BAD_BULLET = """Subject: Scouting Note on Caveman

Hi Mark,

I'm Sender. Found you through Caveman. Reasons to talk:
- We fund founders
- We move fast
- We are friendly

Best,
Sender
"""


def _build_long_essay() -> str:
    body = "Subject: Scouting Note on Caveman\n\nHi Mark,\n\nI'm Sender. "
    body += ("word " * 200)
    body += "\n\nBest,\nSender\n"
    return body


def _run_selftest() -> int:
    cases: List[tuple[str, bool, str]] = []  # (name, expect_ok, body) plus optional check

    good_samples = {
        "GOOD_1": GOOD_1,
        "GOOD_2": GOOD_2,
        "GOOD_3": GOOD_3,
        "GOOD_4": GOOD_4,
        "GOOD_5": GOOD_5,
    }
    bad_samples = {
        "BAD_BLACKLIST": BAD_BLACKLIST,
        "BAD_EMDASH": BAD_EMDASH,
        "BAD_MISSING_SUBJECT": BAD_MISSING_SUBJECT,
        "BAD_BULLET": BAD_BULLET,
    }

    failures: List[str] = []
    total = 0

    # Good cases
    for name, body in good_samples.items():
        total += 1
        r = lint_email(body)
        if not r.ok:
            failures.append(f"{name} expected ok=True, got violations={r.violations}")

    # Bad cases
    for name, body in bad_samples.items():
        total += 1
        r = lint_email(body)
        if r.ok:
            failures.append(f"{name} expected ok=False, but lint passed")
        elif not r.violations:
            failures.append(f"{name} ok=False but violations list is empty")

    # Edge case: empty
    total += 1
    r = lint_email("")
    if r.ok:
        failures.append("EMPTY expected ok=False")

    # Word count edge case: 200-word essay
    total += 1
    long_body = _build_long_essay()
    r = lint_email(long_body)
    if not any("word count" in v and "> 110" in v for v in r.violations):
        failures.append(f"LONG_ESSAY expected 'word count > 110' violation, got {r.violations}")

    # Em dash edge case
    total += 1
    em_body = "Subject: Scouting Note on Test\n\nHi — I'm Sender. Brief call?"
    r = lint_email(em_body)
    if not any("em dash" in v for v in r.violations):
        failures.append(f"EM_DASH expected 'em dash' violation, got {r.violations}")

    # Verify dataclass JSON-serializable
    total += 1
    sample = lint_email(GOOD_1)
    try:
        d = asdict(sample)
        import json
        json.dumps(d)
    except Exception as e:
        failures.append(f"JSON_SERIALIZE failed: {e}")

    passes = total - len(failures)
    if failures:
        print(f"FAIL: {passes}/{total} cases")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"PASS: {passes}/{total} cases")
    return 0


def main(argv: List[str]) -> int:
    # Accept --selftest or bare invocation
    if len(argv) <= 1 or argv[1] in ("--selftest", "-t"):
        return _run_selftest()
    print(f"unknown arg: {argv[1]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
