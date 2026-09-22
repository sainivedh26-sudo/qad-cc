import { useEffect, useRef } from "react";

/** Reactive cinematic waveform — uses real audio when an <audio>/<video> element is provided, otherwise breathes ambiently. */
export function Visualizer({ mediaEl, active = true }: { mediaEl?: HTMLMediaElement | null; active?: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const audioCtx = useRef<AudioContext | null>(null);
  const analyser = useRef<AnalyserNode | null>(null);
  const source = useRef<MediaElementAudioSourceNode | null>(null);
  const data = useRef<Uint8Array | null>(null);
  const wired = useRef(false);

  useEffect(() => {
    if (!mediaEl || wired.current) return;
    try {
      const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
      const ac: AudioContext = new Ctx();
      const an = ac.createAnalyser();
      an.fftSize = 512;
      an.smoothingTimeConstant = 0.85;
      const src = ac.createMediaElementSource(mediaEl);
      src.connect(an);
      an.connect(ac.destination);
      audioCtx.current = ac;
      analyser.current = an;
      source.current = src;
      data.current = new Uint8Array(an.frequencyBinCount);
      wired.current = true;
    } catch { /* element already wired or browser blocked */ }
  }, [mediaEl]);

  useEffect(() => {
    const c = canvas.current; if (!c) return;
    const ctx = c.getContext("2d"); if (!ctx) return;
    let raf = 0; const start = performance.now();
    const draw = (now: number) => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const W = c.clientWidth * dpr, H = c.clientHeight * dpr;
      if (c.width !== W) c.width = W; if (c.height !== H) c.height = H;
      ctx.clearRect(0, 0, W, H);

      const N = 96;
      const values: number[] = new Array(N);
      if (analyser.current && data.current) {
        analyser.current.getByteFrequencyData(data.current as any);
        const bin = Math.floor(data.current.length / N);
        for (let i = 0; i < N; i++) {
          let s = 0; for (let j = 0; j < bin; j++) s += data.current[i * bin + j];
          values[i] = s / bin / 255;
        }
      } else {
        const t = (now - start) / 1000;
        for (let i = 0; i < N; i++) {
          const x = i / N;
          values[i] = (
            Math.sin(t * 1.1 + x * 6) * 0.25 +
            Math.sin(t * 0.6 + x * 14) * 0.18 +
            0.35
          );
        }
      }

      const cx = W / 2, cy = H / 2;
      const step = W / N;
      // upper + lower mirrored, gradient stroke
      const grad = ctx.createLinearGradient(0, 0, W, 0);
      grad.addColorStop(0, "rgba(95,251,241,0.0)");
      grad.addColorStop(0.3, "rgba(95,251,241,0.85)");
      grad.addColorStop(0.6, "rgba(139,92,246,0.85)");
      grad.addColorStop(1, "rgba(139,92,246,0)");

      ctx.lineCap = "round";
      ctx.strokeStyle = grad;

      for (let pass = 0; pass < 3; pass++) {
        ctx.lineWidth = (3 - pass) * dpr;
        ctx.globalAlpha = active ? 0.35 + pass * 0.25 : 0.15;
        ctx.beginPath();
        for (let i = 0; i < N; i++) {
          const v = values[i] * (H * 0.42);
          const x = i * step + step / 2;
          ctx.moveTo(x, cy - v);
          ctx.lineTo(x, cy + v);
        }
        ctx.stroke();
      }

      // particle highlights along curve
      ctx.globalAlpha = active ? 0.9 : 0.4;
      for (let i = 0; i < N; i += 4) {
        const v = values[i] * (H * 0.42);
        const x = i * step + step / 2;
        ctx.fillStyle = i % 8 === 0 ? "rgba(95,251,241,.9)" : "rgba(139,92,246,.7)";
        ctx.beginPath(); ctx.arc(x, cy - v, dpr * 1.2, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.arc(x, cy + v, dpr * 1.2, 0, Math.PI * 2); ctx.fill();
      }

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [active]);

  return (
    <div className="relative h-28 md:h-32 w-full overflow-hidden rounded-2xl metallic-surface">
      <div className="absolute inset-0 pointer-events-none" style={{
        background: "radial-gradient(60% 80% at 50% 50%, color-mix(in oklab, var(--resonance-cyan) 8%, transparent), transparent 70%)"
      }} />
      <canvas ref={canvas} className="absolute inset-0 w-full h-full" />
    </div>
  );
}