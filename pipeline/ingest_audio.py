"""
BBC Sound Effects Audio Ingestion Pipeline
==========================================
Reads BBCSoundEffects.csv, locates each WAV file in sounds/,
chunks it according to audio_type rules, extracts embeddings and
acoustic features, and writes a JSONL manifest for the Rust uploader.

Usage:
    python pipeline/ingest_audio.py [--limit N] [--manifest out.jsonl] [--workers N]
"""

import argparse
import csv
import json
import math
import os
import re
import sys
import time
import warnings
from pathlib import Path
from typing import Iterator

warnings.filterwarnings("ignore")

import numpy as np
import librosa
import soundfile as sf
import torch
from tqdm import tqdm
from dotenv import load_dotenv
load_dotenv()

# CLAP for audio-language embeddings (audio_dense, 512-dim)
import laion_clap

# Sentence-transformers for text embeddings (text_dense, 384-dim)
from sentence_transformers import SentenceTransformer

from audio_type import classify_category, get_chunk_params

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
SOUNDS_DIR = ROOT / "sounds"
CSV_PATH   = ROOT / "BBCSoundEffects.csv"

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_KEY = os.getenv("QDRANT_KEY")

SR = 22050   # resample target for librosa feature extraction
CLAP_SR = 48000  # CLAP expects 48 kHz


# ── Models (lazy-loaded singletons) ───────────────────────────────────────────
_clap_model: laion_clap.CLAP_Module | None = None
_text_model: SentenceTransformer | None = None


def get_clap() -> laion_clap.CLAP_Module:
    global _clap_model
    if _clap_model is None:
        print("Loading CLAP model (first run downloads ~1 GB checkpoint)...")
        _clap_model = laion_clap.CLAP_Module(enable_fusion=False, amodel="HTSAT-tiny")
        _clap_model.load_ckpt()
        _clap_model.eval()
    return _clap_model


def get_text_model() -> SentenceTransformer:
    global _text_model
    if _text_model is None:
        print("Loading sentence-transformer (all-MiniLM-L6-v2)...")
        _text_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _text_model


# ── File resolution ────────────────────────────────────────────────────────────
def resolve_path(location: str, cd_name: str) -> Path | None:
    """
    BBC files live at: sounds/{CDName}/{description}..{location}
    We find by matching the trailing location ID in the CDName folder.
    """
    folder = SOUNDS_DIR / cd_name
    if not folder.exists():
        return None
    # location = "07076044.wav" — match any file ending with this
    for f in folder.iterdir():
        if f.name.endswith(location):
            return f
    return None


# ── Chunking ───────────────────────────────────────────────────────────────────
def chunk_audio(
    y: np.ndarray,
    sr: int,
    window_sec: float,
    stride_sec: float,
) -> Iterator[tuple[float, float, np.ndarray]]:
    """Yield (start_sec, end_sec, samples) for each chunk."""
    total_sec = len(y) / sr
    window_samples = int(window_sec * sr)
    stride_samples = int(stride_sec * sr)

    if len(y) < window_samples:
        # File shorter than window — yield whole thing
        yield 0.0, total_sec, y
        return

    pos = 0
    while pos + window_samples <= len(y):
        chunk = y[pos : pos + window_samples]
        start = pos / sr
        end = (pos + window_samples) / sr
        yield start, end, chunk
        pos += stride_samples

    # Tail chunk if significant remainder
    remaining = len(y) - pos
    if remaining > window_samples * 0.4:
        chunk = y[pos:]
        yield pos / sr, total_sec, chunk


# ── Feature extraction ─────────────────────────────────────────────────────────
def extract_acoustic_features(y: np.ndarray, sr: int) -> dict:
    """Compute lightweight acoustic features with librosa."""
    rms = float(librosa.feature.rms(y=y).mean())
    centroid = float(librosa.feature.spectral_centroid(y=y, sr=sr).mean())
    zcr = float(librosa.feature.zero_crossing_rate(y=y).mean())

    try:
        tempo_arr, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo_arr) if tempo_arr > 10 else None
    except Exception:
        bpm = None

    # Loudness approximation (simple RMS → LUFS estimate)
    loudness_lufs = 20 * math.log10(rms + 1e-9) - 3.0

    return {
        "energy": round(min(rms * 10, 1.0), 4),
        "loudness_lufs": round(loudness_lufs, 2),
        "bpm": round(bpm, 1) if bpm else None,
        "spectral_centroid": round(centroid, 1),
        "zero_crossing_rate": round(zcr, 5),
    }


# ── CLAP audio embedding ───────────────────────────────────────────────────────
def embed_audio_chunk(chunk_y: np.ndarray, chunk_sr: int) -> list[float]:
    """Embed a raw waveform chunk with CLAP. Returns 512-dim list."""
    model = get_clap()
    # Resample to CLAP's expected 48 kHz
    if chunk_sr != CLAP_SR:
        chunk_y = librosa.resample(chunk_y, orig_sr=chunk_sr, target_sr=CLAP_SR)
    # CLAP expects float32 tensor of shape (1, samples)
    audio_tensor = torch.from_numpy(chunk_y).float().unsqueeze(0)
    with torch.no_grad():
        emb = model.get_audio_embedding_from_data(x=audio_tensor, use_tensor=True)
    return emb[0].cpu().numpy().tolist()


# ── Text embedding ─────────────────────────────────────────────────────────────
def embed_text(text: str) -> list[float]:
    """Encode text with sentence-transformers. Returns 384-dim list."""
    model = get_text_model()
    emb = model.encode(text, normalize_embeddings=True)
    return emb.tolist()


# ── BM25-style sparse vector ───────────────────────────────────────────────────
_VOCAB: dict[str, int] = {}
_VOCAB_NEXT = 0


def _vocab_id(term: str) -> int:
    global _VOCAB_NEXT
    if term not in _VOCAB:
        _VOCAB[term] = _VOCAB_NEXT
        _VOCAB_NEXT += 1
    return _VOCAB[term]


def build_sparse_vector(tags: list[str], description: str) -> tuple[list[int], list[float]]:
    """Simple TF-IDF-inspired sparse vector from tags + description tokens."""
    tokens: list[str] = []
    for tag in tags:
        tokens.extend(re.findall(r"[a-z]+", tag.lower()))
    tokens.extend(re.findall(r"[a-z]+", description.lower()))

    # Term frequency
    tf: dict[str, int] = {}
    for t in tokens:
        if len(t) > 2:  # skip stopword-length tokens
            tf[t] = tf.get(t, 0) + 1

    indices = [_vocab_id(t) for t in tf]
    values  = [float(c) / len(tokens) for c in tf.values()]
    return indices, values


# ── Caption builder ────────────────────────────────────────────────────────────
def build_caption(description: str, category: str, audio_type: str, cd_name: str) -> str:
    parts = [description.rstrip(".")]
    if audio_type == "ambience":
        parts.append(f"ambient background sound from {category.lower()}")
    elif audio_type == "music_bed":
        parts.append("music bed suitable for background scoring")
    elif audio_type == "impact":
        parts.append("sharp impact or explosive sound effect")
    elif audio_type == "transition_fx":
        parts.append("transition sound effect whoosh or swipe")
    elif audio_type == "stinger":
        parts.append("short musical stinger sting")
    else:
        parts.append(f"sound effect: {category.lower()}")
    return ". ".join(parts)


# ── Main pipeline ──────────────────────────────────────────────────────────────
def build_manifest(
    limit: int | None = None,
    manifest_path: str = "manifest.jsonl",
    skip_existing: bool = True,
) -> int:
    """
    Process all BBC audio files and write chunks to a JSONL manifest.
    Returns total chunks written.
    """
    existing_ids: set[str] = set()
    if skip_existing and os.path.exists(manifest_path):
        with open(manifest_path) as f:
            for line in f:
                rec = json.loads(line)
                existing_ids.add(rec["chunk_id"])
        print(f"Resuming — {len(existing_ids)} chunks already in manifest.")

    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if limit:
        rows = rows[:limit]

    total_chunks = 0
    skipped_files = 0

    with open(manifest_path, "a", encoding="utf-8") as out:
        for row in tqdm(rows, desc="Processing BBC files", unit="file"):
            location = row["location"]          # e.g. "07076044.wav"
            track_id = location.replace(".wav", "")
            description = row["description"]
            category    = row["category"]
            cd_name     = row["CDName"]
            secs        = int(row["secs"]) if row["secs"].isdigit() else 0

            wav_path = resolve_path(location, cd_name)
            if wav_path is None:
                skipped_files += 1
                continue

            audio_type = classify_category(category)
            params     = get_chunk_params(audio_type)

            # Skip if all chunks for this track already exist
            # (check first chunk id as proxy)
            first_chunk_id = f"{track_id}_chunk_000"
            if first_chunk_id in existing_ids:
                continue

            try:
                y, file_sr = librosa.load(str(wav_path), sr=None, mono=True)
            except Exception as e:
                skipped_files += 1
                continue

            # Resample for librosa features
            y_feat = librosa.resample(y, orig_sr=file_sr, target_sr=SR) if file_sr != SR else y

            tags = list({
                w for w in re.findall(r"[a-z]+", (description + " " + category + " " + cd_name).lower())
                if len(w) > 3
            })

            caption = build_caption(description, category, audio_type, cd_name)
            text_vec = embed_text(caption)
            sparse_idx, sparse_val = build_sparse_vector(tags, description)

            chunk_idx = 0
            for start_sec, end_sec, chunk_y in chunk_audio(y_feat, SR, params.window_sec, params.stride_sec):
                chunk_id = f"{track_id}_chunk_{chunk_idx:03d}"
                if chunk_id in existing_ids:
                    chunk_idx += 1
                    continue

                # Acoustic features from this specific chunk
                feats = extract_acoustic_features(chunk_y, SR)

                # CLAP audio embedding for this chunk
                audio_vec = embed_audio_chunk(chunk_y, SR)

                record = {
                    "chunk_id":   chunk_id,
                    "track_id":   track_id,
                    "start_sec":  round(start_sec, 3),
                    "end_sec":    round(end_sec, 3),
                    "duration":   round(end_sec - start_sec, 3),
                    "audio_type": audio_type,
                    "category":   category,
                    "cd_name":    cd_name,
                    "description":description,
                    "caption":    caption,
                    "tags":       tags[:20],
                    "source_path":str(wav_path.relative_to(ROOT)),
                    "source":     "bbc_sound_effects",
                    "license":    "CC BY 4.0",
                    **feats,
                    "audio_dense":  audio_vec,
                    "text_dense":   text_vec,
                    "sparse_indices": sparse_idx,
                    "sparse_values":  sparse_val,
                }
                out.write(json.dumps(record) + "\n")
                total_chunks += 1
                chunk_idx += 1

    print(f"\nDone. Total chunks written: {total_chunks}  |  Files skipped: {skipped_files}")
    return total_chunks


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",    type=int,  default=None,  help="Max CSV rows to process")
    parser.add_argument("--manifest", type=str,  default="manifest.jsonl")
    parser.add_argument("--no-resume",action="store_true", help="Start fresh (overwrite manifest)")
    args = parser.parse_args()

    if args.no_resume and os.path.exists(args.manifest):
        os.remove(args.manifest)

    build_manifest(
        limit=args.limit,
        manifest_path=args.manifest,
        skip_existing=not args.no_resume,
    )
