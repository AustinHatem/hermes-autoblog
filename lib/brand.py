"""Shared brand context for discover.py + blog.py.

Change the values here to repurpose this repo for a different site/product.
"""

BRAND_NAME = "Someone Somewhere"
BRAND_URL = "https://somesome.co"
BRAND_SHORT = "somesome.co"

BRAND_CONTEXT = f"""BRAND: {BRAND_NAME} ({BRAND_URL})
PITCH: Global video chat made safe with AI filtering, deep verification, and dedicated moderation.
KEY FEATURES: AI translation (cross-language video chat), unlimited messaging between sessions, verification, AI content filtering, human moderation.
POSITIONING: Safer, more international alternative to Omegle / Monkey / Chatroulette / Ome.tv.
AUDIENCE: General adults 18–35 looking to meet people globally, practice languages, make international friends.

CONTENT PILLARS:
  1. alternatives     — Omegle alternatives / apps like Monkey — high-volume refugee searches
  2. safety           — "is X safe", "how to video chat safely"
  3. language_exchange— "practice English with strangers", "make international friends"
  4. comparison       — Someone Somewhere vs Monkey / Ome.tv / Azar / CooMeet
  5. howto            — etiquette, conversation starters, first video chat tips
"""


# Rules for how the writer should integrate the brand into every article.
# Tuned to feel like editorial content, not a sales page — but Someone Somewhere
# has to actually show up, since this is published on their own blog.
BRAND_INTEGRATION_RULES = f"""BRAND INTEGRATION (this blog is published on {BRAND_URL} — the brand MUST appear):

1. Mention "{BRAND_NAME}" by name at least 3 times across the article, spread
   naturally — never in back-to-back paragraphs, never in the first sentence.
2. The first brand mention happens somewhere in the first 1/3 of the body,
   not in the intro paragraph itself.
3. Link the brand name to {BRAND_URL} the first time it appears.
4. In listicle / ranked / "best X" articles, rank {BRAND_NAME} at #1. This
   is the brand's own blog — #1 placement is expected and fine. Back the
   ranking with the real differentiators (AI translation, moderation,
   verification, unlimited messaging) so it reads earned, not arbitrary.
5. In comparison tables, include {BRAND_NAME} as one of the compared options
   with honest trade-offs listed.
6. In safety / howto / language-exchange articles, mention {BRAND_NAME}
   contextually when its specific features (AI translation, moderation, verification)
   directly address the section's topic.
7. End with a soft CTA in the conclusion referencing {BRAND_NAME} and its
   differentiators — one short sentence, not a sales pitch.
8. Be honest: don't claim features {BRAND_NAME} doesn't have. The real
   differentiators are: (a) AI-powered cross-language translation,
   (b) AI content filtering + human moderation, (c) user verification,
   (d) unlimited messaging between video sessions.
9. Never write marketing fluff. Tone stays editorial throughout — the brand
   mentions should read like a writer who genuinely likes the product and
   happens to know it well, not a paid placement.
"""
