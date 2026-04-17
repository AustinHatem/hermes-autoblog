"""Free Google Autocomplete — the suggestqueries endpoint, no key needed.

Gives us what people actually type. No volume numbers, but free and unlimited-ish.
"""
import urllib.parse
import urllib.request

from .cache import get, put

ENDPOINT = "https://suggestqueries.google.com/complete/search"


def suggest(query: str, hl: str = "en") -> list[str]:
    cached = get("autocomplete", {"q": query, "hl": hl}, max_age_days=7)
    if cached is not None:
        return cached

    params = urllib.parse.urlencode({
        "client": "firefox",  # firefox client returns plain JSON
        "hl": hl,
        "q": query,
    })
    req = urllib.request.Request(
        f"{ENDPOINT}?{params}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            import json
            data = json.loads(r.read().decode())
        # Shape: [query, [suggestions...]]
        suggestions = data[1] if len(data) > 1 and isinstance(data[1], list) else []
    except Exception:
        suggestions = []
    put("autocomplete", {"q": query, "hl": hl}, suggestions)
    return suggestions


def expand(seed: str, depth: int = 1, hl: str = "en") -> list[str]:
    """Expand a seed via alphabetical A-Z prepending to pull more long-tails.
    depth=0 → just the seed's suggestions (1 call)
    depth=1 → seed + 26 alphabetical variants (27 calls, all cached)
    """
    all_suggestions: set[str] = set(suggest(seed, hl=hl))
    if depth >= 1:
        for letter in "abcdefghijklmnopqrstuvwxyz":
            all_suggestions.update(suggest(f"{seed} {letter}", hl=hl))
    return sorted(all_suggestions)
