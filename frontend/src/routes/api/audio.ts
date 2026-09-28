import { createFileRoute } from "@tanstack/react-router";
import * as fs from "fs";
import * as path from "path";

// Helper to load HF_API_KEY from process.env or the parent directory's .env file
function getHfApiKey(): string | undefined {
  if (process.env.HF_API_KEY) {
    return process.env.HF_API_KEY;
  }
  try {
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
    console.error("[audio-api] Failed to read .env file manually:", err);
  }
  return undefined;
}

// -------------------------------------------------------------
// Manifest Indexing & Normalization Helper Functions
// -------------------------------------------------------------

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
  normalized = normalized.trim().replace(/\s+/g, " ");

  return normalized;
}

function getWordSet(str: string): Set<string> {
  return new Set(str.split(" ").filter((w) => w.length > 0));
}

function calculateSimilarity(str1: string, str2: string): number {
  const words1 = getWordSet(str1);
  const words2 = getWordSet(str2);
  
  if (words1.size === 0 || words2.size === 0) return 0;
  
  let intersection = 0;
  for (const w of words1) {
    if (words2.has(w)) {
      intersection++;
    }
  }
  
  const union = new Set([...words1, ...words2]).size;
  return intersection / union;
}

// -------------------------------------------------------------
// Asset Resolver Engine
// -------------------------------------------------------------

interface Sibling {
  rfilename: string;
}

interface DatasetResponse {
  siblings?: Sibling[];
}

interface ManifestEntry {
  filename: string;          // Original path in the dataset, e.g. "FreeSound/music_bed/glitter drone.aif.169372.mp3"
  basename: string;          // Basename, e.g. "glitter drone.aif.169372.mp3"
  normalizedFilename: string;// Lowercased full path with forward slashes
  cleanBasename: string;     // Normalized basename for matching, e.g. "glitter drone"
  extension: string;         // E.g. ".mp3"
  category: string;          // E.g. "music_bed"
  fullDatasetPath: string;   // Full dataset path (same as filename)
}

class AssetResolver {
  private manifest: ManifestEntry[] = [];
  private isLoaded = false;
  private loadPromise: Promise<void> | null = null;
  private resolutionCache = new Map<string, string>(); // Cache from requestedPath -> resolved HF path

  public async ensureLoaded(): Promise<void> {
    if (this.isLoaded) return;
    if (this.loadPromise) return this.loadPromise;

    this.loadPromise = (async () => {
      try {
        console.log("[audio-resolver] Starting in-memory dataset tree indexing...");
        const hfToken = getHfApiKey();
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
          
          // Category is the direct parent folder name
          const category = parts.length > 2 ? parts[parts.length - 2] : (parts.length > 1 ? parts[0] : "");
          
          // Extract extension
          const extIndex = basename.lastIndexOf(".");
          const extension = extIndex !== -1 ? basename.substring(extIndex) : "";
          
          // Normalized versions
          const cleanBasename = normalizeString(basename);
          const normalizedFilename = fullPath.toLowerCase().replace(/\\/g, "/");

          return {
            filename: fullPath,
            basename,
            normalizedFilename,
            cleanBasename,
            extension,
            category,
            fullDatasetPath: fullPath,
          };
        });

        this.isLoaded = true;
        console.log(`[audio-resolver] Indexing complete. Loaded ${this.manifest.length} assets into in-memory manifest.`);
      } catch (err) {
        console.error("[audio-resolver] Critical error during startup asset tree fetch:", err);
        this.loadPromise = null; // Reset to allow retry on subsequent requests
        throw err;
      }
    })();

    return this.loadPromise;
  }

  public resolve(requestedPath: string): string | undefined {
    // 1. Check in-memory resolution cache first for high performance
    if (this.resolutionCache.has(requestedPath)) {
      return this.resolutionCache.get(requestedPath);
    }

    if (!this.isLoaded) {
      console.warn("[audio-resolver] Manifest not yet loaded. Trying standard clean fallback resolution.");
      let clean = requestedPath.replace(/\\/g, "/");
      while (clean.startsWith("sounds/")) {
        clean = clean.substring(7);
      }
      return clean;
    }

    const cleanRequested = requestedPath.replace(/\\/g, "/");
    const lastSlash = cleanRequested.lastIndexOf("/");
    const basename = lastSlash !== -1 ? cleanRequested.substring(lastSlash + 1) : cleanRequested;
    const normalizedRequested = normalizeString(basename);

    // Step 1: Exact normalized basename match
    const exactMatch = this.manifest.find((m) => m.cleanBasename === normalizedRequested);
    if (exactMatch) {
      this.resolutionCache.set(requestedPath, exactMatch.filename);
      return exactMatch.filename;
    }

    // Step 2: Simple substring match
    const substringMatch = this.manifest.find(
      (m) => m.cleanBasename.includes(normalizedRequested) || normalizedRequested.includes(m.cleanBasename)
    );
    if (substringMatch) {
      this.resolutionCache.set(requestedPath, substringMatch.filename);
      return substringMatch.filename;
    }

    // Step 3: Jaccard word-level similarity fuzzy match
    let bestMatch: ManifestEntry | undefined = undefined;
    let bestScore = 0;

    for (const entry of this.manifest) {
      const score = calculateSimilarity(normalizedRequested, entry.cleanBasename);
      if (score > bestScore) {
        bestScore = score;
        bestMatch = entry;
      }
    }

    if (bestMatch && bestScore > 0.3) {
      console.log(`[audio-resolver] Fuzzy resolved "${basename}" to "${bestMatch.filename}" with similarity score ${bestScore.toFixed(3)}`);
      this.resolutionCache.set(requestedPath, bestMatch.filename);
      return bestMatch.filename;
    }

    return undefined;
  }
}

const resolverInstance = new AssetResolver();

// -------------------------------------------------------------
// Route Handler Definition
// -------------------------------------------------------------

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges",
};

export const Route = createFileRoute("/api/audio")({
  server: {
    handlers: {
      OPTIONS: async () => {
        return new Response(null, {
          status: 204,
          headers: {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "86400",
          },
        });
      },
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const relativePath = url.searchParams.get("path");
        if (!relativePath) {
          return new Response("Missing path", {
            status: 400,
            headers: corsHeaders,
          });
        }

        // Ensure in-memory manifest is loaded and ready
        try {
          await resolverInstance.ensureLoaded();
        } catch (e) {
          console.error("[audio-api] Manifest loading failed. Falling back to default path parsing.", e);
        }

        // Resolve requested path to actual Hugging Face dataset path
        let resolvedPath = resolverInstance.resolve(relativePath);
        
        // Graceful fallback if resolver returns undefined
        if (!resolvedPath) {
          console.warn(`[audio-api] Warning: Could not resolve "${relativePath}" via manifest. Using fallback path.`);
          let clean = relativePath.replace(/\\/g, "/");
          while (clean.startsWith("sounds/")) {
            clean = clean.substring(7);
          }
          resolvedPath = clean;
        }

        const hfUrl = `https://huggingface.co/datasets/Pandago/qad-buc/resolve/main/${resolvedPath}`;

        // Debug logging showing: requested name, resolved dataset path, final URL
        console.log(`[audio-api] DEBUG RESOLUTION:
  - Requested Name:      "${relativePath}"
  - Resolved Dataset Path: "${resolvedPath}"
  - Final HF URL:         "${hfUrl}"`);

        const hfToken = getHfApiKey();
        const headers: Record<string, string> = {};
        if (hfToken) {
          headers["Authorization"] = `Bearer ${hfToken}`;
        }

        // Forward Range header if present to support scrubbing/seeking in browser
        const rangeHeader = request.headers.get("range");
        if (rangeHeader) {
          headers["range"] = rangeHeader;
        }

        try {
          const hfResponse = await fetch(hfUrl, { headers });

          if (!hfResponse.ok) {
            console.error(`[audio-api] Failed to fetch from Hugging Face (${hfResponse.status}): ${hfUrl}`);
            return new Response(`Failed to fetch from Hugging Face: ${hfResponse.statusText}`, {
              status: hfResponse.status,
              headers: corsHeaders,
            });
          }

          // Construct response headers, forwarding crucial streaming headers from HF
          const responseHeaders = new Headers();
          const headersToForward = [
            "content-type",
            "content-length",
            "content-range",
            "accept-ranges",
            "cache-control",
          ];

          for (const headerName of headersToForward) {
            const value = hfResponse.headers.get(headerName);
            if (value) {
              responseHeaders.set(headerName, value);
            }
          }

          // Fallback for Content-Type
          if (!responseHeaders.has("content-type")) {
            let contentType = "audio/wav";
            if (resolvedPath.endsWith(".mp3")) {
              contentType = "audio/mpeg";
            } else if (resolvedPath.endsWith(".ogg")) {
              contentType = "audio/ogg";
            }
            responseHeaders.set("content-type", contentType);
          }

          // Inject CORS headers
          for (const [key, val] of Object.entries(corsHeaders)) {
            responseHeaders.set(key, val);
          }

          // Return stream back to the browser
          return new Response(hfResponse.body, {
            status: hfResponse.status,
            statusText: hfResponse.statusText,
            headers: responseHeaders,
          });

        } catch (error: any) {
          console.error(`[audio-api] Connection error while fetching from Hugging Face:`, error);
          return new Response(`Connection error: ${error.message || error}`, {
            status: 500,
            headers: corsHeaders,
          });
        }
      },
    },
  },
});
