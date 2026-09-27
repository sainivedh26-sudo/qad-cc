"""
Audio Stem Separation
=====================
Extracts audio from a video file and separates it into two stems:
  - vocals.wav    — dialogue, voice, narration
  - no_vocals.wav — background music, ambient, SFX

Uses Meta's Demucs (htdemucs model) with automatic GPU acceleration.
The model is downloaded once (~320 MB) and cached in ~/.cache/torch/hub/.

Output:
    separated/{video_filename_stem}/vocals.wav
    separated/{video_filename_stem}/no_vocals.wav

Install:
    pip install demucs

Usage:
    python pipeline/separate_audio.py frontend/public/input_video.mp4
    python pipeline/separate_audio.py input.mp4 --model htdemucs_ft
"""

import argparse
import shutil
import sys
from pathlib import Path

import torch

ROOT          = Path(__file__).parent.parent
SEPARATED_DIR = ROOT / "separated"
DEVICE        = "cuda" if torch.cuda.is_available() else "cpu"


def separate(video_path: str, model: str = "htdemucs") -> tuple[Path, Path]:
    """
    Run demucs --two-stems=vocals on the video file.

    Returns (vocals_path, no_vocals_path) — both as absolute Paths.
    Raises RuntimeError if demucs is not installed or separation fails.
    """
    try:
        from demucs.separate import main as demucs_main
    except ImportError:
        raise RuntimeError(
            "demucs not installed. Run:  pip install demucs\n"
            "GPU acceleration requires torch with CUDA (already installed in your venv)."
        )

    video_path = Path(video_path).resolve()
    stem_name  = video_path.stem          # e.g. "input_video"
    out_dir    = SEPARATED_DIR / stem_name
    out_dir.mkdir(parents=True, exist_ok=True)

    vocals_dst    = out_dir / "vocals.wav"
    novocals_dst  = out_dir / "no_vocals.wav"

    # Skip if both stems already exist (re-run idempotent)
    if vocals_dst.exists() and novocals_dst.exists():
        print(f"[separate] Stems already exist at {out_dir} — skipping.")
        return vocals_dst, novocals_dst

    # Demucs writes to: {SEPARATED_DIR}/{model}/{stem_name}/vocals.wav
    demucs_out = SEPARATED_DIR / "_demucs_tmp"
    demucs_out.mkdir(parents=True, exist_ok=True)

    print(f"[separate] Running demucs on {video_path.name}  (device={DEVICE}, model={model})")
    print("[separate] This may take 1-3 minutes on first run (model download + separation).")

    demucs_main([
        "--two-stems=vocals",
        f"--device={DEVICE}",
        f"--name={model}",
        f"--out={demucs_out}",
        str(video_path),
    ])

    # Move stems to our canonical location
    demucs_model_dir = demucs_out / model / stem_name
    if not demucs_model_dir.exists():
        raise RuntimeError(
            f"[separate] Demucs output not found at {demucs_model_dir}. "
            "Check that ffmpeg is installed (needed to extract audio from MP4)."
        )

    shutil.move(str(demucs_model_dir / "vocals.wav"),    str(vocals_dst))
    shutil.move(str(demucs_model_dir / "no_vocals.wav"), str(novocals_dst))

    # Clean up demucs temp tree
    shutil.rmtree(str(demucs_out), ignore_errors=True)

    print(f"[separate] Stems written:")
    print(f"  Voice      : {vocals_dst}")
    print(f"  Background : {novocals_dst}")

    return vocals_dst, novocals_dst


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("video",  help="Input video file (MP4, MKV, …)")
    parser.add_argument("--model", default="htdemucs",
                        help="Demucs model name (default: htdemucs)")
    args = parser.parse_args()

    vocals, novocals = separate(args.video, args.model)
    print(f"\nVocals    : {vocals}")
    print(f"Background: {novocals}")
