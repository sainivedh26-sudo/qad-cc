"""
Video Compositor
================
Reads timeline.jsonl and renders a final MP4 that mixes:

  [A] Original voice stem    — vocals.wav   (from separate_audio.py)
  [B] Original background    — no_vocals.wav (from separate_audio.py)
  [C] AI scene audio         — primary match per scene  (from HF repo)
  [D] AI complementary layers — Discovery/Recommendation matches (from HF repo)

Each track has an independently controllable volume.  Per-scene AI audio
can be muted entirely (clip flag in timeline).

Stems A and B run for the full video duration.
Tracks C and D are placed at the scene's start_sec timestamp.

Requirements:
    .env must contain:  HF_API_KEY=hf_...   (READ access to Pandago/qad-buc)
    separate_audio.py must have been run to produce the stem files, OR
    pass --vocals-vol 0 --novocals-vol 0 to skip stem mixing.

Usage:
    python pipeline/compose_video.py
        --video      frontend/public/input_video.mp4
        --timeline   timeline.jsonl
        --out        frontend/public/output_matched.mp4
        --vocals-vol 0.8
        --novocals-vol 0.0
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import soundfile as sf
from moviepy import (
    VideoFileClip,
    CompositeAudioClip,
    AudioArrayClip,
)
from tqdm import tqdm

from hf_fetch import fetch_audio_batch

ROOT          = Path(__file__).parent.parent
SEPARATED_DIR = ROOT / "separated"

# Crossfade: each AI clip extends this many seconds past its scene boundary.
# CompositeAudioClip sums overlapping regions → equal-power fade-out/fade-in.
CROSSFADE_SEC = 1.5

# RMS target before volume scaling (0.15 gives good headroom with multiple layers summed)
TARGET_RMS = 0.15


# ── Audio utilities ────────────────────────────────────────────────────────────

def _to_stereo_f32(data: np.ndarray) -> np.ndarray:
    """Convert any channel layout to float32 stereo (N, 2)."""
    if data.ndim == 1:
        data = np.stack([data, data], axis=1)
    elif data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    return data[:, :2].astype(np.float32)


def _resample(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return data
    from scipy.signal import resample
    n_out = int(len(data) * dst_sr / src_sr)
    return resample(data, n_out).astype(np.float32)


def _apply_fades(chunk: np.ndarray, sr: int, fade_sec: float = 0.1) -> np.ndarray:
    """Equal-power (sin/cos) fade-in and fade-out for seamless crossfades.

    Equal-power preserves perceived loudness: sin²θ + cos²θ = 1 at all points,
    so the summed energy during an overlap stays constant (no dip or surge).
    """
    fade = min(int(fade_sec * sr), len(chunk) // 4)
    if fade > 0:
        t        = np.linspace(0.0, np.pi / 2, fade, dtype=np.float32)
        fade_in  = np.sin(t)   # 0 → 1
        fade_out = np.cos(t)   # 1 → 0
        chunk[:fade]  *= fade_in[:, None]
        chunk[-fade:] *= fade_out[:, None]
    return chunk


def load_stem(stem_path: Path, target_sr: int, volume: float, duration_sec: float) -> np.ndarray | None:
    """
    Load a full-length stem (vocals or no_vocals) and scale to the given volume.
    Pads / trims to exactly duration_sec.
    """
    if not stem_path.exists() or volume <= 0.0:
        return None
    try:
        data, sr = sf.read(str(stem_path), dtype="float32", always_2d=True)
    except Exception as e:
        print(f"  [compose] WARN stem read failed {stem_path.name}: {e}")
        return None

    data = _to_stereo_f32(data)
    data = _resample(data, sr, target_sr)

    needed = int(duration_sec * target_sr)
    if len(data) < needed:
        pad = np.zeros((needed - len(data), 2), dtype=np.float32)
        data = np.concatenate([data, pad], axis=0)
    else:
        data = data[:needed]

    rms = float(np.sqrt(np.mean(data ** 2)))
    if rms > 1e-6:
        gain = min(TARGET_RMS / rms, 10.0)   # cap at +20 dB boost
        data = data * gain
    data = np.clip(data, -0.95, 0.95)
    data = data * float(volume)

    return data


def load_chunk_audio(
    local_path: str,
    chunk_start: float,
    chunk_end: float,
    scene_duration: float,
    target_sr: int = 44100,
    volume: float = 0.35,
    fade_sec: float = 0.1,
) -> np.ndarray | None:
    """
    Load [chunk_start, chunk_end] from a local audio file, loop to fill
    scene_duration, apply fades, and scale to volume.

    fade_sec controls the fade-in / fade-out length.  Pass CROSSFADE_SEC here
    so the clip extends (and fades out) into the next scene's territory —
    CompositeAudioClip sums the overlap for a smooth crossfade.

    Returns float32 stereo (N, 2) or None on failure.
    """
    path = Path(local_path)
    if not path.exists():
        return None
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception:
        return None

    start_sample = int(chunk_start * sr)
    end_sample   = int(chunk_end   * sr)
    chunk = data[start_sample:end_sample]

    if len(chunk) == 0:
        return None

    chunk = _to_stereo_f32(_resample(chunk, sr, target_sr) if sr != target_sr else chunk)

    # Loop to fill scene_duration (which already includes the crossfade tail)
    needed = int(scene_duration * target_sr)
    if len(chunk) < needed:
        repeats = (needed // len(chunk)) + 1
        chunk = np.tile(chunk, (repeats, 1))
    chunk = chunk[:needed]

    chunk = _apply_fades(chunk, target_sr, fade_sec=fade_sec)

    rms = float(np.sqrt(np.mean(chunk ** 2)))
    if rms > 1e-6:
        gain = min(TARGET_RMS / rms, 10.0)   # cap at +20 dB boost
        chunk = chunk * gain
    chunk = np.clip(chunk, -0.95, 0.95)
    chunk = chunk * float(volume)

    return chunk


# ── Main composition ───────────────────────────────────────────────────────────

def compose(
    video_path:    str,
    timeline_path: str,
    output_path:   str,
    vocals_vol:    float = 0.8,
    novocals_vol:  float = 0.0,
) -> None:
    print(f"Video   : {video_path}")
    print(f"Timeline: {timeline_path}")
    print(f"Stems   : voice={vocals_vol:.0%}  background={novocals_vol:.0%}")

    # ── Load timeline ────────────────────────────────────────────────────────
    entries: list[dict] = []
    with open(timeline_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    entries.sort(key=lambda e: e["start_sec"])
    print(f"Scenes  : {len(entries)}")

    # ── Open video ───────────────────────────────────────────────────────────
    clip          = VideoFileClip(video_path)
    video_duration = clip.duration
    target_sr     = 44100
    print(f"Duration: {video_duration:.1f}s  fps={clip.fps}")

    # ── Collect HF source paths (primary + layers, skip muted scenes) ────────
    all_source_paths: list[str] = []
    for entry in entries:
        if entry.get("muted"):          # scene AI audio muted by user
            continue
        if entry.get("matches"):
            sp = entry["matches"][0].get("source_path", "")
            if sp:
                all_source_paths.append(sp)
        for lm in entry.get("layer_matches", []):
            sp = lm.get("source_path", "")
            if sp:
                all_source_paths.append(sp)

    unique_count = len(set(all_source_paths))
    print(f"HF audio files to fetch: {unique_count}")

    # ── Fetch all HF audio files once, build all tracks inside the context ───
    with fetch_audio_batch(all_source_paths) as local_map:
        audio_clips: list[AudioArrayClip] = []

        # ── [A+B] Stem tracks (full-length, global) ──────────────────────────
        video_stem = Path(video_path).stem
        stems_dir  = SEPARATED_DIR / video_stem

        vocals_data = load_stem(stems_dir / "vocals.wav",    target_sr, vocals_vol,   video_duration)
        novocals_data = load_stem(stems_dir / "no_vocals.wav", target_sr, novocals_vol, video_duration)

        if vocals_data is not None:
            ac = AudioArrayClip(vocals_data, fps=target_sr)
            ac = ac.with_start(0)
            audio_clips.append(ac)
            print(f"  [stems] Voice track loaded  (vol={vocals_vol:.0%})")

        if novocals_data is not None:
            ac = AudioArrayClip(novocals_data, fps=target_sr)
            ac = ac.with_start(0)
            audio_clips.append(ac)
            print(f"  [stems] Background track loaded  (vol={novocals_vol:.0%})")

        if vocals_data is None and novocals_data is None and (vocals_vol > 0 or novocals_vol > 0):
            print(f"  [stems] WARN: stem files not found in {stems_dir}")
            print("  Run: python pipeline/separate_audio.py <video>  to generate them.")

        # ── [C+D] Scene AI audio tracks ──────────────────────────────────────
        matched = 0
        skipped = 0

        for entry in tqdm(entries, desc="Building AI audio tracks"):
            scene_start = entry["start_sec"]
            scene_end   = min(entry["end_sec"], video_duration)
            scene_dur   = scene_end - scene_start

            if scene_dur <= 0 or not entry.get("matches"):
                skipped += 1
                continue

            # User muted AI audio for this scene
            if entry.get("muted"):
                skipped += 1
                continue

            top         = entry["matches"][0]
            source_path = top["source_path"]
            chunk_start = top["chunk_start_sec"]
            chunk_end   = top["chunk_end_sec"]
            primary_vol = top.get("volume", 0.62)   # 0.62 = PRIMARY_VOLUME from layer_audio.py

            local_path = local_map.get(source_path)
            if local_path is None:
                skipped += 1
                continue

            # Extend clip by CROSSFADE_SEC past the scene boundary so it fades
            # out into the next scene — CompositeAudioClip sums the overlap.
            xfade_dur = scene_dur + CROSSFADE_SEC

            samples = load_chunk_audio(
                str(local_path), chunk_start, chunk_end,
                scene_duration=xfade_dur, target_sr=target_sr,
                volume=primary_vol, fade_sec=CROSSFADE_SEC,
            )
            if samples is None:
                skipped += 1
                continue

            ac = AudioArrayClip(samples, fps=target_sr)
            ac = ac.with_start(scene_start)
            audio_clips.append(ac)
            matched += 1

            # Log what's being mixed for this scene
            audio_type = top.get("audio_type", "?")
            print(f"  [{scene_start:5.1f}s]  L1 {audio_type:<14} vol={primary_vol:.2f}"
                  f"  {source_path.split('/')[-1][:38]}")

            # Complementary layers — same crossfade treatment
            layers_added = 0
            for lm in entry.get("layer_matches", []):
                lsp       = lm.get("source_path", "")
                lstart    = lm.get("chunk_start_sec", 0.0)
                lend      = lm.get("chunk_end_sec",   0.0)
                layer_vol = lm.get("volume", 0.38)   # 0.38 = new layer default
                l_type    = lm.get("audio_type", "?")

                l_local = local_map.get(lsp)
                if not l_local:
                    print(f"           L{layers_added+2} {l_type:<14} MISSING {lsp!r:.40}")
                    continue
                l_samples = load_chunk_audio(
                    str(l_local), lstart, lend,
                    scene_duration=xfade_dur, target_sr=target_sr,
                    volume=layer_vol, fade_sec=CROSSFADE_SEC,
                )
                if l_samples is None:
                    continue
                lac = AudioArrayClip(l_samples, fps=target_sr)
                lac = lac.with_start(scene_start)
                audio_clips.append(lac)
                layers_added += 1
                print(f"           L{layers_added+1} {l_type:<14} vol={layer_vol:.2f}"
                      f"  {lsp.split('/')[-1][:38]}")

        print(f"AI matched : {matched} scenes  |  skipped: {skipped}")

        if not audio_clips:
            print("No audio tracks to compose — nothing to render.")
            sys.exit(1)

        # ── Composite + render ───────────────────────────────────────────────
        composite = CompositeAudioClip(audio_clips)
        composite = composite.with_duration(video_duration)

        final = clip.with_audio(composite)

        print(f"Rendering -> {output_path} ...")
        final.write_videofile(
            output_path,
            codec          = "libx264",
            audio_codec    = "aac",
            audio_fps      = target_sr,
            temp_audiofile = "temp_audio.m4a",
            remove_temp    = True,
            logger         = "bar",
            threads        = 4,
            preset         = "fast",
        )

    print(f"\nDone: {output_path}")
    print("Temporary HF audio files deleted.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video",        default="videoplayback.mp4")
    parser.add_argument("--timeline",     default="timeline.jsonl")
    parser.add_argument("--out",          default="output_matched.mp4")
    parser.add_argument("--vocals-vol",   type=float, default=0.8,
                        help="Voice stem volume 0..1 (default 0.8)")
    parser.add_argument("--novocals-vol", type=float, default=0.0,
                        help="Background stem volume 0..1 (default 0.0)")
    args = parser.parse_args()
    compose(
        args.video, args.timeline, args.out,
        vocals_vol=args.vocals_vol,
        novocals_vol=args.novocals_vol,
    )
