"""DataForSEO keyword data. Heavily cached. Aggressive budget caps.

Only uses the cheap endpoints:
  - /v3/keywords_data/google_ads/search_volume/live  (~$0.05 per 1000 kw)

Never touches SERP endpoints ($2/1000 — 40x more expensive).
"""
import base64
import json
import os
import urllib.error
import urllib.request

from .cache import get, put

API_BASE = "https://api.dataforseo.com/v3"
MAX_KW_PER_CALL = 1000  # API hard limit
MAX_CALLS_PER_RUN = 2   # budget guardrail; raise only if you know why


class BudgetExceeded(Exception):
    pass


def is_configured() -> bool:
    return bool(os.getenv("DATAFORSEO_LOGIN") and os.getenv("DATAFORSEO_PASSWORD"))


def _auth_header() -> str:
    login = os.getenv("DATAFORSEO_LOGIN", "")
    password = os.getenv("DATAFORSEO_PASSWORD", "")
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    return f"Basic {token}"


def _post(path: str, payload):
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": _auth_header(),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # Surface the actual JSON error body instead of a bare HTTP code
        body = e.read().decode(errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"status_code": e.code, "status_message": body[:300]}


# in-process call counter so we can hard-cap per run
_call_count = 0


def search_volume(keywords: list[str], location_code: int = 2840,
                  language_code: str = "en") -> dict[str, dict]:
    """Return {keyword: {volume:int, competition:str, cpc:float}} for each keyword.

    location_code 2840 = United States. language_code 'en' = English.
    Results are cached individually by keyword — only uncached ones hit the API.
    """
    global _call_count
    results: dict[str, dict] = {}
    uncached: list[str] = []
    for kw in keywords:
        # DFS rejects keywords longer than 10 words — silently drop them
        # (they'd crash the whole batch otherwise)
        if len(kw.split()) > 10:
            continue
        cached = get("dfs_volume", {"kw": kw, "loc": location_code, "lang": language_code},
                     max_age_days=30)
        if cached is not None:
            results[kw] = cached
        else:
            uncached.append(kw)

    if not uncached:
        return results

    if not is_configured():
        # No creds — return what we cached; unknowns stay absent (caller decides).
        return results

    # Batch in chunks of MAX_KW_PER_CALL
    for i in range(0, len(uncached), MAX_KW_PER_CALL):
        if _call_count >= MAX_CALLS_PER_RUN:
            raise BudgetExceeded(
                f"Hit MAX_CALLS_PER_RUN={MAX_CALLS_PER_RUN}. "
                f"Raise it in lib/dataforseo.py if this was intentional."
            )
        batch = uncached[i:i + MAX_KW_PER_CALL]
        payload = [{
            "keywords": batch,
            "location_code": location_code,
            "language_code": language_code,
            "include_adult_keywords": True,
        }]
        _call_count += 1
        resp = _post("/keywords_data/google_ads/search_volume/live", payload)
        # Top-level error (auth, verification, rate limit, etc.)
        top_status = resp.get("status_code", 0)
        if top_status >= 40000:
            print(f"[dataforseo] API error {top_status}: {resp.get('status_message')}")
            return results
        tasks = resp.get("tasks", [])
        if not tasks or tasks[0].get("status_code", 0) >= 40000:
            msg = tasks[0].get("status_message") if tasks else resp.get("status_message")
            print(f"[dataforseo] task failed: {msg}")
            continue
        for item in (tasks[0].get("result") or []):
            kw = item.get("keyword")
            if not kw:
                continue
            row = {
                "volume": item.get("search_volume") or 0,
                "competition": item.get("competition") or "",
                "cpc": item.get("cpc") or 0.0,
            }
            results[kw] = row
            put("dfs_volume", {"kw": kw, "loc": location_code, "lang": language_code}, row)

    return results


def call_stats() -> dict:
    return {"calls_this_run": _call_count, "max_per_run": MAX_CALLS_PER_RUN}
