"""Auto Bird Dog — Tavily-powered talent finder dashboard.

Two free-form chatboxes (ICP + email voice) → live Codex+Tavily pipeline →
candidate cards with a lazy email preview and a transparent demo-only Send.
No SQLite, no roster, no Gmail — every demo is a fresh user prompt.
"""
from __future__ import annotations

import html as _html
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

# Ensure project root on sys.path so `adaptive.*` imports work whether Streamlit
# is launched from dashboard/ or repo root.
_ROOT_FOR_IMPORTS = Path(__file__).resolve().parent.parent
if str(_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORTS))

import streamlit as st

from adaptive import finder
from adaptive.finder import Candidate, EmailPreview

CSS_PATH = Path(__file__).resolve().parent / "theme.css"
QR_PATH = Path(__file__).resolve().parent / "qr_lan.png"


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

_DEFAULT_STATE: dict = {
    "icp_prompt": "",
    "email_prompt": "",
    "events": [],            # list[{type, msg, meta, ts}]
    "candidates": [],        # list[Candidate]
    "email_cache": {},       # {candidate_idx: EmailPreview}
    "sent_idx": set(),       # {candidate_idx} that have been demo-sent
    "last_search_query": "",
    "tavily_call_count": 0,
    "codex_call_count": 0,
    "ran_once": False,
}


def _ensure_state() -> None:
    for k, v in _DEFAULT_STATE.items():
        if k not in st.session_state:
            # Use copies so each session gets its own list/dict/set.
            st.session_state[k] = v.copy() if isinstance(v, (list, dict, set)) else v


# ---------------------------------------------------------------------------
# CSS / chrome
# ---------------------------------------------------------------------------

def inject_css() -> None:
    try:
        css = CSS_PATH.read_text()
    except FileNotFoundError:
        css = ""
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Hero strip
# ---------------------------------------------------------------------------

def render_hero() -> None:
    host_label = os.getenv("VULTR_URL", "").strip() or "localhost:8501"
    left, right = st.columns([3, 1], gap="small")
    with left:
        st.markdown(
            '<div style="margin:0 0 4px 0;">'
            '<h1 class="hero-title">🐕 Auto Bird Dog</h1>'
            '<div class="hero-sub">Find anyone. See the cold email before you send it.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f'<div style="text-align:right;padding-top:24px;">'
            f'<div class="hero-powered">Hosted at</div>'
            f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:12px;color:var(--text-secondary);">'
            f'{_html.escape(host_label)}</div></div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Sponsor pills
# ---------------------------------------------------------------------------

def _pill_html(name: str, tier: str, sub: str, status: str) -> str:
    # `tier` arg kept for API stability; deliberately not rendered (user request).
    cls = "sponsor-pill live" if status == "live" else "sponsor-pill ready"
    dot = "dot live" if status == "live" else "dot ready"
    return (
        f'<div class="{cls}">'
        f'<div class="row1"><span class="{dot}"></span>'
        f'<span class="name">{_html.escape(name)}</span></div>'
        f'<div class="sub">{_html.escape(sub)}</div>'
        f'</div>'
    )


def render_sponsor_pills() -> None:
    codex_n = int(st.session_state.get("codex_call_count", 0))
    tavily_n = int(st.session_state.get("tavily_call_count", 0))
    vultr_status = "live" if os.getenv("VULTR_HOSTED", "").strip() else "ready"
    vultr_sub = os.getenv("VULTR_URL", "").strip() or "deployment-ready"

    pills = [
        _pill_html("OpenAI Codex", "Platinum", f"live · {codex_n} calls", "live"),
        _pill_html("Tavily", "Gold", f"live · {tavily_n} searches", "live"),
        _pill_html("Vultr", "Platinum", vultr_sub, vultr_status),
        _pill_html("HackerSquad", "Bronze", "community partner", "ready"),
    ]
    cols = st.columns(4, gap="small")
    for col, html in zip(cols, pills):
        with col:
            st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Quick-fill presets
# ---------------------------------------------------------------------------

_PRESETS: list[tuple[str, str, str, str]] = [
    (
        "🎯 VC sourcing founders",
        "vc",
        "Early-stage technical founders in the SF Bay Area working on AI agents or developer tools",
        "Casual but professional. Lead with a specific observation about their work. 80 words.",
    ),
    (
        "🔧 HR for senior engineers",
        "hr",
        "Senior backend engineers with distributed-systems experience open to series-A roles",
        "Warm. Mention we are hiring on a small infra team. 80 words. No fluff.",
    ),
    (
        "💼 Sales for B2B buyers",
        "sales",
        "Heads of revenue operations at series-B SaaS companies in North America",
        "Concise. Lead with one number that hints at the pain. Ask for a 15-minute call.",
    ),
]


def _apply_preset(icp_text: str, email_text: str) -> None:
    """Streamlit on_click handler — runs before the next rerun, so when the
    text_area widgets are rebuilt they read these freshly written values."""
    st.session_state["icp_input"] = icp_text
    st.session_state["email_prompt_input_widget"] = email_text
    st.session_state["icp_prompt"] = icp_text
    st.session_state["email_prompt"] = email_text


def render_inputs() -> tuple[str, str]:
    # Streamlit renders each st.markdown() in its own DOM block, so wrapping
    # widgets in a <div class="glass-card"> renders as an empty visible card.
    # Style each input section directly via theme.css instead.
    st.markdown('<div class="input-label">Who do you want to find?</div>', unsafe_allow_html=True)
    icp = st.text_area(
        "icp_prompt_input",
        value=st.session_state.get("icp_prompt", ""),
        placeholder="e.g. Senior ML engineers at YC startups in SF working on agents",
        height=90,
        label_visibility="collapsed",
        key="icp_input",
    )

    st.markdown('<div class="input-label" style="margin-top:12px;">How should the email sound? <span class="input-label-aux">(optional)</span></div>', unsafe_allow_html=True)
    email_prompt = st.text_area(
        "email_prompt_input",
        value=st.session_state.get("email_prompt", ""),
        placeholder="e.g. Warm. Mention hiring at a series-A team. 80 words max.",
        height=70,
        label_visibility="collapsed",
        key="email_prompt_input_widget",
    )

    # Preset buttons
    st.markdown('<div style="height:6px;"></div>', unsafe_allow_html=True)
    p1, p2, p3 = st.columns(3, gap="small")
    cols = [p1, p2, p3]
    for col, (label, key, icp_text, email_text) in zip(cols, _PRESETS):
        with col:
            # on_click fires BEFORE the next rerun, so the text_area below
            # picks up the new value cleanly. Writing inline here would crash
            # because the widget with key="icp_input" is already instantiated.
            st.button(
                label,
                key=f"preset-{key}",
                use_container_width=True,
                on_click=_apply_preset,
                args=(icp_text, email_text),
            )

    # Action row
    st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
    a1, a2 = st.columns([3, 1], gap="small")
    with a1:
        find_clicked = st.button(
            "▶ Find candidates",
            type="primary",
            disabled=not icp.strip(),
            use_container_width=True,
            key="btn-find",
        )
    with a2:
        if st.button("↻ Reset", use_container_width=True, key="btn-reset"):
            _reset_session()
            st.rerun()

    if find_clicked and icp.strip():
        run_pipeline(icp.strip(), email_prompt.strip())

    return icp, email_prompt


def _reset_session() -> None:
    for k in list(_DEFAULT_STATE.keys()):
        v = _DEFAULT_STATE[k]
        st.session_state[k] = v.copy() if isinstance(v, (list, dict)) else v


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(icp: str, email_prompt: str) -> None:
    st.session_state["icp_prompt"] = icp
    st.session_state["email_prompt"] = email_prompt
    st.session_state["events"] = []
    st.session_state["candidates"] = []
    st.session_state["email_cache"] = {}
    st.session_state["sent_idx"] = set()

    phase_labels = {
        "plan": "🤖 Step 1/4 · Planning queries",
        "search": "🔍 Step 2/4 · Searching the open web",
        "filter": "🧹 Step 3/4 · Ranking results by person-likeliness",
        "parse": "🧠 Step 4/4 · Extracting named people",
    }

    with st.status("🤖 Starting pipeline...", expanded=True) as status:

        def on_event(t: str, m: str, meta: dict) -> None:
            ts = time.strftime("%H:%M:%S", time.localtime())
            # Persist for activity feed + sponsor counters.
            st.session_state["events"].append(
                {"type": t, "msg": m, "meta": meta, "ts": time.time()})
            if t == "tavily_search_start":
                st.session_state["tavily_call_count"] = int(
                    st.session_state.get("tavily_call_count", 0)) + 1
            if t in ("query_refine_start", "parse_start", "email_gen_start"):
                st.session_state["codex_call_count"] = int(
                    st.session_state.get("codex_call_count", 0)) + 1
            if t == "query_refine_done" and meta.get("query"):
                st.session_state["last_search_query"] = meta["query"]

            # Live narration in the open status box.
            if t == "query_refine_start":
                status.update(label=phase_labels["plan"])
                status.markdown(
                    f"`{ts}` 🤖 **Codex** is reading your ICP and planning the search...")
            elif t == "query_refine_done":
                persona = meta.get("persona") or ""
                queries = meta.get("queries") or []
                if persona:
                    status.markdown(f"`{ts}` 💡 **Persona understood** — _{persona}_")
                if queries:
                    qs = "\n".join(f"   • `{q}`" for q in queries)
                    status.markdown(
                        f"`{ts}` 🗺️ **Plan** — {len(queries)} diverse queries:\n{qs}")
            elif t == "tavily_search_start":
                status.update(label=phase_labels["search"])
                status.markdown(
                    f"`{ts}` 🔍 **Tavily** searching `{meta.get('query', '')}`")
            elif t == "tavily_search_done":
                if "Filtered" in m:
                    status.update(label=phase_labels["filter"])
                    status.markdown(f"`{ts}` 🧹 {m}")
                else:
                    status.markdown(f"`{ts}`    ↳ {m}")
            elif t == "parse_start":
                status.update(label=phase_labels["parse"])
                status.markdown(
                    f"`{ts}` 🧠 **Codex** extracting individuals from "
                    f"{meta.get('count', '?')} hits with persona context...")
            elif t == "candidate_extracted":
                if meta.get("skipped"):
                    status.markdown(f"`{ts}`    ✗ skipped non-person hit")
                else:
                    name = meta.get("name") or ""
                    role = meta.get("role") or ""
                    suffix = f" · {role}" if role else ""
                    status.markdown(f"`{ts}`    ✓ **{name}**{suffix}")
            elif t == "parse_done":
                status.markdown(f"`{ts}` ✅ {m}")
            elif t == "error":
                status.markdown(f"`{ts}` ⚠️ **Error** — {m}")

        try:
            cands = finder.find_candidates(icp, n=8, on_event=on_event)
            st.session_state["candidates"] = cands
            st.session_state["ran_once"] = True
            if cands:
                status.update(
                    label=f"✅ Done — {len(cands)} named candidates",
                    state="complete",
                    expanded=False,
                )
            else:
                status.update(
                    label="⚠️ No named candidates returned — try a more specific ICP",
                    state="error",
                )
        except Exception as e:
            on_event("error", f"Pipeline failed: {e!r}", {})
            status.update(label="⚠️ Pipeline failed", state="error")

    # No st.rerun() — let render_candidates() in main() paint below the status
    # box in the same script run, so the status stays visible alongside cards.


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------

_EVENT_ICONS = {
    "query_refine_start": "🤖",
    "query_refine_done": "💡",
    "tavily_search_start": "🔍",
    "tavily_search_done": "🔍",
    "parse_start": "🧠",
    "candidate_extracted": "✓",
    "parse_done": "✅",
    "email_gen_start": "✍️",
    "email_gen_done": "✓",
    "error": "⚠",
}


def render_activity_feed() -> None:
    events = st.session_state.get("events", [])
    st.markdown('<div class="section-title">Live activity</div>', unsafe_allow_html=True)
    if not events:
        st.markdown(
            '<div style="color:var(--text-muted);padding:18px 0;font-size:14px;">'
            'Type an ICP and click <b>Find candidates</b> to start streaming events here.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    parts = ['<div class="activity-feed">']
    # Show oldest at top so the user reads the pipeline in order.
    for ev in events[-80:]:
        icon = _EVENT_ICONS.get(ev["type"], "•")
        ts = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
        msg = _html.escape(ev["msg"])
        parts.append(
            f'<div class="activity-row">'
            f'<span class="activity-icon">{icon}</span>'
            f'<span class="activity-ts">{ts}</span>'
            f'<span class="activity-msg">{msg}</span>'
            f'</div>'
        )
    parts.append('</div>')
    st.markdown("".join(parts), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Candidate cards
# ---------------------------------------------------------------------------

def _send_email_for(idx: int) -> None:
    """on_click handler for the demo Send button — flips the sent flag."""
    sent: set = st.session_state.setdefault("sent_idx", set())
    sent.add(idx)


def _redraft_for(idx: int) -> None:
    """on_click handler for Re-draft — drops the cached preview and sent flag."""
    cache = st.session_state.setdefault("email_cache", {})
    cache.pop(idx, None)
    sent: set = st.session_state.setdefault("sent_idx", set())
    sent.discard(idx)


def _render_candidate_card(idx: int, c: Candidate) -> None:
    name = _html.escape(c.name or "Unknown")
    role = _html.escape(c.role or "—")
    company = _html.escape(c.company or "")
    link = c.link or ""
    link_label = _html.escape(link.replace("https://", "").replace("http://", "")[:64])
    snippet = _html.escape((c.snippet or "")[:180])

    st.markdown(
        f'<div class="candidate-card">'
        f'<div class="cand-name">{name}</div>'
        f'<div class="cand-role">{role}'
        + (f' · <span class="cand-company">{company}</span>' if company else '')
        + '</div>'
        + (f'<a class="cand-link" href="{_html.escape(link)}" target="_blank" rel="noopener">{link_label}</a>' if link else '')
        + f'<div class="cand-snippet">{snippet}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    cache: dict[int, EmailPreview] = st.session_state.get("email_cache", {})
    sent: set = st.session_state.get("sent_idx", set())
    preview = cache.get(idx)

    if preview is None:
        # State: fresh — only the Draft button. Run inline so st.spinner is
        # visible during the 3-5s Codex call (on_click would freeze the UI
        # silently with no feedback).
        if st.button("✍️ Draft email", key=f"draft-{idx}", use_container_width=True):
            def on_event(t: str, m: str, meta: dict) -> None:
                st.session_state["events"].append(
                    {"type": t, "msg": m, "meta": meta, "ts": time.time()})
                if t == "email_gen_start":
                    st.session_state["codex_call_count"] = int(
                        st.session_state.get("codex_call_count", 0)) + 1

            with st.spinner(f"Codex drafting email for {c.name}..."):
                new_preview = finder.generate_email_preview(
                    c, st.session_state.get("email_prompt", ""), on_event=on_event)
            cache[idx] = new_preview
            st.rerun()
        return

    # State: drafted (and possibly sent) — render inline preview.
    badge_cls = "badge badge-meeting" if preview.lint_ok else "badge badge-warm"
    badge_text = "lint pass" if preview.lint_ok else f"{len(preview.lint_violations)} lint violations"
    st.markdown(
        f'<div class="email-preview-inline">'
        f'<div class="email-pre-meta">'
        f'<span class="modal-meta-item">Subject:</span> '
        f'<span class="modal-subject">{_html.escape(preview.subject)}</span>'
        f' <span class="{badge_cls}">{badge_text}</span>'
        f' <span class="modal-meta-item">· {preview.word_count} words</span>'
        f'</div>'
        f'<pre class="email-pre-body">{_html.escape(preview.body)}</pre>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if not preview.lint_ok and preview.lint_violations:
        with st.expander("Lint violations", expanded=False):
            for v in preview.lint_violations:
                st.markdown(f"- {v}")

    if idx in sent:
        target = c.email or "[email not extracted]"
        st.markdown(
            f'<div class="demo-send-banner">'
            f'<span class="demo-send-icon">✓</span> '
            f'<b>Demo sent</b> — would deliver to '
            f'<code>{_html.escape(target)}</code>. No real email was sent.'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.button(
            "↻ Re-draft",
            key=f"redraft-{idx}",
            use_container_width=True,
            on_click=_redraft_for,
            args=(idx,),
        )
    else:
        b1, b2 = st.columns([2, 1], gap="small")
        with b1:
            st.button(
                "📤 Send (demo)",
                key=f"send-{idx}",
                type="primary",
                use_container_width=True,
                on_click=_send_email_for,
                args=(idx,),
            )
        with b2:
            st.button(
                "↻ Re-draft",
                key=f"redraft-{idx}",
                use_container_width=True,
                on_click=_redraft_for,
                args=(idx,),
            )


def render_candidates() -> None:
    cands: list[Candidate] = st.session_state.get("candidates", [])
    st.markdown('<div class="section-title">Candidates found</div>', unsafe_allow_html=True)
    if not cands:
        if st.session_state.get("ran_once"):
            st.markdown(
                '<div style="color:var(--text-muted);padding:18px 0;font-size:14px;">'
                'No candidates extracted from this query. Try a more specific ICP, or one of the presets.'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            _render_ghost_candidates()
        return
    for idx, c in enumerate(cands):
        _render_candidate_card(idx, c)


def _render_ghost_candidates() -> None:
    st.markdown(
        '<div style="opacity:0.55;display:flex;flex-direction:column;gap:10px;">'
        '<div class="candidate-card ghost">'
        '<div class="cand-name">Maya Chen</div>'
        '<div class="cand-role">ML Engineer · Hugging Face</div>'
        '<div class="cand-snippet">— ghost preview · click <b>Find candidates</b> above —</div>'
        '</div>'
        '<div class="candidate-card ghost">'
        '<div class="cand-name">Alex Patel</div>'
        '<div class="cand-role">Founder · stealth agents startup</div>'
        '<div class="cand-snippet">— ghost preview —</div>'
        '</div>'
        '<div class="candidate-card ghost">'
        '<div class="cand-name">Priya Rao</div>'
        '<div class="cand-role">Researcher · NeurIPS 2024 author</div>'
        '<div class="cand-snippet">— ghost preview —</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Auto Bird Dog",
        layout="wide",
        initial_sidebar_state="collapsed",
        menu_items={"Get help": None, "Report a bug": None, "About": None},
    )
    inject_css()
    _ensure_state()

    render_hero()
    render_sponsor_pills()
    st.markdown('<div style="height:14px;"></div>', unsafe_allow_html=True)

    render_inputs()

    # Live activity + candidates side-by-side
    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)
    left, right = st.columns([1.5, 1], gap="medium")
    with left:
        render_activity_feed()
    with right:
        render_candidates()


if __name__ == "__main__":
    main()
