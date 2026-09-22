import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CinematicEditor } from "@/components/qad/CinematicEditor";
import { ExportPanel } from "@/components/qad/ExportPanel";
import { useQad } from "@/components/qad/QadContext";

export const Route = createFileRoute("/editor")({
  head: () => ({
    meta: [
      { title: "QAD Studio — AI Cinematic Editor" },
      { name: "description", content: "Direct your film's sonic atmosphere in the QAD AI cinematic editor." },
      { property: "og:title", content: "QAD Studio — Cinematic Editor" },
      { property: "og:description", content: "Where every scene finds its sound." },
    ],
  }),
  component: EditorPage,
});

const LOADER_PHRASES = [
  "Calibrating sonic memory…",
  "Reading the rhythm of your film…",
  "Mapping emotion to atmosphere…",
  "Entering the cutting room…",
];

function EditorPage() {
  const { fileUrl, stage } = useQad();
  const navigate = useNavigate();
  const [phrase, setPhrase] = useState(0);

  useEffect(() => {
    if (!fileUrl) {
      // No film loaded — go back home
      navigate({ to: "/" });
      return;
    }
    const tRot = window.setInterval(() => setPhrase((p) => (p + 1) % LOADER_PHRASES.length), 1000);
    return () => { clearInterval(tRot); };
  }, [fileUrl, navigate]);

  const loading = stage !== "done";

  return (
    <main className="relative bg-[color:var(--void)] text-foreground min-h-screen overflow-x-clip">
      {/* Top nav */}
      <header className="relative z-30 flex items-center justify-between px-6 md:px-12 pt-7">
        <Link to="/" className="flex items-center gap-3 group">
          <span className="size-2 rounded-full bg-[color:var(--resonance-cyan)] shadow-[0_0_14px_var(--resonance-cyan)]" />
          <span className="font-display text-xl tracking-tight">QAD</span>
          <span className="hidden md:inline text-[10px] tracking-cine text-white/30 ml-3 group-hover:text-white/60 transition-colors">← Return to the void</span>
        </Link>
        <span className="text-[10px] tracking-cine text-white/40 font-mono hidden sm:block">STUDIO · CUTTING ROOM</span>
      </header>

      <CinematicEditor />
      <ExportPanel />

      <AnimatePresence>
        {loading && (
          <motion.div
            initial={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.9, ease: [0.2, 0.7, 0, 1] }}
            className="fixed inset-0 z-[100] grid place-items-center bg-[color:var(--void)]"
          >
            <div className="absolute inset-0" style={{
              background: "radial-gradient(60% 50% at 50% 50%, color-mix(in oklab, var(--resonance-cyan) 10%, transparent), transparent 70%)"
            }} />
            <div className="cinema-grain" />
            <div className="relative text-center px-6">
              <motion.div
                animate={{ scale: [1, 1.06, 1], opacity: [0.7, 1, 0.7] }}
                transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
                className="mx-auto size-24 rounded-full"
                style={{
                  background: "radial-gradient(circle, color-mix(in oklab, var(--resonance-cyan) 60%, transparent), transparent 70%)",
                  boxShadow: "0 0 80px color-mix(in oklab, var(--resonance-cyan) 40%, transparent)",
                }}
              />
              <p className="mt-10 text-[10px] tracking-cine text-white/40">Entering the studio</p>
              <AnimatePresence mode="wait">
                <motion.p
                  key={phrase}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.5 }}
                  className="mt-3 font-display text-xl md:text-2xl text-white/85"
                >
                  {LOADER_PHRASES[phrase]}
                </motion.p>
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  );
}