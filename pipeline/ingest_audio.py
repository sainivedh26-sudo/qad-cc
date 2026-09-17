"""
Audio Ingestion Pipeline
========================
Ingests audio from two sources:
  1. BBC Sound Effects  — read from BBCSoundEffects.csv + sounds/{CDName}/
  2. FreeSound          — read from sounds/FreeSound/{audio_type}/

Both are chunked by audio_type, CLAP-embedded, and written to a JSONL
manifest for the Rust bulk uploader.

Near-duplicate suppression: if a new chunk's CLAP embedding has cosine
similarity >= --dedup-threshold (default 0.92) to any already-indexed
chunk of the same audio type, it is skipped.  This prevents the same
sound appearing many times under different filenames.

GPU acceleration: models run on CUDA when available; batched forward
passes minimise VRAM usage with automatic OOM recovery.

Usage:
    python pipeline/ingest_audio.py [--manifest out.jsonl] [--batch N] [--dedup 0.92]
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
from dataclasses import dataclass, field
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

import laion_clap
from sentence_transformers import SentenceTransformer

from audio_type import classify_category, get_chunk_params

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
SOUNDS_DIR = ROOT / "sounds"
CSV_PATH   = ROOT / "BBCSoundEffects.csv"

SR      = 22050   # librosa feature extraction sample rate
CLAP_SR = 48000   # CLAP expected sample rate

# ── Device ─────────────────────────────────────────────────────────────────────
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"[device] Using: {DEVICE}" + (
    f"  ({torch.cuda.get_device_name(0)})" if DEVICE.type == "cuda" else ""
))

# ── Model singletons ───────────────────────────────────────────────────────────
_clap_model: laion_clap.CLAP_Module | None = None
_text_model: SentenceTransformer        | None = None


def get_clap() -> laion_clap.CLAP_Module:
    global _clap_model
    if _clap_model is None:
        print("Loading CLAP model (HTSAT-tiny)...")
        m = laion_clap.CLAP_Module(enable_fusion=False, amodel="HTSAT-tiny")
        m.load_ckpt()
        m = m.to(DEVICE)
        m.eval()
        _clap_model = m
        print(f"  CLAP loaded on {DEVICE}")
    return _clap_model


def get_text_model() -> SentenceTransformer:
    global _text_model
    if _text_model is None:
        print("Loading sentence-transformer (all-MiniLM-L6-v2)...")
        _text_model = SentenceTransformer("all-MiniLM-L6-v2", device=str(DEVICE))
        print(f"  SentenceTransformer loaded on {DEVICE}")
    return _text_model


MIN_AUDIO_BYTES = 8_000  # HTML error pages are always ~3 KB
AUDIO_EXTS      = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}


def is_valid_wav(path: Path) -> bool:
    """
    Accept WAV files (RIFF header) or any other recognised audio format
    that is large enough to not be an HTML error page.
    """
    if not path.exists() or path.stat().st_size < MIN_AUDIO_BYTES:
        return False
    if path.suffix.lower() in (".mp3", ".ogg", ".flac", ".m4a"):
        return True   # size check is sufficient; librosa will validate on load
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"RIFF"
    except OSError:
        return False


# ── File resolution ────────────────────────────────────────────────────────────
def resolve_path(location: str, cd_name: str) -> Path | None:
    folder = SOUNDS_DIR / cd_name.strip()
    if not folder.exists():
        return None
    for f in folder.iterdir():
        if f.name.endswith(location):
            return f if is_valid_wav(f) else None
    return None


# ── Chunking ───────────────────────────────────────────────────────────────────
def chunk_audio(
    y: np.ndarray, sr: int, window_sec: float, stride_sec: float
) -> Iterator[tuple[float, float, np.ndarray]]:
    """Yield (start_sec, end_sec, samples) for each chunk."""
    total_sec      = len(y) / sr
    window_samples = int(window_sec * sr)
    stride_samples = int(stride_sec * sr)

    if len(y) < window_samples:
        yield 0.0, total_sec, y
        return

    pos = 0
    while pos + window_samples <= len(y):
        yield pos / sr, (pos + window_samples) / sr, y[pos : pos + window_samples]
        pos += stride_samples

    remaining = len(y) - pos
    if remaining > window_samples * 0.4:
        yield pos / sr, total_sec, y[pos:]


# ── Batch CLAP audio embedding ─────────────────────────────────────────────────
def _forward_sub_batch(
    model: laion_clap.CLAP_Module,
    chunks: list[np.ndarray],
    device: torch.device,
) -> list[list[float]]:
    """Single padded forward pass for a list of already-resampled arrays."""
    max_len = max(len(c) for c in chunks)
    padded  = np.zeros((len(chunks), max_len), dtype=np.float32)
    for i, c in enumerate(chunks):
        padded[i, : len(c)] = c
    tensor = torch.from_numpy(padded).to(device)
    with torch.no_grad():
        embs = model.get_audio_embedding_from_data(x=tensor, use_tensor=True)
    return embs.cpu().numpy().tolist()


def _embed_oom_safe(
    model: laion_clap.CLAP_Module,
    chunks: list[np.ndarray],
    device: torch.device,
) -> list[list[float]]:
    """Recursively halve the batch on CUDA OOM; last resort falls back to CPU."""
    if not chunks:
        return []
    try:
        if device.type == "cuda":
            torch.cuda.empty_cache()
        return _forward_sub_batch(model, chunks, device)
    except RuntimeError as e:
        if "out of memory" not in str(e).lower() or device.type != "cuda":
            raise
        torch.cuda.empty_cache()
        if len(chunks) == 1:
            # Can't halve further — run this one chunk on CPU
            return _forward_sub_batch(model, chunks, torch.device("cpu"))
        mid = len(chunks) // 2
        left  = _embed_oom_safe(model, chunks[:mid], device)
        right = _embed_oom_safe(model, chunks[mid:], device)
        return left + right


def embed_audio_batch(
    chunks_native: list[np.ndarray],
    chunks_sr: int,
    batch_size: int = 16,
) -> list[list[float]]:
    """
    Embed a list of raw waveforms with CLAP on GPU (auto-recovers from OOM).
    Chunks are resampled to CLAP_SR, zero-padded, forwarded in sub-batches.
    Returns a list of 512-dim float lists.
    """
    model = get_clap()

    # Resample everything to 48 kHz once
    resampled = (
        [librosa.resample(c, orig_sr=chunks_sr, target_sr=CLAP_SR) for c in chunks_native]
        if chunks_sr != CLAP_SR else list(chunks_native)
    )

    results: list[list[float]] = []
    for start in range(0, len(resampled), batch_size):
        sub = resampled[start : start + batch_size]
        results.extend(_embed_oom_safe(model, sub, DEVICE))

    return results


# ── Batch text embedding ───────────────────────────────────────────────────────
def embed_text_batch(texts: list[str], batch_size: int = 512) -> list[list[float]]:
    """Encode a list of captions with sentence-transformers (GPU-batched)."""
    model = get_text_model()
    embs  = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return embs.tolist()


# ── Feature extraction ─────────────────────────────────────────────────────────
def extract_acoustic_features(y: np.ndarray, sr: int) -> dict:
    rms      = float(librosa.feature.rms(y=y).mean())
    centroid = float(librosa.feature.spectral_centroid(y=y, sr=sr).mean())
    zcr      = float(librosa.feature.zero_crossing_rate(y=y).mean())
    try:
        tempo_arr, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo_arr) if tempo_arr > 10 else None
    except Exception:
        bpm = None
    loudness_lufs = 20 * math.log10(rms + 1e-9) - 3.0
    return {
        "energy":             round(min(rms * 10, 1.0), 4),
        "loudness_lufs":      round(loudness_lufs, 2),
        "bpm":                round(bpm, 1) if bpm else None,
        "spectral_centroid":  round(centroid, 1),
        "zero_crossing_rate": round(zcr, 5),
    }


# ── Sparse vector ──────────────────────────────────────────────────────────────
_VOCAB: dict[str, int] = {}
_VOCAB_NEXT = 0


def _vocab_id(term: str) -> int:
    global _VOCAB_NEXT
    if term not in _VOCAB:
        _VOCAB[term] = _VOCAB_NEXT
        _VOCAB_NEXT += 1
    return _VOCAB[term]


def build_sparse_vector(tags: list[str], description: str) -> tuple[list[int], list[float]]:
    tokens: list[str] = []
    for tag in tags:
        tokens.extend(re.findall(r"[a-z]+", tag.lower()))
    tokens.extend(re.findall(r"[a-z]+", description.lower()))
    tf: dict[str, int] = {}
    for t in tokens:
        if len(t) > 2:
            tf[t] = tf.get(t, 0) + 1
    indices = [_vocab_id(t) for t in tf]
    values  = [float(c) / max(len(tokens), 1) for c in tf.values()]
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
@dataclass
class FileRecord:
    row:        dict
    wav_path:   Path
    audio_type: str
    caption:    str
    tags:       list[str]


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Fast cosine similarity between two unit-normalised vectors."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    na = np.linalg.norm(va)
    nb = np.linalg.norm(vb)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def build_manifest(
    limit:          int | None = None,
    manifest_path:  str = "manifest.jsonl",
    skip_existing:  bool = True,
    clap_batch:     int = 8,
    text_batch:     int = 512,
    dedup_threshold: float = 0.92,
) -> int:
    """
    Two-phase ingestion:
      Phase 1 — scan CSV, resolve paths, build captions, batch-embed captions.
      Phase 2 — per-file: load audio, chunk, batch-embed with CLAP, write manifest.
    """

    # ── Load existing chunk IDs for resume ──────────────────────────────────
    existing_ids: set[str] = set()
    if skip_existing and os.path.exists(manifest_path):
        with open(manifest_path) as f:
            for line in f:
                existing_ids.add(json.loads(line)["chunk_id"])
        print(f"Resume — {len(existing_ids)} chunks already written.")

    # ── Phase 1: collect files to process ───────────────────────────────────
    print("\nPhase 1: scanning sources...")
    files: list[FileRecord] = []

    # ── 1a: BBC Sound Effects (CSV-driven) ──────────────────────────────────
    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]

    skipped_missing = 0
    skipped_invalid = 0
    skipped_done    = 0

    for row in tqdm(rows, desc="  BBC CSV", unit="file", ncols=90):
        location  = row["location"]
        track_id  = location.replace(".wav", "")
        first_id  = f"{track_id}_chunk_000"
        if first_id in existing_ids:
            skipped_done += 1
            continue

        cd_name_clean = row["CDName"].strip()
        folder = SOUNDS_DIR / cd_name_clean
        if not folder.exists():
            skipped_missing += 1
            continue

        wav_path = next(
            (f for f in folder.iterdir() if f.name.endswith(location)), None
        )
        if wav_path is None:
            skipped_missing += 1
            continue
        if not is_valid_wav(wav_path):
            skipped_invalid += 1
            continue

        audio_type = classify_category(row["category"])
        tags = list({
            w for w in re.findall(r"[a-z]+",
                (row["description"] + " " + row["category"] + " " + cd_name_clean).lower())
            if len(w) > 3
        })
        caption = build_caption(row["description"], row["category"], audio_type, cd_name_clean)
        row["CDName"] = cd_name_clean
        files.append(FileRecord(
            row={"chunk_id_prefix": track_id, "description": row["description"],
                 "category": row["category"], "cd_name": cd_name_clean,
                 "source": "bbc_sound_effects", "license": "CC BY 4.0"},
            wav_path=wav_path, audio_type=audio_type, caption=caption, tags=tags,
        ))

    print(f"    BBC valid: {sum(1 for f in files):,}  |  done: {skipped_done:,}  "
          f"|  missing: {skipped_missing:,}  |  HTML/invalid: {skipped_invalid:,}")

    # ── 1b: FreeSound (folder-driven: sounds/FreeSound/{audio_type}/*.wav) ──
    fs_root = SOUNDS_DIR / "FreeSound"
    fs_new = 0
    if fs_root.exists():
        for type_dir in sorted(fs_root.iterdir()):
            if not type_dir.is_dir():
                continue
            audio_type = type_dir.name  # folder name IS the audio_type
            for wav_path in type_dir.iterdir():
                if wav_path.suffix.lower() not in AUDIO_EXTS:
                    continue
                if not is_valid_wav(wav_path):
                    continue

                # Stable ID: stem of filename (contains freesound ID)
                track_id = re.sub(r"[^\w]", "_", wav_path.stem)[:60]
                first_id = f"fs_{track_id}_chunk_000"
                if first_id in existing_ids:
                    skipped_done += 1
                    continue

                # Caption: use filename words as description
                name_words = re.sub(r"[_\-\.]+", " ", wav_path.stem).strip()
                caption    = f"{name_words}. {audio_type} sound effect"
                tags = list({
                    w for w in re.findall(r"[a-z]+", name_words.lower())
                    if len(w) > 2
                })

                files.append(FileRecord(
                    row={"chunk_id_prefix": f"fs_{track_id}",
                         "description": name_words, "category": audio_type,
                         "cd_name": "FreeSound", "source": "freesound",
                         "license": "CC"},
                    wav_path=wav_path, audio_type=audio_type,
                    caption=caption, tags=tags,
                ))
                fs_new += 1
        print(f"    FreeSound valid: {fs_new:,}  |  done: {skipped_done:,}")
    else:
        print(f"    FreeSound: not found (run pipeline/freesound_download.py to add more audio)")

    print(f"\n  Total to embed: {len(files):,}")

    if not files:
        print("Nothing to do.")
        return 0

    # ── Phase 1b: batch-embed all captions (sentence-transformer) ───────────
    print(f"\nPhase 1b: embedding {len(files):,} captions with sentence-transformer...")
    all_captions = [fr.caption for fr in files]
    text_vecs    = embed_text_batch(all_captions, batch_size=text_batch)
    print(f"  Caption embeddings done ({len(text_vecs)} x 384-dim)")

    # ── Phase 2: cross-file batched chunking + CLAP embedding ───────────────
    use_dedup  = 0.0 < dedup_threshold < 1.0
    dedup_note = f"dedup>={dedup_threshold}" if use_dedup else "dedup=off"
    print(f"\nPhase 2: cross-file batched CLAP embedding")
    print(f"  device={DEVICE}  cross-batch={clap_batch}  {dedup_note}")

    # Dedup state: per audio_type numpy matrix of seen embeddings (fast cosine via matmul)
    seen_mats: dict[str, np.ndarray] = {}   # audio_type -> (N, 512) float32

    if use_dedup and skip_existing and os.path.exists(manifest_path):
        print("  Loading existing embeddings for dedup...")
        tmp: dict[str, list] = {}
        with open(manifest_path) as f:
            for line in f:
                rec = json.loads(line)
                tmp.setdefault(rec.get("audio_type", "foley"), []).append(rec["audio_dense"])
        for atype, vecs in tmp.items():
            seen_mats[atype] = np.array(vecs, dtype=np.float32)
        print(f"  Dedup index: {sum(m.shape[0] for m in seen_mats.values()):,} existing chunks")

    # ── Pending-chunk buffer: accumulate across files, flush when full ───────
    # Each entry: (fr, text_vec, chunk_idx, start_sec, end_sec, chunk_y_22k)
    pending: list[tuple] = []
    total_chunks  = 0
    dedup_dropped = 0
    skipped_files = 0
    error_counts: dict[str, int] = {}
    error_samples: list[str]     = []

    def flush_buffer(out_f):
        """Embed all pending chunks in one CLAP call, apply dedup, write records."""
        nonlocal total_chunks, dedup_dropped
        if not pending:
            return

        raw = [p[5] for p in pending]   # chunk_y_22k arrays
        audio_vecs = embed_audio_batch(raw, SR, batch_size=len(raw))

        for (fr, text_vec, chunk_idx, start_sec, end_sec, chunk_y), audio_vec in zip(pending, audio_vecs):
            meta      = fr.row
            track_id  = meta["chunk_id_prefix"]
            atype     = fr.audio_type
            av        = np.array(audio_vec, dtype=np.float32)

            # ── Vectorised dedup ──────────────────────────────────────────
            if use_dedup and atype in seen_mats and seen_mats[atype].shape[0] > 0:
                mat   = seen_mats[atype]
                norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
                norm_av = np.linalg.norm(av) + 1e-9
                sims  = (mat / norms) @ (av / norm_av)
                if sims.max() >= dedup_threshold:
                    dedup_dropped += 1
                    continue

            # Add to dedup matrix
            if use_dedup:
                row = av.reshape(1, -1)
                if atype in seen_mats:
                    seen_mats[atype] = np.vstack([seen_mats[atype], row])
                else:
                    seen_mats[atype] = row

            sparse_idx, sparse_val = build_sparse_vector(fr.tags, meta["description"])
            feats = extract_acoustic_features(chunk_y, SR)

            record = {
                "chunk_id":       f"{track_id}_chunk_{chunk_idx:03d}",
                "track_id":       track_id,
                "start_sec":      round(start_sec, 3),
                "end_sec":        round(end_sec, 3),
                "duration":       round(end_sec - start_sec, 3),
                "audio_type":     atype,
                "category":       meta["category"],
                "cd_name":        meta["cd_name"],
                "description":    meta["description"],
                "caption":        fr.caption,
                "tags":           fr.tags[:20],
                "source_path":    str(fr.wav_path.relative_to(ROOT)),
                "source":         meta["source"],
                "license":        meta["license"],
                **feats,
                "audio_dense":    audio_vec,
                "text_dense":     text_vec,
                "sparse_indices": sparse_idx,
                "sparse_values":  sparse_val,
            }
            out_f.write(json.dumps(record) + "\n")
            total_chunks += 1

        pending.clear()

    with open(manifest_path, "a", encoding="utf-8") as out:
        pbar = tqdm(
            zip(files, text_vecs), total=len(files),
            desc="Loading files", unit="file", ncols=90,
            dynamic_ncols=True,
        )
        for fr, text_vec in pbar:
            meta     = fr.row
            track_id = meta["chunk_id_prefix"]

            try:
                y, file_sr = librosa.load(str(fr.wav_path), sr=None, mono=True)
            except Exception as e:
                skipped_files += 1
                etype = type(e).__name__
                error_counts[etype] = error_counts.get(etype, 0) + 1
                if len(error_samples) < 5:
                    error_samples.append(f"  {fr.wav_path.name}: [{etype}] {str(e)[:100]}")
                continue

            y_feat = librosa.resample(y, orig_sr=file_sr, target_sr=SR) if file_sr != SR else y
            params = get_chunk_params(fr.audio_type)

            for idx, (start_sec, end_sec, chunk_y) in enumerate(
                chunk_audio(y_feat, SR, params.window_sec, params.stride_sec)
            ):
                cid = f"{track_id}_chunk_{idx:03d}"
                if cid in existing_ids:
                    continue
                pending.append((fr, text_vec, idx, start_sec, end_sec, chunk_y))

                # Flush when cross-file batch is full
                if len(pending) >= clap_batch:
                    flush_buffer(out)
                    pbar.set_postfix(chunks=total_chunks, dedup=dedup_dropped, err=skipped_files)

        flush_buffer(out)  # final partial batch

    print(f"\nDone.")
    print(f"  Chunks written   : {total_chunks:,}")
    if use_dedup:
        print(f"  Dedup dropped    : {dedup_dropped:,}  (>= {dedup_threshold})")
    print(f"  Files skipped    : {skipped_files:,}")
    if error_counts:
        print(f"\n  Errors:")
        for etype, count in sorted(error_counts.items(), key=lambda x: -x[1]):
            print(f"    {etype}: {count}")
        for s in error_samples:
            print(s)
    return total_chunks


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",     type=int,   default=None,  help="Max BBC CSV rows")
    parser.add_argument("--manifest",  type=str,   default="manifest.jsonl")
    parser.add_argument("--no-resume", action="store_true", help="Overwrite manifest")
    parser.add_argument("--batch",     type=int,   default=32,    help="Cross-file CLAP batch size")
    parser.add_argument("--text-batch",type=int,   default=512,   help="Sentence-transformer batch size")
    parser.add_argument("--dedup",     type=float, default=0.0,
                        help="Dedup threshold 0.95-0.99 (0=off, default off)")
    args = parser.parse_args()

    if args.no_resume and os.path.exists(args.manifest):
        os.remove(args.manifest)

    build_manifest(
        limit=args.limit,
        manifest_path=args.manifest,
        skip_existing=not args.no_resume,
        clap_batch=args.batch,
        text_batch=args.text_batch,
        dedup_threshold=args.dedup,
    )
