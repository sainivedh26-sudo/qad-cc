import { motion } from "framer-motion";
import { Check } from "lucide-react";
import { useQad } from "./QadContext";
import { MiniWave } from "./CinematicEditor";
import type { Scene, AudioRec } from "./qad-data";

export function SoundtrackPanel({ scene, otherRecs }: { scene: Scene; otherRecs: AudioRec[] }) {
  const { selectAudio } = useQad();
  return (
    <div className="glass-panel rounded-3xl pt-5 px-6 pb-4 flex flex-col justify-between h-full w-full min-w-0">
      <div className="flex items-end justify-between mb-4 gap-3">
        <div className="min-w-0">
          <p className="text-[10px] tracking-cine text-white/40">Scene · {scene.mood}</p>
          <h3 className="font-display text-base md:text-lg mt-1.5 leading-tight">Other Suited Soundtracks</h3>
        </div>
        <span className="text-[10px] tracking-cine text-white/30 font-mono whitespace-nowrap">{otherRecs.length} tracks</span>
      </div>
      
      <div className="flex flex-col gap-2 max-h-[220px] overflow-y-auto pr-1 no-scrollbar flex-1 justify-start">
        {otherRecs.map((r, i) => (
          <motion.button
            key={r.id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: i * 0.04 }}
            onClick={() => selectAudio(scene.id, r.id)}
            className="group text-left rounded-xl py-2.5 px-4 metallic-surface border border-white/5 hover:border-[color:var(--resonance-cyan)]/40 hover:shadow-[0_0_30px_-10px_var(--resonance-cyan)] transition-all duration-300 flex items-center justify-between gap-4 w-full cursor-pointer h-[52px]"
          >
            {/* Left Column: Song Details & Specs */}
            <div className="min-w-0 flex-1">
              <p className="font-display text-[13px] md:text-sm text-white truncate font-medium">{r.title}</p>
              <div className="flex items-center gap-2 mt-0.5 text-[9px] tracking-cine text-white/45 font-mono truncate">
                <span>{r.artist}</span>
                <span>·</span>
                <span>{r.mood}</span>
                <span>·</span>
                <span className="text-[color:var(--resonance-cyan)]">{r.compatibility}% MATCH</span>
              </div>
            </div>

            {/* Right Column: Audio Waves & Action Button */}
            <div className="flex items-center gap-3 shrink-0">
              <MiniWave className="h-3 w-10 md:w-12" active={false} />
              <span className="text-[9px] font-mono text-white/40">{r.duration}</span>
              <span className="size-6 rounded-full grid place-items-center bg-white/5 group-hover:bg-[color:var(--resonance-cyan)] group-hover:text-black transition-all">
                <Check className="size-3" />
              </span>
            </div>
          </motion.button>
        ))}
      </div>
    </div>
  );
}