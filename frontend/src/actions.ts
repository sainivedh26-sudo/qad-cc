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

    if (!this.isLoaded) {
      let clean = fallbackPath.replace(/\\/g, "/");
      while (clean.startsWith("sounds/")) {
        clean = clean.substring(7);
      }
      return clean;
    }

    // Strip chunk suffix: e.g. "fs_glitter_drone_aif_169372_chunk_015" -> "fs_glitter_drone_aif_169372"
    const trackId = chunkId.replace(/_chunk_\d{3}$/, "");

    let resolved: string | undefined = undefined;

    if (trackId.startsWith("fs_")) {
      // FreeSound match logic
      const fsTrack = trackId.substring(3); // strip "fs_"
      const normalizedTrack = normalizeString(fsTrack);

      // Exact match normalized basename
      const match = this.manifest.find((m) => m.cleanBasename === normalizedTrack);
      if (match) {
        resolved = match.filename;
      } else {
        // Fallback to substring
        const subMatch = this.manifest.find(
          (m) => m.cleanBasename.includes(normalizedTrack) || normalizedTrack.includes(m.cleanBasename)
        );
        if (subMatch) {
          resolved = subMatch.filename;
        }
      }
    } else {
      // BBC Sound Effects match logic (numeric track ID match in filename)
      const match = this.manifest.find((m) => m.filename.includes(trackId));
      if (match) {
        resolved = match.filename;
      }
    }

    if (resolved) {
      console.log(`[actions-resolver] Resolved chunk ID "${chunkId}" to HF dataset path "${resolved}"`);
      this.resolutionCache.set(chunkId, resolved);
      return resolved;
    }

    // Fallback: clean original path
    console.warn(`[actions-resolver] Warning: Could not resolve chunk ID "${chunkId}". Using clean fallback: "${fallbackPath}"`);
    let clean = fallbackPath.replace(/\\/g, "/");
    while (clean.startsWith("sounds/")) {
      clean = clean.substring(7);
    }
    return clean;
  }
}

const exportResolver = new ExportAssetResolver();

interface TimelineEntry {
  scene_id: string;
  start_sec: number;
  end_sec: number;
  duration: number;
  needs: string;
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

        return {
          id: index,
          label: `${labelStr} Scene`,
          mood: labelStr,
          tone: labelStr,
          energy: entry.matches[0]?.energy || 0.5,
          recs: recs,
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
  .inputValidator((data: unknown) => data as Record<number, string>)
  .handler(async ({ data: selections }) => {
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

      // Run compose_video.py to render the final video
      console.log("[server] Running video compositor (compose_video.py)...");
      execSync(
        `python pipeline/compose_video.py --video frontend/public/input_video.mp4 --timeline timeline.jsonl --out frontend/public/output_matched.mp4`,
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
