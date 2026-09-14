"""
Video Analysis Pipeline
=======================
Uses PySceneDetect + OpenCV to:
  1. Detect scene boundaries
  2. Extract keyframes per scene
  3. Score motion (optical flow magnitude)
  4. Analyze color palette → mood
  5. Build query objects (JSONL) for the Rust retrieval service

No VLM used — scene description is fully heuristic.

Usage:
    python pipeline/analyze_video.py <video.mp4> [--queries queries.jsonl]
"""

import argparse
import json
import math
import os
import warnings
from pathlib import Path
from typing import NamedTuple

warnings.filterwarnings("ignore")

import cv2
import numpy as np
from tqdm import tqdm

from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector

from audio_type import classify_category  # reuse for needs mapping


# ── Color palette → mood ───────────────────────────────────────────────────────
def dominant_color_mood(frame_bgr: np.ndarray) -> dict:
    """
    Analyze a frame's HSV histogram to estimate mood/environment keywords.
    Returns dict with keys: mood, environment, brightness_level.
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    mean_v = float(v.mean()) / 255.0       # brightness
    mean_s = float(s.mean()) / 255.0       # saturation
    mean_h = float(h.mean())               # hue (0-179 in OpenCV)

    mood: list[str] = []
    environment: list[str] = []

    # Brightness
    if mean_v < 0.25:
        mood.append("dark")
        mood.append("tense")
        brightness_level = "dark"
    elif mean_v < 0.5:
        mood.append("moody")
        brightness_level = "dim"
    elif mean_v < 0.75:
        mood.append("neutral")
        brightness_level = "medium"
    else:
        mood.append("bright")
        mood.append("open")
        brightness_level = "bright"

    # Saturation
    if mean_s < 0.15:
        mood.append("cold")
        mood.append("austere")
    elif mean_s > 0.6:
        mood.append("vibrant")
        mood.append("energetic")

    # Hue → environment hints
    if mean_h < 15 or mean_h > 165:        # red
        mood.append("dramatic")
        environment.append("warm")
    elif 15 <= mean_h < 35:                # orange/yellow
        mood.append("warm")
        environment.append("golden")
    elif 35 <= mean_h < 75:                # green
        environment.append("nature")
        environment.append("outdoor")
    elif 75 <= mean_h < 130:               # cyan/blue
        mood.append("calm")
        environment.append("sky")
        if mean_v > 0.5:
            environment.append("outdoor")
        else:
            environment.append("night")
    elif 130 <= mean_h <= 165:             # purple/magenta
        mood.append("mysterious")
        mood.append("dramatic")

    return {
        "mood": list(dict.fromkeys(mood))[:4],
        "environment": list(dict.fromkeys(environment))[:4],
        "brightness_level": brightness_level,
    }


# ── Motion scoring ─────────────────────────────────────────────────────────────
def compute_motion_score(frames: list[np.ndarray]) -> float:
    """
    Optical flow between consecutive frame pairs → mean magnitude.
    Returns score in [0, 1].
    """
    if len(frames) < 2:
        return 0.0

    magnitudes: list[float] = []
    prev_gray = cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)

    for frame in frames[1:]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        flow = cv2.calcOpticalFlowFarneback(
            prev_gray, gray,
            None, 0.5, 3, 15, 3, 5, 1.2, 0
        )
        mag, _ = cv2.cartToPolar(flow[:, :, 0], flow[:, :, 1])
        magnitudes.append(float(mag.mean()))
        prev_gray = gray

    mean_mag = sum(magnitudes) / len(magnitudes)
    # Normalize: typical motion is 0–15 pixels/frame; clamp at 20
    return round(min(mean_mag / 20.0, 1.0), 4)


# ── Scene → audio needs ────────────────────────────────────────────────────────
def infer_audio_needs(
    duration: float,
    motion_score: float,
    mood: list[str],
    environment: list[str],
    brightness_level: str,
) -> str:
    """Heuristically determine what kind of audio this scene needs."""
    if duration < 2.0:
        return "stinger"
    if motion_score > 0.7:
        return "impact" if duration < 4.0 else "transition_fx"
    if duration < 3.5 and motion_score > 0.4:
        return "transition_fx"
    if any(m in mood for m in ("dark", "tense", "mysterious", "dramatic")):
        return "music_bed"
    if any(e in environment for e in ("outdoor", "nature", "sky", "night")):
        return "ambience"
    if duration > 6.0:
        return "ambience"
    return "foley"


# ── Scene description builder ──────────────────────────────────────────────────
def build_scene_description(
    scene_id: str,
    start: float,
    end: float,
    motion_score: float,
    mood: list[str],
    environment: list[str],
    brightness_level: str,
    needs: str,
) -> str:
    """
    Build a natural-language scene description purely from heuristics.
    This becomes the CLAP text query embedding.
    """
    duration = end - start
    parts: list[str] = []

    # Motion
    if motion_score > 0.7:
        parts.append("fast moving dynamic scene with intense motion")
    elif motion_score > 0.4:
        parts.append("moderately active scene with noticeable movement")
    elif motion_score > 0.15:
        parts.append("calm scene with gentle movement")
    else:
        parts.append("still quiet scene with almost no movement")

    # Mood
    if mood:
        parts.append(f"{' and '.join(mood[:2])} atmosphere")

    # Environment
    if environment:
        parts.append(f"{' '.join(environment[:2])} setting")

    # Brightness
    if brightness_level == "dark":
        parts.append("dark low-light visuals")
    elif brightness_level == "bright":
        parts.append("bright open visuals")

    # Duration hint
    if duration < 3:
        parts.append("very short moment")
    elif duration > 8:
        parts.append("long continuous sequence")

    # Audio need
    type_phrases = {
        "ambience":     "suitable for ambient background sound",
        "music_bed":    "needs background music scoring",
        "impact":       "needs sharp impact sound effect",
        "transition_fx":"needs transition whoosh sound effect",
        "stinger":      "needs short musical stinger",
        "foley":        "needs foley sound effects",
    }
    parts.append(type_phrases.get(needs, "needs sound effect"))

    return ". ".join(parts)


# ── Main pipeline ──────────────────────────────────────────────────────────────
def analyze_video(video_path: str, output_path: str = "queries.jsonl") -> int:
    """
    Detect scenes in video, build query objects, write to JSONL.
    Returns number of scenes.
    """
    print(f"Analyzing: {video_path}")

    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=27.0))
    scene_manager.detect_scenes(video, show_progress=True)
    scene_list = scene_manager.get_scene_list()
    print(f"Detected {len(scene_list)} scenes.")

    # Also open with OpenCV for frame access
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    queries: list[dict] = []

    for idx, (start_tc, end_tc) in enumerate(tqdm(scene_list, desc="Building queries")):
        start_sec = start_tc.get_seconds()
        end_sec   = end_tc.get_seconds()
        duration  = end_sec - start_sec

        start_frame = int(start_sec * fps)
        end_frame   = int(end_sec * fps)

        # Sample up to 8 evenly spaced frames for analysis
        sample_count = min(8, max(2, int(duration * 2)))
        frame_indices = np.linspace(start_frame, end_frame - 1, sample_count, dtype=int)
        sample_frames: list[np.ndarray] = []

        for fi in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(fi))
            ret, frame = cap.read()
            if ret and frame is not None:
                sample_frames.append(frame)

        if not sample_frames:
            continue

        # Motion score
        motion_score = compute_motion_score(sample_frames)

        # Color / mood from middle frame
        mid_frame = sample_frames[len(sample_frames) // 2]
        color_info = dominant_color_mood(mid_frame)

        # Infer audio need
        needs = infer_audio_needs(
            duration=duration,
            motion_score=motion_score,
            mood=color_info["mood"],
            environment=color_info["environment"],
            brightness_level=color_info["brightness_level"],
        )

        # Build text description for CLAP text encoder query
        description = build_scene_description(
            scene_id=f"scene_{idx:04d}",
            start=start_sec,
            end=end_sec,
            motion_score=motion_score,
            mood=color_info["mood"],
            environment=color_info["environment"],
            brightness_level=color_info["brightness_level"],
            needs=needs,
        )

        query = {
            "scene_id":        f"scene_{idx:04d}",
            "start_sec":       round(start_sec, 3),
            "end_sec":         round(end_sec, 3),
            "duration":        round(duration, 3),
            "motion_score":    motion_score,
            "mood":            color_info["mood"],
            "environment":     color_info["environment"],
            "brightness_level":color_info["brightness_level"],
            "needs":           needs,
            "description":     description,
        }
        queries.append(query)

    cap.release()

    with open(output_path, "w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q) + "\n")

    print(f"Wrote {len(queries)} query objects -> {output_path}")
    return len(queries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("video", help="Input video file")
    parser.add_argument("--queries", default="queries.jsonl", help="Output JSONL path")
    args = parser.parse_args()

    analyze_video(args.video, args.queries)
