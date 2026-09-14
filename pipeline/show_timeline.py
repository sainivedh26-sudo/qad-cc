"""
Print a human-readable timeline from timeline.jsonl output.

Usage:
    python pipeline/show_timeline.py [--timeline timeline.jsonl]
"""

import argparse
import json
from pathlib import Path


def show(timeline_path: str) -> None:
    entries = []
    with open(timeline_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    entries.sort(key=lambda e: e["start_sec"])

    print(f"\n{'='*72}")
    print(f"  SCENE -> AUDIO TIMELINE  ({len(entries)} scenes)")
    print(f"{'='*72}\n")

    for entry in entries:
        start = entry["start_sec"]
        end   = entry["end_sec"]
        dur   = entry["duration"]
        sid   = entry["scene_id"]
        needs = entry["needs"]

        print(f"[{start:6.1f}s -> {end:6.1f}s]  {sid}  ({dur:.1f}s)  needs={needs}")

        for i, m in enumerate(entry["matches"], 1):
            score  = m["score"]
            cid    = m["chunk_id"]
            atype  = m["audio_type"]
            cap    = m["caption"][:70]
            src    = Path(m["source_path"]).name[:50]
            energy = m["energy"]
            cs     = m["chunk_start_sec"]
            ce     = m["chunk_end_sec"]
            print(f"  #{i}  score={score:.3f}  [{cs:.1f}s-{ce:.1f}s]  {atype:12s}  E={energy:.2f}")
            print(f"       {cap}")
            print(f"       {src}")

        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline", default="timeline.jsonl")
    args = parser.parse_args()
    show(args.timeline)
