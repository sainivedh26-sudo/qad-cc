"""
FreeSound Audio Downloader
==========================
Downloads CC-licensed sounds from freesound.org into
sounds/FreeSound/{audio_type}/ as MP3 previews (no conversion —
librosa loads MP3 directly during ingest).

Setup:
  1. Register free account -> https://freesound.org/apiv2/apply/
  2. Add  FREESOUND_KEY=<your key>  to .env
  3. pip install freesound

Usage:
    python pipeline/freesound_download.py               # 800 per type (~5 k total)
    python pipeline/freesound_download.py --per-type 300 --threads 16
    python pipeline/freesound_download.py --types ambience foley
"""

import argparse
import os
import re
import sys
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

try:
    import freesound
except ImportError:
    print("ERROR: pip install freesound")
    sys.exit(1)

try:
    import requests as _requests
    _USE_REQUESTS = True
except ImportError:
    import urllib.request
    _USE_REQUESTS = False

from tqdm import tqdm

ROOT       = Path(__file__).parent.parent
SOUNDS_DIR = ROOT / "sounds" / "FreeSound"
MIN_AUDIO_BYTES = 8_000

# ── Queries per audio type ─────────────────────────────────────────────────────
TYPE_QUERIES: dict[str, list[str]] = {
    "ambience": [
        "outdoor nature ambience",
        "rain forest ambience",
        "city street crowd noise",
        "indoor room tone quiet",
        "ocean waves beach",
        "wind storm atmosphere",
        "cafe restaurant chatter",
        "park birds outdoor",
        "subway train station",
        "night insects crickets",
        "fire crackling campfire",
        "busy market crowd",
        "snow winter cold",
        "cave tunnel underground",
        "desert wind dry heat",
        "arena stadium sports crowd",
    ],
    "foley": [
        "footsteps wooden floor",
        "door open close creak",
        "cloth fabric rustle movement",
        "paper book handling",
        "keys coins small objects",
        "keyboard typing click",
        "glass clink bottle",
        "cutlery kitchen cooking",
        "zipper bag handling",
        "tools hammer drill",
        "sport ball game",
        "cartoon comedy sound effect",
        "camera click shutter",
        "water pour drip liquid",
        "food crunch chew eating",
        "robot laser sci-fi",
    ],
    "impact": [
        "explosion large boom",
        "punch hit thud impact",
        "glass shatter break",
        "door slam hard",
        "metal crash clang",
        "thunder lightning",
        "gunshot single fire",
        "object fall heavy",
        "wood crack snap break",
        "bass hit low frequency",
        "body fall impact",
        "debris collapse rubble",
    ],
    "transition_fx": [
        "whoosh fast air swoosh",
        "electronic glitch digital",
        "swipe slide interface",
        "cinematic riser buildup",
        "descending sweep fall",
        "cartoon transition swipe",
        "tape rewind backward",
        "camera flash snap",
        "warp speed fast movement",
        "energy charge release",
        "power down startup",
        "scan beep electronic",
    ],
    "stinger": [
        "short musical sting",
        "notification chime alert",
        "success win positive jingle",
        "error fail negative buzz",
        "horror scare sting",
        "fanfare short brass",
        "game power up collect",
        "news broadcast ident",
        "comedy trombone wah",
        "tension suspense sting",
        "victory celebration short",
        "magic sparkle chime",
    ],
    "music_bed": [
        "ambient background music loop",
        "cinematic dramatic underscore",
        "soft piano emotional",
        "electronic ambient drone",
        "acoustic guitar gentle background",
        "dark tension strings",
        "upbeat cheerful happy",
        "sad melancholy emotional",
        "epic orchestral powerful",
        "lo-fi chill hip hop",
        "corporate motivational inspire",
        "horror dark atmospheric",
        "romantic gentle waltz",
        "action intense fast",
    ],
    "creature": [
        "dog bark growl",
        "cat meow purr hiss",
        "horse neigh whinny",
        "bird chirp tweet song",
        "wolf howl wild",
        "lion tiger roar",
        "insect cricket bug",
        "frog toad amphibian",
        "whale dolphin ocean animal",
        "cow farm animal",
        "monster creature growl",
        "dinosaur prehistoric roar",
        "jungle wildlife exotic",
        "bear growl large",
    ],
    "vehicle": [
        "car engine start drive",
        "car pass by",
        "car crash accident",
        "truck lorry engine",
        "motorcycle engine rev",
        "train railway station",
        "airplane jet engine takeoff",
        "helicopter rotor blade",
        "boat ship engine water",
        "bicycle wheel",
        "bus public transport",
        "ambulance police siren",
        "race car formula speed",
        "submarine underwater vessel",
    ],
    "human": [
        "crowd applause cheering",
        "laughter laugh giggle",
        "scream shout yell",
        "baby cry infant",
        "crowd talking walla",
        "whisper quiet voice",
        "breathing breath exhale",
        "cough sneeze",
        "crowd booing angry",
        "crowd murmur quiet",
        "sports crowd chant",
        "children playground",
    ],
    "tension": [
        "tension suspense dark",
        "horror eerie creepy",
        "drone dark atmosphere",
        "heartbeat pulse",
        "thriller suspense music",
        "ominous low rumble",
        "sinister dark drone",
        "countdown ticking clock",
        "rising tension buildup",
        "dread anxiety texture",
        "dark ambient pad",
        "nightmare horror texture",
    ],
}

TYPE_MAX_SECS: dict[str, int] = {
    "ambience":      60,
    "music_bed":     90,
    "foley":         15,
    "impact":         6,
    "stinger":       10,
    "transition_fx":  5,
    "creature":      20,
    "vehicle":       60,
    "human":         30,
    "tension":       90,
}

LICENSE_FILTER = '(license:"Creative Commons 0" OR license:"Attribution")'


def sanitize(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()[:100]


def is_valid_audio(path: Path) -> bool:
    """Accept any audio file >= MIN_AUDIO_BYTES."""
    return path.exists() and path.stat().st_size >= MIN_AUDIO_BYTES


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; audio-pipeline/1.0)",
    "Accept":     "audio/mpeg, audio/*, */*",
}


def fetch_url(url: str, dest: Path) -> bool:
    """
    Download url -> dest.  Works correctly across Windows drives (C: temp -> D: dest).
    Returns True on success.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the SAME directory as dest to avoid cross-drive rename
    tmp_path = dest.with_suffix(".tmp")
    try:
        if _USE_REQUESTS:
            r = _requests.get(url, headers=_HEADERS, timeout=30, stream=True)
            r.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        else:
            import urllib.request
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp, \
                 open(tmp_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        if tmp_path.stat().st_size < MIN_AUDIO_BYTES:
            tmp_path.unlink(missing_ok=True)
            return False

        shutil.move(str(tmp_path), str(dest))   # works across drives
        return True
    except Exception:
        tmp_path.unlink(missing_ok=True)
        return False


def collect_search_results(
    client: freesound.FreesoundClient,
    audio_type: str,
    target: int,
) -> list[tuple[str, str]]:
    """
    Run all queries for the type and return (preview_url, filename) pairs,
    deduplicated by sound ID, up to `target` items.
    """
    queries  = TYPE_QUERIES[audio_type]
    max_secs = TYPE_MAX_SECS[audio_type]
    per_q    = max(20, (target * 2) // len(queries))  # oversample, dedup later

    seen_ids: set[int] = set()
    items: list[tuple[str, str]] = []

    for query in queries:
        if len(items) >= target * 2:   # enough candidates
            break
        try:
            results = client.text_search(
                query=query,
                filter=f'{LICENSE_FILTER} duration:[0.5 TO {max_secs}]',
                fields="id,name,previews",
                page_size=min(per_q, 150),
                sort="score",
            )
        except Exception as e:
            tqdm.write(f"  [search] {query}: {e}")
            continue

        for sound in results:
            if len(items) >= target * 2:
                break
            if sound.id in seen_ids:
                continue
            preview = (
                getattr(sound.previews, "preview_hq_mp3", None)
                or getattr(sound.previews, "preview_lq_mp3", None)
            )
            if not preview:
                continue
            seen_ids.add(sound.id)
            fname = f"{sanitize(sound.name)}.{sound.id}.mp3"
            items.append((preview, fname))

    return items[:target]


def download_type(
    client: freesound.FreesoundClient,
    audio_type: str,
    target: int,
    threads: int,
) -> int:
    out_dir = SOUNDS_DIR / audio_type
    out_dir.mkdir(parents=True, exist_ok=True)

    # Count what's already there
    already = sum(1 for f in out_dir.iterdir() if is_valid_audio(f))
    need    = target - already
    if need <= 0:
        tqdm.write(f"  {audio_type}: already have {already} >= {target}, skipping")
        return 0

    tqdm.write(f"\n[{audio_type}]  have {already}, need {need} more  "
               f"({len(TYPE_QUERIES[audio_type])} queries)")

    # Step 1: gather search results (fast, API calls only)
    items = collect_search_results(client, audio_type, already + need + 50)

    # Filter already-downloaded items
    to_dl = [
        (url, fname) for url, fname in items
        if not is_valid_audio(out_dir / fname)
    ][:need]

    if not to_dl:
        tqdm.write(f"  {audio_type}: nothing new to download")
        return 0

    # Step 2: parallel download (no conversion — just save MP3)
    downloaded = 0
    with tqdm(total=len(to_dl), desc=f"  {audio_type:12s}", unit="file",
              ncols=90, leave=True) as pbar:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            futures = {
                pool.submit(fetch_url, url, out_dir / fname): fname
                for url, fname in to_dl
            }
            for fut in as_completed(futures):
                if fut.result():
                    downloaded += 1
                pbar.update(1)
                pbar.set_postfix(ok=downloaded)

    return downloaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--per-type", type=int, default=800,
        help="Target sounds per type (default: 800 -> ~5k total)",
    )
    parser.add_argument(
        "--types", nargs="+", default=list(TYPE_QUERIES.keys()),
        choices=list(TYPE_QUERIES.keys()),
    )
    parser.add_argument(
        "--threads", type=int, default=12,
        help="Parallel download threads (default: 12)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("FREESOUND_KEY")
    if not api_key:
        print("ERROR: FREESOUND_KEY not set in .env")
        print("  1. Register at https://freesound.org/apiv2/apply/")
        print("  2. Add FREESOUND_KEY=<key> to your .env")
        sys.exit(1)

    client = freesound.FreesoundClient()
    client.set_token(api_key)

    # Clean up any leftover .tmp files from crashed previous runs
    if SOUNDS_DIR.exists():
        stale = list(SOUNDS_DIR.rglob("*.tmp"))
        for f in stale:
            f.unlink(missing_ok=True)
        if stale:
            print(f"Cleaned {len(stale)} stale .tmp files")

    # Show current on-disk counts before starting
    print()
    for atype in args.types:
        d = SOUNDS_DIR / atype
        n = sum(1 for f in d.iterdir() if is_valid_audio(f)) if d.exists() else 0
        bar = "#" * min(n // 10, 40)
        print(f"  {atype:14s} {n:4d}/{args.per_type}  [{bar}]")
    print()

    total_target = args.per_type * len(args.types)
    print(f"Target   : {args.per_type} sounds x {len(args.types)} types = {total_target:,}")
    print(f"Threads  : {args.threads}")
    print(f"Output   : {SOUNDS_DIR}")

    grand_total = 0
    for atype in args.types:
        n = download_type(client, atype, args.per_type, args.threads)
        grand_total += n
        on_disk = sum(1 for f in (SOUNDS_DIR / atype).iterdir()
                      if is_valid_audio(f)) if (SOUNDS_DIR / atype).exists() else 0
        print(f"  {atype}: +{n}  ({on_disk} on disk)")

    print(f"\nTotal downloaded : {grand_total:,}")
    print(f"Location         : {SOUNDS_DIR}\\{{type}}\\*.mp3")
    print()
    print("Next:")
    print("  python pipeline\\ingest_audio.py --manifest manifest.jsonl")


if __name__ == "__main__":
    main()
