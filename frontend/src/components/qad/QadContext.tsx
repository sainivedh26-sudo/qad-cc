import { createContext, useContext, useState, useCallback, useRef, type ReactNode } from "react";
import { SCENES, type Scene } from "./qad-data";
import { processVideoServer, composeVideoServer } from "../../actions";

export type Stage = "idle" | "scene" | "search" | "direction" | "composition" | "done";

export const STAGE_META: Record<Exclude<Stage, "idle" | "done">, { index: string; label: string; phrases: string[] }> = {
  scene:       { index: "01", label: "Scene Understanding",      phrases: ["Reading cinematic rhythm.", "Understanding visual momentum.", "Mapping color mood to feeling."] },
  search:      { index: "02", label: "Semantic Memory Search",   phrases: ["Searching memory for resonance.", "Exploring atmospheric similarity.", "Consulting sonic archives."] },
  direction:   { index: "03", label: "Audio Direction",          phrases: ["Synchronizing emotional cadence.", "Composing sonic atmosphere.", "Aligning breath with motion."] },
  composition: { index: "04", label: "Final Composition",        phrases: ["Balancing emotional layers.", "Preparing final cut.", "Mastering for the room."] },
};

type Ctx = {
  file: File | null;
  fileUrl: string | null;
  stage: Stage;
  progress: number;     // 0..1
  scored: boolean;      // soundtrack attached
  ambientOn: boolean;
  setAmbient: (b: boolean) => void;
  startPipeline: (f: File) => void;
  toggleScore: () => void;
  reset: () => void;
  prompt: string;
  setPrompt: (s: string) => void;
  // per-scene state: selected audio id (default: top rec)
  selections: Record<number, string>;
  acceptedScenes: Record<number, boolean>;
  selectAudio: (sceneId: number, audioId: string) => void;
  acceptAudio: (sceneId: number) => void;
  rejectAudio: (sceneId: number) => void;
  rejectingScene: number | null;
  closeReject: () => void;

  // New integrations
  scenes: Scene[];
  exportState: "idle" | "loading" | "done";
  exportVideo: () => Promise<void>;
};

const QadCtx = createContext<Ctx | null>(null);

export function QadProvider({ children }: { children: ReactNode }) {
  const [file, setFile] = useState<File | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [stage, setStage] = useState<Stage>("idle");
  const [progress, setProgress] = useState(0);
  const [scored, setScored] = useState(false);
  const [ambientOn, setAmbient] = useState(false);
  const [prompt, setPrompt] = useState("");
  
  // Dynamic scenes list from real pipeline
  const [scenes, setScenes] = useState<Scene[]>(SCENES);
  
  const defaultSel = SCENES.reduce<Record<number, string>>((acc, s) => {
    acc[s.id] = s.recs[0].id; return acc;
  }, {});
  const [selections, setSelections] = useState<Record<number, string>>(defaultSel);
  const [acceptedScenes, setAccepted] = useState<Record<number, boolean>>({});
  const [rejectingScene, setRejecting] = useState<number | null>(null);
  const [exportState, setExportState] = useState<"idle" | "loading" | "done">("idle");
  const timer = useRef<number | null>(null);

  const reset = useCallback(() => {
    if (timer.current) window.clearInterval(timer.current);
    setFile(null); 
    setFileUrl(null); 
    setStage("idle"); 
    setProgress(0); 
    setScored(false);
    setScenes(SCENES);
    setExportState("idle");
    const resetSel = SCENES.reduce<Record<number, string>>((acc, s) => {
      acc[s.id] = s.recs[0].id; return acc;
    }, {});
    setSelections(resetSel);
    setAccepted({});
  }, []);

  const startPipeline = useCallback((f: File) => {
    setFile(f);
    setFileUrl(URL.createObjectURL(f));
    setAmbient(false); // ambience yields to the film
    setStage("scene");
    setProgress(0);
    setScored(false);

    if (timer.current) window.clearInterval(timer.current);
    
    const stages: Stage[] = ["scene", "search", "direction", "composition"];
    const startedAt = performance.now();
    const visualDuration = 18000; // 18s target cinematic visual pacing
    
    // Prepare FormData for the server action
    const formData = new FormData();
    formData.append("file", f);
    formData.append("prompt", prompt);
    
    let pipelineFinished = false;
    let responseScenes: Scene[] | null = null;
    
    processVideoServer({ data: formData })
      .then((res: any) => {
        if (res.success && res.scenes) {
          responseScenes = res.scenes as Scene[];
          pipelineFinished = true;
          console.log("[QadContext] Real pipeline completed successfully!");
        } else {
          pipelineFinished = true;
        }
      })
      .catch((err: any) => {
        console.error("[QadContext] Backend pipeline call failed:", err);
        pipelineFinished = true;
      });

    timer.current = window.setInterval(() => {
      const elapsed = performance.now() - startedAt;
      
      // Let the progress slow down as it approaches 96%
      let t = Math.min(0.96, elapsed / visualDuration);
      
      if (pipelineFinished) {
        // Accelerate completion since processing is finished
        t = 1.0;
      }
      
      setProgress(t);
      
      if (t < 1.0) {
        const idx = Math.min(stages.length - 1, Math.floor(t * stages.length));
        setStage(stages[idx]);
      } else {
        if (timer.current) window.clearInterval(timer.current);
        
        if (responseScenes && responseScenes.length > 0) {
          setScenes(responseScenes);
          
          // Pre-populate selections for the new dynamically generated scenes, ensuring
          // consecutive scenes do not repeat the exact same audio file if alternatives exist.
          const newSel: Record<number, string> = {};
          let lastSourcePath = "";
          responseScenes.forEach((s) => {
            if (s.recs && s.recs.length > 0) {
              const bestFit = s.recs.find((r) => r.source_path !== lastSourcePath) ?? s.recs[0];
              newSel[s.id] = bestFit.id;
              lastSourcePath = bestFit.source_path || "";
            }
          });
          setSelections(newSel);
          setScored(true);
        }
       // Keep the local blob URL for editing
        setStage("done");
      }
    }, 150);
  }, [prompt]);

  const exportVideo = useCallback(async () => {
    if (exportState !== "idle") return;
    setExportState("loading");
    console.log("[QadContext] Exporting video with active selections:", selections);
    
    try {
      const res = await composeVideoServer({ data: selections });
      if (res.success) {
        setExportState("done");
        
        // Direct the player to output_matched.mp4 and turn on the soundtrack!
        const nextUrl = `/output_matched.mp4?t=${Date.now()}`;
        setFileUrl(nextUrl);
        setScored(true);
        
        console.log("[QadContext] Export completed successfully! Playing output_matched.mp4");

        // Trigger dynamic download of output_matched.mp4 in the user's browser
        const a = document.createElement("a");
        a.href = "/output_matched.mp4";
        a.download = "output_matched.mp4";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      } else {
        setExportState("idle");
      }
    } catch (err) {
      console.error("[QadContext] Video composition failed:", err);
      setExportState("idle");
    }
  }, [exportState, selections]);

  const toggleScore = useCallback(() => setScored((s) => !s), []);

  const selectAudio = useCallback((sceneId: number, audioId: string) => {
    setSelections((p) => ({ ...p, [sceneId]: audioId }));
    setAccepted((p) => ({ ...p, [sceneId]: true }));
    setRejecting(null);
  }, []);
  const acceptAudio = useCallback((sceneId: number) => {
    setAccepted((p) => ({ ...p, [sceneId]: true }));
  }, []);
  const rejectAudio = useCallback((sceneId: number) => {
    setAccepted((p) => ({ ...p, [sceneId]: false }));
    setRejecting(sceneId);
  }, []);
  const closeReject = useCallback(() => setRejecting(null), []);

  return (
    <QadCtx.Provider value={{
      file, fileUrl, stage, progress, scored, ambientOn, setAmbient, startPipeline, toggleScore, reset,
      prompt, setPrompt,
      selections, acceptedScenes, selectAudio, acceptAudio, rejectAudio,
      rejectingScene, closeReject,
      scenes, exportState, exportVideo,
    }}>
      {children}
    </QadCtx.Provider>
  );
}

export function useQad() {
  const v = useContext(QadCtx);
  if (!v) throw new Error("useQad must be used inside QadProvider");
  return v;
}