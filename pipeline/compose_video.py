"""
Video Compositor
================
Reads timeline.jsonl, takes the top-1 audio match per scene,
fetches the corresponding audio chunk from the HuggingFace repo
(Pandago/qad-buc), slices the correct window, and renders a final
MP4 with the original video (muted) + matched audio overlay.

Audio files are downloaded to a temporary directory and automatically
deleted once the video has been rendered — nothing is cached on disk.

Requirements:
    .env must contain:  HF_API_KEY=hf_...   (READ access to the repo)

Usage:
    python pipeline/compose_video.py
        --video  videoplayback.mp4
        --timeline timeline.jsonl
        --out    output_matched.mp4
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import soundfile as sf
from moviepy.editor import (
    VideoFileClip,
    AudioFileClip,
    CompositeAudioClip,
)
from moviepy.audio.AudioClip import AudioArrayClip
from tqdm import tqdm

# HF fetcher — all local audio access is replaced by this
from hf_fetch import fetch_audio_batch


# ── Audio loading (operates on a local temp path, NOT ROOT-relative) ───────────
def load_chunk_audio(
    local_path: str,
    chunk_start: float,
    chunk_end: float,
    scene_duration: float,
    target_sr: int = 44100,
) -> np.ndarray | None:
    """
    Load [chunk_start, chunk_end] seconds from a local audio file.

    local_path is a fully-resolved temp path handed to us by fetch_audio_batch.
    If the chunk is shorter than scene_duration it is looped to fill the scene.

    Returns float32 stereo array shape (N, 2), or None on failure.
    """
    path = Path(local_path)
    if not path.exists():
        return None

    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception:
        return None

    # Slice to the requested chunk window
    start_sample = int(chunk_start * sr)
    end_sample   = int(chunk_end   * sr)
    chunk = data[start_sample:end_sample]

    if len(chunk) == 0:
        return None

    # Resample to target_sr when needed
    if sr != target_sr:
        from scipy.signal import resample
        n_out = int(len(chunk) * target_sr / sr)
        chunk = resample(chunk, n_out).astype(np.float32)

    # Ensure stereo
    if chunk.ndim == 1:
        chunk = np.stack([chunk, chunk], axis=1)
    elif chunk.shape[1] == 1:
        chunk = np.repeat(chunk, 2, axis=1)
    chunk = chunk[:, :2]   # drop channels > 2

    # Loop to fill scene_duration
    needed = int(scene_duration * target_sr)
    if len(chunk) < needed:
        repeats = (needed // len(chunk)) + 1
        chunk = np.tile(chunk, (repeats, 1))
    chunk = chunk[:needed]

    # Gentle 0.1 s fade-in / fade-out to avoid clicks at scene boundaries
    fade = min(int(0.1 * target_sr), len(chunk) // 4)
    if fade > 0:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        chunk[:fade]  *= ramp[:, None]
        chunk[-fade:] *= ramp[::-1, None]

    # Normalise to −12 dBFS ceiling so audio doesn't overpower the video
    peak = np.abs(chunk).max()
    if peak > 0:
        chunk = chunk / peak * 0.25

    return chunk


# ── Main composition ───────────────────────────────────────────────────────────
def compose(video_path: str, timeline_path: str, output_path: str) -> None:
    print(f"Video   : {video_path}")
    print(f"Timeline: {timeline_path}")

    # ── Load timeline ────────────────────────────────────────────────────────
    entries: list[dict] = []
    with open(timeline_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    entries.sort(key=lambda e: e["start_sec"])
    print(f"Scenes  : {len(entries)}")

    # ── Collect every unique source_path needed ──────────────────────────────
    all_source_paths: list[str] = []

    for entry in entries:
        if entry.get("matches"):
            sp = entry["matches"][0].get("source_path", "")
            if sp:
                all_source_paths.append(sp)

        for layer in entry.get("layer_matches", []):
            sp = layer.get("source_path", "")
            if sp:
                all_source_paths.append(sp)

    unique_count = len(set(all_source_paths))
    print(f"Unique audio files to fetch: {unique_count}  (from HF repo Pandago/qad-buc)")

    # ── Open video ───────────────────────────────────────────────────────────
    clip = VideoFileClip(video_path)
    video_duration = clip.duration
    target_sr = 44100
    print(f"Duration: {video_duration:.1f}s  fps={clip.fps}")

    # ── Fetch all audio files from HF, build audio tracks, render ────────────
    # fetch_audio_batch downloads to a temp dir; the dir is wiped when
    # the `with` block exits — AFTER the video has been fully rendered.
    with fetch_audio_batch(all_source_paths) as local_map:
        audio_clips = []
        matched  = 0
        skipped  = 0

        for entry in tqdm(entries, desc="Building audio tracks"):
            scene_start = entry["start_sec"]
            scene_end   = min(entry["end_sec"], video_duration)
            scene_dur   = scene_end - scene_start

            if scene_dur <= 0 or not entry.get("matches"):
                skipped += 1
                continue

            scene_mix = None

    # ---------- PRIMARY ----------
    top = entry["matches"][0]

    local_path = local_map.get(top["source_path"])

    if local_path:
        primary = load_chunk_audio(
            str(local_path),
            top["chunk_start_sec"],
            top["chunk_end_sec"],
            scene_dur,
            target_sr,
        )

        if primary is not None:
            primary_vol = top.get("volume", 0.62)
            scene_mix = primary * primary_vol

    # ---------- LAYERS ----------
    for layer in entry.get("layer_matches", []):

        local_path = local_map.get(layer["source_path"])

        if not local_path:
            continue

        layer_audio = load_chunk_audio(
            str(local_path),
            layer["chunk_start_sec"],
            layer["chunk_end_sec"],
            scene_dur,
            target_sr,
        )

        if layer_audio is None:
            continue

        layer_vol = layer.get("volume", 0.35)

        if scene_mix is None:
            scene_mix = layer_audio * layer_vol
        else:
            scene_mix += layer_audio * layer_vol

# ---------- NORMALIZE ----------
        if scene_mix is None:
            skipped += 1
            continue
        print(
            f"Scene {entry['scene_id']} "
            f"primary=1 "
            f"layers={len(entry.get('layer_matches', []))}"
        )
        peak = np.max(np.abs(scene_mix))

        if peak > 1.0:
            scene_mix = scene_mix / peak

        ac = AudioArrayClip(scene_mix.astype(np.float32), fps=target_sr)
        ac = ac.set_start(scene_start)

        audio_clips.append(ac)
        matched += 1

        print(f"Matched : {matched} scenes  |  skipped: {skipped}")

        if not audio_clips:
            print("No audio clips to compose — check that source files exist in the HF repo.")
            sys.exit(1)

        # Composite all audio tracks over the full video duration
        composite_audio = CompositeAudioClip(audio_clips)
        composite_audio = composite_audio.set_duration(video_duration)

        final = clip.set_audio(composite_audio)

        print(f"Rendering -> {output_path} ...")
        final.write_videofile(
            output_path,
            codec       = "libx264",
            audio_codec = "aac",
            audio_fps   = target_sr,
            temp_audiofile = "temp_audio.m4a",
            remove_temp = True,
            logger      = "bar",
            threads     = 4,
            preset      = "fast",
        )

    # ── Temp dir is deleted here — no audio left on disk ────────────────────
    print(f"\nDone: {output_path}")
    print("Temporary audio files deleted.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video",    default="videoplayback.mp4")
    parser.add_argument("--timeline", default="timeline.jsonl")
    parser.add_argument("--out",      default="output_matched.mp4")
    args = parser.parse_args()
    compose(args.video, args.timeline, args.out)