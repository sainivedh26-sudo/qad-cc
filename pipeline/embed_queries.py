"""
Embed scene queries with the CLAP text encoder so the Rust retriever
can query audio_dense vectors directly (cross-modal retrieval).

Reads  queries.jsonl  (from analyze_video.py)
Writes queries_embedded.jsonl  (adds "text_dense" field per scene)

Usage:
    python pipeline/embed_queries.py [--queries queries.jsonl] [--out queries_embedded.jsonl]
"""

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import torch

# ── Safe torch.load patch ──────────────────────────────────────────────────────
# laion_clap calls torch.load() without map_location which causes a fatal
# Windows CUDA crash (exit 0xC0000005 / STATUS_ACCESS_VIOLATION) when the
# checkpoint was saved on a different CUDA device than what's currently live.
# We monkey-patch torch.load before importing laion_clap so every downstream
# checkpoint restore always lands on CPU first; the model is moved to the
# target device afterwards.
_orig_torch_load = torch.load

def _cpu_safe_torch_load(f, map_location=None, **kwargs):
    # Force CPU restore; move to target device after model.to(DEVICE)
    if map_location is None:
        map_location = "cpu"
    return _orig_torch_load(f, map_location=map_location, **kwargs)

torch.load = _cpu_safe_torch_load
# ──────────────────────────────────────────────────────────────────────────────

import laion_clap
from tqdm import tqdm

# Default to CUDA if available; set env var CLAP_DEVICE=cpu to force CPU.
_device_str = os.environ.get("CLAP_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
DEVICE = torch.device(_device_str)
print(f"[device] Using: {DEVICE}" + (
    f"  ({torch.cuda.get_device_name(0)})" if DEVICE.type == "cuda" else ""
))

_clap_model = None

def get_clap():
    global _clap_model
    if _clap_model is None:
        print("Loading CLAP model...")
        m = laion_clap.CLAP_Module(enable_fusion=False, amodel="HTSAT-tiny")
        # load_ckpt now calls our patched torch.load → always restores to CPU
        m.load_ckpt()
        # Then move to target device (CUDA or CPU) — safe because weights are on CPU
        m = m.to(DEVICE)
        m.eval()
        _clap_model = m
    return _clap_model


def embed_text_clap(texts: list[str]) -> list[list[float]]:
    """Batch encode texts with CLAP text encoder on GPU → 512-dim each."""
    model = get_clap()
    with torch.no_grad():
        embs = model.get_text_embedding(texts, use_tensor=True)
    return embs.cpu().numpy().tolist()


def embed_queries(queries_path: str, output_path: str) -> None:
    queries = []
    with open(queries_path) as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))

    print(f"Embedding {len(queries)} scene queries with CLAP text encoder...")

    # Batch encode for efficiency
    BATCH = 32
    all_descriptions = [q["description"] for q in queries]

    all_embeddings: list[list[float]] = []
    for i in tqdm(range(0, len(all_descriptions), BATCH), desc="Encoding"):
        batch = all_descriptions[i : i + BATCH]
        embs = embed_text_clap(batch)
        all_embeddings.extend(embs)

    with open(output_path, "w", encoding="utf-8") as f:
        for query, emb in zip(queries, all_embeddings):
            query["text_dense"] = emb
            f.write(json.dumps(query) + "\n")

    print(f"Written {len(queries)} embedded queries -> {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default="queries.jsonl")
    parser.add_argument("--out",     default="queries_embedded.jsonl")
    args = parser.parse_args()
    embed_queries(args.queries, args.out)
