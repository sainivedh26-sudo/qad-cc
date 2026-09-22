export type AudioRec = {
  id: string;
  title: string;
  artist: string;
  mood: string;
  duration: string;
  compatibility: number; // 0..100
  source_path?: string;
  chunk_start_sec?: number;
  chunk_end_sec?: number;
};

export type Scene = {
  id: number;
  label: string;
  mood: string;       // emotional feel title e.g. "Melancholic Drift"
  tone: string;
  energy: number;     // 0..1
  recs: AudioRec[];   // top 5 audio recommendations
  start_sec?: number;
  end_sec?: number;
};

const moods = [
  "Awe & Solitude",
  "Descent into Tension",
  "Quiet Threshold",
  "Suspended Pulse",
  "Melancholic Drift",
  "Cold Awakening",
  "Resonant Build",
  "Hopeful Horizon",
  "Weight of Eclipse",
  "Emotional Bloom",
  "Vast Voyage",
  "Calm Return",
];

const sceneLabels = [
  "Origin", "Descent", "Threshold", "Signal", "Memory", "Awakening",
  "Resonance", "Horizon", "Eclipse", "Bloom", "Voyage", "Return",
];

const audioPool = [
  ["Celestial Rise", "Atmos Library", "Epic · Spatial"],
  ["Deep Horizons", "Nocturne Sound", "Ambient · Wide"],
  ["Pulse of Eternity", "Aria Score", "Cinematic · Tension"],
  ["Silent Gravity", "Void Ensemble", "Minimal · Dark"],
  ["Aurora Drift", "Northern Tones", "Atmospheric · Hopeful"],
  ["Obsidian Tide", "Resonance Lab", "Cold · Vast"],
  ["Glass Cathedral", "Sacred Frame", "Sacred · Spatial"],
  ["Inner Cinder", "Ember Score", "Emotional · Warm"],
  ["Lunar Echoes", "Apollo Strings", "Cinematic · Drift"],
  ["The Long Wait", "Stillframe", "Suspense · Slow"],
  ["Solar Bloom", "Helios Audio", "Triumphant · Build"],
  ["Final Breath", "Coda Works", "Closure · Calm"],
];

function durationFromEnergy(e: number) {
  const secs = 90 + Math.round(e * 180);
  const m = Math.floor(secs / 60), s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export const SCENES: Scene[] = sceneLabels.map((label, i) => {
  const energy = 0.3 + ((i * 13) % 60) / 100;
  // Build 5 recs per scene by rotating the pool
  const recs: AudioRec[] = Array.from({ length: 5 }).map((_, k) => {
    const a = audioPool[(i + k * 3) % audioPool.length];
    return {
      id: `s${i}-a${k}`,
      title: a[0],
      artist: a[1],
      mood: a[2],
      duration: durationFromEnergy((energy + k * 0.07) % 1),
      compatibility: Math.max(62, 98 - k * 7 - ((i * 3) % 5)),
    };
  });
  return {
    id: i,
    label,
    mood: moods[i % moods.length],
    tone: moods[i % moods.length],
    energy,
    recs,
  };
});