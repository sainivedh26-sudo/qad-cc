/// Audio Pipeline — Rust component
///
/// Two subcommands:
///   upload   — reads manifest.jsonl and bulk-upserts to Qdrant via gRPC
///   retrieve — reads queries.jsonl and runs concurrent named-vector searches
///
/// Usage:
///   cargo run --release -- upload --manifest manifest.jsonl --batch 128
///   cargo run --release -- retrieve --queries queries_embedded.jsonl --top 5

use std::{
    collections::HashMap,
    fs::File,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    sync::Arc,
    time::Instant,
};

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use futures::stream::{self, StreamExt};
use indicatif::{ProgressBar, ProgressStyle};
use serde::{Deserialize, Serialize};

use qdrant_client::{
    Payload, Qdrant,
    qdrant::{
        Condition, Filter, PointStruct, SearchPointsBuilder,
        UpsertPointsBuilder, Vector,
    },
};

// ── Config ─────────────────────────────────────────────────────────────────────
fn qdrant_url() -> String {
    std::env::var("QDRANT_URL_GRPC")
        .unwrap_or_else(|_| panic!("QDRANT_URL_GRPC not set — add it to .env or environment"))
}

fn qdrant_key() -> String {
    std::env::var("QDRANT_KEY")
        .unwrap_or_else(|_| panic!("QDRANT_KEY not set — add it to .env or environment"))
}

const COLLECTION: &str = "audio_chunks";

// ── CLI ────────────────────────────────────────────────────────────────────────
#[derive(Parser)]
#[command(name = "audio_pipeline", about = "BBC Audio × Qdrant pipeline")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Bulk-upload manifest.jsonl chunks to Qdrant
    Upload {
        #[arg(long, default_value = "manifest.jsonl")]
        manifest: PathBuf,
        /// Points per gRPC upsert call
        #[arg(long, default_value_t = 128)]
        batch: usize,
        /// Parallel upsert workers
        #[arg(long, default_value_t = 4)]
        workers: usize,
    },
    /// Query Qdrant with scene query objects and write ranked results
    Retrieve {
        #[arg(long, default_value = "queries_embedded.jsonl")]
        queries: PathBuf,
        /// Results per scene
        #[arg(long, default_value_t = 5)]
        top: usize,
        /// Output file for timeline results
        #[arg(long, default_value = "timeline.jsonl")]
        output: PathBuf,
        /// Parallel query workers
        #[arg(long, default_value_t = 8)]
        workers: usize,
    },
}

// ── Manifest record (matches Python output) ────────────────────────────────────
#[derive(Debug, Deserialize, Clone)]
struct ManifestChunk {
    chunk_id: String,
    track_id: String,
    start_sec: f64,
    end_sec: f64,
    duration: f64,
    audio_type: String,
    category: String,
    cd_name: String,
    description: String,
    caption: String,
    tags: Vec<String>,
    source_path: String,
    source: String,
    license: String,
    energy: f64,
    loudness_lufs: f64,
    bpm: Option<f64>,
    spectral_centroid: f64,
    zero_crossing_rate: f64,
    audio_dense: Vec<f32>,
    text_dense: Vec<f32>,
    sparse_indices: Vec<u32>,
    sparse_values: Vec<f32>,
}

// ── Query record (from Python video analysis) ──────────────────────────────────
#[derive(Debug, Deserialize, Clone)]
struct SceneQuery {
    scene_id: String,
    start_sec: f64,
    end_sec: f64,
    duration: f64,
    motion_score: f64,
    mood: Vec<String>,
    environment: Vec<String>,
    brightness_level: String,
    needs: String,
    description: String,
    // Pre-computed text embedding (added by embed_queries.py — 512-dim CLAP)
    #[serde(default)]
    text_dense: Vec<f32>,
}

// ── Result records ─────────────────────────────────────────────────────────────
#[derive(Debug, Serialize)]
struct TimelineEntry {
    scene_id: String,
    start_sec: f64,
    end_sec: f64,
    duration: f64,
    needs: String,
    matches: Vec<AudioMatch>,
}

#[derive(Debug, Serialize)]
struct AudioMatch {
    chunk_id: String,
    track_id: String,
    score: f64,
    chunk_start_sec: f64,
    chunk_end_sec: f64,
    audio_type: String,
    caption: String,
    source_path: String,
    energy: f64,
}

// ── Qdrant client ──────────────────────────────────────────────────────────────
fn build_client() -> Result<Qdrant> {
    Qdrant::from_url(&qdrant_url())
        .api_key(qdrant_key())
        .build()
        .context("Failed to build Qdrant client")
}

// ── UPLOAD ─────────────────────────────────────────────────────────────────────
async fn run_upload(manifest: PathBuf, batch_size: usize, workers: usize) -> Result<()> {
    println!("Loading manifest: {}", manifest.display());

    let file = File::open(&manifest)
        .with_context(|| format!("Cannot open {}", manifest.display()))?;

    let chunks: Vec<ManifestChunk> = BufReader::new(file)
        .lines()
        .filter_map(|l| l.ok())
        .filter(|l| !l.trim().is_empty())
        .filter_map(|l| {
            serde_json::from_str::<ManifestChunk>(&l)
                .map_err(|e| eprintln!("Manifest parse error: {e}"))
                .ok()
        })
        .collect();

    println!("Chunks to upload: {}", chunks.len());

    let bar = Arc::new(ProgressBar::new(chunks.len() as u64));
    bar.set_style(
        ProgressStyle::default_bar()
            .template("{wide_bar} {pos}/{len} | {per_sec} | ETA {eta}")
            .unwrap(),
    );

    let client = Arc::new(build_client()?);

    // Split into batches (vec of vecs)
    let batches: Vec<Vec<ManifestChunk>> = chunks
        .chunks(batch_size)
        .map(|s| s.to_vec())
        .collect();

    stream::iter(batches)
        .map(|batch| {
            let client = Arc::clone(&client);
            let bar = Arc::clone(&bar);
            async move {
                let n = batch.len();
                let points: Vec<PointStruct> = batch
                    .into_iter()
                    .filter_map(|c| chunk_to_point(c).ok())
                    .collect();

                if points.is_empty() {
                    return;
                }

                if let Err(e) = client
                    .upsert_points(
                        UpsertPointsBuilder::new(COLLECTION, points).wait(false),
                    )
                    .await
                {
                    eprintln!("Upsert error: {e}");
                }
                bar.inc(n as u64);
            }
        })
        .buffer_unordered(workers)
        .for_each(|_| async {})
        .await;

    bar.finish_with_message("Upload complete");
    println!("Done. Collection = '{COLLECTION}'");
    Ok(())
}

fn chunk_to_point(chunk: ManifestChunk) -> Result<PointStruct> {
    let id = chunk_id_to_u64(&chunk.chunk_id);

    // Named vectors: HashMap<String, Vector> → Vectors via From impl
    let mut vecs: HashMap<String, Vector> = HashMap::new();
    vecs.insert("audio_dense".into(), Vector::new_dense(chunk.audio_dense));
    vecs.insert("text_dense".into(),  Vector::new_dense(chunk.text_dense));
    vecs.insert(
        "text_sparse".into(),
        Vector::new_sparse(chunk.sparse_indices, chunk.sparse_values),
    );

    // Payload
    let mut payload = Payload::new();
    payload.insert("chunk_id",          chunk.chunk_id);
    payload.insert("track_id",          chunk.track_id);
    payload.insert("start_sec",         chunk.start_sec);
    payload.insert("end_sec",           chunk.end_sec);
    payload.insert("duration",          chunk.duration);
    payload.insert("audio_type",        chunk.audio_type);
    payload.insert("category",          chunk.category);
    payload.insert("cd_name",           chunk.cd_name);
    payload.insert("description",       chunk.description);
    payload.insert("caption",           chunk.caption);
    payload.insert("source_path",       chunk.source_path);
    payload.insert("source",            chunk.source);
    payload.insert("license",           chunk.license);
    payload.insert("energy",            chunk.energy);
    payload.insert("loudness_lufs",     chunk.loudness_lufs);
    payload.insert("spectral_centroid", chunk.spectral_centroid);
    payload.insert("zero_crossing_rate",chunk.zero_crossing_rate);
    if let Some(bpm) = chunk.bpm {
        payload.insert("bpm", bpm);
    }
    payload.insert("tags", chunk.tags.join(","));

    Ok(PointStruct::new(id, vecs, payload))
}

fn chunk_id_to_u64(chunk_id: &str) -> u64 {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut h = DefaultHasher::new();
    chunk_id.hash(&mut h);
    h.finish()
}

// ── RETRIEVE ──────────────────────────────────────────────────────────────────
async fn run_retrieve(
    queries_path: PathBuf,
    top_k: usize,
    output: PathBuf,
    workers: usize,
) -> Result<()> {
    println!("Loading queries: {}", queries_path.display());

    let file = File::open(&queries_path)
        .with_context(|| format!("Cannot open {}", queries_path.display()))?;

    let queries: Vec<SceneQuery> = BufReader::new(file)
        .lines()
        .filter_map(|l| l.ok())
        .filter(|l| !l.trim().is_empty())
        .filter_map(|l| {
            serde_json::from_str::<SceneQuery>(&l)
                .map_err(|e| eprintln!("Query parse error: {e}"))
                .ok()
        })
        .collect();

    println!(
        "Querying {} scenes (top-{}, {} workers)...",
        queries.len(),
        top_k,
        workers
    );

    let client = Arc::new(build_client()?);

    let bar = Arc::new(ProgressBar::new(queries.len() as u64));
    bar.set_style(
        ProgressStyle::default_bar()
            .template("{wide_bar} {pos}/{len} scenes | {elapsed_precise}")
            .unwrap(),
    );

    let start = Instant::now();

    let mut results: Vec<TimelineEntry> = stream::iter(queries)
        .map(|query| {
            let client = Arc::clone(&client);
            let bar = Arc::clone(&bar);
            async move {
                let entry = search_scene(&client, &query, top_k).await;
                bar.inc(1);
                entry
            }
        })
        .buffer_unordered(workers)
        .filter_map(|r| async move {
            match r {
                Ok(e)  => Some(e),
                Err(e) => { eprintln!("Search error: {e}"); None }
            }
        })
        .collect()
        .await;

    bar.finish_with_message("Retrieval complete");

    // Sort by scene start time
    results.sort_by(|a, b| a.start_sec.partial_cmp(&b.start_sec).unwrap());

    println!(
        "Retrieved {} timeline entries in {:.1}s",
        results.len(),
        start.elapsed().as_secs_f32()
    );

    let mut out = File::create(&output)
        .with_context(|| format!("Cannot create {}", output.display()))?;
    for entry in &results {
        writeln!(out, "{}", serde_json::to_string(entry)?)?;
    }
    println!("Timeline → {}", output.display());
    Ok(())
}

async fn search_scene(
    client: &Qdrant,
    query: &SceneQuery,
    top_k: usize,
) -> Result<TimelineEntry> {
    if query.text_dense.is_empty() {
        anyhow::bail!(
            "Scene '{}' has no text_dense embedding. Run embed_queries.py first.",
            query.scene_id
        );
    }

    // Filter by audio_type that matches the scene's need
    let filter = Filter::must([Condition::matches(
        "audio_type",
        query.needs.clone(),
    )]);

    // Cross-modal retrieval: CLAP text embedding (512-dim) queries audio_dense (512-dim CLAP audio)
    // CLAP aligns audio and text in the same embedding space — this IS the bridge
    let search = SearchPointsBuilder::new(
        COLLECTION,
        query.text_dense.clone(),
        top_k as u64,
    )
    .vector_name("audio_dense")
    .filter(filter)
    .with_payload(true)
    .build();

    let response = client.search_points(search).await?;

    let matches: Vec<AudioMatch> = response
        .result
        .into_iter()
        .map(|hit| {
            let p = &hit.payload;
            AudioMatch {
                chunk_id:       payload_str(p, "chunk_id"),
                track_id:       payload_str(p, "track_id"),
                score:          hit.score as f64,
                chunk_start_sec:payload_f64(p, "start_sec"),
                chunk_end_sec:  payload_f64(p, "end_sec"),
                audio_type:     payload_str(p, "audio_type"),
                caption:        payload_str(p, "caption"),
                source_path:    payload_str(p, "source_path"),
                energy:         payload_f64(p, "energy"),
            }
        })
        .collect();

    Ok(TimelineEntry {
        scene_id:  query.scene_id.clone(),
        start_sec: query.start_sec,
        end_sec:   query.end_sec,
        duration:  query.duration,
        needs:     query.needs.clone(),
        matches,
    })
}

// ── Payload helpers ────────────────────────────────────────────────────────────
use qdrant_client::qdrant::value::Kind;

fn payload_str(
    p: &HashMap<String, qdrant_client::qdrant::Value>,
    key: &str,
) -> String {
    p.get(key)
        .and_then(|v| v.kind.as_ref())
        .and_then(|k| match k {
            Kind::StringValue(s) => Some(s.clone()),
            _ => None,
        })
        .unwrap_or_default()
}

fn payload_f64(
    p: &HashMap<String, qdrant_client::qdrant::Value>,
    key: &str,
) -> f64 {
    p.get(key)
        .and_then(|v| v.kind.as_ref())
        .and_then(|k| match k {
            Kind::DoubleValue(d) => Some(*d),
            Kind::IntegerValue(i) => Some(*i as f64),
            _ => None,
        })
        .unwrap_or(0.0)
}

// ── Entry point ────────────────────────────────────────────────────────────────
#[tokio::main]
async fn main() -> Result<()> {
    dotenvy::dotenv().ok();   // loads .env if present, silently ignores if missing
    let cli = Cli::parse();
    match cli.command {
        Command::Upload { manifest, batch, workers } => {
            run_upload(manifest, batch, workers).await?;
        }
        Command::Retrieve { queries, top, output, workers } => {
            run_retrieve(queries, top, output, workers).await?;
        }
    }
    Ok(())
}
