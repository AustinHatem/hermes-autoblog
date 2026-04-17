#!/usr/bin/env python3
import html
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

REPO = Path(__file__).resolve().parent
OUTPUT_DIR = REPO / "output"
FRAMER_TOOL = Path("/opt/data/framer-blog-tools/addAndPublishBlogPost.mjs")
TMP_POST_JSON = Path("/opt/data/framer-blog-tools/tmp_autoblog_post.json")


def latest_markdown() -> Path:
    mds = sorted(OUTPUT_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mds:
        raise RuntimeError("No markdown files found in output/")
    return mds[0]


def md_to_html(markdown_text: str) -> str:
    out = []
    in_ul = False

    for raw in markdown_text.splitlines():
        s = raw.strip()
        if not s:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            continue

        if s.startswith("### "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f'<h3 dir="auto">{html.escape(s[4:])}</h3>')
        elif s.startswith("## "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f'<h2 dir="auto">{html.escape(s[3:])}</h2>')
        elif s.startswith("# "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f'<h1 dir="auto">{html.escape(s[2:])}</h1>')
        elif re.match(r"^[-*]\s+", s):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            item = re.sub(r"^[-*]\s+", "", s)
            out.append(f"<li>{html.escape(item)}</li>")
        else:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f'<p dir="auto">{html.escape(s)}</p>')

    if in_ul:
        out.append("</ul>")

    return "\n".join(out)


def fetch_unsplash_image(query: str) -> dict | None:
    access_key = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()
    if not access_key:
        return None

    url = "https://api.unsplash.com/photos/random"
    headers = {"Authorization": f"Client-ID {access_key}"}

    fallback_query = re.sub(r"\b\d{4}\b", "", query.lower())
    fallback_query = re.sub(r"[^a-z\s]", " ", fallback_query)
    fallback_query = re.sub(r"\s+", " ", fallback_query).strip()
    attempts = [query, fallback_query, "video chat"]

    data = None
    for q in attempts:
        if not q:
            continue
        params = {
            "query": q,
            "orientation": "landscape",
            "content_filter": "high",
        }
        r = requests.get(url, params=params, headers=headers, timeout=20)
        if r.status_code == 200:
            data = r.json()
            break
        # Unsplash returns 404 when no matches; try next fallback query.
        if r.status_code != 404:
            raise RuntimeError(f"Unsplash request failed: {r.status_code} {r.text[:200]}")

    if not data:
        return None

    return {
        "url": data.get("urls", {}).get("regular"),
        "alt": data.get("alt_description") or query,
        "photographer": data.get("user", {}).get("name") or "Unsplash",
        "photo_page": data.get("links", {}).get("html") or "",
    }


def build_post(md_path: Path) -> dict:
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        body = "\n".join(lines[1:])
    else:
        title = md_path.stem
        body = text

    body = body.split("\n---\n")[0]
    html_content = md_to_html(body)

    slug = md_path.stem.split("-", 2)[-1]
    post = {
        "title": title,
        "slug": slug,
        "content": html_content,
        "contentType": "html",
        "subtitle": "Updated guide from SomeSomeone Editorial",
        "date": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "authorName": "SomeSomeone Team",
        "authorPosition": "Editorial",
        "published": True,
    }

    image = fetch_unsplash_image(title)
    if image and image.get("url"):
        post["image"] = image["url"]
        post["imageAlt"] = image["alt"]
        post["coverImage"] = image["url"]
        post["coverImageAlt"] = image["alt"]
        credit = f"Photo by {image['photographer']} on Unsplash"
        post["subtitle"] = f"Updated guide • {credit}"

    return post


def publish(post: dict) -> None:
    TMP_POST_JSON.write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
    cmd = ["node", str(FRAMER_TOOL), str(TMP_POST_JSON)]
    subprocess.run(cmd, check=True, cwd="/opt/data/framer-blog-tools")


def main() -> None:
    md = latest_markdown()
    post = build_post(md)
    publish(post)
    print(f"Published slug: {post['slug']}")


if __name__ == "__main__":
    main()
