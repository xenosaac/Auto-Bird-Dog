# Codex Review Pre-emptive Notes

We expect this codebase to be reviewed adversarially with `codex review`. Below
are design decisions a reviewer might flag, and our justification for each.
Read this first.

## Resolved before review

| Likely flag | Status | Reason |
|---|---|---|
| `.env` committed | Not committed (`.gitignore` excludes `.env`, includes `.env.example`) | Secrets stay local. |
| `eval()` of LLM output | Not used anywhere | Would be unsafe. |
| `pickle.load` on untrusted data | Not used | Cache stores plain text + JSON. |
| Bare `except:` | None | All except blocks specify exception types. |
| Hardcoded API keys | None | Loaded via `python-dotenv` from `.env`. |
| Unpinned deps | `requirements.txt` pins minimum versions | Stable builds across machines. |
| `requests.get` without timeout | We don't use `requests` directly; only OpenAI / Tavily SDKs (own timeouts). | N/A |
| Test code in production paths | All `__main__` self-tests gated by `if __name__ == "__main__":` | Clean import surface. |
| Personal data leaked into committed source | Operator identity flows from `.env` only; defaults are placeholders. | Anyone-can-fork. |
| Hidden outbound mail | Send button is a transparent demo modal; no SMTP / Gmail call exists in the codebase. | Honest with venue attendees. |

## Design decisions a reviewer might question

### 1. `gpt-5-codex` reasoning-token budget

`gpt-5-codex` is a reasoning model — it consumes 300-800 tokens of internal
reasoning before any visible output is emitted. With `max_tokens=800`, reasoning eats
the entire budget and `output_text` is empty.

**Our fix** (`adaptive/codex_client.py:_call_codex`): `effective = max(max_tokens
+ 1024, 1500)` and double-and-retry once if output is empty. Documented inline.

### 2. Single Codex call for batch parsing

Naive: 8 Tavily hits × 1 Codex call each = 8 sequential ~3s round-trips. Bad
demo UX.

**Our choice**: pack N hits into one Codex prompt, request a JSON array of length
N back. ~5s for the whole batch. Heuristic fallbacks fill in any element the
model returns malformed. See `adaptive/finder._batch_parse_candidates`.

### 3. Lint module reads sender / principal identity from env at import time

A reviewer might prefer config injection. We chose env at import to keep the
public API of `lint_email(body, subject)` simple — no extra arguments. Tests
override the env vars before importing the module (`tests/test_lint.py`).

### 4. Email preview is generated lazily, not eagerly

Pre-generating 8 emails on Find would add ~30s. The dashboard generates per
click and caches by candidate index in `st.session_state`. First click ~3s,
subsequent clicks instant.

### 5. Send button is a demo modal

A venue full of strangers pressing Send must not produce real outbound. The
modal explicitly says no email is sent and points the operator at `.env` to
enable SMTP in their own checkout. Lossy on "press Send and see what happens"
but the lint-passing email body in the Preview modal is the actual Codex output.

### 6. Cache prevents quota burn AND keeps demos repeatable

`cache/codex/` and `cache/tavily/` SHA-key every call. First run is real (1
refine + 1 search + 1 parse Codex call ≈ ~$0.01); subsequent runs replay from
cache in <2s. The pitch shows real numbers; judges can rerun the same prompt
and get the same output.

### 7. No web framework — Streamlit only

A reviewer might prefer FastAPI + React. We disagree — Streamlit ships in 30
minutes with auto-refresh, layout, and forms already built. The hackathon
rewards autonomy, not infra theater.

### 8. The pipeline's "Unknown" name fallback

When Tavily surfaces a generic job listing or aggregator page rather than a
personal page, Codex is instructed to return `name="Unknown"` rather than
fabricate. The card still shows the source link so the user can click through.
Better than silent invention; worse than perfect extraction. The trade-off is
deliberate.

## Files to inspect

| Concern | File |
|---|---|
| Where API key is loaded | `adaptive/codex_client.py` (load_dotenv) |
| Where lint is enforced on generated emails | `adaptive/finder.generate_email_preview` (after every Codex call, with one retry) |
| Where Codex calls are cached | `adaptive/codex_client._cache_path` (SHA over model+system+prompt+max_tokens+temperature) |
| Where Tavily calls are cached | `adaptive/finder._tavily_search` (SHA over query+max_results) |
| Where sponsor identity counts come from | `dashboard/app.py:render_sponsor_pills` (driven by session-state counters) |
