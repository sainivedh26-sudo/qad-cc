import { createServerFn } from "@tanstack/react-start";
import * as fs from "fs";
import * as path from "path";
import { execSync } from "child_process";

// Helper to get project root directory (one level up from frontend)
const getProjectRoot = () => {
  return path.resolve(process.cwd(), "..");
};

// Helper to load HF_API_KEY from process.env or the parent directory's .env file
// Uses dynamic imports to prevent Vite client-side bundle compilation warnings
async function getHfApiKey(): Promise<string | undefined> {
  if (process.env.HF_API_KEY) {
    return process.env.HF_API_KEY;
  }
  try {
    const fs = await import("fs");
    const path = await import("path");
    const projectRoot = path.resolve(process.cwd(), "..");
    const envPath = path.resolve(projectRoot, ".env");
    if (fs.existsSync(envPath)) {
      const content = fs.readFileSync(envPath, "utf-8");
      const match = content.match(/^HF_API_KEY\s*=\s*(.*)$/m);
      if (match) {
        return match[1].trim();
      }
    }
  } catch (err) {
    console.error("[actions-resolver] Failed to read .env file manually:", err);
  }
  return undefined;
}

function normalizeString(str: string): string {
  let normalized = str.toLowerCase();
  try {
    normalized = decodeURIComponent(normalized);
  } catch (e) {
    // Ignore decode errors
  }

  // Remove common audio extensions recursively
  normalized = normalized.replace(/\.(mp3|wav|aif|aiff|ogg|flac|aac)/g, "");

  // Remove Freesound numeric suffixes (5 to 8 digits preceded by dot, space, underscore, or hyphen)
  normalized = normalized.replace(/[\._\-\s]\d{5,8}\b/g, "");
  // Also remove standalone 5 to 8 digit numbers
  normalized = normalized.replace(/\b\d{5,8}\b/g, "");

  // Replace all other non-word symbols except letters and numbers with spaces
  normalized = normalized.replace(/[^a-z0-9]/g, " ");
  // Collapse multiple spaces and trim
  return normalized.trim().replace(/\s+/g, " ");
}

// -------------------------------------------------------------
// Exporter Asset Resolver Engine
// -------------------------------------------------------------

interface Sibling {
  rfilename: string;
}

interface DatasetResponse {
  siblings?: Sibling[];
}

interface ManifestEntry {
  filename: string;
  cleanBasename: string;
}

class ExportAssetResolver {
  private manifest: ManifestEntry[] = [];
  private isLoaded = false;
  private loadPromise: Promise<void> | null = null;
  private resolutionCache = new Map<string, string>(); // Cache from chunkId -> resolved HF path

  public async ensureLoaded(): Promise<void> {
    if (this.isLoaded) return;
    if (this.loadPromise) return this.loadPromise;

    this.loadPromise = (async () => {
      try {
        console.log("[actions-resolver] Loading Hugging Face dataset tree for exporter...");
        const hfToken = await getHfApiKey();
        const headers: Record<string, string> = {};
        if (hfToken) {
          headers["Authorization"] = `Bearer ${hfToken}`;
        }

        const url = "https://huggingface.co/api/datasets/Pandago/qad-buc";
        const res = await fetch(url, { headers });
        if (!res.ok) {
          throw new Error(`Failed to fetch dataset info: ${res.statusText}`);
        }

        const json = (await res.json()) as DatasetResponse;
        const siblings = json.siblings || [];
        
        this.manifest = siblings.map((s) => {
          const fullPath = s.rfilename;
          const parts = fullPath.split("/");
          const basename = parts[parts.length - 1];
          const cleanBasename = normalizeString(basename);
          return {
            filename: fullPath,
            cleanBasename,
          };
        });

        this.isLoaded = true;
        console.log(`[actions-resolver] Exporter manifest loaded. Indexed ${this.manifest.length} items.`);
      } catch (err) {
        console.error("[actions-resolver] Error loading exporter asset manifest:", err);
        this.loadPromise = null;
        throw err;
      }
    })();

    return this.loadPromise;
  }

  public resolve(chunkId: string, fallbackPath: string): string {
    if (this.resolutionCache.has(chunkId)) {
      return this.resolutionCache.get(chunkId)!;
    }

    // Clean the direct source_path — strip backslashes and leading "sounds/" segments.
    // The Qdrant payload source_path is already the correct HF path, just prefixed.
    let directPath = fallbackPath.replace(/\\/g, "/");
    while (directPath.startsWith("sounds/")) {
      directPath = directPath.substring(7);
    }

    if (!this.isLoaded) {
      return directPath;
    }

    // Strip chunk suffix: e.g. "fs_glitter_drone_aif_169372_chunk_015" -> "fs_glitter_drone_aif_169372"
    const trackId = chunkId.replace(/_chunk_\d{3}$/, "");

    let resolved: string | undefined = undefined;

    if (trackId.startsWith("fs_")) {
      // ── Step 1: exact normalized-name match ─────────────────────────────
      const fsTrack = trackId.substring(3); // strip "fs_"
      const normalizedTrack = normalizeString(fsTrack);

      resolved = this.manifest.find((m) => m.cleanBasename === normalizedTrack)?.filename;

      // ── Step 2: match by FreeSound numeric ID extracted from source_path ─
      // e.g. "sounds/FreeSound/tension/Lost Valley.612377.mp3" → id "612377"
      if (!resolved) {
        const idMatch = directPath.match(/\.(\d{5,8})\.(mp3|wav|aif|aiff|ogg|flac)$/i);
        if (idMatch) {
          const fsId = idMatch[1];
          resolved = this.manifest.find((m) => m.filename.includes(`.${fsId}.`))?.filename;
        }
      }

      // ── Step 3: substring match — skip entries with trivial cleanBasename ─
      // (Files like "65421.wav.506168.mp3" normalise to "" and match everything)
      if (!resolved) {
        resolved = this.manifest.find(
          (m) =>
            m.cleanBasename.length >= 4 &&
            (m.cleanBasename.includes(normalizedTrack) ||
              normalizedTrack.includes(m.cleanBasename))
        )?.filename;
      }

    } else {
      // ── BBC: match by the numeric track ID embedded in the filename ──────
      resolved = this.manifest.find((m) => m.filename.includes(trackId))?.filename;
    }

    if (resolved) {
      this.resolutionCache.set(chunkId, resolved);
      return resolved;
    }

    // ── Final fallback: the cleaned source_path is already correct ─────────
    // (The Qdrant payload contains the real HF path — just trust it.)
    this.resolutionCache.set(chunkId, directPath);
    return directPath;
  }
}

const exportResolver = new ExportAssetResolver();

interface TimelineEntry {
  scene_id: string;
  start_sec: number;
  end_sec: number;
  duration: number;
  needs: string;
  muted?: boolean;
  matches: Array<{
    chunk_id: string;
    track_id: string;
    score: number;
    chunk_start_sec: number;
    chunk_end_sec: number;
    audio_type: string;
    caption: string;
    source_path: string;
    energy: number;
    volume?: number;
  }>;
  layer_matches?: Array<{
    layer: number;
    audio_type: string;
    chunk_id: string;
    source_path: string;
    chunk_start_sec: number;
    chunk_end_sec: number;
    caption: string;
    score: number;
    energy: number;
    volume: number;
  }>;
}

export const processVideoServer = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => data as FormData)
  .handler(async ({ data }) => {
    console.log("[server] Starting processVideoServer...");
    const file = data.get("file") as File;
    const prompt = (data.get("prompt") as string) || "";

    if (!file) {
      throw new Error("No file uploaded");
    }

    const projectRoot = getProjectRoot();
    const publicDir = path.join(process.cwd(), "public");
    
    // Ensure public folder exists
    if (!fs.existsSync(publicDir)) {
      fs.mkdirSync(publicDir, { recursive: true });
    }

    const inputVideoPath = path.join(publicDir, "input_video.mp4");
    console.log(`[server] Saving uploaded video to ${inputVideoPath}...`);
    
    const arrayBuffer = await file.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);
    await fs.promises.writeFile(inputVideoPath, buffer);

    try {
      console.log("[server] Running Step 1/3: video analysis (analyze_video.py)...");
      execSync(
        `python pipeline/analyze_video.py frontend/public/input_video.mp4 --queries queries.jsonl`,
        { cwd: projectRoot, stdio: "inherit" }
      );

      console.log("[server] Running Step 2/3: embedding queries (embed_queries.py)...");
      execSync(
        `python pipeline/embed_queries.py --queries queries.jsonl --out queries_embedded.jsonl`,
        { cwd: projectRoot, stdio: "inherit" }
      );

      console.log("[server] Running Step 3/3: retrieving audio from Qdrant via Rust component...");
      execSync(
        `cargo run --release -- retrieve --queries queries_embedded.jsonl --top 5 --output timeline.jsonl`,
        { cwd: projectRoot, stdio: "inherit" }
      );

      console.log("[server] Running Step 3.5/3: enriching timeline with complementary audio layers...");
      execSync(
        `python pipeline/layer_audio.py --timeline timeline.jsonl --queries queries_embedded.jsonl`,
        { cwd: projectRoot, stdio: "inherit" }
      );

      // Keep a cached copy of the original timeline results for customized exports
      const originalTimelinePath = path.join(projectRoot, "timeline.jsonl");
      const cacheTimelinePath = path.join(projectRoot, "timeline_original.jsonl");
      fs.copyFileSync(originalTimelinePath, cacheTimelinePath);

      console.log("[server] Pipeline processing complete. Parsing timeline.jsonl...");

      // Pre-load the exporter manifest to resolve chunk IDs to actual HF paths
      try {
        await exportResolver.ensureLoaded();
      } catch (err) {
        console.error("[server] Failed to pre-load export resolver manifest:", err);
      }

      // Read and parse timeline.jsonl
      const timelineContent = fs.readFileSync(originalTimelinePath, "utf-8");
      const lines = timelineContent.split("\n").filter((l) => l.trim() !== "");
      
      const parsedScenes = lines.map((line, index) => {
        const entry = JSON.parse(line) as TimelineEntry;
        
        const recs = entry.matches.map((m) => {
          const durationSecs = m.chunk_end_sec - m.chunk_start_sec;
          const mins = Math.floor(durationSecs / 60);
          const secs = Math.round(durationSecs % 60);
          const durationStr = `${mins}:${String(secs).padStart(2, "0")}`;
          
          // Resolve correct HF dataset path from the chunk ID
          const hfSourcePath = exportResolver.resolve(m.chunk_id, m.source_path);
          
          return {
            id: m.chunk_id,
            title: m.caption || `Audio Chunk ${m.chunk_id.substring(0, 8)}`,
            artist: "BBC Sound Effects",
            mood: m.audio_type.charAt(0).toUpperCase() + m.audio_type.slice(1),
            duration: durationStr,
            compatibility: Math.round(m.score * 100),
            source_path: hfSourcePath, // Store exact HF path for preview playback
            chunk_start_sec: m.chunk_start_sec,
            chunk_end_sec: m.chunk_end_sec,
          };
        });

        // Generate a friendly label based on index and need
        const labelStr = entry.needs.charAt(0).toUpperCase() + entry.needs.slice(1);

        // Parse complementary layers — resolve source_path to exact HF path so
        // the browser can play layers live via /api/audio (same as primary recs)
        const layer_recs = (entry.layer_matches ?? []).map((lm: any) => {
          const hfLayerPath = lm.chunk_id
            ? exportResolver.resolve(lm.chunk_id, lm.source_path ?? "")
            : (lm.source_path ?? "");
          return {
            layer:           lm.layer ?? 1,
            audio_type:      lm.audio_type ?? "",
            chunk_id:        lm.chunk_id ?? "",
            source_path:     hfLayerPath,           // ← resolved for /api/audio
            chunk_start_sec: lm.chunk_start_sec ?? 0,
            chunk_end_sec:   lm.chunk_end_sec ?? 0,
            caption:         lm.caption ?? "",
            score:           lm.score ?? 0,
            energy:          lm.energy ?? 0.5,
            volume:          lm.volume ?? 0.38,     // ← matches new layer_audio.py defaults
          };
        });

        return {
          id: index,
          label: `${labelStr} Scene`,
          mood: labelStr,
          tone: labelStr,
          energy: entry.matches[0]?.energy || 0.5,
          recs: recs,
          layer_recs: layer_recs.length > 0 ? layer_recs : undefined,
          start_sec: entry.start_sec,
          end_sec: entry.end_sec,
        };
      });

      console.log(`[server] Parsed ${parsedScenes.length} scenes successfully.`);
      return {
        success: true,
        scenes: parsedScenes,
      };

    } catch (err: any) {
      console.error("[server] Error during pipeline execution:", err);
      throw new Error(`Pipeline execution failed: ${err?.message || err}`);
    }
  });

export const composeVideoServer = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => data as {
    selections: Record<number, string>;
    layerVolumes: Record<number, number[]>;
    stemVolumes: { vocals: number; noVocals: number };
    primaryVolumes: Record<number, number>;
    sceneAudioMuted: Record<number, boolean>;
  })
  .handler(async ({ data }) => {
    const { selections, layerVolumes, stemVolumes, primaryVolumes, sceneAudioMuted } = data;
    console.log("[server] Starting composeVideoServer with selections:", selections);
    
    const projectRoot = getProjectRoot();
    const cacheTimelinePath = path.join(projectRoot, "timeline_original.jsonl");
    const outputTimelinePath = path.join(projectRoot, "timeline.jsonl");

    if (!fs.existsSync(cacheTimelinePath)) {
      throw new Error("No original timeline found. Please run the pipeline first.");
    }

    // Ensure exporter manifest is loaded to resolve chunk IDs to exact HF paths
    try {
      await exportResolver.ensureLoaded();
    } catch (err) {
      console.error("[server] Failed to load export resolver manifest in composeVideoServer:", err);
    }

    try {
      // Read original timeline entries
      const originalContent = fs.readFileSync(cacheTimelinePath, "utf-8");
      const lines = originalContent.split("\n").filter((l) => l.trim() !== "");
      const customizedLines: string[] = [];

      for (let index = 0; index < lines.length; index++) {
        const entry = JSON.parse(lines[index]) as TimelineEntry;
        const selectedId = selections[index];

        // Resolve each match's source_path to its exact Hugging Face path
        for (const m of entry.matches) {
          m.source_path = exportResolver.resolve(m.chunk_id, m.source_path);
        }

        // Resolve layer_match source_paths to exact HF paths (same as primary matches)
        if (entry.layer_matches) {
          entry.layer_matches.forEach((lm: any) => {
            if (lm.chunk_id) {
              lm.source_path = exportResolver.resolve(lm.chunk_id, lm.source_path);
            }
          });
        }

        // Apply user-adjusted layer volumes
        if (entry.layer_matches && layerVolumes[index]) {
          entry.layer_matches.forEach((lm: any, lIdx: number) => {
            const userVol = layerVolumes[index]?.[lIdx];
            if (userVol !== undefined) lm.volume = userVol;
          });
        }

        // Apply per-scene mute flag
        (entry as any).muted = !!sceneAudioMuted[index];

        // Apply per-scene primary volume (default 0.62 matches layer_audio.py PRIMARY_VOLUME)
        if (entry.matches.length > 0) {
          const vol = primaryVolumes[index] ?? 0.62;
          (entry.matches[0] as any).volume = vol;
        }

        if (selectedId && entry.matches.length > 0) {
          // Find the index of the selected match
          const matchIndex = entry.matches.findIndex((m) => m.chunk_id === selectedId);
          if (matchIndex > -1) {
            console.log(`[server] Scene ${index}: swapping selected match '${selectedId}' to position 0`);
            const selectedMatch = entry.matches[matchIndex];
            
            // Remove the match from its current position and insert at position 0
            const updatedMatches = [...entry.matches];
            updatedMatches.splice(matchIndex, 1);
            updatedMatches.unshift(selectedMatch);
            
            entry.matches = updatedMatches;
          }
        }
        customizedLines.push(JSON.stringify(entry));
      }

      // Write customized timeline to disk
      fs.writeFileSync(outputTimelinePath, customizedLines.join("\n") + "\n", "utf-8");
      console.log(`[server] Customized timeline written to ${outputTimelinePath}`);

      // Run compose_video.py — original audio muted (stems disabled for now)
      console.log("[server] Running video compositor (compose_video.py)...");
      execSync(
        `python pipeline/compose_video.py --video frontend/public/input_video.mp4 --timeline timeline.jsonl --out frontend/public/output_matched.mp4 --vocals-vol 0 --novocals-vol 0`,
        { cwd: projectRoot, stdio: "inherit" }
      );

      console.log("[server] Rendering complete. output_matched.mp4 created.");
      return {
        success: true,
      };

    } catch (err: any) {
      console.error("[server] Error during video composition:", err);
      throw new Error(`Video composition failed: ${err?.message || err}`);
    }
  });
