# Session Handoff — Auto Bird Dog (GTM_AGIHOUSE)

**Product**: Auto Bird Dog — Tavily-powered talent finder (v3 pivot, 2026-05-09).
**Status**: build complete. Pipeline + dashboard verified end-to-end.
**Event**: AGI House Autonomous GTM Hackathon, 2026-05-09.

If you are picking this up in a fresh conversation: read this file first, then
`README.md`, then `DEMO.md`, then the project plan at
`~/.claude/plans/hackathon-autonomous-gtm-hackathon-with-logical-puppy.md`.

---

## What this project is

A live web-sourcing agent. The user types a free-form ICP, Codex turns it into a
precise web query, Tavily searches the open web, Codex extracts a real person
from each result, and a personalized cold email is generated on demand for any
candidate. Every demo is a fresh user prompt — no roster, no Gmail, no private
data baked in.

**The pitch in one sentence**: type who you want to find → watch the agent search
the web → preview the personalized cold email before any send.

---

## Where things live

| Path | What |
|---|---|
| `adaptive/finder.py` | NEW. Single-shot pipeline: refine → search → batch parse → lazy email. |
| `adaptive/codex_client.py` | gpt-5-codex via responses API + disk cache + 429 fallback to gpt-4o. |
| `adaptive/enrich.py` | Older Tavily wrapper. Kept for compatibility. The new pipeline uses its own free-form Tavily wrapper inside `finder._tavily_search`. |
| `adaptive/lint.py` | Cold-email blacklist (25+ phrases, em/en dashes, ellipses, bullets, template chars, word count, sender first-name mention, "Scouting Note on" subject). Reads `SENDER_FIRST` from env at import (placeholder default). |
| `dashboard/app.py` | Streamlit app: hero strip, four sponsor pills, two free-form chatboxes, three preset buttons, live activity feed, candidate cards, inline email preview, demo-only Send button. |
| `dashboard/theme.css` | Liquid-glass design tokens. textarea overrides, candidate-card styles, inline preview + demo-send banner styles. |
| `tests/test_lint.py` | Standalone pytest cases for the lint gate. |
| `cache/codex/` | SHA-keyed Codex response cache (warm). |
| `cache/tavily/` | SHA-keyed Tavily search cache (warm). |
| `.env.example` | Template — `OPENAI_API_KEY`, `TAVILY_API_KEY`, optional `SENDER_FIRST`, optional `VULTR_URL`. NO other identity fields. |

---

## Verify everything works

```bash
cd GTM_AGIHOUSE && source .venv/bin/activate

# 1. finder module imports clean
python -c "from adaptive.finder import find_candidates, generate_email_preview, refine_query; print('OK')"

# 2. lint module passes
python -m adaptive.lint --selftest
python tests/test_lint.py

# 3. End-to-end pipeline (uses cache if warm)
python -m adaptive.finder

# 4. Dashboard boots clean
pkill -f "streamlit run dashboard"; sleep 1
streamlit run dashboard/app.py --server.headless true --server.port 8501 \
  --browser.gatherUsageStats false > /tmp/streamlit.log 2>&1 &
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/   # 200
grep -iE "error|traceback|exception" /tmp/streamlit.log | head -3 # empty
```

---

## Run the demo

```bash
cd GTM_AGIHOUSE && source .venv/bin/activate
streamlit run dashboard/app.py --server.port 8501 --server.headless true
```

Open `http://localhost:8501/`. Type an ICP into the first chatbox, optionally a
voice instruction into the second, click **Find candidates**, watch the activity
feed populate, click **Preview email** on a card, click **Send** to see the
transparent demo modal.

---

## Architecture decisions

1. **Single Codex call for batch parsing** — packing N Tavily hits into one
   prompt avoids N sequential ~3s round trips.
2. **Lazy email preview** — generated only when the user clicks Preview on a
   candidate card. Keeps the initial Find step snappy. Cached per candidate so
   reruns from session state don't regenerate.
3. **`st.status()` + session-state events list** — no SQLite, no subprocess.
   Pipeline runs in-process, callback writes events into `st.session_state`,
   `st.rerun()` repaints the activity feed.
4. **Email-prompt chatbox is functional, not decorative** — the user's voice
   instructions are piped into the email generator system prompt.
5. **Three quick-fill preset buttons** — VC sourcing, HR for senior eng, Sales
   B2B. Lowers the barrier for venue attendees.
6. **Multi-user safe** — `st.session_state` is per browser session; Codex/Tavily
   caches are global and shared (a feature, not a bug).
7. **Send is a demo modal** — no hidden Gmail call. Honest with judges and
   attendees.
8. **Sponsor pill state** — `live` if the run has hit the API at least once
   (counts come from session state), `ready` for Vultr unless `VULTR_HOSTED`
   env is set.

---

## What is NOT here (intentionally)

- No roster, no production candidate database, no Gmail OAuth, no SQLite event
  store. The previous build had all of these; the v3 pivot dropped them.
- No `orchestrator.py`, `source_bandit.py`, `rubric_learner.py`,
  `reply_simulator.py`, `outcome_loader.py`, `icp_presets.py`,
  `variant_generator.py`, `state/run.sqlite`. These were removed when the
  product pivoted to the free-form chatbox flow.

## Known quirks

- `gpt-5-codex` is a reasoning model — needs `max_output_tokens >= 1500` or
  output is empty. Already handled in `codex_client.py:_call_codex`.
- Email generation cache is keyed by candidate identity + email_prompt + sender
  + principal name. Changing `.env` values forces fresh drafts.
- If Tavily returns mostly job listings (e.g. queries that look like job posts),
  Codex fills the candidate cards with name="Unknown" rather than fabricating.
  Steer ICP prompts toward people, not openings, for cleaner extraction.
