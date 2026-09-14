# Qad - a.k.a Qdrant audio director

Automatically matches royalty-free sound effects and music to every scene in a video — not by keyword search, but by understanding what is visually happening and retrieving the audio that emotionally and contextually fits. A video is split into scenes using PySceneDetect; each scene is analysed for motion energy, colour mood, and environment to produce a natural-language description. That description is encoded with LAION CLAP's text encoder (512-dim) and sent as a vector query against a Qdrant collection of BBC Sound Effects chunks, each embedded with CLAP's audio encoder. Because CLAP aligns audio and text in the same embedding space, the query crosses modalities directly — no tags, no keyword matching. Qdrant stores the 16 k-track library as named vectors (`audio_dense`, `text_dense`, `text_sparse`) with TurboQuantization bits4 for fast retrieval, and the Rust retrieval service queries all scenes in parallel over gRPC, assembling a per-second timeline of ranked audio candidates in seconds. The final step composites the matched clips onto the muted video with smooth fade-in/out transitions and renders an MP4.

The ingestion side is a Python pipeline: BBC WAV files are chunked by type (ambience 5 s / music-bed 7 s / transition FX 1.5 s / foley 3 s / impact 2.5 s) with overlapping windows, CLAP audio embeddings and sentence-transformer text embeddings are extracted per chunk, and a JSONL manifest is written for a Rust bulk uploader that batches gRPC upserts at up to 4 parallel workers. The video side runs PySceneDetect → OpenCV optical flow for motion scoring → colour histogram mood analysis → CLAP text embedding → Rust retrieval → MoviePy compositor, all driven from the `pipeline/` scripts below.

---

## Requirements

- Python 3.11+, Rust 1.78+, `ffmpeg` on PATH
- A [Qdrant Cloud](https://cloud.qdrant.io) cluster (free tier works)
- BBC Sound Effects library — run `python bbcDownload.py --max-secs 30` to fetch the short-form files first (~15 GB)

```
pip install -r requirements.txt
# PyTorch: swap +cpu for +cu121 if you have a CUDA GPU
```

Copy `.env.example` to `.env` and fill in your cluster URLs and API key.

---

## Get Started

```bash
# 1. Download BBC audio (short files first, runs in background)
python bbcDownload.py --max-secs 30 --threads 8

# 2. Create the Qdrant collection (run once)
python pipeline/setup_collection.py

# 3. Embed audio chunks and write manifest
python pipeline/ingest_audio.py --manifest manifest.jsonl

# 4. Upload to Qdrant (Rust, parallel gRPC)
cargo run --release -- upload --manifest manifest.jsonl --batch 128 --workers 4

# 5. Analyse your video and build scene queries
python pipeline/analyze_video.py your_video.mp4 --queries queries.jsonl

# 6. Embed scene descriptions with CLAP text encoder
python pipeline/embed_queries.py --queries queries.jsonl --out queries_embedded.jsonl

# 7. Retrieve matching audio for every scene
cargo run --release -- retrieve --queries queries_embedded.jsonl --top 5 --output timeline.jsonl

# 8. Render final video with matched audio
python pipeline/compose_video.py --video your_video.mp4 --timeline timeline.jsonl --out output_matched.mp4
```

---

## Project Layout

```
.
├── pipeline/
│   ├── setup_collection.py   # create Qdrant collection with TurboQuant bits4
│   ├── ingest_audio.py       # chunk + CLAP-embed BBC WAVs → manifest.jsonl
│   ├── audio_type.py         # category → audio_type + chunk window rules
│   ├── analyze_video.py      # PySceneDetect + motion/colour analysis → queries.jsonl
│   ├── embed_queries.py      # CLAP text-encode scene descriptions
│   ├── show_timeline.py      # pretty-print timeline.jsonl
│   └── compose_video.py      # composite matched audio onto muted video
├── src/main.rs               # Rust: `upload` + `retrieve` subcommands (gRPC)
├── bbcDownload.py            # BBC Sound Effects downloader (fixed CDN URL)
├── BBCSoundEffects.csv       # 16 k-track metadata index
├── Cargo.toml
└── requirements.txt
```

---

## Environment Variables (`.env`)

| Variable | Used by | Description |
|---|---|---|
| `QDRANT_URL` | Python | REST endpoint, port 6333 |
| `QDRANT_URL_GRPC` | Rust | gRPC endpoint, port 6334 |
| `QDRANT_KEY` | Both | Qdrant Cloud API key |
