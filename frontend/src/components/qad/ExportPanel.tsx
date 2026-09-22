import { ArrowRight, Sparkles, Volume2, Film, Award, Loader2, Check } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useQad } from "./QadContext";

const FEATS = [
  { icon: Sparkles, label: "4K Resolution" },
  { icon: Volume2,  label: "Surround Sound" },
  { icon: Film,     label: "Cinematic Quality" },
  { icon: Award,    label: "Ready for Theatre" },
];

export function ExportPanel() {
  const { exportState, exportVideo } = useQad();

  return (
    <section id="export" className="relative px-4 md:px-10 pt-12 pb-32">
      <div className="max-w-[1600px] mx-auto">
        <div className="relative glass-panel rounded-3xl overflow-hidden px-8 py-16 md:py-24 text-center">
          <div className="absolute inset-0 pointer-events-none" style={{
            background: "radial-gradient(60% 70% at 50% 50%, color-mix(in oklab, var(--resonance-cyan) 9%, transparent), transparent 70%)"
          }} />
          <div className="cinema-grain" />

          <p className="text-[10px] tracking-cine text-white/40">07 / Final Cut</p>
          <h2 className="font-display text-4xl md:text-6xl mt-4">Export Final Cut</h2>
          <p className="mt-4 text-white/55 max-w-md mx-auto">Render your AI-directed cinematic masterpiece.</p>

          <div className="mt-12 flex flex-wrap items-center justify-center gap-x-12 gap-y-5">
            {FEATS.map(({ icon: Icon, label }) => (
              <div key={label} className="flex items-center gap-2 text-[10px] tracking-cine text-white/55">
                <Icon className="size-3.5 text-white/40" strokeWidth={1.5} />
                {label}
              </div>
            ))}
          </div>

          <motion.button
            type="button"
            onClick={exportVideo}
            disabled={exportState !== "idle"}
            whileHover={exportState === "idle" ? { scale: 1.02 } : undefined}
            whileTap={exportState === "idle" ? { scale: 0.98 } : undefined}
            className={`metallic-button mt-12 rounded-full pl-8 pr-3 py-3 inline-flex items-center gap-5 transition-all duration-500 ${exportState === "done" ? "ring-1 ring-[color:var(--resonance-cyan)] shadow-[0_0_60px_-10px_var(--resonance-cyan)]" : ""} ${exportState === "loading" ? "opacity-80 cursor-progress" : ""}`}
          >
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={exportState}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.3 }}
                className="text-[12px] tracking-cine text-white"
              >
                {exportState === "idle" && "Export Now"}
                {exportState === "loading" && "Rendering Final Cut…"}
                {exportState === "done" && "Export Complete"}
              </motion.span>
            </AnimatePresence>
            <span className={`size-9 rounded-full grid place-items-center transition-all ${exportState === "done" ? "bg-[color:var(--resonance-cyan)] text-black" : "bg-white text-black"}`}>
              {exportState === "loading" ? <Loader2 className="size-4 animate-spin" strokeWidth={2} />
                : exportState === "done" ? <Check className="size-4" strokeWidth={2.5} />
                : <ArrowRight className="size-4" strokeWidth={2} />}
            </span>
          </motion.button>
        </div>

        <footer className="flex flex-wrap justify-between items-center gap-4 mt-10 px-4 text-[10px] tracking-cine text-white/30">
          <span>© QAD Studio · MMXXVI</span>
          <span>Built for the void.</span>
          <span>Resonance · v0.1</span>
        </footer>
      </div>
    </section>
  );
}