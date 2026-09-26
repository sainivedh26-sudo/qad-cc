import heroScene from "@/assets/hero-scene.jpg";
import { motion } from "framer-motion";

export function Hero() {
  return (
    <section className="relative min-h-[100svh] w-full overflow-hidden">
      {/* Cinematic hero plate (used as the "video bleed" placeholder until a real loop is wired) */}
      <img
        src={heroScene}
        alt=""
        aria-hidden
        width={1920}
        height={1080}
        className="absolute inset-0 w-full h-full object-cover opacity-90 scale-[1.02]"
      />
      {/* Atmospheric blends — fade into deep void */}
      <div className="absolute inset-0" style={{
        background:
          "radial-gradient(120% 80% at 50% 35%, transparent 0%, color-mix(in oklab, var(--void) 60%, transparent) 55%, var(--void) 90%)"
      }} />
      <div className="absolute inset-0 bg-gradient-to-t from-black/95 via-black/0 to-black/95" />
      <div className="cinema-grain" />

      {/* Top bar */}
      <div className="relative z-20 flex items-center justify-between px-8 md:px-14 pt-8">
        <span className="font-display text-xl tracking-tight">QAD</span>
        <span className="hidden md:inline text-[10px] tracking-cine text-white/35 ml-3">AI Audio Director</span>
      </div>

      {/* Title */}
      <div className="relative z-10 px-8 md:px-14 pt-14 md:pt-16 max-w-8xl mx-auto">
        <div className="max-w-3xl">
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 1.1, ease: [0.2, 0.7, 0, 1] }}
          >
            <p className="text-[10px] tracking-cine text-white/45 mb-6">Q A D — 01 / Cinematic AI</p>
            <h1 className="font-display text-5xl md:text-7xl lg:text-[5.5rem] leading-[0.95] text-balance">
              AI Audio Director<br />
              <span className="text-white/55">for cinematic</span><br />
              <span className="text-white/55">storytelling.</span>
            </h1>
            <p className="mt-8 max-w-md text-white/55 text-pretty leading-relaxed">
              An emotionally intelligent system that watches your film, reads its rhythm, and composes a fully directed sonic atmosphere frame by frame.
            </p>
            <div className="mt-10 flex items-center gap-4">
              <a href="#upload" className="metallic-button rounded-full px-6 py-3 text-[11px] tracking-cine text-white/90">
                Score a Film
              </a>
            </div>
          </motion.div>
        </div>
      </div>

      {/* Scroll cue */}
      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-20 flex flex-col items-center gap-3 opacity-60">
        <span className="text-[9px] tracking-cine text-white/50">Scroll to Score</span>
        <span className="h-10 w-px bg-gradient-to-b from-white/60 to-transparent" />
      </div>
    </section>
  );
}