"""
Multi-Layer Audio Composition via Qdrant Discovery + Recommendation APIs
=========================================================================
For each scene in timeline.jsonl, finds 1-2 complementary audio layers:

  Layer 2 — Qdrant Discovery API (v1.7+)
    target  = scene CLAP text embedding  (same 512-dim CLAP space as audio)
    context = [{positive: foundation_chunk_audio_vector}]
    filter  = audio_type == complementary_type AND duration >= min_secs
    → "close to this scene, but stay in the same zone as the foundation sound"

  Layer 3 — Qdrant Recommendation API
    positive = [foundation_chunk_qdrant_id]
    filter   = audio_type == third_type AND duration >= min_secs
    → "similar mood family to the foundation, but a different texture"

Pairing rules decide which types stack well together per primary type.
Output: timeline.jsonl enriched with a `layer_matches` list per entry.

Usage:
    python pipeline/layer_audio.py
    python pipeline/layer_audio.py --timeline timeline.jsonl --queries queries_embedded.jsonl
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Filter, FieldCondition, MatchValue, Range,
        RecommendQuery, RecommendInput,
    )
except ImportError:
    print("pip install qdrant-client")
    sys.exit(1)

from tqdm import tqdm

QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_KEY = os.environ["QDRANT_KEY"]
COLLECTION = "audio_chunks"

# ── Pairing rules ──────────────────────────────────────────────────────────────
# primary_type -> [(layer2_type, default_vol), (layer3_type, default_vol)]
# Empty list = scene is too short / dense to layer (stingers, fast transitions)
LAYER_RULES: dict[str, list[tuple[str, float]]] = {
    "ambience":      [("foley",     0.38), ("music_bed",  0.24)],
    "music_bed":     [("ambience",  0.40), ("tension",    0.28)],
    "tension":       [("ambience",  0.38), ("music_bed",  0.22)],
    "foley":         [("ambience",  0.40)],
    "impact":        [("tension",   0.32), ("ambience",   0.28)],
    "creature":      [("ambience",  0.40)],
    "vehicle":       [("ambience",  0.38)],
    "human":         [("ambience",  0.32), ("foley",      0.24)],
    "transition_fx": [],  # solo — too short
    "stinger":       [],  # solo — very short
}

PRIMARY_VOLUME = 0.62


# ── Helpers ────────────────────────────────────────────────────────────────────
def _type_filter(audio_type: str, min_duration: float) -> Filter:
    return Filter(must=[
        FieldCondition(key="audio_type",  match=MatchValue(value=audio_type)),
        FieldCondition(key="duration",    range=Range(gte=min_duration)),
    ])


def _payload_to_layer(payload: dict, layer_idx: int, volume: float) -> dict:
    return {
        "layer":           layer_idx,
        "audio_type":      payload.get("audio_type", ""),
        "chunk_id":        payload.get("chunk_id", ""),
        "source_path":     payload.get("source_path", ""),
        "chunk_start_sec": payload.get("start_sec",  0.0),
        "chunk_end_sec":   payload.get("end_sec",    0.0),
        "caption":         payload.get("caption",    ""),
        "score":           0.0,   # Discovery/Recommend scores aren't direct similarity
        "energy":          payload.get("energy",     0.5),
        "volume":          volume,
    }


# ── Core layer-finding logic ───────────────────────────────────────────────────
def find_layers(
    client:          QdrantClient,
    scene_vector:    list[float],         # 512-dim CLAP text embedding for the scene
    primary_type:    str,
    scene_duration:  float,
) -> list[dict]:
    """
    Returns 0-2 complementary layer dicts for this scene.
    Steps:
      1. Re-search primary type to get the foundation chunk's audio vector + Qdrant ID.
      2. Use Discovery API for layer 2 (context-constrained to foundation zone).
      3. Use Recommendation API for layer 3 (positive = foundation ID).
    """
    rules = LAYER_RULES.get(primary_type, [])
    if not rules:
        return []

    # Minimum chunk duration for each layer (scene-proportional, floor at 1.5s)
    min_dur = max(1.5, scene_duration * 0.3)

    # ── Step 1: find foundation chunk (same primary type) ──────────────────
    # qdrant-client ≥ 1.12 uses query_points() instead of search()
    try:
        resp = client.query_points(
            collection_name=COLLECTION,
            query=scene_vector,                              # plain list[float] → nearest
            using="audio_dense",
            query_filter=_type_filter(primary_type, min_dur),
            with_vectors=["audio_dense"],
            with_payload=True,
            limit=1,
        )
        primary_hits = resp.points
    except Exception as e:
        print(f"  [layer] Foundation search failed for {primary_type}: {e}")
        return []

    if not primary_hits:
        return []

    foundation        = primary_hits[0]
    foundation_id     = foundation.id                                      # Qdrant ID
    # vector dict: {name: list[float]}  (with_vectors=["audio_dense"])
    foundation_vector = (foundation.vector or {}).get("audio_dense", [])  # 512-dim

    if not foundation_vector:
        return []

    layers: list[dict] = []

    # ── Layer 2: Nearest-audio to foundation zone, different type ─────────
    # We query by the foundation's own audio vector so the result "sounds similar"
    # to the primary chunk while belonging to the target complementary type.
    layer2_type, layer2_vol = rules[0]
    try:
        disc_resp = client.query_points(
            collection_name=COLLECTION,
            query=foundation_vector,              # anchor = primary audio vector
            using="audio_dense",
            query_filter=_type_filter(layer2_type, min_dur),
            with_payload=True,
            limit=3,
        )
        disc_hits = disc_resp.points
        if disc_hits and disc_hits[0].payload:
            layers.append(_payload_to_layer(disc_hits[0].payload, 1, layer2_vol))
    except Exception as e:
        print(f"  [layer] Foundation-zone search ({layer2_type}) failed: {e}")

    # ── Layer 3: Recommendation (positive = foundation Qdrant ID) ─────────
    if len(rules) >= 2 and scene_duration >= 4.0:
        layer3_type, layer3_vol = rules[1]
        try:
            rec_resp = client.query_points(
                collection_name=COLLECTION,
                query=RecommendQuery(
                    recommend=RecommendInput(positive=[foundation_id], negative=[])
                ),
                using="audio_dense",
                query_filter=_type_filter(layer3_type, min_dur),
                with_payload=True,
                limit=3,
            )
            rec_hits = rec_resp.points
            if rec_hits and rec_hits[0].payload:
                layers.append(_payload_to_layer(rec_hits[0].payload, 2, layer3_vol))
        except Exception as e:
            print(f"  [layer] Recommend ({layer3_type}) failed: {e}")

    return layers


# ── Main ───────────────────────────────────────────────────────────────────────
def enrich_timeline(
    timeline_path:  str = "timeline.jsonl",
    queries_path:   str = "queries_embedded.jsonl",
) -> int:
    # ── Load scene CLAP vectors from queries_embedded.jsonl ─────────────────
    scene_vectors: dict[str, list[float]] = {}
    with open(queries_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            sid = q.get("scene_id", "")
            vec = q.get("text_dense")        # 512-dim CLAP from embed_queries.py
            if sid and vec:
                scene_vectors[sid] = vec

    # ── Load timeline entries ────────────────────────────────────────────────
    entries: list[dict] = []
    with open(timeline_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    print(f"Scenes       : {len(entries)}")
    print(f"CLAP vectors : {len(scene_vectors)}")

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_KEY)

    enriched_count = 0
    for entry in tqdm(entries, desc="Layering scenes", unit="scene"):
        sid          = entry.get("scene_id", "")
        matches      = entry.get("matches", [])
        duration     = entry.get("duration", 0.0)

        scene_vector = scene_vectors.get(sid)
        if not scene_vector or not matches:
            entry["layer_matches"] = []
            continue

        primary_type = matches[0].get("audio_type", "foley")

        # Set primary volume on the existing top match
        matches[0]["volume"] = PRIMARY_VOLUME

        layer_matches = find_layers(
            client=client,
            scene_vector=scene_vector,
            primary_type=primary_type,
            scene_duration=duration,
        )

        entry["layer_matches"] = layer_matches
        if layer_matches:
            enriched_count += 1

    # ── Write enriched timeline (in-place) ──────────────────────────────────
    with open(timeline_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    print(f"\nDone. {enriched_count}/{len(entries)} scenes have complementary layers.")
    return enriched_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline", default="timeline.jsonl")
    parser.add_argument("--queries",  default="queries_embedded.jsonl")
    args = parser.parse_args()
    enrich_timeline(args.timeline, args.queries)
