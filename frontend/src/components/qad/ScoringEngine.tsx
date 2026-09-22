import { AnimatePresence, motion } from "framer-motion";
import { useMemo } from "react";
import { Orb } from "./Orb";
import { STAGE_META, useQad, type Stage } from "./QadContext";

const ORDER: Exclude<Stage, "idle" | "done">[] = ["scene", "search", "direction", "composition"];

export function ScoringEngine() {
  const { stage, progress } = useQad();

  const phrase = useMemo(() => {
    if (stage === "idle" || stage === "done") return "Awaiting your film.";
    const meta = STAGE_META[stage];
    const idx = Math.floor(((progress * ORDER.length) % 1) * meta.phrases.length);
    return meta.phrases[Math.min(meta.phrases.length - 1, idx)];
  }, [stage, progress]);

  const activeIdx = stage === "idle" ? -1 : stage === "done" ? ORDER.length : ORDER.indexOf(stage as never);
  const pct = Math.round(progress * 100);

  return (
    <section id="scoring" className="relative px-6 md:px-14 py-32">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-end justify-between mb-12">
          <div>
            <p className="text-[10px] tracking-cine text-white/40">03 / Engine</p>
            <h2 className="font-display text-3xl md:text-5xl mt-3">Scoring Engine</h2>
          </div>
          <span className="text-[10px] tracking-cine text-white/30">QAD is understanding your film…</span>
        </div>

        <div className="relative grid lg:grid-cols-[1.1fr_1fr] gap-10 glass-panel rounded-3xl p-8 md:p-12 overflow-hidden">
          {/* Atmospheric layer */}
          <div className="absolute inset-0 pointer-events-none" style={{
            background: "radial-gradient(40% 50% at 30% 50%, color-mix(in oklab, var(--resonance-cyan) 8%, transparent), transparent 60%)"
          }} />
          <div className="cinema-grain" />

          {/* LEFT: orb + phrase */}
          <div className="relative min-h-[360px] flex flex-col items-center justify-center text-center">
            <Orb size={260} intensity={stage === "idle" ? 0.5 : 1.2} />
            <div className="mt-10 h-10">
              <AnimatePresence mode="wait">
                <motion.p
                  key={phrase}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.9, ease: [0.2, 0.7, 0, 1] }}
                  className="font-display text-xl text-white/90"
                >
                  {phrase}
                </motion.p>
              </AnimatePresence>
            </div>
            <div className="mt-10 w-full max-w-sm">
              <div className="flex justify-between text-[10px] tracking-cine text-white/40 mb-2">
                <span>Overall Progress</span>
                <span className="font-mono text-white/70">{pct}%</span>
              </div>
              <div className="h-px w-full bg-white/10 overflow-hidden">
                <motion.div
                  className="h-full"
                  style={{ background: "linear-gradient(90deg, transparent, var(--resonance-cyan), var(--resonance-violet))" }}
                  animate={{ width: `${pct}%` }}
                  transition={{ duration: 0.5, ease: "easeOut" }}
                />
              </div>
            </div>
          </div>

          {/* RIGHT: stage list */}
          <div className="relative flex flex-col justify-center gap-1">
            {ORDER.map((k, i) => {
              const meta = STAGE_META[k];
              const state: "done" | "active" | "pending" =
                i < activeIdx ? "done" : i === activeIdx ? "active" : "pending";
              return (
                <div key={k} className="grid grid-cols-[auto_1fr_auto] items-start gap-5 py-5 border-t border-white/5 first:border-t-0">
                  <span className="font-mono text-xs text-white/30 pt-0.5">{meta.index}</span>
                  <div>
                    <div className="flex items-center gap-3">
                      <h3 className={`font-display text-base tracking-tight transition-colors ${state === "pending" ? "text-white/35" : "text-white"}`}>
                        {meta.label}
                      </h3>
                      {state === "active" && (
                        <span className="size-1.5 rounded-full bg-[color:var(--resonance-cyan)] shadow-[0_0_10px_var(--resonance-cyan)] animate-pulse" />
                      )}
                    </div>
                    <p className={`text-xs mt-1.5 transition-colors ${state === "pending" ? "text-white/25" : "text-white/50"}`}>
                      {meta.phrases[0]}
                    </p>
                  </div>
                  <span className={`font-mono text-[10px] pt-1 ${state === "done" ? "text-white/60" : state === "active" ? "text-[color:var(--resonance-cyan)]" : "text-white/20"}`}>
                    {state === "done" ? "DONE" : state === "active" ? "•••" : "—"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}