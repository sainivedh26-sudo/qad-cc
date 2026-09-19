"""
Upload audio library to Hugging Face using upload_large_folder.
===============================================================
- Stages valid-only files via hardlinks (zero extra disk space)
- BBC: only RIFF/WAV files — skips HTML placeholders
- FreeSound: all MP3s
- Resumable, multi-threaded, resilient to connection errors
- Live top progress bar showing files committed

Setup:
  huggingface-cli login
  pip install -U huggingface_hub

Usage:
  python pipeline/upload_to_hf.py --repo yourname/qdrant-audio-library
  python pipeline/upload_to_hf.py --repo yourname/qdrant-audio-library --dry-run
"""

import argparse
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from tqdm import tqdm

try:
    from huggingface_hub import HfApi, create_repo
except ImportError:
    print("ERROR: pip install -U huggingface_hub")
    sys.exit(1)

_HF_TOKEN = os.environ.get("HF_API_KEY")

ROOT         = Path(__file__).parent.parent
SOUNDS_DIR   = ROOT / "sounds"
STAGING_DIR  = ROOT / "sounds_upload"   # hardlink staging area
MIN_AUDIO    = 8_000
AUDIO_EXTS   = {".wav", ".mp3", ".ogg", ".flac"}


# ── Validation ─────────────────────────────────────────────────────────────────
def is_valid_audio(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < MIN_AUDIO:
        return False
    if path.suffix.lower() in (".mp3", ".ogg", ".flac"):
        return True
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"RIFF"
    except OSError:
        return False


# ── Hardlink staging ───────────────────────────────────────────────────────────
def link_or_copy(src: Path, dst: Path) -> None:
    """Create a hardlink dst -> src. Falls back to copy on cross-device or error."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def build_staging(staging: Path) -> int:
    """
    Populate staging dir with hardlinks for valid BBC + FreeSound files.
    Returns total file count.
    """
    total = 0

    # ── BBC valid WAVs ────────────────────────────────────────────────────────
    print("Scanning BBC files...")
    bbc_src = SOUNDS_DIR
    for folder in sorted(bbc_src.iterdir()):
        if not folder.is_dir() or folder.name == "FreeSound":
            continue
        for f in folder.iterdir():
            if f.suffix.lower() == ".wav" and is_valid_audio(f):
                dst = staging / "BBC" / folder.name / f.name
                link_or_copy(f, dst)
                total += 1

    # ── FreeSound ─────────────────────────────────────────────────────────────
    print("Scanning FreeSound files...")
    fs_src = SOUNDS_DIR / "FreeSound"
    if fs_src.exists():
        for type_dir in sorted(fs_src.iterdir()):
            if not type_dir.is_dir():
                continue
            for f in type_dir.iterdir():
                if f.suffix.lower() in AUDIO_EXTS and is_valid_audio(f):
                    dst = staging / "FreeSound" / type_dir.name / f.name
                    link_or_copy(f, dst)
                    total += 1

    return total


# ── Progress monitor thread ────────────────────────────────────────────────────
def monitor_progress(staging: Path, total: int, stop_event: threading.Event) -> None:
    """
    Watches the upload cache inside staging dir and updates a top-pinned
    tqdm bar with committed file count.
    """
    cache_dir = staging / ".cache" / "huggingface"
    bar = tqdm(
        total=total,
        desc="  Committed",
        unit="file",
        position=0,
        leave=True,
        ncols=90,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} files [{elapsed}<{remaining}]",
    )
    last = 0
    while not stop_event.is_set():
        committed = 0
        if cache_dir.exists():
            for meta_file in cache_dir.rglob("*.metadata"):
                try:
                    content = meta_file.read_text()
                    if '"commit_info"' in content:
                        committed += 1
                except OSError:
                    pass
        if committed > last:
            bar.update(committed - last)
            last = committed
        time.sleep(5)
    # Don't auto-fill — show real final count only
    bar.close()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo",      required=True,
                        help="HF repo, e.g. yourname/qdrant-audio-library")
    parser.add_argument("--workers",   type=int, default=16)
    parser.add_argument("--dry-run",   action="store_true",
                        help="Stage files and count only, no upload")
    parser.add_argument("--no-restage", action="store_true",
                        help="Skip re-scanning if staging dir already exists")
    args = parser.parse_args()

    # ── Stage valid files ────────────────────────────────────────────────────
    if STAGING_DIR.exists() and args.no_restage:
        total = sum(1 for _ in STAGING_DIR.rglob("*") if _.is_file()
                    and not _.name.endswith(".metadata")
                    and ".cache" not in _.parts)
        print(f"Using existing staging dir: {total:,} files")
    else:
        print(f"Building staging dir: {STAGING_DIR}")
        STAGING_DIR.mkdir(exist_ok=True)
        total = build_staging(STAGING_DIR)
        print(f"Staged {total:,} files  (hardlinks, no extra disk space)")

    # Size estimate
    size_gb = sum(
        f.stat().st_size for f in STAGING_DIR.rglob("*")
        if f.is_file() and ".cache" not in str(f)
    ) / 1e9
    print(f"Total size: {size_gb:.2f} GB")
    print(f"Destination: https://huggingface.co/datasets/{args.repo}")

    if args.dry_run:
        print("\n[dry-run] Staging complete. Re-run without --dry-run to upload.")
        return

    api = HfApi(token=_HF_TOKEN)

    # ── Create repo ──────────────────────────────────────────────────────────
    create_repo(repo_id=args.repo, repo_type="dataset",
                exist_ok=True, private=True, token=_HF_TOKEN)
    print(f"\nRepo ready. Starting upload with {args.workers} workers...\n")

    # ── Start progress monitor ───────────────────────────────────────────────
    stop_evt = threading.Event()
    monitor  = threading.Thread(
        target=monitor_progress, args=(STAGING_DIR, total, stop_evt), daemon=True
    )
    monitor.start()

    # ── Upload ───────────────────────────────────────────────────────────────
    try:
        api.upload_large_folder(
            repo_id=args.repo,
            repo_type="dataset",
            folder_path=str(STAGING_DIR),
            num_workers=args.workers,
            token=_HF_TOKEN,
        )
    finally:
        stop_evt.set()
        monitor.join(timeout=10)

    print(f"\nUpload complete: https://huggingface.co/datasets/{args.repo}")
    print(f"Staging dir kept at {STAGING_DIR} — safe to delete manually.")


if __name__ == "__main__":
    main()
