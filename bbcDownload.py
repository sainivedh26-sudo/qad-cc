"""
BBC Sound Effects Downloader — fixed URL + validation + resume support
=======================================================================
Uses the correct CDN: https://sound-effects-media.bbcrewind.co.uk/wav/

Run modes
---------
  python bbcDownload.py             # download everything
  python bbcDownload.py --max-secs 30   # only files ≤30 s (fastest start)
  python bbcDownload.py --recheck   # re-download any HTML placeholder files
"""

import argparse
import csv
import os
import re
import shutil
import sys
import time
import urllib.request
from multiprocessing.pool import ThreadPool
from pathlib import Path

THREAD_COUNT = 8
MAX_FILENAME_LENGTH = 143
BBC_CDN = "https://sound-effects-media.bbcrewind.co.uk/wav/"

# Minimum valid WAV size — HTML error pages are always ~3 KB
MIN_WAV_BYTES = 8_000


def is_valid_wav(path: Path) -> bool:
    """Return True if the file looks like a real RIFF/WAVE file."""
    if not path.exists():
        return False
    if path.stat().st_size < MIN_WAV_BYTES:
        return False
    try:
        with open(path, "rb") as f:
            header = f.read(4)
        return header == b"RIFF"
    except OSError:
        return False


class Downloader:
    def __init__(self, thread_count=THREAD_COUNT, max_secs=None, recheck=False):
        self.thread_count = thread_count
        self.max_secs = max_secs
        self.recheck = recheck
        self.samples = self.get_samples()
        self.total_count = len(self.samples)
        self.finished = 0
        self.failed = 0
        print(f"Files queued for download: {self.total_count}")

    def download_all(self):
        if not self.samples:
            print("Nothing to download.")
            return
        results = ThreadPool(self.thread_count).map(self.download, self.samples)
        print("Download complete.")
        failures = [(fp, e) for ok, fp, e in results if not ok]
        if failures:
            print(f"{len(failures)} failures:")
            for fp, e in failures[:20]:
                print(f"  {fp}: {e}")

    def download(self, sample):
        url, filepath = sample
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; audio-pipeline/1.0)",
                    "Accept": "audio/wav, audio/*, */*",
                },
            )
            temp_path, _headers = urllib.request.urlretrieve(url)
            # Validate before saving
            temp = Path(temp_path)
            if not is_valid_wav(temp):
                temp.unlink(missing_ok=True)
                self.failed += 1
                return False, filepath, "Response was not a valid WAV"
            shutil.move(temp_path, filepath)
            self.finished += 1
            pct = self.finished / self.total_count * 100
            print(f"[{pct:.0f}%] {filepath.name}")
            return True, filepath, None
        except Exception as e:
            self.failed += 1
            print(f"FAILED {filepath.name}: {e}", file=sys.stderr)
            return False, filepath, e

    def get_samples(self):
        samples = []
        csv_path = Path(__file__).parent / "BBCSoundEffects.csv"
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Duration filter
                if self.max_secs is not None:
                    secs = int(row["secs"]) if row["secs"].isdigit() else 9999
                    if secs > self.max_secs:
                        continue

                folder = self.sanitize(row["CDName"])
                location = row["location"]
                suffix = "." + location
                max_desc_len = MAX_FILENAME_LENGTH - len(suffix)
                desc = self.sanitize(row["description"])[:max_desc_len]
                filename = desc + suffix
                filepath = Path("sounds") / folder / filename

                # Skip if already valid
                if is_valid_wav(filepath) and not self.recheck:
                    continue

                url = BBC_CDN + location
                samples.append((url, filepath))

        # Sort shorter files first — get usable data faster
        return samples

    @staticmethod
    def sanitize(path: str) -> str:
        return re.sub(r"[^\w\-&,()\. ]", "_", path).strip()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-secs",
        type=int,
        default=None,
        help="Only download files with duration <= N seconds",
    )
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="Re-download files that exist but are HTML placeholders",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=THREAD_COUNT,
    )
    args = parser.parse_args()

    d = Downloader(
        thread_count=args.threads,
        max_secs=args.max_secs,
        recheck=args.recheck,
    )
    d.download_all()
