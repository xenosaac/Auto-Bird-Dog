# Auto Bird Dog — 3-Minute Pitch Script

## Pre-pitch setup (5 min before)

1. On the demo laptop:
   ```bash
   cd GTM_AGIHOUSE && source .venv/bin/activate
   streamlit run dashboard/app.py --server.port 8501 --server.headless true
   ```
2. Confirm the dashboard loads at `http://localhost:8501/` and the four sponsor
   pills render (Codex/Tavily live, Vultr/HackerSquad ready or live depending
   on deploy).
3. Have a fresh ICP prompt in mind. Avoid topics covered by the warmed cache so
   the audience watches a real Codex+Tavily round-trip rather than a cache hit.

## The pitch

### 0:00 — 0:30 — Problem

> "In VC slang, a *bird dog* is the human scout who finds deals before partners
> see them. We built **Auto Bird Dog** — a bird dog anyone can run from their
> browser."
>
> "Most GTM agents come with a built-in roster — they only impress with the
> operator's own data. We wanted the opposite: a fresh user prompt every demo,
> live web search, real candidates, real personalized emails."

### 0:30 — 1:30 — Live demo

> "I'll type a prompt. You watch."
>
> [Type ICP into the first chatbox] _e.g. "Heads of revenue ops at series-B SaaS
> in North America"._
>
> [Optionally type a voice instruction] _e.g. "Concise. Lead with one number that
> hints at the pain. 80 words."_
>
> [Click **Find candidates**]
>
> "Codex just refined the prompt into a precise web query. Tavily searched the
> open web. Codex parsed the hits into eight candidate cards in one call.
> About three Codex calls plus one Tavily call total."
>
> [Click **Preview email** on a card]
>
> "Codex wrote a personalized cold email referencing what we know about this
> person. Our 25-phrase lint gate rejects AI slop — em dashes, 'caught my
> attention', 'fascinating' — it has to come back clean or we retry once."
>
> [Click **Send**]
>
> "Send is a transparent demo modal. No SMTP behind it. That's on purpose so
> anyone at the venue can try the agent without burning the operator's mailbox.
> When you check out the repo, you set `.env` to your own identity and SMTP."

### 1:30 — 2:30 — Why this is honest GTM

> "Three things we did NOT do, on purpose."
>
> "**One**: no roster. Every run is the user's prompt. The agent has to perform
> on cold queries — that's the actual product, not a curated showcase."
>
> "**Two**: no fake send. We don't auto-email anyone during the demo. The
> production path uses the same Codex generator with a lint gate; what changes is
> the SMTP credentials in `.env`."
>
> "**Three**: no private data in the repo. The committed code knows only
> placeholder names. Operator identity comes from `.env`. Anyone forks this and
> runs their own instance the same way."

### 2:30 — 3:00 — Sponsor alignment + ask

> "Tavily is the marquee technology — open web search is what makes the agent
> not a roster lookup. Codex is doing all the language work: query refinement,
> entity extraction, email writing. Vultr hosts the single Streamlit process.
> HackerSquad gets a community submission post-hackathon."
>
> "VC, recruiter, sales rep — type your ICP. The agent finds the people. You
> see the email before it goes out. That's the whole product."

[Q&A]

## Anticipated questions

**Q: How accurate is the candidate extraction?**
A: When Tavily surfaces personal pages (LinkedIn profile, GitHub bio, company
about page), Codex extracts the right person consistently. When the top hits are
job listings or generic landing pages, the card falls back to "Unknown" rather
than fabricating a name. We over-sample (request 2N hits, take top N) and the
user can see the source link on every card.

**Q: What does the lint gate actually catch?**
A: 25+ blacklisted phrases ("caught my attention", "I came across", "fascinating",
"impressive", "innovative", "I hope this finds you well", etc.), em/en dashes,
ellipses, bullet points, code fences, template artifact characters (`{}[]<>`),
word-count overflow (>110), missing sender first-name mention, missing
"Scouting Note on" subject prefix.

**Q: What happens if Codex 429s?**
A: `adaptive/codex_client.py` falls back to `gpt-4o` automatically. The fallback
is tested.

**Q: Real send?**
A: Set SMTP or Gmail in `.env` and replace the demo modal with a one-line send
call. We deliberately did not ship that path so a venue full of strangers could
try the agent without anyone getting accidental email.

**Q: Hosting?**
A: Single Streamlit process, no exotic infra. `VULTR_URL` env var flips the
sponsor pill once a deploy is live.

## Backup if something breaks live

- **Codex 403 / rate limit**: cached prompts replay instantly. If you stay on a
  prompt the cache has seen, the demo is offline-replay safe.
- **Streamlit crashes**: re-run `streamlit run dashboard/app.py` and refresh.
- **Tavily down**: pipeline emits an error event, returns no candidates. Pivot
  to talking through the architecture diagram in `README.md`.
- **Wifi drops**: warmed cache (`cache/codex/`, `cache/tavily/`) replays the
  last few prompts without internet.
