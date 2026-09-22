import { useCallback, useRef, useState } from "react";
import { motion } from "framer-motion";
import { useNavigate } from "@tanstack/react-router";
import { useQad } from "./QadContext";

export function UploadZone() {
  const { startPipeline, file, prompt, setPrompt } = useQad();
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const handleFile = useCallback((f: File | null | undefined) => {
    if (!f) return;
    if (!f.type.startsWith("video/")) return;
    startPipeline(f);
    navigate({ to: "/editor" });
  }, [startPipeline, navigate]);

  return (
    <section id="upload" className="relative px-6 md:px-14 py-32">
      <div className="max-w-6xl mx-auto">
        <div className="flex items-end justify-between mb-10">
          <div>
            <p className="text-[10px] tracking-cine text-white/40">02 / Upload</p>
            <h2 className="font-display text-3xl md:text-5xl mt-3">The void awaits your film.</h2>
          </div>
          <span className="hidden md:block text-[10px] tracking-cine text-white/30">ProRes · DNxHR · H.264 · H.265 · 4K / 8K</span>
        </div>

        <div className="grid lg:grid-cols-[1.4fr_1fr] gap-6">
        <motion.label
          htmlFor="qad-file"
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); handleFile(e.dataTransfer.files?.[0]); }}
          className="group relative block aspect-[16/10] lg:aspect-auto lg:min-h-[420px] w-full rounded-3xl cursor-pointer overflow-hidden glass-panel"
          whileHover={{ scale: 1.005 }}
          transition={{ duration: 0.6 }}
        >
          {/* Atmospheric layers */}
          <div className="absolute inset-0" style={{
            background:
              "radial-gradient(60% 60% at 50% 50%, color-mix(in oklab, var(--resonance-cyan) 8%, transparent) 0%, transparent 60%)"
          }} />
          <div className={`absolute inset-0 transition-opacity duration-700 ${over ? "opacity-100" : "opacity-0"}`} style={{
            background: "radial-gradient(40% 40% at 50% 50%, color-mix(in oklab, var(--resonance-cyan) 25%, transparent), transparent 70%)"
          }} />
          {/* particle dots */}
          <Particles active={over} />
          {/* dashed cinematic border */}
          <div className="absolute inset-6 rounded-2xl border border-dashed border-white/15 group-hover:border-white/30 transition-colors" />

          <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-6">
            <div className="size-14 rounded-full grid place-items-center border border-white/15 mb-5"
                 style={{ background: "radial-gradient(circle, color-mix(in oklab, var(--resonance-cyan) 25%, transparent), transparent 70%)" }}>
              <span className="size-1.5 rounded-full bg-white shadow-[0_0_14px_white]" />
            </div>
            <p className="font-display text-lg md:text-2xl text-white/90">Drop your film into the void</p>
            <p className="mt-2 text-[11px] tracking-cine text-white/40">or click to browse</p>
            {file && <p className="mt-4 text-[11px] font-mono text-white/60 truncate max-w-md">{file.name}</p>}
          </div>
        </motion.label>

        {/* Prompt textarea */}
        <div className="relative rounded-3xl glass-panel p-6 md:p-7 flex flex-col min-h-[280px] lg:min-h-[420px]">
          <div className="flex items-center justify-between mb-4">
            <p className="text-[10px] tracking-cine text-white/40">Direction Prompt · Optional</p>
            <span className="text-[10px] tracking-cine text-white/30 font-mono">{prompt.length}/600</span>
          </div>
          <p className="font-display text-lg text-white/90 mb-3">Whisper your intent to the director.</p>
          <div className="relative flex-1 group">
            <div className="pointer-events-none absolute -inset-px rounded-2xl opacity-0 group-focus-within:opacity-100 transition-opacity duration-700"
                 style={{ background: "radial-gradient(60% 80% at 50% 50%, color-mix(in oklab, var(--resonance-cyan) 22%, transparent), transparent 70%)" }} />
            <textarea
              value={prompt}
              maxLength={600}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="e.g. A melancholic descent into memory restrained strings, distant atmospheres, no percussion until the final scene."
              className="relative w-full h-full min-h-[180px] resize-none rounded-2xl bg-black/40 border border-white/10 focus:border-[color:var(--resonance-cyan)]/40 focus:outline-none focus:shadow-[0_0_40px_-10px_var(--resonance-cyan)] transition-all duration-500 p-4 text-sm text-white/85 placeholder:text-white/25 font-light leading-relaxed"
            />
          </div>
          <p className="mt-4 text-[10px] tracking-cine text-white/35">QAD will weave this into the score</p>
        </div>
        </div>

        <input ref={inputRef} id="qad-file" type="file" accept="video/*" hidden
               onChange={(e) => handleFile(e.target.files?.[0])} />
      </div>
    </section>
  );
}

function Particles({ active }: { active: boolean }) {
  return (
    <div className={`absolute inset-0 transition-opacity duration-700 ${active ? "opacity-100" : "opacity-40"}`}>
      {Array.from({ length: 28 }).map((_, i) => {
        const top = (i * 53) % 100, left = (i * 37) % 100, d = 4 + (i % 6);
        return (
          <span
            key={i}
            className="absolute size-[2px] rounded-full bg-white/40"
            style={{
              top: `${top}%`, left: `${left}%`,
              animation: `drift ${d}s ease-in-out ${i * 0.13}s infinite`,
              opacity: 0.25 + ((i % 5) / 10),
              boxShadow: i % 4 === 0 ? "0 0 6px color-mix(in oklab, var(--resonance-cyan) 60%, transparent)" : undefined,
            }}
          />
        );
      })}
    </div>
  );
}