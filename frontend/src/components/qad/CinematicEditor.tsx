import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Play, Pause, SkipBack, SkipForward, RotateCcw, Maximize2, Check, X, Repeat } from "lucide-react";
import thumbs from "@/assets/thumbs-grid.jpg";
import { useQad } from "./QadContext";
import { Visualizer } from "./Visualizer";
import { SoundtrackPanel } from "./SoundtrackPanel";
import { SCENES } from "./qad-data";

function fmt(t: number) {
  if (!isFinite(t) || t < 0) return "00:00";
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function CinematicEditor() {
  const {
    fileUrl, scored, toggleScore,
    selections, acceptedScenes, acceptAudio, rejectAudio, selectAudio,
    rejectingScene, closeReject, scenes,
  } = useQad();

  const handleReject = () => {
    const video = videoRef.current;
    if (video) {
      video.pause();
      setPlaying(false);
      // Snap playhead to the beginning of the rejected scene
      if (activeScene && activeScene.start_sec !== undefined) {
        video.currentTime = activeScene.start_sec + 0.001;
      }
    }
    rejectAudio(activeScene.id);
  };

  const videoRef = useRef<HTMLVideoElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [dur, setDur] = useState(0);
  const [mediaEl, setMediaEl] = useState<HTMLMediaElement | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [audioEl, setAudioEl] = useState<HTMLAudioElement | null>(null);

  // Initialize a single global HTMLAudioElement instance for this editor session
  useEffect(() => {
    const audio = new Audio();
    audio.crossOrigin = "anonymous";
    setAudioEl(audio);
    audioRef.current = audio;
    return () => {
      audio.pause();
      audioRef.current = null;
      setAudioEl(null);
    };
  }, []);

  const activeIdx = (() => {
    if (scenes.length === 0) return 0;
    // Find the scene containing the current playback time using start_sec and end_sec
    const idx = scenes.findIndex(
      (s) => s.start_sec !== undefined && s.end_sec !== undefined && time >= s.start_sec && time <= s.end_sec
    );
    if (idx !== -1) return idx;
    
    // Fallback to linear interpolation
    return dur > 0 ? Math.min(scenes.length - 1, Math.floor((time / dur) * scenes.length)) : 0;
  })();
  const activeScene = scenes[activeIdx];
  const selectedAudioId = selections[activeScene.id];
  const selectedAudio = activeScene.recs.find((r) => r.id === selectedAudioId) ?? activeScene.recs[0];

  useEffect(() => {
    const v = videoRef.current; if (!v) return;
    setMediaEl(v);
    const onTime = () => setTime(v.currentTime);
    const onDur = () => setDur(v.duration || 0);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnded = () => setPlaying(false);
    
    // Lock seeking and seeked events to keep audio frame-perfectly in sync
    const onSeeking = () => {
      if (audioRef.current && activeScene && activeScene.start_sec !== undefined && selectedAudio?.chunk_start_sec !== undefined) {
        const offset = v.currentTime - activeScene.start_sec;
        audioRef.current.currentTime = selectedAudio.chunk_start_sec + Math.max(0, offset);
      }
    };

    v.addEventListener("timeupdate", onTime);
    v.addEventListener("loadedmetadata", onDur);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("ended", onEnded);
    v.addEventListener("seeking", onSeeking);
    v.addEventListener("seeked", onSeeking);
    
    return () => {
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("loadedmetadata", onDur);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("ended", onEnded);
      v.removeEventListener("seeking", onSeeking);
      v.removeEventListener("seeked", onSeeking);
    };
  }, [fileUrl, activeScene, selectedAudio]);
  const accepted = !!acceptedScenes[activeScene.id];
  const otherRecs = activeScene.recs.filter((r) => r.id !== selectedAudio.id);

  // auto-scroll film strip
  useEffect(() => {
    const el = stripRef.current; if (!el) return;
    const child = el.children[activeIdx] as HTMLElement | undefined;
    if (!child) return;
    const offset = child.offsetLeft - el.clientWidth / 2 + child.clientWidth / 2;
    el.scrollTo({ left: offset, behavior: "smooth" });
  }, [activeIdx]);

  const jumpToScene = (i: number) => {
    const v = videoRef.current; if (!v || !dur) return;
    const scene = scenes[i];
    if (scene && scene.start_sec !== undefined) {
      v.currentTime = scene.start_sec + 0.001;
    } else {
      v.currentTime = (i / scenes.length) * dur + 0.001;
    }
  };

  // Synchronize audio track with video playback states
  useEffect(() => {
    const video = videoRef.current;
    const audio = audioRef.current;
    if (!video || !audio) return;

    if (!scored) {
      audio.pause();
      return;
    }

    // Determine current audio source path from match rec
    if (selectedAudio && selectedAudio.source_path) {
      const expectedSrc = `/api/audio?path=${encodeURIComponent(selectedAudio.source_path)}`;
      const currentUrl = new URL(audio.src, window.location.href);
      const expectedUrl = new URL(expectedSrc, window.location.href);
      
      if (currentUrl.href !== expectedUrl.href) {
        audio.src = expectedSrc;
        audio.load();
      }
    } else {
      audio.pause();
      return;
    }

    // Sync play/pause states
    if (playing) {
      if (activeScene && activeScene.start_sec !== undefined && selectedAudio.chunk_start_sec !== undefined) {
        const offset = video.currentTime - activeScene.start_sec;
        const targetTime = selectedAudio.chunk_start_sec + Math.max(0, offset);
        
        // Sync time if out of sync by more than 0.2 seconds
        if (Math.abs(audio.currentTime - targetTime) > 0.2) {
          audio.currentTime = targetTime;
        }
      }
      
      audio.play().catch((err) => {
        console.warn("[CinematicEditor] Audio playback blocked by browser:", err);
      });
    } else {
      audio.pause();
    }
  }, [playing, scored, activeIdx, selectedAudioId, activeScene]);

  // Auto-play the video and audio once the editor loads with real scenes
  useEffect(() => {
    if (fileUrl && scenes.length > 0 && scenes !== SCENES) {
      const v = videoRef.current;
      if (v) {
        // Automatically play when loader ends
        v.play()
          .then(() => setPlaying(true))
          .catch((err) => console.warn("[CinematicEditor] Auto-play on load blocked by browser:", err));
      }
    }
  }, [fileUrl, scenes]);

  const togglePlay = () => {
    const v = videoRef.current; if (!v) return;
    if (v.paused) v.play(); else v.pause();
  };
  const replay = () => { const v = videoRef.current; if (!v) return; v.currentTime = 0; v.play(); };
  const skipBack = () => {
    const v = videoRef.current; if (!v || !dur) return;
    const prevIdx = activeIdx - 1;
    if (prevIdx >= 0 && scenes[prevIdx]) {
      const s = scenes[prevIdx];
      v.currentTime = s.start_sec !== undefined ? s.start_sec + 0.001 : (prevIdx / scenes.length) * dur + 0.001;
    } else {
      v.currentTime = 0;
    }
  };
  const skipFwd = () => {
    const v = videoRef.current; if (!v || !dur) return;
    const nextIdx = activeIdx + 1;
    if (nextIdx < scenes.length && scenes[nextIdx]) {
      const s = scenes[nextIdx];
      v.currentTime = s.start_sec !== undefined ? s.start_sec + 0.001 : (nextIdx / scenes.length) * dur + 0.001;
    } else {
      v.currentTime = Math.max(0, dur - 0.05);
    }
  };
  const seek = (frac: number) => { const v = videoRef.current; if (!v || !dur) return; v.currentTime = frac * dur; };

  return (
    <section id="editor" className="relative px-4 md:px-10 pt-6 md:pt-8 pb-16 md:pb-24">
      <div className="max-w-[1300px] mx-auto">
        <div className="flex flex-wrap items-end justify-between gap-4 mb-8 px-2">
          <div>
            <p className="text-[10px] tracking-cine text-white/40">04 / Editor</p>
            <h2 className="font-display text-3xl md:text-5xl mt-3">AI Cinematic Editor</h2>
          </div>
        </div>

        <div className="glass-panel rounded-3xl overflow-hidden">
          <div className="grid lg:grid-cols-[1fr_380px]">
            {/* MAIN MONITOR */}
            <div className="relative">
              <div className="relative aspect-video bg-black overflow-hidden">
                {fileUrl ? (
                  <video
                    ref={videoRef}
                    src={fileUrl}
                    className="absolute inset-0 w-full h-full object-contain"
                    playsInline
                    muted={!scored}
                    crossOrigin="anonymous"
                    onClick={togglePlay}
                  />
                ) : (
                  <div className="absolute inset-0 grid place-items-center text-center">
                    <p className="text-white/50">No film loaded.</p>
                  </div>
                )}
                <div className="pointer-events-none absolute inset-0" style={{
                  background: "radial-gradient(80% 60% at 50% 100%, color-mix(in oklab, var(--resonance-cyan) 10%, transparent), transparent 70%)"
                }} />
                <div className="absolute top-2.5 left-2.5 text-[10px] tracking-cine font-mono bg-black/40 px-2 py-1 rounded">
                  {scored
                    ? <span className="text-[color:var(--resonance-cyan)]">AI-DIRECTED · LIVE</span>
                    : <span className="text-white/50">SILENT · ORIGINAL</span>}
                </div>
              </div>

              {/* Seek bar */}
              <div className="px-6 pt-3">
                <div
                  className="relative h-1 rounded-full bg-white/10 cursor-pointer group"
                  onClick={(e) => {
                    const r = e.currentTarget.getBoundingClientRect();
                    seek((e.clientX - r.left) / r.width);
                  }}
                >
                  <div className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-[color:var(--resonance-cyan)] to-[color:var(--resonance-violet)]"
                       style={{ width: dur > 0 ? `${(time / dur) * 100}%` : "0%" }} />
                  {/* scene ticks */}
                  {scenes.map((sc, i) => {
                    const pct = sc.start_sec !== undefined && dur > 0 
                      ? (sc.start_sec / dur) * 100 
                      : (i / scenes.length) * 100;
                    return (
                      <span key={i} className="absolute top-1/2 -translate-y-1/2 size-1 rounded-full bg-white/40"
                            style={{ left: `${pct}%` }} />
                    );
                  })}
                </div>
              </div>

              {/* Controls bar */}
              <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-4 font-mono text-[11px] text-white/60">
                <span className="tabular-nums">{fmt(time)}</span>
                <div className="flex items-center gap-2">
                  <ControlBtn onClick={replay} title="Replay"><RotateCcw className="size-4" /></ControlBtn>
                  <ControlBtn onClick={skipBack} title="Previous scene"><SkipBack className="size-4" /></ControlBtn>
                  <button onClick={togglePlay} className="size-11 rounded-full grid place-items-center metallic-button">
                    {playing ? <Pause className="size-4 text-white" /> : <Play className="size-4 text-white translate-x-[1px]" />}
                  </button>
                  <ControlBtn onClick={skipFwd} title="Next scene"><SkipForward className="size-4" /></ControlBtn>
                  <ControlBtn onClick={() => videoRef.current?.requestFullscreen()} title="Fullscreen"><Maximize2 className="size-4" /></ControlBtn>
                </div>
                <span className="tabular-nums">{fmt(dur)}</span>
              </div>
            </div>

            {/* SIDE — active scene + audio decision */}
            <aside className="border-t lg:border-t-0 lg:border-l border-white/5 p-6 md:p-7 flex flex-col gap-5">
              <div>
                <p className="text-[10px] tracking-cine text-white/40">Active Scene · {String(activeScene.id + 1).padStart(2, "0")} / {scenes.length}</p>
                <motion.p
                  key={activeScene.id}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.5 }}
                  className="font-display text-2xl md:text-3xl mt-2 leading-tight"
                >
                  {activeScene.mood}
                </motion.p>
                <p className="text-xs text-white/50 mt-1">{activeScene.label} · energy {Math.round(activeScene.energy * 100)}</p>
              </div>

              <div className="hairline" />

              <div>
                <p className="text-[10px] tracking-cine text-white/40 mb-3">Top-Rated AI Audio</p>
                <motion.div
                  key={selectedAudio.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.5 }}
                  className={`relative rounded-2xl p-4 metallic-surface border transition-all duration-500 ${accepted ? "border-[color:var(--resonance-cyan)]/40 shadow-[0_0_40px_-15px_var(--resonance-cyan)]" : "border-white/10"}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-display text-lg text-white truncate">{selectedAudio.title}</p>
                      <p className="text-[11px] text-white/50 mt-0.5 truncate">{selectedAudio.artist} · {selectedAudio.mood}</p>
                    </div>
                    <span className="text-[10px] font-mono text-[color:var(--resonance-cyan)] whitespace-nowrap">{selectedAudio.compatibility}% MATCH</span>
                  </div>
                  <MiniWave className="mt-4" active={playing && scored} />
                  <div className="mt-4 grid grid-cols-3 gap-2 text-[10px] tracking-cine text-white/40">
                    <Meta label="Scene" value={`#${String(activeScene.id + 1).padStart(2, "0")}`} />
                    <Meta label="Duration" value={selectedAudio.duration} />
                    <Meta label="Confidence" value={`${selectedAudio.compatibility}`} />
                  </div>
                </motion.div>

                <AnimatePresence mode="wait">
                  {accepted ? (
                    <motion.div
                      key="accepted"
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      className="mt-4 flex items-center justify-between gap-2"
                    >
                      <div className="flex items-center gap-2 text-[11px] tracking-cine text-[color:var(--resonance-cyan)]">
                        <Check className="size-3.5" /> Locked into the scene
                      </div>
                      <button
                        onClick={handleReject}
                        className="text-[11px] tracking-cine text-white/60 hover:text-white inline-flex items-center gap-1.5 transition-colors"
                      >
                        <Repeat className="size-3" /> Replace audio
                      </button>
                    </motion.div>
                  ) : (
                    <motion.div
                      key="actions"
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      className="mt-4 grid grid-cols-2 gap-2"
                    >
                      <button
                        onClick={() => acceptAudio(activeScene.id)}
                        className="rounded-full py-2.5 text-[11px] tracking-cine bg-white text-black hover:bg-white/90 transition-colors inline-flex items-center justify-center gap-1.5"
                      >
                        <Check className="size-3.5" /> Accept
                      </button>
                      <button
                        onClick={handleReject}
                        className="rounded-full py-2.5 text-[11px] tracking-cine border border-white/15 text-white/80 hover:border-white/30 hover:text-white transition-colors inline-flex items-center justify-center gap-1.5"
                      >
                        <X className="size-3.5" /> Reject
                      </button>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </aside>
          </div>

          {/* FILM STRIP */}
          <div className="border-t border-white/5 p-4 md:p-5">
            <div ref={stripRef} className="flex gap-2 overflow-x-auto no-scrollbar pb-2 scroll-smooth">
              {scenes.map((sc, i) => {
                const col = i % 4, row = Math.floor(i / 4) % 3;
                const active = i === activeIdx;
                const isAccepted = !!acceptedScenes[sc.id];
                return (
                  <button
                    key={i}
                    onClick={() => jumpToScene(i)}
                    className={`relative flex-none w-[150px] md:w-[180px] aspect-video rounded-md overflow-hidden transition-all duration-500 ${active ? "ring-1 ring-[color:var(--resonance-cyan)] scale-[1.04] shadow-[0_0_30px_-5px_var(--resonance-cyan)]" : "opacity-60 hover:opacity-90"}`}
                  >
                    <div className="absolute inset-0" style={{
                      backgroundImage: `url(${thumbs})`,
                      backgroundSize: "400% 300%",
                      backgroundPosition: `${(col / 3) * 100}% ${(row / 2) * 100}%`,
                    }} />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent" />
                    <div className="absolute top-1.5 left-2 text-[9px] font-mono text-white/70">{String(i + 1).padStart(2, "0")}</div>
                    <div className="absolute bottom-1.5 left-2 right-2">
                      <p className="text-[10px] tracking-cine text-white/90 truncate">{sc.label}</p>
                    </div>
                    <span className={`absolute top-1.5 right-2 size-1.5 rounded-full ${isAccepted ? "bg-[color:var(--resonance-cyan)] shadow-[0_0_8px_var(--resonance-cyan)]" : "bg-white/30"}`} />
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Below: visualizer + soundtracks */}
        <div className="grid lg:grid-cols-[58%_42%] gap-6 mt-6 w-full">
          <div className="glass-panel rounded-3xl pt-5 px-6 pb-4 w-full min-w-0">
            <div className="flex items-end justify-between mb-4">
              <div>
                <p className="text-[10px] tracking-cine text-white/40">06 / Reactive Audio</p>
                <h3 className="font-display text-xl mt-2">Live Resonance</h3>
              </div>
              <p className="text-[10px] tracking-cine text-white/40 font-mono">{scored ? `${selectedAudio.title} · Now Playing` : "Awaiting Score"}</p>
            </div>
            <Visualizer mediaEl={audioEl} active={playing && scored} />
          </div>
          <SoundtrackPanel scene={activeScene} otherRecs={otherRecs} />
        </div>
      </div>

      {/* Reject modal */}
      <AnimatePresence>
        {rejectingScene !== null && (
          <RejectModal
            onClose={closeReject}
            sceneId={rejectingScene}
            currentSelectedAudioId={selections[rejectingScene]}
            onPick={(id) => {
              // 1. Lock selection in provider
              selectAudio(rejectingScene, id);
              
              // 2. Close modal and stay paused at the beginning of that scene until user clicks play
              setTimeout(() => {
                const video = videoRef.current;
                if (video) {
                  const currentScene = scenes[rejectingScene];
                  if (currentScene && currentScene.start_sec !== undefined) {
                    video.currentTime = currentScene.start_sec + 0.001;
                  }
                  video.pause();
                  setPlaying(false);
                }
              }, 100);
            }}
          />
        )}
      </AnimatePresence>
    </section>
  );
}

function RejectModal({
  sceneId,
  onClose,
  onPick,
  currentSelectedAudioId,
}: {
  sceneId: number;
  onClose: () => void;
  onPick: (id: string) => void;
  currentSelectedAudioId?: string;
}) {
  const { scenes, fileUrl } = useQad();
  const scene = scenes[sceneId];
  
  // Set default selection to currently active track if available, else first track
  const [tempSelectedAudioId, setTempSelectedAudioId] = useState(
    currentSelectedAudioId ?? scene.recs[0]?.id
  );
  
  const tempSelectedAudio = scene.recs.find((r) => r.id === tempSelectedAudioId) ?? scene.recs[0];

  const [playingPreview, setPlayingPreview] = useState(false);
  const modalVideoRef = useRef<HTMLVideoElement | null>(null);
  const previewAudioRef = useRef<HTMLAudioElement | null>(null);

  // Initialize modal preview audio element
  useEffect(() => {
    const audio = new Audio();
    audio.crossOrigin = "anonymous";
    previewAudioRef.current = audio;

    return () => {
      audio.pause();
      previewAudioRef.current = null;
    };
  }, []);

  const startSec = scene.start_sec ?? 0;
  const endSec = scene.end_sec ?? 0;
  const chunkStart = tempSelectedAudio.chunk_start_sec ?? 0;
  const sourcePath = tempSelectedAudio.source_path ?? "";

  // Update audio source and reset video player when selected audio changes
  useEffect(() => {
    const audio = previewAudioRef.current;
    const video = modalVideoRef.current;
    if (!audio) return;
    
    audio.pause();
    if (video) {
      video.pause();
      video.currentTime = startSec;
    }
    setPlayingPreview(false);
    
    audio.src = `/api/audio?path=${encodeURIComponent(sourcePath)}`;
    audio.load();

    // Auto-play on track selection changes
    const offset = 0;
    audio.currentTime = chunkStart + offset;
    
    Promise.all([
      video ? video.play() : Promise.resolve(),
      audio.play()
    ])
      .then(() => setPlayingPreview(true))
      .catch((err) => {
        console.warn("[RejectModal] Autoplay on switch blocked:", err);
        setPlayingPreview(false);
      });
  }, [tempSelectedAudioId, tempSelectedAudio, startSec, sourcePath, chunkStart]);

  // Keep video and audio in sync during playback
  useEffect(() => {
    const video = modalVideoRef.current;
    const audio = previewAudioRef.current;
    if (!video || !audio) return;

    const onTimeUpdate = () => {
      // Loop if it exceeds the scene end time
      if (video.currentTime >= endSec) {
        video.currentTime = startSec;
        audio.currentTime = chunkStart;
        if (playingPreview) {
          Promise.all([video.play(), audio.play()]).catch(() => {});
        }
        return;
      }

      // Sync audio time with video offset
      const offset = video.currentTime - startSec;
      const targetAudioTime = chunkStart + Math.max(0, offset);
      if (Math.abs(audio.currentTime - targetAudioTime) > 0.2) {
        audio.currentTime = targetAudioTime;
      }
    };

    video.addEventListener("timeupdate", onTimeUpdate);
    return () => {
      video.removeEventListener("timeupdate", onTimeUpdate);
    };
  }, [startSec, endSec, chunkStart, playingPreview]);

  const togglePlayPreview = () => {
    const video = modalVideoRef.current;
    const audio = previewAudioRef.current;
    if (!video || !audio) return;

    if (playingPreview) {
      video.pause();
      audio.pause();
      setPlayingPreview(false);
    } else {
      // Ensure video is in correct starting boundary
      if (video.currentTime < startSec || video.currentTime >= endSec) {
        video.currentTime = startSec;
      }
      
      const offset = video.currentTime - startSec;
      audio.currentTime = chunkStart + Math.max(0, offset);

      // Play both synchronously
      Promise.all([
        video.play(),
        audio.play()
      ])
        .then(() => setPlayingPreview(true))
        .catch((err) => console.warn("[RejectModal] Synced playback blocked by browser:", err));
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.4 }}
      className="fixed inset-0 z-[80] grid place-items-center p-4 md:p-8"
    >
      {/* Blurred background */}
      <button className="absolute inset-0 bg-black/60 backdrop-blur-xl" onClick={onClose} aria-label="Close" />
      
      <motion.div
        initial={{ opacity: 0, scale: 0.94, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96 }}
        transition={{ duration: 0.55, ease: [0.2, 0.7, 0, 1] }}
        className="relative w-full max-w-4xl glass-panel rounded-3xl p-6 md:p-10 text-left overflow-hidden border border-white/5 shadow-2xl animate-fade-in"
      >
        {/* Header bar */}
        <div className="flex items-start justify-between gap-4 mb-6 md:mb-8">
          <div>
            <p className="text-[10px] tracking-cine text-white/40">Reassign scene · {scene.mood}</p>
            <h3 className="font-display text-2xl md:text-3xl mt-2">Sonic Alternatives</h3>
            <p className="text-xs text-white/50 mt-1">Review and sample different emotions chosen by the AI.</p>
          </div>
          <button onClick={onClose} className="size-9 rounded-full grid place-items-center text-white/60 hover:text-white hover:bg-white/5 transition-colors">
            <X className="size-4" />
          </button>
        </div>

        {/* Two columns */}
        <div className="grid md:grid-cols-[55%_45%] gap-6 md:gap-8 items-stretch w-full">
          
          {/* LEFT SIDE: Options list - Fixed height scrollable container */}
          <div className="flex flex-col gap-3 h-[440px] overflow-y-auto pr-2 no-scrollbar w-full min-w-0">
            <p className="text-[10px] tracking-cine text-white/40 mb-1">Top-Rated Soundtracks</p>
            {scene.recs.map((r) => {
              const active = r.id === tempSelectedAudioId;
              return (
                <button
                  key={r.id}
                  onClick={() => setTempSelectedAudioId(r.id)}
                  className={`group text-left rounded-2xl p-4 metallic-surface border transition-all duration-300 h-[105px] flex flex-col justify-between w-full ${
                    active 
                      ? "border-[color:var(--resonance-cyan)] shadow-[0_0_15px_-5px_var(--resonance-cyan)]" 
                      : "border-white/10 hover:border-white/20"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3 w-full">
                    <div className="min-w-0 flex-1">
                      <p className="font-display text-[15px] text-white truncate">{r.title}</p>
                      <p className="text-[10px] text-white/50 mt-0.5 truncate">{r.artist} · {r.mood}</p>
                    </div>
                    <span className={`text-[10px] font-mono whitespace-nowrap pt-0.5 ${
                      active ? "text-[color:var(--resonance-cyan)] font-medium" : "text-white/40"
                    }`}>{r.compatibility}%</span>
                  </div>
                  <div className="flex items-center justify-between text-[9px] tracking-cine text-white/45 w-full h-4">
                    <span>{r.duration}</span>
                    <span className={`text-[color:var(--resonance-cyan)] font-semibold flex items-center gap-1 transition-opacity duration-300 ${active ? "opacity-100" : "opacity-0 pointer-events-none"}`}>
                      <span className="size-1 bg-[color:var(--resonance-cyan)] rounded-full animate-ping" />
                      SELECTED PREVIEW
                    </span>
                  </div>
                </button>
              );
            })}
          </div>

          {/* RIGHT SIDE: Current selection detail & looped video player - Fixed matching height */}
          <div className="flex flex-col justify-between gap-4 p-5 md:p-6 rounded-2xl glass-panel border border-white/5 bg-white/[0.02] h-[440px] w-full min-w-0">
            <div className="flex justify-between items-start h-10">
              <div>
                <p className="text-[10px] tracking-cine text-white/40">Looped Scene Visual Clip</p>
                <h4 className="font-display text-base text-white mt-1 leading-tight truncate max-w-[200px]">{scene.label}</h4>
              </div>
              <span className="text-[9px] font-mono bg-white/10 text-white/90 px-2 py-0.5 rounded">
                {(endSec - startSec).toFixed(1)}s
              </span>
            </div>

            {/* looper video card */}
            <div 
              className="relative aspect-video rounded-xl overflow-hidden border border-white/10 bg-black group cursor-pointer h-[150px]" 
              onClick={togglePlayPreview}
            >
              <video
                ref={modalVideoRef}
                src={fileUrl || undefined}
                className="w-full h-full object-contain"
                playsInline
                muted
              />
              {/* Play/Pause glassmorphic overlay */}
              <div className={`absolute inset-0 bg-black/30 flex items-center justify-center transition-opacity duration-300 ${playingPreview ? "opacity-0 group-hover:opacity-100" : "opacity-100"}`}>
                <div className="size-12 rounded-full grid place-items-center bg-white text-black shadow-lg hover:scale-105 active:scale-95 transition-all">
                  {playingPreview ? <Pause className="size-4 animate-pulse" /> : <Play className="size-4 translate-x-[0.5px]" />}
                </div>
              </div>
              
              <div className="absolute bottom-2 left-2 right-2 flex items-center justify-between text-[8px] font-mono bg-black/60 backdrop-blur-md px-2 py-0.5 rounded text-white/80">
                <span className="flex items-center gap-1"><span className="size-1 bg-red-500 rounded-full animate-pulse" /> LOOPING CLIP</span>
                <span>{startSec.toFixed(1)}s - {endSec.toFixed(1)}s</span>
              </div>
            </div>

            {tempSelectedAudio ? (
              <div className="flex flex-col gap-2 p-3 rounded-xl border border-white/5 bg-white/[0.01] h-[85px] justify-between">
                <p className="text-[9px] tracking-cine text-white/35 uppercase">Active Soundtrack Overlay</p>
                <div className="min-w-0 font-mono">
                  <p className="font-display text-[13px] text-white truncate">{tempSelectedAudio.title}</p>
                  <p className="text-[10px] text-white/50 mt-0.5 truncate">{tempSelectedAudio.artist} · {tempSelectedAudio.compatibility}% MATCH</p>
                </div>
                <MiniWave active={playingPreview} className="h-5 mt-1" />
              </div>
            ) : (
              <div className="flex items-center justify-center h-[85px] rounded-xl border border-white/5 bg-white/[0.01]">
                <p className="text-xs text-white/40 text-center">No track selected.</p>
              </div>
            )}

            <button
              onClick={() => onPick(tempSelectedAudioId)}
              className="w-full rounded-full py-3 bg-white text-black font-semibold text-xs tracking-cine hover:bg-white/90 active:scale-[0.98] transition-all flex items-center justify-center gap-2 cursor-pointer h-11"
            >
              <Check className="size-3.5" /> Select this audio
            </button>
          </div>

        </div>
      </motion.div>
    </motion.div>
  );
}

function ControlBtn({ children, onClick, title }: { children: React.ReactNode; onClick?: () => void; title?: string }) {
  return (
    <button onClick={onClick} title={title} className="size-9 rounded-full grid place-items-center text-white/70 hover:text-white hover:bg-white/5 transition-colors">
      {children}
    </button>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="tracking-cine text-white/35">{label}</p>
      <p className="font-mono text-white/80 mt-1 text-[11px]">{value}</p>
    </div>
  );
}

export function MiniWave({ className, active = false }: { className?: string; active?: boolean }) {
  const bars = 28;
  return (
    <div className={`flex items-end gap-[2px] h-6 ${className ?? ""}`}>
      {Array.from({ length: bars }).map((_, i) => {
        const base = 20 + ((i * 37) % 70);
        return (
          <span
            key={i}
            className="flex-1 rounded-sm bg-gradient-to-t from-[color:var(--resonance-violet)]/60 to-[color:var(--resonance-cyan)]/90"
            style={{
              height: `${base}%`,
              animation: active ? `wavepulse ${0.9 + (i % 5) * 0.12}s ease-in-out ${i * 0.04}s infinite alternate` : undefined,
              opacity: active ? 1 : 0.55,
            }}
          />
        );
      })}
    </div>
  );
}