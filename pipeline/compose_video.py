"""
Video Compositor
================
Reads timeline.jsonl, takes the top-1 audio match per scene,
slices the correct chunk from the source WAV, places it at the
scene's timestamp, and renders a final MP4 with:
  - original video (muted)
  - matched BBC audio clips laid over each scene

Usage:
    python pipeline/compose_video.py
        --video  videoplayback.mp4
        --timeline timeline.jsonl
        --out    output_matched.mp4
"""

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import soundfile as sf
from moviepy import (
    VideoFileClip,
    AudioFileClip,
    CompositeAudioClip,
    AudioArrayClip,
)
from tqdm import tqdm

ROOT = Path(__file__).parent.parent


def load_chunk_audio(source_path: str, chunk_start: float, chunk_end: float,
                     scene_duration: float, target_sr: int = 44100) -> np.ndarray | None:
    """
    Load [chunk_start, chunk_end] seconds from a WAV file.
    If the chunk is shorter than scene_duration, loop it to fill.
    Returns float32 stereo array shape (N, 2), or None on failure.
    """
    full_path = ROOT / source_path
    if not full_path.exists():
        return None

    try:
        data, sr = sf.read(str(full_path), dtype="float32", always_2d=True)
    except Exception:
        return None

    # Slice to chunk window
    start_sample = int(chunk_start * sr)
    end_sample   = int(chunk_end   * sr)
    chunk = data[start_sample:end_sample]

    if len(chunk) == 0:
        return None

    # Resample to target_sr if needed (simple linear for now)
    if sr != target_sr:
        from scipy.signal import resample
        n_out = int(len(chunk) * target_sr / sr)
        chunk = resample(chunk, n_out).astype(np.float32)

    # Make stereo
    if chunk.ndim == 1:
        chunk = np.stack([chunk, chunk], axis=1)
    elif chunk.shape[1] == 1:
        chunk = np.repeat(chunk, 2, axis=1)
    chunk = chunk[:, :2]   # drop >2 channels

    # Loop to fill scene_duration
    needed = int(scene_duration * target_sr)
    if len(chunk) < needed:
        repeats = (needed // len(chunk)) + 1
        chunk = np.tile(chunk, (repeats, 1))
    chunk = chunk[:needed]

    # Gentle fade-in/out (0.1s) to avoid clicks
    fade = min(int(0.1 * target_sr), len(chunk) // 4)
    if fade > 0:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        chunk[:fade]  *= ramp[:, None]
        chunk[-fade:] *= ramp[::-1, None]

    # Normalise to -12 dBFS ceiling so it doesn't overpower
    peak = np.abs(chunk).max()
    if peak > 0:
        chunk = chunk / peak * 0.25   # -12 dBFS

    return chunk


def compose(video_path: str, timeline_path: str, output_path: str) -> None:
    print(f"Video   : {video_path}")
    print(f"Timeline: {timeline_path}")

    # Load timeline
    entries = []
    with open(timeline_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    entries.sort(key=lambda e: e["start_sec"])
    print(f"Scenes  : {len(entries)}")

    # Open video, mute original audio
    clip = VideoFileClip(video_path)
    video_duration = clip.duration
    target_sr = 44100

    print(f"Duration: {video_duration:.1f}s  fps={clip.fps}")

    # Build per-scene audio arrays
    audio_clips = []
    matched = 0
    skipped = 0

    for entry in tqdm(entries, desc="Building audio tracks"):
        scene_start = entry["start_sec"]
        scene_end   = min(entry["end_sec"], video_duration)
        scene_dur   = scene_end - scene_start

        if scene_dur <= 0 or not entry["matches"]:
            skipped += 1
            continue

        top = entry["matches"][0]
        source_path  = top["source_path"]
        chunk_start  = top["chunk_start_sec"]
        chunk_end    = top["chunk_end_sec"]

        samples = load_chunk_audio(source_path, chunk_start, chunk_end,
                                   scene_dur, target_sr)
        if samples is None:
            skipped += 1
            continue

        # AudioArrayClip expects shape (N, 2) and fps
        ac = AudioArrayClip(samples, fps=target_sr)
        ac = ac.with_start(scene_start)
        audio_clips.append(ac)
        matched += 1

    print(f"Matched : {matched} scenes  |  skipped: {skipped}")

    if not audio_clips:
        print("No audio clips to compose — check that source WAV files exist.")
        sys.exit(1)

    # Composite all audio tracks, no original video audio
    composite_audio = CompositeAudioClip(audio_clips)
    composite_audio = composite_audio.with_duration(video_duration)

    # Attach to video (original audio muted = we just replace it)
    final = clip.with_audio(composite_audio)

    print(f"Rendering -> {output_path} ...")
    final.write_videofile(
        output_path,
        codec="libx264",
        audio_codec="aac",
        audio_fps=target_sr,
        temp_audiofile="temp_audio.m4a",
        remove_temp=True,
        logger="bar",
        threads=4,
        preset="fast",
    )
    print(f"Done: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video",    default="videoplayback.mp4")
    parser.add_argument("--timeline", default="timeline.jsonl")
    parser.add_argument("--out",      default="output_matched.mp4")
    args = parser.parse_args()
    compose(args.video, args.timeline, args.out)
