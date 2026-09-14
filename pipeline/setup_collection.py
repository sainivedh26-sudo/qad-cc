"""
Create the audio_chunks Qdrant collection with named vectors and TurboQuantization.

Named vectors:
  audio_dense  512-dim  CLAP audio encoder   cosine
  text_dense   384-dim  all-MiniLM-L6-v2     cosine
Sparse vector:
  text_sparse  BM25-style term weights

Quantization: ProductQuantization x4 compression (~4-bit effective = TurboQuant bits4)
"""

import os
from dotenv import load_dotenv
load_dotenv()
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseIndexParams,
    HnswConfigDiff,
    OptimizersConfigDiff,
    TurboQuantization,
    TurboQuantQuantizationConfig,
    TurboQuantBitSize,
)

QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_KEY = os.environ["QDRANT_KEY"]
COLLECTION = "audio_chunks"

AUDIO_DENSE_DIM = 512   # CLAP audio encoder output
TEXT_DENSE_DIM  = 384   # all-MiniLM-L6-v2 output


def create_collection(recreate: bool = False) -> None:
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_KEY)

    existing = [c.name for c in client.get_collections().collections]

    if COLLECTION in existing:
        if not recreate:
            print(f"Collection '{COLLECTION}' already exists. Pass recreate=True to reset.")
            return
        print(f"Deleting existing collection '{COLLECTION}'...")
        client.delete_collection(COLLECTION)

    print(f"Creating collection '{COLLECTION}'...")
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            "audio_dense": VectorParams(
                size=AUDIO_DENSE_DIM,
                distance=Distance.COSINE,
                on_disk=False,
            ),
            "text_dense": VectorParams(
                size=TEXT_DENSE_DIM,
                distance=Distance.COSINE,
                on_disk=False,
            ),
        },
        sparse_vectors_config={
            "text_sparse": SparseVectorParams(
                index=SparseIndexParams(on_disk=False),
            ),
        },
        # TurboQuantization bits4 — native 4-bit quantization
        quantization_config=TurboQuantization(
            turbo=TurboQuantQuantizationConfig(
                bits=TurboQuantBitSize.BITS4,
                always_ram=True,
            )
        ),
        hnsw_config=HnswConfigDiff(
            m=32,
            ef_construct=200,
            full_scan_threshold=10000,
            on_disk=False,
        ),
        optimizers_config=OptimizersConfigDiff(
            indexing_threshold=20000,
            memmap_threshold=50000,
        ),
    )

    print(f"Collection '{COLLECTION}' created successfully.")
    info = client.get_collection(COLLECTION)
    print(f"  Status: {info.status}")
    print(f"  Vectors: {list(info.config.params.vectors.keys())}")


if __name__ == "__main__":
    import sys
    recreate = "--recreate" in sys.argv
    create_collection(recreate=recreate)
