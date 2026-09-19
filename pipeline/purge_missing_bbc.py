"""
Purge BBC chunks from Qdrant whose source files are invalid / absent from HF.
=============================================================================
Uses the same is_valid_wav() test that upload_to_hf.py used when deciding
what to include in the HF repo — no network HEAD checks needed.

The Qdrant scroll result is cached to purge_cache.json on first run.
Subsequent runs load from the cache (skip the slow scroll).

Usage:
    python pipeline/purge_missing_bbc.py           # delete invalid chunks
    python pipeline/purge_missing_bbc.py --dry-run # preview only
    python pipeline/purge_missing_bbc.py --rescan  # force re-scroll Qdrant
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

try:
    from qdrant_client import QdrantClient
except ImportError:
    print("pip install qdrant-client")
    sys.exit(1)

ROOT       = Path(__file__).parent.parent
SOUNDS_DIR = ROOT / "sounds"
CACHE_FILE = ROOT / "purge_cache.json"

QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_KEY = os.environ["QDRANT_KEY"]
COLLECTION = "audio_chunks"
MIN_AUDIO  = 8_000


def is_valid_wav(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < MIN_AUDIO:
        return False
    if path.suffix.lower() in (".mp3", ".ogg", ".flac", ".m4a"):
        return True
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"RIFF"
    except OSError:
        return False


def scroll_bbc_points(client: QdrantClient) -> dict[str, list]:
    """Scroll all points and return {source_path: [point_ids]} for BBC files only."""
    path_to_ids: dict[str, list] = defaultdict(list)
    total = 0
    offset = None

    while True:
        results, offset = client.scroll(
            collection_name=COLLECTION,
            with_payload=["source_path", "source"],
            with_vectors=False,
            limit=1000,
            offset=offset,
        )
        for point in results:
            sp  = point.payload.get("source_path", "")
            src = point.payload.get("source", "")
            if sp and (src == "bbc_sound_effects"
                       or sp.replace("\\", "/").startswith("sounds/BBC")):
                path_to_ids[sp].append(point.id)
        total += len(results)
        print(f"  scrolled {total:,} points, "
              f"{sum(len(v) for v in path_to_ids.values()):,} BBC...", end="\r")
        if offset is None:
            break

    print()
    return dict(path_to_ids)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be deleted without deleting")
    parser.add_argument("--rescan",  action="store_true",
                        help="Force re-scroll Qdrant even if cache exists")
    args = parser.parse_args()

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_KEY)

    # ── Load or build scroll cache ───────────────────────────────────────────
    if CACHE_FILE.exists() and not args.rescan:
        print(f"Loading cached scroll from {CACHE_FILE.name} (use --rescan to refresh)")
        with open(CACHE_FILE) as f:
            path_to_ids: dict[str, list] = json.load(f)
    else:
        print("Scrolling Qdrant (BBC points only)...")
        path_to_ids = scroll_bbc_points(client)
        with open(CACHE_FILE, "w") as f:
            json.dump(path_to_ids, f)
        print(f"Cached to {CACHE_FILE.name}")

    unique_paths = list(path_to_ids.keys())
    total_points = sum(len(v) for v in path_to_ids.values())
    print(f"BBC: {total_points:,} points across {len(unique_paths):,} unique files")

    # ── Check validity locally (same test upload_to_hf.py used) ─────────────
    print("\nChecking local validity (no network needed)...")
    missing_paths: list[str] = []
    ok = 0

    for sp in unique_paths:
        local = ROOT / sp.replace("\\", "/")
        if is_valid_wav(local):
            ok += 1
        else:
            missing_paths.append(sp)

    print(f"  Valid (on HF)  : {ok:,} files")
    print(f"  Invalid/missing: {len(missing_paths):,} files → not on HF")

    # Collect point IDs to delete
    ids_to_delete: list = []
    for sp in missing_paths:
        ids_to_delete.extend(path_to_ids[sp])

    print(f"\nPoints to purge : {len(ids_to_delete):,}")
    if missing_paths:
        for sp in missing_paths[:20]:
            print(f"  {sp}  ({len(path_to_ids[sp])} chunks)")
        if len(missing_paths) > 20:
            print(f"  ... and {len(missing_paths) - 20} more")

    if not ids_to_delete:
        print("\nNothing to delete — collection is clean.")
        return

    if args.dry_run:
        print("\n[dry-run] No changes made. Remove --dry-run to delete.")
        return

    # ── Delete in batches ────────────────────────────────────────────────────
    print("\nDeleting...")
    BATCH = 1000
    deleted = 0
    for start in range(0, len(ids_to_delete), BATCH):
        batch = ids_to_delete[start : start + BATCH]
        client.delete(collection_name=COLLECTION, points_selector=batch)
        deleted += len(batch)
        print(f"  {deleted:,}/{len(ids_to_delete):,} deleted...", end="\r")
    print()

    # Clear cache so a future --rescan gives fresh counts
    CACHE_FILE.unlink(missing_ok=True)
    print(f"\nDone. Purged {deleted:,} stale BBC chunks from Qdrant.")


if __name__ == "__main__":
    main()
