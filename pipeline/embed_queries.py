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
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import torch
import laion_clap
from tqdm import tqdm


_clap_model = None

def get_clap():
    global _clap_model
    if _clap_model is None:
        print("Loading CLAP model...")
        _clap_model = laion_clap.CLAP_Module(enable_fusion=False, amodel="HTSAT-tiny")
        _clap_model.load_ckpt()
        _clap_model.eval()
    return _clap_model


def embed_text_clap(texts: list[str]) -> list[list[float]]:
    """Batch encode texts with CLAP text encoder → 512-dim each."""
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
