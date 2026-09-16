"""
Maps BBC Sound Effects categories → audio_type + chunking parameters.

10 types:
  ambience      sustained environment / atmosphere
  music_bed     background music / underscore
  foley         everyday object / movement sounds
  impact        hits, explosions, crashes, bursts
  stinger       short musical sting / jingle / alert
  transition_fx whoosh, sweep, riser, glitch
  creature      animal, wildlife, monster, bird calls
  vehicle       transport — car, train, plane, boat
  human         voice, laughter, breathing, body sounds
  tension       drone, pulse, dark riser, horror texture
"""

from typing import NamedTuple


class ChunkParams(NamedTuple):
    window_sec: float
    stride_sec: float


CHUNK_PARAMS: dict[str, ChunkParams] = {
    "ambience":      ChunkParams(window_sec=5.0,  stride_sec=2.0),
    "music_bed":     ChunkParams(window_sec=7.0,  stride_sec=2.5),
    "foley":         ChunkParams(window_sec=3.0,  stride_sec=1.0),
    "impact":        ChunkParams(window_sec=2.5,  stride_sec=0.5),
    "stinger":       ChunkParams(window_sec=2.0,  stride_sec=0.5),
    "transition_fx": ChunkParams(window_sec=1.5,  stride_sec=0.25),
    "creature":      ChunkParams(window_sec=3.0,  stride_sec=1.0),
    "vehicle":       ChunkParams(window_sec=5.0,  stride_sec=2.0),
    "human":         ChunkParams(window_sec=4.0,  stride_sec=1.5),
    "tension":       ChunkParams(window_sec=6.0,  stride_sec=2.0),
}

# Rules evaluated top-to-bottom — first match wins
_RULES: list[tuple[list[str], str]] = [

    # ── Stinger (must beat music_bed — short musical hits) ─────────────────
    (["sting", "stinger", "jingle", "fanfare", "ident", "alert tone",
      "notification", "power up", "level up", "score", "hit point",
      "news sting", "comedy sting"], "stinger"),

    # ── Transition FX ──────────────────────────────────────────────────────
    (["whoosh", "swipe", "riser", "sweep", "transition", "flyby",
      "fly-by", "fly by", "zoom", "glitch", "rewind", "fast forward",
      "reverse", "swoosh"], "transition_fx"),

    # ── Tension / dark atmosphere ──────────────────────────────────────────
    (["tension", "suspense", "horror", "scary", "dark drone", "pulse",
      "heartbeat", "thriller", "dread", "eerie", "sinister",
      "creepy", "ominous"], "tension"),

    # ── Music beds ─────────────────────────────────────────────────────────
    (["music", "orchestra", "orchestral", "piano", "guitar", "violin",
      "cello", "bass", "drum", "brass", "string", "choir", "organ",
      "synthesizer", "synth", "electronic music", "folk", "jazz",
      "classical", "ambient music", "tribal music", "beat",
      "melody", "tune", "song", "score", "soundtrack",
      "underscore", "theme"], "music_bed"),

    # ── Creature / animal ──────────────────────────────────────────────────
    (["animal", "wildlife", "creature", "monster", "beast",
      "dog", "cat", "horse", "cow", "sheep", "pig", "goat",
      "lion", "tiger", "bear", "wolf", "fox", "deer",
      "bird", "birdsong", "owl", "crow", "parrot", "eagle",
      "insect", "bee", "cricket", "frog", "fish", "whale",
      "dolphin", "dinosaur", "roar", "bark", "meow", "moo",
      "neigh", "growl", "chirp", "tweet", "hiss"], "creature"),

    # ── Vehicle / transport ────────────────────────────────────────────────
    (["car", "vehicle", "automobile", "truck", "lorry", "van", "taxi",
      "bus", "motorbike", "motorcycle", "scooter", "bicycle",
      "train", "railway", "tram", "underground", "metro",
      "aircraft", "plane", "airplane", "helicopter", "jet",
      "rocket", "spacecraft", "ship", "boat", "ferry",
      "submarine", "engine", "motor", "exhaust", "tyre",
      "wheel", "gearbox", "siren", "horn", "traffic"], "vehicle"),

    # ── Human sounds ──────────────────────────────────────────────────────
    (["voice", "vocal", "speech", "laugh", "laughter", "giggle",
      "cry", "crying", "scream", "shout", "whisper", "breath",
      "breathing", "cough", "sneeze", "snore", "heartbeat",
      "footstep", "walk", "run", "clap", "crowd cheer",
      "crowd boo", "applause", "human", "body", "baby",
      "child", "man", "woman", "person"], "human"),

    # ── Impact ────────────────────────────────────────────────────────────
    (["explosion", "explode", "bomb", "blast", "gunshot", "gun",
      "shot", "rifle", "pistol", "cannon", "missile", "rocket",
      "crash", "smash", "bang", "thud", "slam", "hit",
      "punch", "kick", "break", "shatter", "crack",
      "burst", "pop", "thunder"], "impact"),

    # ── Ambience / atmosphere ─────────────────────────────────────────────
    (["sea", "ocean", "wave", "beach", "rain", "wind", "forest",
      "jungle", "river", "waterfall", "lake", "pond",
      "crowd", "city", "street", "market", "airport", "station",
      "pub", "restaurant", "cafe", "bar", "office", "factory",
      "farm", "countryside", "park", "nature", "outdoor",
      "indoor", "room tone", "roomtone", "walla", "atmos",
      "atmosphere", "ambience", "background", "hum", "drone",
      "buzz", "africa", "india", "asia", "arctic",
      "church", "mosque", "temple", "stadium", "arena",
      "snow", "ice", "desert", "cave", "tunnel",
      "fire", "flames", "burning"], "ambience"),

    # ── Foley (catch-all for object / everyday sounds) ────────────────────
    (["door", "window", "lock", "key", "handle",
      "paper", "book", "magazine", "cloth", "fabric",
      "phone", "computer", "keyboard", "mouse", "camera",
      "glass", "bottle", "cup", "mug", "bowl", "plate",
      "clock", "tick", "bell", "alarm", "buzzer",
      "machine", "mechanical", "tool", "hammer", "drill",
      "water", "splash", "drip", "pour", "liquid",
      "food", "crunch", "chew", "bite", "cooking",
      "sport", "ball", "bat", "racket", "whistle",
      "science fiction", "sci-fi", "robot", "laser",
      "cartoon", "comic", "comedy sound"], "foley"),
]


def classify_category(category: str) -> str:
    """Return audio_type from a BBC category string."""
    lower = category.lower()
    for keywords, audio_type in _RULES:
        if any(kw in lower for kw in keywords):
            return audio_type
    return "foley"  # safe default


def get_chunk_params(audio_type: str) -> ChunkParams:
    return CHUNK_PARAMS.get(audio_type, CHUNK_PARAMS["foley"])
