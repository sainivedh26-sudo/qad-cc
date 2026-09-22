import { useEffect, useRef } from "react";

/** Living cinematic orb — slow breathing, soft cursor deflection, conscious feel. */
export function Orb({ size = 220, intensity = 1 }: { size?: number; intensity?: number }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current; if (!el) return;
    let raf = 0; let tx = 0, ty = 0, x = 0, y = 0;
    const onMove = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      const dx = e.clientX - cx, dy = e.clientY - cy;
      const d = Math.hypot(dx, dy);
      const pull = Math.max(0, 1 - d / 600);
      tx = (dx / d || 0) * 18 * pull;
      ty = (dy / d || 0) * 18 * pull;
    };
    const tick = () => {
      x += (tx - x) * 0.06; y += (ty - y) * 0.06;
      el.style.transform = `translate3d(${x.toFixed(2)}px, ${y.toFixed(2)}px, 0)`;
      raf = requestAnimationFrame(tick);
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    raf = requestAnimationFrame(tick);
    return () => { window.removeEventListener("pointermove", onMove); cancelAnimationFrame(raf); };
  }, []);

  const s = `${size}px`;
  return (
    <div className="relative pointer-events-none" style={{ width: s, height: s }}>
      {/* outer halo */}
      <div className="absolute inset-[-40%] rounded-full blur-3xl animate-breathe"
           style={{ background: `radial-gradient(circle, color-mix(in oklab, var(--resonance-cyan) ${30 * intensity}%, transparent) 0%, transparent 60%)` }} />
      <div className="absolute inset-[-20%] rounded-full blur-2xl animate-breathe"
           style={{ animationDelay: "-2s", background: `radial-gradient(circle, color-mix(in oklab, var(--resonance-violet) ${24 * intensity}%, transparent) 0%, transparent 60%)` }} />
      {/* core */}
      <div ref={ref} className="absolute inset-0 will-change-transform">
        <div className="absolute inset-0 rounded-full"
             style={{
               background: "radial-gradient(circle at 35% 30%, rgba(255,255,255,.95), rgba(180,220,255,.4) 30%, rgba(40,60,110,.6) 55%, rgba(8,12,28,1) 80%)",
               boxShadow: "inset 0 0 60px rgba(120,180,255,.35), inset 0 0 120px rgba(0,0,0,.7), 0 30px 80px -20px rgba(95,251,241,.25)",
             }} />
        <div className="absolute inset-[6%] rounded-full"
             style={{
               background: "radial-gradient(circle at 60% 70%, color-mix(in oklab, var(--resonance-violet) 35%, transparent), transparent 55%)",
               mixBlendMode: "screen",
             }} />
        {/* ring */}
        <div className="absolute inset-[-8%] rounded-full border border-white/10 animate-drift" />
      </div>
    </div>
  );
}