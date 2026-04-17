#!/usr/bin/env python3
"""Pop the next queued topic from the store and write a blog for it.

Default: writes the highest-scored queued topic, one at a time.
  python auto.py                    # write the #1 queued topic
  python auto.py --topic-id 7       # write a specific topic by id
  python auto.py --list             # show queued topics, no writing
  python auto.py --list --all       # show queued + written

If the queue is empty, prompts you to run discover.py first.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from lib import store


def cmd_list(show_all: bool) -> None:
    st = store.stats()
    print(f"Store: {st['keywords_total']} keywords "
          f"({st['keywords_validated']} with volume), "
          f"{st['topics_by_status'].get('queued',0)} queued, "
          f"{st['topics_by_status'].get('written',0)} written\n")
    if show_all:
        topics = store.list_topics(limit=200)
    else:
        topics = store.list_topics(status="queued", limit=200)
    if not topics:
        print("(no topics — run discover.py first)")
        return
    for t in topics:
        marker = "✓" if t["status"] == "written" else "•"
        print(f"{marker} [{t['id']:>3}] score={t['score']:.1f} "
              f"[{t.get('pillar') or '?':<16}] {t['title']}")
        print(f"       kws: {', '.join(t['keywords'])}")
        if t.get("rationale"):
            print(f"       why: {t['rationale']}")
        if t["status"] == "written" and t.get("output_path"):
            print(f"       file: {t['output_path']}")
        print()


def cmd_write(topic_id: int | None, rounds: int) -> None:
    topic = store.next_queued(topic_id=topic_id)
    if not topic:
        if topic_id is not None:
            sys.exit(f"Topic id {topic_id} not found or not queued.")
        sys.exit("No queued topics. Run: python discover.py --niche \"...\" --brainstorm 20")

    print(f"━━━ Writing topic #{topic['id']} (score {topic['score']:.1f})")
    print(f"    title:    {topic['title']}")
    print(f"    pillar:   {topic.get('pillar') or '?'}")
    print(f"    keywords: {', '.join(topic['keywords'])}")
    print()

    # Invoke blog.py as a subprocess — it handles the writer/reviewer loop.
    cmd = [
        sys.executable, "blog.py",
        "--topic", topic["title"],
        "--keywords", ", ".join(topic["keywords"]),
        "--rounds", str(rounds),
    ]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        sys.exit(f"blog.py failed with exit code {result.returncode}")

    # Find the most recent file in output/ and link it to this topic
    out_dir = Path("output")
    mds = sorted(out_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if mds:
        store.mark_written(topic["id"], str(mds[0]))
        print(f"\n✓ Marked topic #{topic['id']} as written → {mds[0]}")
    else:
        print("\n⚠ No .md found in output/ — topic left in 'queued' state.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic-id", type=int, default=None,
                    help="Write a specific queued topic by id (default: highest-scored)")
    ap.add_argument("--rounds", type=int, default=3,
                    help="Max writer/reviewer rounds (default 3)")
    ap.add_argument("--list", action="store_true",
                    help="List queued topics and exit")
    ap.add_argument("--all", action="store_true",
                    help="With --list, include already-written topics")
    args = ap.parse_args()

    if args.list:
        cmd_list(show_all=args.all)
        return

    cmd_write(args.topic_id, args.rounds)


if __name__ == "__main__":
    main()
