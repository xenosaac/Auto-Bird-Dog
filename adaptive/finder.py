"""Single-shot talent finder. Codex refines ICP into a web search query, Tavily
runs the search, Codex extracts a candidate from each hit, and (lazily) writes
a personalized cold-email preview that passes the lint gate.

Public API:
    refine_query(icp_prompt) -> str
    find_candidates(icp_prompt, *, n=8, on_event=None) -> list[Candidate]
    generate_email_preview(candidate, email_prompt="", *, on_event=None) -> EmailPreview

Streaming events (synchronous on_event callback). The dashboard pumps these
into st.session_state for live display:
    query_refine_start / query_refine_done
    tavily_search_start / tavily_search_done
    parse_start / candidate_extracted / parse_done
    email_gen_start / email_gen_done
    error
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv

load_dotenv()

# Local import so cache + fallback behavior stays consistent across modules.
from adaptive import codex_client
from adaptive.lint import lint_email

EventCallback = Callable[[str, str, dict], None]

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TAVILY_CACHE_DIR = _REPO_ROOT / "cache" / "tavily"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    name: str
    role: str
    company: str
    link: str
    snippet: str
    source_query: str
    email: str | None = None


@dataclass
class EmailPreview:
    subject: str
    body: str
    word_count: int
    lint_ok: bool
    lint_violations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emit(on_event: Optional[EventCallback], etype: str, msg: str, meta: dict) -> None:
    if on_event is None:
        return
    try:
        on_event(etype, msg, meta)
    except Exception:
        # Never let UI bugs break the pipeline.
        pass


def _strip_quotes(s: str) -> str:
    s = s.strip()
    # Remove wrapping ASCII or smart quotes (single or double).
    while len(s) >= 2 and s[0] in ('"', "'", "“", "”", "‘", "’") and s[-1] in ('"', "'", "“", "”", "‘", "’"):
        s = s[1:-1].strip()
    return s


def _strip_codefence(s: str) -> str:
    """Pull JSON out of a ```json ... ``` fence if present."""
    s = s.strip()
    if s.startswith("```"):
        # Drop opening fence line
        lines = s.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines).strip()
    return s


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _first_email(text: str) -> str | None:
    if not text:
        return None
    m = _EMAIL_RE.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# URL signals — boost person profiles, sink job-board / aggregator pages
# ---------------------------------------------------------------------------

_AGGREGATOR_PATTERNS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "wellfound.com/jobs",
    "indeed.com", "glassdoor.", "linkedin.com/jobs", "ycombinator.com/jobs",
    "/jobs/", "/careers/", "/openings", "/positions", "/job/",
    "jobs.lever.co", "jobs.ashby", "builtin.com/jobs",
    "stackoverflow.com/jobs", "remoteok.io", "weworkremotely.com",
    "monster.com", "ziprecruiter.com",
)


def _url_canonical(url: str) -> str:
    if not url:
        return ""
    u = url.strip().split("#", 1)[0].split("?", 1)[0]
    if u.endswith("/"):
        u = u[:-1]
    return u.lower()


def _person_score(url: str, title: str) -> int:
    """Higher = more likely a single named person; negative = job board / listing."""
    u = (url or "").lower()
    t = (title or "").lower()
    if not u:
        return -10
    score = 0
    if "linkedin.com/in/" in u:
        score += 6
    elif "github.com/" in u:
        path = u.split("github.com/", 1)[1].strip("/")
        # Single-segment path = user profile, not a repo or org.
        score += 5 if (path and "/" not in path) else 1
    elif "scholar.google.com/citations" in u:
        score += 5
    elif "researchgate.net/profile" in u:
        score += 4
    elif "medium.com/@" in u or "substack.com" in u:
        score += 3
    elif "/about" in u or "/team/" in u or "/people/" in u or "/bio" in u:
        score += 2
    for p in _AGGREGATOR_PATTERNS:
        if p in u:
            score -= 6
            break
    if any(w in t for w in ("ceo", "cto", "founder", "co-founder", "principal",
                            "director", "head of", " vp ", "engineer",
                            "researcher", "scientist", "lead")):
        score += 1
    return score


# ---------------------------------------------------------------------------
# Tavily wrapper (free-form, dedicated to finder use case)
# ---------------------------------------------------------------------------

def _tavily_search(query: str, max_results: int = 12) -> list[dict[str, Any]]:
    """Run a Tavily search with disk cache. Returns a list of {title,url,content}.

    Raises on import / API errors so the caller can surface them in the UI.
    """
    key = os.getenv("TAVILY_API_KEY", "")
    if not key or len(key) < 20:
        raise RuntimeError("TAVILY_API_KEY missing or invalid")

    cache_key = f"finder\x00{query}\x00{max_results}"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    cp = _TAVILY_CACHE_DIR / f"{digest}.json"
    if cp.exists():
        try:
            cached = json.loads(cp.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                return cached
        except Exception:
            pass

    from tavily import TavilyClient

    client = TavilyClient(api_key=key)
    r = client.search(query, max_results=max_results, search_depth="basic")
    raw = r.get("results", []) or []
    out: list[dict[str, Any]] = []
    for hit in raw:
        out.append({
            "title": (hit.get("title") or "")[:240],
            "url": hit.get("url") or "",
            "content": (hit.get("content") or "")[:600],
            "score": hit.get("score"),
        })

    _TAVILY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(out), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Step 1: refine ICP prompt into a search query
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = (
    "You are the planner step of a talent-sourcing agent. Before any web search "
    "happens you must understand WHO the user wants to find and WHY, then design "
    "diverse queries that surface REAL PEOPLE on the open web (not job listings or "
    "aggregator pages).\n\n"
    "Output ONE JSON object only. No prose, no markdown fence. Schema:\n"
    "{\n"
    '  "intent": "<one sentence: what we are hunting for and why>",\n'
    '  "persona": "<2-4 phrases: seniority + role + industry + distinguishing signal>",\n'
    '  "queries": ["<q1>", "<q2>", "<q3>"]\n'
    "}\n\n"
    "Rules for the three queries:\n"
    "- Three DIFFERENT angles. Do not repeat the same keywords across all three.\n"
    "- Each query is 5-12 plain keyword tokens. No surrounding quotes.\n"
    "- AT LEAST ONE query MUST use a profile-pinning site: filter chosen from "
    "site:linkedin.com/in/  site:github.com  site:scholar.google.com/citations.\n"
    "- AVOID job-board fuel words (jobs, hiring, openings, careers, apply, salary, "
    "recruiter, posting). They surface greenhouse/lever/indeed instead of people.\n"
    "- Prefer person-anchoring tokens (profile, about, bio, blog, talks, "
    "publications, founder, principal, lead, head of, alumni).\n"
    "- Keep them tight."
)


@dataclass
class SearchPlan:
    intent: str
    persona: str
    queries: list[str]


def plan_searches(icp_prompt: str) -> SearchPlan:
    """Single Codex call: understand intent → produce 3 diverse, person-anchored queries.

    This is the harness. Without it, Tavily gets a vague query and returns
    aggregator/job-listing pages with no named people on them.
    """
    icp_prompt = (icp_prompt or "").strip()
    if not icp_prompt:
        return SearchPlan(intent="", persona="", queries=[])

    raw = codex_client.generate(
        prompt=f"ICP description:\n{icp_prompt}\n\nReturn the planning JSON only.",
        system=_PLAN_SYSTEM,
        max_tokens=600,
        temperature=0.4,
        cache=True,
    )

    intent = ""
    persona = ""
    queries: list[str] = []

    s = _strip_codefence(raw or "").strip()
    parsed: dict | None = None
    try:
        v = json.loads(s)
        if isinstance(v, dict):
            parsed = v
    except Exception:
        # Extract first balanced { ... } substring if Codex wrapped JSON in prose.
        start = s.find("{")
        end = s.rfind("}")
        if 0 <= start < end:
            try:
                v = json.loads(s[start : end + 1])
                if isinstance(v, dict):
                    parsed = v
            except Exception:
                parsed = None

    if parsed:
        intent = str(parsed.get("intent", "") or "").strip()[:240]
        persona = str(parsed.get("persona", "") or "").strip()[:240]
        qs = parsed.get("queries", []) or []
        if isinstance(qs, list):
            for q in qs:
                if isinstance(q, str):
                    qq = _strip_quotes(q.strip())
                    if qq:
                        queries.append(qq[:200])

    # Defensive fallback: malformed JSON → use the raw first line as one query.
    if not queries:
        line = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
        line = _strip_quotes(line)
        if line.lower().startswith("query:"):
            line = line.split(":", 1)[1].strip()
        queries.append((line or icp_prompt)[:200])

    seen: set[str] = set()
    deduped: list[str] = []
    for q in queries:
        k = q.lower()
        if k in seen:
            continue
        seen.add(k)
        deduped.append(q)
        if len(deduped) >= 3:
            break

    if not persona:
        persona = icp_prompt[:200]
    if not intent:
        intent = f"Find people matching: {persona}"

    return SearchPlan(intent=intent, persona=persona, queries=deduped)


def refine_query(icp_prompt: str) -> str:
    """Backwards-compatible single-query helper. Returns the first planned query."""
    plan = plan_searches(icp_prompt)
    return plan.queries[0] if plan.queries else (icp_prompt or "")[:200]


# ---------------------------------------------------------------------------
# Step 2: parse Tavily hits → list[Candidate] in a SINGLE Codex call
# ---------------------------------------------------------------------------

def _parse_system_prompt(intent: str, persona: str) -> str:
    return (
        "You are the parsing step of a talent-sourcing agent.\n"
        f"Context — we are sourcing: {persona}\n"
        f"Intent: {intent}\n\n"
        "You receive a JSON array of search results, each shaped {i, title, url, content}. "
        "Return a JSON array (no prose, no markdown fence) of length EXACTLY N — one element "
        "per input result, in the same order. Each element MUST be an object: "
        '{"name": str|null, "role": str, "company": str, "link": str, "snippet": str, "email": str|null}.\n\n'
        "Decision rule for `name` — be GENEROUS, not strict:\n"
        "- If the title or content clearly names a single individual human (e.g. "
        "'Jane Doe - VP Engineering at Acme', 'Mike Folgner - Adobe', a LinkedIn /in/ "
        "profile URL with the person's name in the title), extract that name. "
        "DO NOT require the person to perfectly match the persona — the persona is "
        "context for disambiguation, not a hard filter. The downstream UI shows "
        "the source link so the operator can judge fit.\n"
        "- Set name=null ONLY when there is no single named individual on the page: "
        "raw job postings without a hiring manager, list-of-many-people articles, "
        "Wikipedia disambiguation pages, generic landing/company pages, news headlines "
        "that mention only a company. NEVER invent a name.\n"
        "- role: best-effort job title (e.g. 'CEO at Higgsfield AI'). Empty string if unclear.\n"
        "- company: the organization the person is most associated with. Empty if unclear.\n"
        "- link: the result's url, unchanged.\n"
        "- snippet: one sentence (<=160 chars) summarizing what the page says about this person, "
        "ideally referencing the persona signal if visible.\n"
        "- email: an email address if it appears verbatim in the content, else null.\n"
        "- Array length MUST equal N — include null-name entries so indices line up. "
        "JSON only."
    )


def _batch_parse_candidates(
    hits: list[dict[str, Any]],
    n: int,
    *,
    intent: str,
    persona: str,
    source_query: str,
    on_event: Optional[EventCallback],
) -> list[Candidate]:
    if not hits:
        return []

    # Pass up to n hits in one call — Codex returns one entry per index.
    selected = hits[:n]
    user_blob = json.dumps(
        [{"i": i, "title": h.get("title", ""), "url": h.get("url", ""), "content": h.get("content", "")[:400]}
         for i, h in enumerate(selected)],
        ensure_ascii=False,
    )
    prompt = (
        f"N = {len(selected)} search results below. Return a JSON array of length {len(selected)} "
        "with one object per result. Drop non-person results by setting name=null (do NOT omit "
        f"them from the array).\n\nResults:\n{user_blob}"
    )

    raw = codex_client.generate(
        prompt=prompt,
        system=_parse_system_prompt(intent, persona),
        max_tokens=2400,
        temperature=0.2,
        cache=True,
    )
    parsed = _coerce_to_list(raw)

    candidates: list[Candidate] = []
    for i, h in enumerate(selected):
        item = parsed[i] if i < len(parsed) and isinstance(parsed[i], dict) else None

        # Skip results Codex marked as not a single named person (name=null /
        # "unknown"). Cards never display Unknowns.
        name_field: str | None = None
        if isinstance(item, dict):
            v = item.get("name")
            if isinstance(v, str):
                name_field = v.strip()
            elif v is None:
                name_field = None
            else:
                name_field = str(v).strip() if v else None

        if not name_field or name_field.lower() in ("unknown", "n/a", "none", "null"):
            _emit(on_event, "candidate_extracted",
                  f"(skipped) {(h.get('title') or h.get('url') or '')[:80]}",
                  {"skipped": True, "url": h.get("url", "")})
            continue

        cand = _candidate_from_item(item, h, source_query)
        # Defensive: heuristic fallback might still produce 'Unknown' for edge cases.
        if cand.name.lower() == "unknown":
            _emit(on_event, "candidate_extracted",
                  f"(skipped) {(h.get('title') or h.get('url') or '')[:80]}",
                  {"skipped": True, "url": h.get("url", "")})
            continue

        candidates.append(cand)
        _emit(on_event, "candidate_extracted",
              f"{cand.name} · {cand.role}",
              {"name": cand.name, "role": cand.role,
               "company": cand.company, "link": cand.link})
    return candidates


def _coerce_to_list(raw: str) -> list[Any]:
    if not raw:
        return []
    s = _strip_codefence(raw).strip()
    # First try a clean parse.
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            # Sometimes a model wraps in {"results": [...]}.
            for key in ("results", "candidates", "items", "data"):
                if key in v and isinstance(v[key], list):
                    return v[key]
            return [v]
    except Exception:
        pass
    # Fallback: extract first balanced [ ... ] substring.
    start = s.find("[")
    end = s.rfind("]")
    if 0 <= start < end:
        chunk = s[start : end + 1]
        try:
            v = json.loads(chunk)
            if isinstance(v, list):
                return v
        except Exception:
            return []
    return []


def _candidate_from_item(item: dict | None, hit: dict[str, Any], source_query: str) -> Candidate:
    title = hit.get("title", "") or ""
    url = hit.get("url", "") or ""
    content = hit.get("content", "") or ""

    name = role = company = snippet = ""
    email: str | None = None
    if isinstance(item, dict):
        name = str(item.get("name", "") or "").strip()
        role = str(item.get("role", "") or "").strip()
        company = str(item.get("company", "") or "").strip()
        link = str(item.get("link", "") or "").strip() or url
        snippet = str(item.get("snippet", "") or "").strip()
        e = item.get("email")
        if isinstance(e, str) and "@" in e:
            email = e.strip()
    else:
        link = url

    # Heuristic fallbacks if Codex failed silently or partially.
    if not name:
        # Try "Name - Role at Company" pattern in the title.
        if " - " in title:
            name = title.split(" - ", 1)[0].strip()
        elif " | " in title:
            name = title.split(" | ", 1)[0].strip()
        else:
            name = "Unknown"
    if not role and " - " in title:
        role = title.split(" - ", 1)[1].split(" - ", 1)[0].strip()[:120]
    if not company and " at " in title.lower():
        # Crude but useful for the empty-company case.
        idx = title.lower().rfind(" at ")
        company = title[idx + 4 :].strip()[:120]
    if not snippet:
        snippet = (content or title)[:160]
    if email is None:
        email = _first_email(content)

    return Candidate(
        name=name[:120] or "Unknown",
        role=role[:200],
        company=company[:200],
        link=link,
        snippet=snippet[:240],
        source_query=source_query,
        email=email,
    )


# ---------------------------------------------------------------------------
# find_candidates — full pipeline
# ---------------------------------------------------------------------------

def find_candidates(
    icp_prompt: str,
    *,
    n: int = 8,
    on_event: Optional[EventCallback] = None,
) -> list[Candidate]:
    """Plan → multi-Tavily → URL filter → parse. Returns up to n NAMED people.

    The harness: Codex reads the ICP and produces an intent + persona + three
    diverse, person-anchored queries. Each query is Tavily-searched. Hits are
    deduped and ranked by 'person-likeliness' (linkedin.com/in/, GitHub user
    profiles, scholar/researchgate up; greenhouse/lever/indeed/glassdoor down).
    Codex then parses the top hits with persona context and drops any result
    that does not pin a single named individual. 'Unknown' cards never surface.
    """
    icp_prompt = (icp_prompt or "").strip()
    if not icp_prompt:
        _emit(on_event, "error", "ICP prompt is empty", {"where": "input"})
        return []

    # 1. Plan — intent + persona + 3 queries (single Codex call).
    _emit(on_event, "query_refine_start",
          "Codex understanding intent and planning queries...", {})
    try:
        plan = plan_searches(icp_prompt)
    except Exception as e:
        _emit(on_event, "error", f"Codex planning failed: {e!r}", {"where": "plan"})
        plan = SearchPlan(intent=icp_prompt[:200], persona=icp_prompt[:200],
                          queries=[icp_prompt[:200]])

    if not plan.queries:
        plan = SearchPlan(intent=icp_prompt[:200], persona=icp_prompt[:200],
                          queries=[icp_prompt[:200]])

    pretty_queries = " · ".join(f'"{q}"' for q in plan.queries)
    _emit(on_event, "query_refine_done",
          f"Persona: {plan.persona} | Queries: {pretty_queries}",
          {"intent": plan.intent, "persona": plan.persona,
           "queries": plan.queries, "query": plan.queries[0]})

    # 2. Multi-Tavily — one call per planned query.
    per_query = max(6, (n * 2) // max(len(plan.queries), 1) + 2)
    raw_hits: list[dict[str, Any]] = []
    for q in plan.queries:
        _emit(on_event, "tavily_search_start",
              f'Tavily searching: "{q}"',
              {"query": q, "max_results": per_query})
        try:
            hits = _tavily_search(q, max_results=per_query)
        except Exception as e:
            _emit(on_event, "error", f"Tavily search failed: {e!r}",
                  {"where": "tavily", "query": q})
            continue
        _emit(on_event, "tavily_search_done",
              f"Got {len(hits)} results for \"{q}\"",
              {"count": len(hits), "query": q})
        for h in hits:
            h2 = dict(h)
            h2["_query"] = q
            raw_hits.append(h2)

    if not raw_hits:
        _emit(on_event, "parse_done", "No search results to parse", {"count": 0})
        return []

    # 3. Dedupe by canonical URL, rank by person-likeliness.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for h in raw_hits:
        canon = _url_canonical(h.get("url", ""))
        if canon and canon in seen:
            continue
        seen.add(canon)
        deduped.append(h)

    deduped.sort(
        key=lambda h: _person_score(h.get("url", ""), h.get("title", "")),
        reverse=True,
    )
    take = min(max(n * 2, 8), len(deduped))
    selected = deduped[:take]

    dropped = len(raw_hits) - len(deduped)
    _emit(on_event, "tavily_search_done",
          f"Filtered: {len(raw_hits)} raw → {len(deduped)} unique → top {take} for parsing"
          + (f" ({dropped} dupes)" if dropped else ""),
          {"raw": len(raw_hits), "unique": len(deduped), "selected": take})

    # 4. Parse — one Codex call with persona context, drops non-person hits.
    _emit(on_event, "parse_start",
          f"Codex parsing {take} hits against persona...",
          {"count": take, "persona": plan.persona})
    try:
        candidates = _batch_parse_candidates(
            selected,
            take,
            intent=plan.intent,
            persona=plan.persona,
            source_query=plan.queries[0],
            on_event=on_event,
        )
    except Exception as e:
        _emit(on_event, "error", f"Codex parse failed: {e!r}", {"where": "parse"})
        return []

    candidates = candidates[:n]
    _emit(on_event, "parse_done",
          f"Extracted {len(candidates)} named people (skipped non-person hits)",
          {"count": len(candidates)})
    return candidates


# ---------------------------------------------------------------------------
# generate_email_preview — Codex writes, lint gates, retry once
# ---------------------------------------------------------------------------

def _email_system_prompt() -> str:
    sender_first = os.getenv("SENDER_FIRST", "").strip() or "Sender"

    return (
        "You write short, personalized cold emails on behalf of "
        f"{sender_first}. Do not invent or reference any other identity, "
        "investor, school, or affiliation beyond the first name above.\n\n"
        "Hard rules (the email is auto-rejected if any rule is violated):\n"
        "1. The subject line MUST start with: Scouting Note on \n"
        "2. The body MUST be plain text, <= 110 words.\n"
        f"3. The body MUST contain the literal first name {sender_first!r}.\n"
        "4. NO em dashes (—), NO en dashes (–), NO ellipses (...), NO unicode ellipsis (…).\n"
        "5. NO markdown, NO bullet points (-, *, •), NO code fences.\n"
        "6. NO template artifacts: the characters { } [ ] < > are forbidden anywhere.\n"
        "7. NO last names, school names, organization names that describe the sender, "
        "or co-author / investor names. Sign off with first name only.\n"
        "8. NO blacklisted phrases including: 'caught my attention', 'I came across', "
        "'fascinating', 'impressive', 'innovative', 'state-of-the-art', "
        "'I hope this email finds you well', 'I'd love to learn more', 'quick question', "
        "'this resonated', 'your innovative approach', 'I wanted to reach out', "
        "'grabbed my attention', 'is serious work', 'real bottleneck', 'game changer', "
        "'solves a real pain point'. Use plain language instead.\n"
        "9. Output exactly two lines for the subject prefix:\n"
        "   Subject: Scouting Note on <short anchor (no braces or brackets)>\n"
        "\n"
        "   <body paragraphs>\n"
        "   Best,\n"
        f"   {sender_first}\n"
    )


def _email_user_prompt(candidate: Candidate, email_prompt: str, retry_violations: list[str]) -> str:
    voice = (email_prompt or "").strip()
    voice_clause = f"\nAdditional voice instructions from the user: {voice}\n" if voice else ""
    retry_clause = ""
    if retry_violations:
        retry_clause = (
            "\nThe previous draft failed the lint gate with these violations: "
            f"{retry_violations}. Rewrite to fix every one of them.\n"
        )
    return (
        "Write a cold-email preview for this candidate:\n"
        f"Name: {candidate.name}\n"
        f"Role: {candidate.role or '(unknown)'}\n"
        f"Company: {candidate.company or '(unknown)'}\n"
        f"Source link: {candidate.link}\n"
        f"What we know about them: {candidate.snippet[:240]}\n"
        + voice_clause
        + retry_clause
        + "\nReturn the subject line and body (no preface, no commentary)."
    )


def _split_subject_and_body(text: str) -> tuple[str, str]:
    text = (text or "").strip()
    if not text:
        return "", ""
    lines = text.splitlines()
    # Skip leading blank lines
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return "", ""
    first = lines[i].strip()
    if first.lower().startswith("subject:"):
        subject = first.split(":", 1)[1].strip()
        # Body = everything after the subject line, skipping one blank line.
        rest = lines[i + 1 :]
        while rest and not rest[0].strip():
            rest = rest[1:]
        return subject, "\n".join(rest).rstrip()
    # No explicit Subject line — invent one from candidate context (caller patches).
    return "", text


def generate_email_preview(
    candidate: Candidate,
    email_prompt: str = "",
    *,
    on_event: Optional[EventCallback] = None,
) -> EmailPreview:
    """Generate a personalized cold-email preview, lint-gated, with one retry."""
    if not candidate or not getattr(candidate, "name", None):
        return EmailPreview(subject="", body="", word_count=0, lint_ok=False,
                            lint_violations=["empty candidate"])

    sender_first = os.getenv("SENDER_FIRST", "").strip() or "Sender"

    _emit(on_event, "email_gen_start", f"Drafting email for {candidate.name}...",
          {"name": candidate.name})

    system = _email_system_prompt()
    violations_so_far: list[str] = []
    last_subject = ""
    last_body = ""
    last_word_count = 0
    last_violations: list[str] = ["initial-not-run"]

    # Stable cache key includes sender first name only — changing .env to a
    # different sender produces a fresh draft. No other identity flows in.
    base_seed = (
        f"{candidate.name}\x00{candidate.role}\x00{candidate.company}\x00"
        f"{(candidate.snippet or '')[:240]}\x00{email_prompt or ''}\x00"
        f"{sender_first}"
    )

    for attempt in range(2):
        prompt = _email_user_prompt(
            candidate,
            email_prompt,
            retry_violations=violations_so_far if attempt > 0 else [],
        )
        # Force a fresh codex call on retry so we don't hit the same cached bad draft.
        cache = attempt == 0
        try:
            raw = codex_client.generate(
                prompt=prompt,
                system=system,
                max_tokens=600,
                temperature=0.6 if attempt == 0 else 0.7,
                cache=cache,
            )
        except Exception as e:
            _emit(on_event, "error", f"Codex email-gen failed: {e!r}", {"where": "email"})
            return EmailPreview(subject="", body="", word_count=0, lint_ok=False,
                                lint_violations=[f"codex error: {e!r}"])

        subject, body = _split_subject_and_body(raw)
        if not subject:
            anchor = (candidate.role or candidate.company or candidate.name or "your work")
            anchor = anchor.split("(")[0].strip()[:60]
            subject = f"Scouting Note on {anchor}"
        # Lint rejects {}<>[] anywhere — strip them defensively before lint.
        for ch in "{}[]<>":
            subject = subject.replace(ch, "")
            body = body.replace(ch, "")
        result = lint_email(body, subject=subject)
        last_subject = subject
        last_body = body
        last_word_count = result.word_count
        last_violations = list(result.violations)

        if result.ok:
            _emit(on_event, "email_gen_done",
                  f"Email ready (lint: pass, {result.word_count} words)",
                  {"name": candidate.name, "lint_ok": True, "violations": [],
                   "word_count": result.word_count})
            return EmailPreview(
                subject=subject,
                body=body,
                word_count=result.word_count,
                lint_ok=True,
                lint_violations=[],
            )

        violations_so_far = result.violations
        # Keep cache key consistent across retries — base_seed is unused but kept
        # in case future code wants a hash. Suppress the unused warning:
        _ = base_seed

    _emit(on_event, "email_gen_done",
          f"Email ready (lint: {len(last_violations)} violations)",
          {"name": candidate.name, "lint_ok": False, "violations": last_violations,
           "word_count": last_word_count})
    return EmailPreview(
        subject=last_subject,
        body=last_body,
        word_count=last_word_count,
        lint_ok=False,
        lint_violations=last_violations,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest() -> int:
    events: list[tuple[str, str]] = []

    def cap(t: str, m: str, meta: dict) -> None:
        events.append((t, m))
        print(f"[{t}] {m}")

    cands = find_candidates(
        "senior ML engineers at YC startups in SF working on agents",
        n=3,
        on_event=cap,
    )
    print(f"\nfound {len(cands)} candidates")
    for c in cands:
        print(f"  - {c.name} · {c.role} · {c.link}")

    if not cands:
        return 1

    preview = generate_email_preview(
        cands[0],
        email_prompt="warm tone, mention hiring at a series-A team",
        on_event=cap,
    )
    print(f"\npreview: lint_ok={preview.lint_ok} words={preview.word_count}")
    print(f"Subject: {preview.subject}")
    print(preview.body)
    if not preview.lint_ok:
        print(f"violations: {preview.lint_violations}")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
