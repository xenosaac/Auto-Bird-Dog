# Auto Bird Dog
### Type a prompt. Watch the agent search the web. Preview the personalized cold email.

**🟢 Live demo**: [http://149.248.39.177](http://149.248.39.177) — hosted on Vultr (Seattle, vc2-1c-1gb)

**Autonomous GTM Hackathon · AGI House × OpenAI Codex × Tavily × Vultr · 2026-05-09**
**Track**: Adaptive Sales Claws (primary) + GTM Intelligence Benchmarks (secondary)

> *In VC slang, a "bird dog" is the human scout who finds deals before partners do.
> Auto Bird Dog is one anyone can run from their browser.*

A **live web-sourcing agent**. The user types a free-form ICP, Codex turns it into a
precise web query, Tavily searches the open web, Codex extracts a real person from
each result, and a personalized cold email is generated on demand for any candidate.
Every demo is a fresh user prompt — no roster, no Gmail, no private data baked in.

## What it does

```
free-form ICP prompt
        │
        ▼
   refine_query   ──►  gpt-5-codex turns the prompt into a precise web query
        │
        ▼
  Tavily search  ──►   open-web hits (LinkedIn, GitHub, company pages, papers)
        │
        ▼
  batch parse    ──►   gpt-5-codex extracts {name, role, company, link, email?} per hit
        │
        ▼
  candidate cards in the dashboard
        │
        └─►  click "Preview email"  ──►  gpt-5-codex writes a lint-gated cold email
        └─►  click "Send"           ──►  transparent demo modal (no real send)
```

Five things the system does:

1. **Codex query refinement** — `gpt-5-codex` (responses API) turns a vague ICP
   description into a precise Google-style search query.
2. **Tavily search** — open-web search with disk-cached results so repeat demos are
   instant and stay under the free-tier credit budget.
3. **Codex batch extraction** — one Codex call parses up to N Tavily hits into
   structured `Candidate` records. Heuristic fallbacks fill in if a result is not
   about a single named person.
4. **Codex email writer** — on demand, generates a personalized cold email that must
   pass a 25+ phrase blacklist + word-count + structural lint gate. One automatic
   retry if lint fails.
5. **Honest demo Send** — the Send button opens a transparent "demo only" modal.
   Sender identity is loaded from `.env` so each operator's own checkout uses their
   own name; the committed code never carries personal data.

## Quick start

```bash
cd GTM_AGIHOUSE
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # paste your OPENAI_API_KEY and TAVILY_API_KEY

# Run the finder pipeline once from the CLI
python -m adaptive.finder

# Open the live dashboard
streamlit run dashboard/app.py --server.port 8501
```

## Verification gates

```bash
.venv/bin/python -m adaptive.lint --selftest                              # lint OK
.venv/bin/python -c "from adaptive.codex_client import probe; probe()"    # codex OK
.venv/bin/python -m adaptive.finder                                       # end-to-end
.venv/bin/streamlit run dashboard/app.py --server.headless true           # HTTP 200
```

## Sponsor alignment

- **Tavily** (Gold, marquee) — every ICP becomes one Tavily search; results stream
  into the dashboard activity feed in real time. Disk-cached so warm runs are ~free.
- **OpenAI Codex** (Platinum) — `gpt-5-codex` via the `responses` API drives query
  refinement, batch extraction, and email generation. Auto-falls-back to `gpt-4o`
  on rate-limit (429). Cached.
- **Vultr** (Platinum) — single-process Streamlit app, ready to drop on a `vc2-1c-1gb`
  VM. Set `VULTR_URL` in `.env` to flip the sponsor pill.
- **HackerSquad** (Bronze) — community partner; we will publish post-hackathon.

## Honest framing

- **No private data is shipped.** `.env` carries the operator's identity for the
  email generator; the committed code only knows about placeholder defaults.
- **Send is a demo modal.** No SMTP, no Gmail call, no surprise outbound. This is
  on purpose so anyone at the venue can try the agent without using the operator's
  mailbox.
- **Cache is the demo.** First-time uncached pipeline runs take ~10–30s for ~3
  Codex calls plus one Tavily call. Cached re-runs (same prompt) replay in <2s,
  which is what the live audience usually sees.

## License

Hackathon prototype.
