"""
Maps BBC Sound Effects categories to audio_type and chunking parameters.
Audio types: ambience | impact | transition_fx | music_bed | stinger | foley
"""

from typing import NamedTuple


class ChunkParams(NamedTuple):
    window_sec: float
    stride_sec: float


CHUNK_PARAMS: dict[str, ChunkParams] = {
    "ambience":     ChunkParams(window_sec=5.0, stride_sec=2.0),
    "music_bed":    ChunkParams(window_sec=7.0, stride_sec=2.5),
    "impact":       ChunkParams(window_sec=2.5, stride_sec=0.5),
    "stinger":      ChunkParams(window_sec=2.0, stride_sec=0.5),
    "transition_fx":ChunkParams(window_sec=1.5, stride_sec=0.25),
    "foley":        ChunkParams(window_sec=3.0, stride_sec=1.0),
}

# Keyword matching against category string (lowercased)
_RULES: list[tuple[list[str], str]] = [
    # Ambience / atmosphere
    (["sea", "ocean", "wave", "beach", "rain", "wind", "forest", "jungle",
      "river", "waterfall", "crowd", "traffic", "city", "street", "market",
      "airport", "station", "pub", "restaurant", "cafe", "office", "factory",
      "farm", "countryside", "park", "bird", "insect", "thunder", "storm",
      "ambience", "atmosphere", "background", "roomtone", "walla", "buzz",
      "hum", "drone", "africa", "india", "asia", "europe", "arctic",
      "abbey", "church", "mosque", "temple", "stadium", "arena"], "ambience"),

    # Music beds
    (["music", "orchestra", "piano", "guitar", "drum", "violin", "cello",
      "brass", "string", "choir", "organ", "synthesizer", "electronic",
      "folk", "jazz", "classical", "tribal"], "music_bed"),

    # Impacts / stingers
    (["explosion", "bomb", "gun", "shot", "blast", "crash", "bang",
      "cannon", "rifle", "pistol", "thunder"], "impact"),

    # Transition FX
    (["whoosh", "swipe", "riser", "sweep", "transition", "fly",
      "zoom"], "transition_fx"),

    # Foley / effects
    (["door", "footstep", "walk", "run", "cloth", "paper", "key",
      "phone", "computer", "keyboard", "glass", "bottle", "cup",
      "clock", "tick", "bell", "alarm", "horn", "whistle",
      "engine", "motor", "machine", "vehicle", "car", "truck",
      "train", "aircraft", "plane", "helicopter", "ship", "boat",
      "animal", "dog", "cat", "horse", "bird", "cow", "sheep",
      "fire", "water", "splash", "drip", "pour"], "foley"),
]


def classify_category(category: str) -> str:
    """Return audio_type from a BBC category string."""
    lower = category.lower()
    for keywords, audio_type in _RULES:
        if any(kw in lower for kw in keywords):
            return audio_type
    return "foley"  # default


def get_chunk_params(audio_type: str) -> ChunkParams:
    return CHUNK_PARAMS.get(audio_type, CHUNK_PARAMS["foley"])
