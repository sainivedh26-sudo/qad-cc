import { Pause, Play } from "lucide-react";
import { useQad } from "./QadContext";

export function AmbientControl() {
  const { ambientOn, setAmbient, file } = useQad();
  if (file) return null; // film takes over after upload
  return (
    <button
      onClick={() => setAmbient(!ambientOn)}
      className="group glass-panel rounded-full pl-2 pr-4 py-2 flex items-center gap-3 hover:bg-white/[0.04] transition-colors"
      aria-label={ambientOn ? "Pause ambient score" : "Play ambient score"}
    >
      <span className="size-7 rounded-full grid place-items-center bg-white/5 border border-white/10">
        {ambientOn ? <Pause className="size-3 text-white/90" strokeWidth={1.5} /> : <Play className="size-3 text-white/90 translate-x-[1px]" strokeWidth={1.5} />}
      </span>
      <span className="flex flex-col items-start leading-none">
        <span className="text-[9px] tracking-cine text-white/40">Ambient Score</span>
        <span className="text-[11px] font-display text-white/90 mt-1">Celestial Drift</span>
      </span>
      <span className={`size-1.5 rounded-full transition-all ${ambientOn ? "bg-[color:var(--resonance-cyan)] shadow-[0_0_10px_var(--resonance-cyan)]" : "bg-white/20"}`} />
    </button>
  );
}