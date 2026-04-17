# Hermes Autoblog

Adversarial SEO blog generator. Two different-family LLMs drive the loop —
**GPT-5 writes, Claude Sonnet 4 reviews** — iterating until the post passes a
7-dimension SEO rubric. Topic & keyword discovery is automated via **GPT-5
brainstorm → Google Autocomplete expansion → DataForSEO volume validation →
Sonnet picker**, with all API responses cached in SQLite to keep paid-API costs
near zero across runs.

Built for an "Omegle alternatives" niche (app: Someone Somewhere,
[somesome.co](https://somesome.co)) but the architecture is niche-agnostic —
only `BRAND_CONTEXT` in `discover.py` and the content pillars need to change to
repurpose it.

## Why two models

One model reviewing its own output has blind spots. A writer and a reviewer
from **different model families** (OpenAI vs Anthropic) produce genuinely
adversarial critique — the reviewer flags weaknesses the writer didn't
anticipate, the writer revises, repeat.

## What one run produces

- A **1,500–2,500 word** markdown blog post with H1/H2/H3 structure, intro hook,
  key takeaways section, and meta description.
- Keywords integrated naturally into H1, the first 100 words, at least two H2s,
  and the conclusion.
- A `.review.json` containing the final rubric scores (7 dimensions, each
  1–10) — pass threshold is ≥8 across all dimensions with a valid word count.

## Architecture

```
┌─ discover.py ─────────────────────────────────────────────────────┐
│  1. GPT-5 brainstorms N topic candidates given niche + brand      │
│  2. For each seed keyword, Google Autocomplete pulls long-tails   │
│  3. DataForSEO validates volume/competition (cached 30d)          │
│  4. Sonnet scores each candidate, assigns 3-5 final keywords      │
│  5. Upserts into store.db, dedupe on normalized title             │
└───────────────────────────────────────────────────────────────────┘
                             ↓
┌─ store.db (SQLite, persistent) ───────────────────────────────────┐
│  keywords        (kw, volume, competition, cpc, validated_at)     │
│  topics          (title, pillar, score, status, output_path)      │
│  topic_keywords  (many-to-many)                                   │
└───────────────────────────────────────────────────────────────────┘
                             ↓
┌─ auto.py ─────────────────────────────────────────────────────────┐
│  Pops highest-scored queued topic (or a specified --topic-id)     │
│  Invokes blog.py as subprocess                                    │
│  Marks topic as written, records output path                      │
└───────────────────────────────────────────────────────────────────┘
                             ↓
┌─ blog.py ─────────────────────────────────────────────────────────┐
│  Round 1:  GPT-5 writer drafts                                    │
│  Round 1:  Sonnet reviewer scores 7 rubric dimensions + word-ct   │
│  Round N:  Writer revises using reviewer feedback                 │
│  Exits when all scores ≥ 8 AND word_count_ok, or at --rounds      │
│  Saves output/<timestamp>-<slug>.md + .review.json                │
└───────────────────────────────────────────────────────────────────┘
```

## Required credentials

Stored in `.env` (gitignored — never commit). Copy `.env.example` and fill in:

| Variable | Purpose | Where to get it |
|---|---|---|
| `OPENAI_API_KEY` | GPT-5 writer + brainstormer | [platform.openai.com](https://platform.openai.com/) |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | Claude Sonnet 4 reviewer via Bedrock | AWS IAM with `bedrock:InvokeModel` on the model ID below |
| `BEDROCK_MODEL_ID` | Cross-region Bedrock inference profile | Default: `us.anthropic.claude-sonnet-4-20250514-v1:0` |
| `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | Real search volume validation | [app.dataforseo.com](https://app.dataforseo.com/) — account must be **verified** before API works (40104 error otherwise) |
| `WRITER_MODEL` | OpenAI model for writer (default `gpt-5`) | — |

Google Autocomplete uses no auth — the public `suggestqueries.google.com`
endpoint.

## Setup

```bash
git clone https://github.com/AustinHatem/hermes-autoblog.git
cd hermes-autoblog
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in credentials
```

Python 3.11+ recommended.

## Daily workflow

### 1. Seed the topic queue (run whenever you want more angles)

Two modes — use both, they complement each other:

**Broad niche mode** — exploratory, spreads across all content pillars. Good for
the first seeding of a niche.

```bash
python discover.py --niche "omegle alternatives, random video chat" --brainstorm 20
```

**Cluster mode** — saturates one high-volume keyword with N genuinely distinct
angles (different demographics, years, feature angles, listicle variants, etc.).
Use this whenever you want to build topical authority around a specific
money keyword.

```bash
python discover.py --cluster "omegle alternatives" --brainstorm 20
python discover.py --cluster "apps like monkey" --brainstorm 15
python discover.py --cluster "chatroulette alternatives" --brainstorm 10
```

Cluster mode's brainstorm prompt enumerates angle dimensions (year /
demographic / platform / price / use-case / feature / region / listicle size /
comparison / problem-framing / safety lens / question-framing) and GPT-5 mixes
them to produce distinct titles that all target the primary keyword.

What happens in both modes:
- GPT-5 brainstorms N candidate topics with seed keywords
- For each seed, Google Autocomplete pulls \~20 real long-tails
- DataForSEO batches all fresh (uncached, >30 days old) keywords into **one**
  call and fetches volume/competition/cpc
- Sonnet scores all candidates 0–10 and assigns 3–5 final keywords per topic
- Topics upsert into `store.db` — **exact-title duplicates are silently
  skipped**, letting you run discover many times without polluting the queue

Repeated keywords skip DFS (cached). Repeated titles skip insert. Run often.

### 2. Inspect the queue

```bash
python auto.py --list           # only queued
python auto.py --list --all     # include already-written
```

Prints each topic with id, score, pillar, keywords, rationale.

### 3. Write one blog

```bash
python auto.py                      # writes the highest-scored queued topic
python auto.py --topic-id 7         # writes a specific topic
python auto.py --rounds 4           # allow more writer/reviewer iterations
```

- Invokes `blog.py` with the topic + keywords
- Saves `output/<timestamp>-<slug>.md` + `<timestamp>-<slug>.review.json`
- Marks topic `status='written'` with `output_path` set
- Will never re-write the same topic (it's no longer in `queued`)

**Default is one blog per run.** Batch mode is deliberately not a default —
write, review, then write the next one.

## Repurposing to a different niche

Change two things:

1. **`BRAND_CONTEXT`** at the top of [`discover.py`](discover.py) — pitch,
   features, audience, positioning.
2. **Content pillars** in the same file (listed in `BRAND_CONTEXT` and
   referenced by `content_pillar` in the brainstorm JSON schema). Optionally
   update the brainstorm prompt's "Rules" section.

Everything else (writer/reviewer loop, rubric, keyword validation, scoring) is
niche-agnostic.

## Cost controls

- **All DataForSEO responses cached 30 days** in `.cache.db` (keyed by
  `keyword + location + language`). Re-running discovery on overlapping seeds
  is free after the first call.
- **Google Autocomplete cached 7 days**.
- **Hard cap**: max 2 DFS calls per run (`MAX_CALLS_PER_RUN` in
  [`lib/dataforseo.py`](lib/dataforseo.py)). Raise only if you understand why.
- Only uses DFS's **cheap endpoint** (`search_volume/live`, ~$0.05 per 1000
  keywords). Never touches SERP endpoints ($2/1000 — 40× more expensive).
- Keywords >10 words are silently dropped before DFS (API rejects them,
  would otherwise crash a full batch).
- `discover.py --dry-run` skips DFS entirely — free-mode uses autocomplete only.

Typical cost per full `discover.py` run: **< $0.01**.
Typical cost per `auto.py` blog: **$0.15–$0.50** (mostly GPT-5 reasoning
tokens — output ~2000 words × up to 3 revision rounds).

## File map

```
hermes-autoblog/
├── blog.py               # writer/reviewer loop — the core adversarial engine
├── discover.py           # topic + keyword discovery pipeline
├── auto.py               # orchestrator — pop queued topic, invoke blog.py
├── lib/
│   ├── cache.py          # generic SQLite TTL cache (.cache.db)
│   ├── store.py          # persistent keyword universe + topic queue (store.db)
│   ├── autocomplete.py   # Google suggestqueries wrapper (free)
│   └── dataforseo.py     # DFS keyword volume API (budgeted, cached)
├── requirements.txt
├── .env.example
└── output/               # generated blogs land here (gitignored)
```

## The rubric (what the reviewer scores)

In [`blog.py`](blog.py), each draft is scored 1–10 on:

1. **seo_keyword_integration** — keywords in H1, intro, H2s, conclusion, naturally
2. **search_intent_match** — does it answer what someone searching this topic actually wants
3. **depth_and_originality** — substantive, not generic AI filler
4. **structure_and_scannability** — H2/H3 hierarchy, lists, short paragraphs
5. **readability** — clear prose, varied sentence length, no bloat
6. **factual_accuracy** — claims are defensible, no invented stats
7. **engagement** — hook, transitions, compelling examples

Plus a boolean `word_count_ok` (roughly 1500–2500 words after markdown-stripping).

Exit condition: **all seven scores ≥ 8 AND `word_count_ok=true`**, OR `--rounds`
reached (default 3).

## Extending

| Task | Where |
|---|---|
| Change writer model | `WRITER_MODEL` env var, or `WRITER_MODEL` default in `blog.py` / `discover.py` |
| Change reviewer model | `BEDROCK_MODEL_ID` env var |
| Add a new content pillar | Edit `BRAND_CONTEXT` pillar list + brainstorm prompt in `discover.py` |
| Change rubric thresholds | `PASS_THRESHOLD` and `RUBRIC_DIMENSIONS` in `blog.py` |
| Change word-count targets | `WORD_MIN` / `WORD_MAX` in `blog.py` |
| Add more DFS endpoints | New wrapper in `lib/dataforseo.py` — must update `MAX_CALLS_PER_RUN` if you add calls |
| Swap Bedrock for direct Anthropic API | Replace `bedrock.converse(...)` calls in `blog.py` and `discover.py` with `anthropic.Anthropic().messages.create(...)` |

## Security

- `.env`, `*.db`, `output/`, `*.log` are all gitignored.
- DataForSEO uses HTTP Basic auth — login + password in env vars only.
- AWS credentials should be scoped to `bedrock:InvokeModel` on the specific
  model ID — no other permissions needed.
- Never commit `.env` or any SQLite file — they contain secrets / personal data
  / cached paid API responses.

## License

MIT (add LICENSE file if needed).
