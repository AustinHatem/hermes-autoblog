#!/usr/bin/env python3
"""
Adversarial blog creator: one model writes, a different model reviews.
Loop until reviewer approves (all rubric scores >= threshold) or max rounds.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import boto3
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

from lib.brand import BRAND_CONTEXT, BRAND_INTEGRATION_RULES, BRAND_NAME

OPENAI_KEY = os.getenv("OPENAI_API_KEY")
WRITER_MODEL = os.getenv("WRITER_MODEL", "gpt-5")
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0"
)
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")

if not OPENAI_KEY:
    sys.exit("Missing OPENAI_API_KEY in .env")
if not os.getenv("AWS_ACCESS_KEY_ID") or not os.getenv("AWS_SECRET_ACCESS_KEY"):
    sys.exit("Missing AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in .env")

writer_client = OpenAI(api_key=OPENAI_KEY)
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

WORD_MIN = 1500
WORD_MAX = 2500
PASS_THRESHOLD = 8  # every rubric dimension must be >= this to exit early

RUBRIC_DIMENSIONS = [
    "seo_keyword_integration",   # keywords appear naturally, good density, in H2/H3 and intro
    "search_intent_match",       # answers what someone searching this topic actually wants
    "depth_and_originality",     # substantive, not generic AI filler
    "structure_and_scannability",# H2/H3 hierarchy, lists, short paragraphs, TOC-friendly
    "readability",               # clear prose, varied sentence length, no bloat
    "factual_accuracy",          # claims are defensible, no hallucinated stats
    "engagement",                # hook, transitions, compelling examples
    "brand_integration",         # brand named 3+ times, linked, editorial (not salesy)
]


def word_count(text: str) -> int:
    # strip markdown syntax-ish so count approximates prose
    cleaned = re.sub(r"[#*_`>\-]", " ", text)
    return len(cleaned.split())


def clean_markdown(text: str) -> str:
    """Defensive post-processing so output renders cleanly in any CMS.

    Fixes common writer slips:
      - doubled list markers ("- - Best for" → "- Best for")
      - unicode bullets (•) or asterisk bullets at line start → "-"
      - trailing whitespace on every line
      - collapses 3+ blank lines to 1 blank line
      - ensures blank line before each heading
    """
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        # Collapse any "- - " / "* - " / "- * " / "• - " at line start (with optional indent)
        m = re.match(r"^(\s*)([-*•])\s+[-*•]\s+(.*)$", stripped)
        if m:
            stripped = f"{m.group(1)}- {m.group(3)}"
        # Normalize bullet char to `-` (keep indent)
        stripped = re.sub(r"^(\s*)[*•](\s+)", r"\1-\2", stripped)
        out.append(stripped)

    # Ensure a blank line before any heading (H1/H2/H3)
    fixed: list[str] = []
    for i, line in enumerate(out):
        if re.match(r"^#{1,6}\s", line) and fixed and fixed[-1].strip() != "":
            fixed.append("")
        fixed.append(line)

    # Collapse 3+ consecutive blank lines down to 1
    collapsed: list[str] = []
    blank_run = 0
    for line in fixed:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 1:
                collapsed.append(line)
        else:
            blank_run = 0
            collapsed.append(line)

    return "\n".join(collapsed).strip() + "\n"


WRITER_SYSTEM = f"""You are a senior SEO content writer for the brand below.
You write blog posts that rank on page 1 of Google because they actually deserve
to — genuine depth, clear structure, and keywords integrated naturally (never
stuffed). The posts are published on the brand's own blog, so the brand must
appear naturally within the content.

{BRAND_CONTEXT}

{BRAND_INTEGRATION_RULES}

OUTPUT RULES (non-negotiable):
- Length: {WORD_MIN}–{WORD_MAX} words of body prose (aim for ~2000).
- Format: Markdown. Start with an H1 title, then a 2–3 sentence hook intro.
- Use H2 sections (4–7 of them) and H3 subsections where useful.
- Include at least one bulleted or numbered list.
- Work the user's target keywords into: the H1, the first 100 words, at least
  two H2s, and the conclusion — but only where they read naturally.
- Include a short "Key takeaways" section near the top or bottom.
- Include a meta description (<=160 chars) at the very end, after a
  `---` separator, prefixed with `META:`.
- No fluff intros ("In today's fast-paced world..."). Get to the point.
- No invented statistics. If you cite a number, make sure it's commonly
  known or clearly framed as illustrative.
- Write in an authoritative, helpful voice. Second person ("you") is fine.

MARKDOWN FORMATTING (strict — these make the output render cleanly in any CMS):
- Use `-` for every bullet. NEVER use `*` or `•` (unicode bullets) or `—` as list markers.
- Never put a dash or bullet character inside bullet content. Write
  `- Best for: fast matches`, NOT `- - Best for: fast matches`.
- Never output two list markers in a row on the same line.
- EVERY non-intro section gets an H2 heading (`## Section title`). In a
  ranked listicle, EVERY ranked entry gets its own H3 heading (`### 1) Name`,
  `### 2) Name`, etc.) — do not put ranked entries under one shared heading
  with only bullets separating them.
- No trailing whitespace on any line (no "two-space soft-break" markdown).
- Leave exactly one blank line before every H2 and H3.
- Tables: use standard Markdown pipe tables with a header separator row.
- Don't use HTML in the output."""


def writer_generate(topic: str, keywords: list[str], prior_feedback: str | None,
                    prior_draft: str | None) -> str:
    kw = ", ".join(keywords)
    if prior_feedback and prior_draft:
        user_msg = f"""Revise the blog post below based on the reviewer's feedback.
Keep what's working; fix what's flagged. Return the COMPLETE revised post,
not a diff.

TOPIC: {topic}
TARGET KEYWORDS: {kw}

--- REVIEWER FEEDBACK ---
{prior_feedback}

--- CURRENT DRAFT ---
{prior_draft}
"""
    else:
        user_msg = f"""Write a blog post.

TOPIC: {topic}
TARGET KEYWORDS (must appear naturally): {kw}

Follow all the output rules. Begin now."""

    resp = writer_client.chat.completions.create(
        model=WRITER_MODEL,
        messages=[
            {"role": "system", "content": WRITER_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )
    return clean_markdown(resp.choices[0].message.content.strip())


REVIEWER_SYSTEM = f"""You are a ruthless SEO editor reviewing a blog post for a
competitive niche. You are NOT the writer — your job is to find weaknesses.

The blog is published on {BRAND_NAME}'s own site, so {BRAND_NAME} must appear
in the content editorially. Score brand_integration based on: is {BRAND_NAME}
mentioned 3+ times, linked once, included naturally in lists/comparisons/tables
where appropriate, and closed with a soft CTA — WITHOUT reading like a
sales page? If {BRAND_NAME} is missing or named once as an afterthought,
brand_integration = 1-3.

You will score the draft on these dimensions, each 1–10:
{chr(10).join(f"  - {d}" for d in RUBRIC_DIMENSIONS)}

Be honest. Most first drafts from LLMs score 6–7 on depth and originality
because they're generic. Reward genuinely useful, specific content and
penalize filler, hedging, and keyword stuffing.

Respond with ONLY valid JSON, no prose before or after, matching this schema:
{{
  "scores": {{ {", ".join(f'"{d}": <int 1-10>' for d in RUBRIC_DIMENSIONS)} }},
  "word_count_ok": <bool, true if roughly {WORD_MIN}-{WORD_MAX} words>,
  "brand_mentions": <int, count of times the brand name appears>,
  "top_issues": [<1-5 specific, actionable fixes. Each a short string.>],
  "keep_doing": [<1-3 things the draft got right>],
  "verdict": "<one short sentence>"
}}"""


def _brand_mention_count(draft: str) -> int:
    return len(re.findall(re.escape(BRAND_NAME), draft, flags=re.IGNORECASE))


def reviewer_evaluate(topic: str, keywords: list[str], draft: str) -> dict:
    kw = ", ".join(keywords)
    mentions = _brand_mention_count(draft)
    brand_note = (
        f"BRAND-NAME MENTIONS (mechanical count): {mentions} — "
        + ("OK" if mentions >= 3 else f"LOW (<3): score brand_integration accordingly")
    )
    user_msg = f"""TOPIC: {topic}
TARGET KEYWORDS: {kw}
WORD COUNT (measured): {word_count(draft)}
{brand_note}

--- DRAFT ---
{draft}
"""
    resp = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": REVIEWER_SYSTEM}],
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        inferenceConfig={"maxTokens": 2000, "temperature": 0.2},
    )
    raw = resp["output"]["message"]["content"][0]["text"].strip()
    # Strip code fences if the model wraps JSON despite instructions
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Last-ditch: pull the first {...} block
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def format_feedback(review: dict) -> str:
    lines = [f"VERDICT: {review.get('verdict', '')}", "", "SCORES:"]
    for dim, score in review.get("scores", {}).items():
        lines.append(f"  {dim}: {score}/10")
    lines.append(f"  word_count_ok: {review.get('word_count_ok')}")
    lines.append("")
    lines.append("TOP ISSUES TO FIX:")
    for issue in review.get("top_issues", []):
        lines.append(f"  - {issue}")
    lines.append("")
    lines.append("KEEP DOING:")
    for good in review.get("keep_doing", []):
        lines.append(f"  - {good}")
    return "\n".join(lines)


def all_pass(review: dict) -> bool:
    scores = review.get("scores", {})
    if not scores or len(scores) < len(RUBRIC_DIMENSIONS):
        return False
    if not review.get("word_count_ok", False):
        return False
    return all(s >= PASS_THRESHOLD for s in scores.values())


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "post"


def main():
    ap = argparse.ArgumentParser(description="Adversarial blog creator.")
    ap.add_argument("--topic", required=True, help="Blog topic / working title")
    ap.add_argument("--keywords", required=True,
                    help="Comma-separated target SEO keywords")
    ap.add_argument("--rounds", type=int, default=3,
                    help="Max write/review rounds (default: 3)")
    ap.add_argument("--output-dir", default="output",
                    help="Where to save the final post (default: output/)")
    args = ap.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"TOPIC:     {args.topic}")
    print(f"KEYWORDS:  {', '.join(keywords)}")
    print(f"WRITER:    {WRITER_MODEL} (OpenAI)")
    print(f"REVIEWER:  {BEDROCK_MODEL_ID} (Bedrock)")
    print(f"ROUNDS:    up to {args.rounds}\n")

    draft = None
    feedback = None
    final_review = None

    for rnd in range(1, args.rounds + 1):
        print(f"━━━ Round {rnd} — Writer drafting... ━━━")
        t0 = time.time()
        draft = writer_generate(args.topic, keywords, feedback, draft)
        wc = word_count(draft)
        print(f"    draft: {wc} words ({time.time()-t0:.1f}s)")

        print(f"━━━ Round {rnd} — Reviewer evaluating... ━━━")
        t0 = time.time()
        review = reviewer_evaluate(args.topic, keywords, draft)
        final_review = review
        print(f"    review done ({time.time()-t0:.1f}s)")
        print(format_feedback(review))
        print()

        if all_pass(review):
            print(f"✓ Passed rubric on round {rnd}. Stopping.\n")
            break
        if rnd < args.rounds:
            feedback = format_feedback(review)
    else:
        print(f"⚠ Hit max rounds ({args.rounds}) without full pass. "
              "Saving the best we have.\n")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = slugify(args.topic)
    md_path = out_dir / f"{stamp}-{slug}.md"
    meta_path = out_dir / f"{stamp}-{slug}.review.json"
    md_path.write_text(draft, encoding="utf-8")
    meta_path.write_text(json.dumps(final_review, indent=2), encoding="utf-8")

    print(f"Saved: {md_path}")
    print(f"Saved: {meta_path}")


if __name__ == "__main__":
    main()
