#!/usr/bin/env python3
"""Topic + keyword discovery for Someone Somewhere SEO content.

Builds / extends a persistent keyword universe (store.db):
  1. GPT-5 brainstorms topic candidates given niche + brand context
  2. Google Autocomplete expands each seed keyword into real long-tails (free)
  3. DataForSEO validates volume/competition for ONLY the uncached/stale keywords
  4. Sonnet scores each candidate; topics are upserted into store (exact-title dedupe)
  5. Prints a summary of what was added vs deduped

Running discover multiple times merges into the same store — cheap after the
first run (everything cached), builds a bigger topic queue over time.
"""
import argparse
import json
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv()

import boto3
from openai import OpenAI

from lib import autocomplete, dataforseo, store
from lib.brand import BRAND_CONTEXT

OPENAI_KEY = os.getenv("OPENAI_API_KEY")
WRITER_MODEL = os.getenv("WRITER_MODEL", "gpt-5")
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0"
)
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")

if not OPENAI_KEY:
    sys.exit("Missing OPENAI_API_KEY in .env")

openai_client = OpenAI(api_key=OPENAI_KEY)
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)


BRAINSTORM_PROMPT = """You generate blog topic ideas for the SEO strategy below.

{brand}

Task: given the niche "{niche}", output a JSON array of {count} topic
candidates. Each candidate is an object:
  {{
    "working_title": "a concrete blog post title — specific, not generic",
    "search_intent": "informational | commercial | transactional",
    "seed_keywords": ["2-4 short keyword phrases a real person would type into Google"],
    "content_pillar": "one of: alternatives, safety, language_exchange, comparison, howto"
  }}

Rules:
- Include a MIX across all five content pillars — don't bias toward one.
- Topical overlap between candidates is FINE — we're building a cluster.
- Titles should be concrete (numbers, demographics, specific comparisons) not vague.
- Seed keywords must be things people actually search — short, lowercase, no branding fluff.
- Prefer titles that can credibly fit 1,800–2,300 words of useful content.
- DO NOT use the brand name in seed_keywords (those are for SEO, not branding).

Respond with ONLY the JSON array, no prose."""


CLUSTER_PROMPT = """You generate a TOPIC CLUSTER of blog posts, all targeting
the same primary keyword with genuinely distinct angles.

{brand}

PRIMARY KEYWORD: "{cluster}"{volume_hint}

Task: output a JSON array of {count} blog post candidates, each targeting
the primary keyword above but from a GENUINELY DIFFERENT angle. Schema:
  {{
    "working_title": "a concrete blog post title — includes the primary keyword or a close variant",
    "search_intent": "informational | commercial | transactional",
    "seed_keywords": ["3-5 keyword phrases — the primary keyword + 2-4 variants/long-tails"],
    "content_pillar": "one of: alternatives, safety, language_exchange, comparison, howto"
  }}

Angle dimensions to vary across candidates (use several, not all):
  - Year / freshness: "in 2026", "this year", "updated"
  - Demographics: "for teens", "for adults", "for introverts", "for language learners", "for couples"
  - Platform: "iOS", "Android", "PC", "browser-based", "no-download"
  - Price: "free", "paid", "freemium", "no-signup"
  - Use case: "to make friends", "for language practice", "for random chat",
    "to video call strangers", "to meet people abroad"
  - Feature angle: "with moderation", "with translation", "with verification",
    "with gender filter", "with text chat"
  - Region: "US", "Europe", "Asia", "global"
  - Listicle variant: "top 5", "top 10", "top 15", "top 25"
  - Comparison framing: "vs X", "ranked", "reviewed", "compared"
  - Problem framing: "after Omegle shut down", "banned on Omegle", "bored of X"
  - Safety lens: "safer than", "parent-approved", "bot-free", "verified users only"
  - Question framing: "which X is best for Y", "is X worth it"

Rules:
- Every title should plausibly rank for the primary keyword or a variant.
- Titles must be GENUINELY DIFFERENT — don't just rephrase the same article 15 times.
- Every candidate's seed_keywords MUST include the primary keyword or a tight variant.
- Primary keyword first in seed_keywords.
- Titles should be concrete (numbers, demographics, specific framing).
- DO NOT use the brand name in seed_keywords.
- A mix of content_pillars is FINE but not required — stay focused on the cluster.

Respond with ONLY the JSON array, no prose."""


SCORER_SYSTEM = """You are a senior SEO strategist scoring blog topic candidates.

For each candidate, assign:
  - score: 0.0 to 10.0, based on:
      * Traffic potential (sum of seed-keyword volumes, discounted by competition)
      * Brand fit for Someone Somewhere
      * Content-market fit (can we write ~2000 useful words?)
  - final_keywords: 3-5 target keywords drawn from the candidate's keyword_pool.
    Favor ones with actual search volume if present. Put the PRIMARY keyword first.
  - rationale: one short sentence on why this topic is worth writing.

Do NOT worry about topical overlap or cannibalization with other candidates —
the user wants multiple posts clustering around the same core keywords.

Respond with ONLY valid JSON matching this schema:
{
  "scored": [
    {
      "working_title": "<copied from input>",
      "score": <float 0-10>,
      "final_keywords": ["primary", "secondary", ...],
      "rationale": "one sentence"
    }
  ]
}"""


def brainstorm(niche: str, count: int) -> list[dict]:
    resp = openai_client.chat.completions.create(
        model=WRITER_MODEL,
        messages=[{
            "role": "user",
            "content": BRAINSTORM_PROMPT.format(
                brand=BRAND_CONTEXT, niche=niche, count=count),
        }],
    )
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    return json.loads(m.group(0) if m else raw)


def brainstorm_cluster(primary_kw: str, count: int) -> list[dict]:
    """Cluster mode: N distinct angles all targeting one primary keyword."""
    # If we already have volume data for the primary keyword, pass it along
    # so GPT-5 knows just how high-value this cluster is.
    existing = store.get_keyword(primary_kw) or {}
    vol = existing.get("volume")
    comp = existing.get("competition")
    volume_hint = ""
    if vol is not None and vol > 0:
        volume_hint = f" (monthly search volume: {vol:,}, competition: {comp or 'n/a'})"

    resp = openai_client.chat.completions.create(
        model=WRITER_MODEL,
        messages=[{
            "role": "user",
            "content": CLUSTER_PROMPT.format(
                brand=BRAND_CONTEXT, cluster=primary_kw, count=count,
                volume_hint=volume_hint),
        }],
    )
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    return json.loads(m.group(0) if m else raw)


def expand_keywords(candidates: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in candidates:
        pool: set[str] = set(c.get("seed_keywords", []))
        for seed in c.get("seed_keywords", []):
            pool.update(autocomplete.suggest(seed))
        # Cap per-topic pool to bound DFS costs
        out[c["working_title"]] = sorted(pool)[:20]
    return out


def validate_with_dfs(all_keywords: set[str]) -> None:
    """Hit DFS only for keywords we haven't validated in the last 30 days.
    Results are written to the keyword store."""
    stale = store.get_keywords_needing_validation(sorted(all_keywords))
    if not stale:
        print(f"[discover] All {len(all_keywords)} keywords already fresh in store — "
              "no DFS call needed.")
        return
    print(f"[discover] {len(stale)}/{len(all_keywords)} keywords need validation "
          f"({len(all_keywords)-len(stale)} already fresh in store).")
    if not dataforseo.is_configured():
        print("[discover] DataForSEO not configured — skipping paid validation.")
        # Still record the keywords (without volume) so future runs can fill them
        for kw in stale:
            store.upsert_keyword(kw)
        return
    try:
        data = dataforseo.search_volume(stale)
    except dataforseo.BudgetExceeded as e:
        print(f"[discover] Budget hit: {e}")
        return
    got_vol = 0
    for kw in stale:
        row = data.get(kw)
        if row:
            store.upsert_keyword(kw, volume=row.get("volume"),
                                 competition=row.get("competition"),
                                 cpc=row.get("cpc"))
            if row.get("volume"):
                got_vol += 1
        else:
            store.upsert_keyword(kw)
    print(f"[discover] Got real volume for {got_vol}/{len(stale)} fresh keywords.")


def score_candidates(candidates: list[dict],
                     pools: dict[str, list[str]]) -> dict[str, dict]:
    """Returns {working_title: {score, final_keywords, rationale}}."""
    # Enrich each candidate with cached volume data from the store
    enriched = []
    for c in candidates:
        title = c["working_title"]
        pool = []
        for kw in pools.get(title, []):
            row = store.get_keyword(kw) or {}
            pool.append({
                "keyword": kw,
                "volume": row.get("volume"),
                "competition": row.get("competition"),
            })
        enriched.append({
            "working_title": title,
            "search_intent": c.get("search_intent"),
            "content_pillar": c.get("content_pillar"),
            "keyword_pool": pool,
        })

    user_msg = json.dumps({"candidates": enriched}, indent=2)
    resp = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": SCORER_SYSTEM}],
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        inferenceConfig={"maxTokens": 4000, "temperature": 0.2},
    )
    raw = resp["output"]["message"]["content"][0]["text"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    parsed = json.loads(m.group(0) if m else raw)
    return {s["working_title"]: s for s in parsed.get("scored", [])}


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Topic + keyword discovery. Two modes:\n"
            "  --niche    broad pillar-spread brainstorm (exploratory)\n"
            "  --cluster  N distinct angles all targeting one high-volume keyword"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--niche", default=None,
                    help="Broad mode: seed phrase(s), comma-separated")
    ap.add_argument("--cluster", default=None,
                    help="Cluster mode: one primary keyword to saturate "
                         "(e.g. 'omegle alternatives'). Produces N angles "
                         "all targeting it.")
    ap.add_argument("--brainstorm", type=int, default=20,
                    help="How many candidates to brainstorm this run (default 20)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Skip DataForSEO — run free-only")
    args = ap.parse_args()

    if not args.niche and not args.cluster:
        ap.error("must provide --niche or --cluster")
    if args.niche and args.cluster:
        ap.error("--niche and --cluster are mutually exclusive")

    before = store.stats()
    mode = "CLUSTER" if args.cluster else "NICHE"
    seed = args.cluster or args.niche
    print(f"MODE:        {mode} — \"{seed}\"")
    print(f"BRAINSTORM:  {args.brainstorm}")
    print(f"DFS:         {'dry-run' if args.dry_run else ('configured' if dataforseo.is_configured() else 'not configured')}")
    print(f"STORE:       {before['keywords_total']} keywords, "
          f"{before['topics_by_status'].get('queued',0)} queued topics, "
          f"{before['topics_by_status'].get('written',0)} written")
    print()

    if args.cluster:
        print(f"━━━ Brainstorming cluster angles (GPT-5) around \"{args.cluster}\"...")
        cands = brainstorm_cluster(args.cluster, args.brainstorm)
    else:
        print("━━━ Brainstorming candidates (GPT-5)...")
        cands = brainstorm(args.niche, args.brainstorm)
    print(f"   got {len(cands)} candidates")

    print("━━━ Expanding keywords via Google Autocomplete...")
    pools = expand_keywords(cands)
    all_kws = {k for kws in pools.values() for k in kws}
    print(f"   {len(all_kws)} unique long-tail keywords")

    # Record every keyword in the store (even without DFS data)
    for kw in all_kws:
        store.upsert_keyword(kw)

    if not args.dry_run:
        print("━━━ Validating with DataForSEO (only fresh keywords)...")
        validate_with_dfs(all_kws)

    print("━━━ Scoring candidates (Sonnet)...")
    scores = score_candidates(cands, pools)

    print("━━━ Merging into topic store (exact-title dedupe)...")
    added, duped = 0, 0
    for c in cands:
        title = c["working_title"]
        s = scores.get(title, {})
        topic_id, is_new = store.add_topic(
            title=title,
            keywords=s.get("final_keywords", c.get("seed_keywords", [])),
            pillar=c.get("content_pillar"),
            search_intent=c.get("search_intent"),
            rationale=s.get("rationale"),
            score=s.get("score", 0.0),
        )
        if is_new:
            added += 1
        else:
            duped += 1

    after = store.stats()
    print(f"\n━━━ Summary")
    print(f"   added:   {added} new topics")
    print(f"   deduped: {duped} (exact title already queued)")
    print(f"   store now: {after['keywords_total']} keywords "
          f"({after['keywords_validated']} with real volume data), "
          f"{after['topics_by_status'].get('queued',0)} queued, "
          f"{after['topics_by_status'].get('written',0)} written")


if __name__ == "__main__":
    main()
