import os, time
from typing import List, Dict, Any, Tuple
from openai import OpenAI
from .utils import normalize_item, to_pair_text, safe_json_extract
import numpy as np

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1-mini")
EMB_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")

# --- Anchors for cold start preference ---
ANCHOR_LABELS = {
    "mundane": "mundane everyday objects, cozy domestic tone",
    "absurd": "surreal, absurd, Dada energy but still PG-13",
    "romance": "romantic, tender, poetic imagery, gentle",
    "pop": "pop culture references from movies, TV, memes (age-appropriate)",
    "tech": "technology, gadgets, software metaphors",
    "nature": "elements, seasons, animals, landscapes",
}

def embed_texts(texts: List[str]) -> List[np.ndarray]:
    resp = client.embeddings.create(model=EMB_MODEL, input=texts)
    return [np.array(d.embedding, dtype=np.float32) for d in resp.data]  # type: ignore

ANCHOR_VECS = embed_texts(list(ANCHOR_LABELS.values()))
ANCHOR_KEYS = list(ANCHOR_LABELS.keys())

def sliders_to_pref(sliders: List[Dict[str, Any]]) -> np.ndarray:
    # Combine players' sliders into weights over anchors
    # creativity -> nature + tech, absurdism -> absurd, romance -> romance, pop_culture -> pop, spice (kept PG in prompt)
    w = np.zeros(len(ANCHOR_VECS), dtype=np.float32)
    for p in sliders:
        s = p.get("sliders", {})
        w[ANCHOR_KEYS.index("absurd")] += float(s.get("absurdism", 0.5))
        w[ANCHOR_KEYS.index("romance")] += float(s.get("romance", 0.2))
        w[ANCHOR_KEYS.index("pop")] += float(s.get("pop_culture", 0.3))
        w[ANCHOR_KEYS.index("tech")] += 0.5 * float(s.get("creativity", 0.5))
        w[ANCHOR_KEYS.index("nature")] += 0.5 * float(s.get("creativity", 0.5))
        w[ANCHOR_KEYS.index("mundane")] += 1.0 - float(s.get("absurdism", 0.5))
    if w.sum() == 0: w += 1.0
    w = w / w.sum()
    # Weighted centroid of anchors
    M = np.vstack(ANCHOR_VECS)
    return (w @ M).astype(np.float32)

# --- Generation prompts ---

SEED_SYS = (
"You generate short, vivid, PG-13 ‘Would you rather be X or Y?’ questions. "
"Rules: Two concise nouns/concepts only (no clauses or scenarios). Avoid clichés, brands, real people, politics. "
"Mix mundane/poetic/odd. Output STRICT JSON per schema, no prose."
)

SEED_USER_TEMPLATE = """Players and preferences (JSON): {players_json}
Constraints:
- Generate {count} unique questions.
- Distribute across buckets: objects, body, places, elements, jobs, foods, abstractions, animals, tech, time.
- 30% exploration (wildcards), 70% guided by sliders.
- PG-13 only; if 'spice' is high, allow playful but non-explicit wording.
Return JSON:
{{
  "questions": [
    {{"x":"a lighthouse","y":"a lightning bolt","tags":["elements","metaphor"],"theme":"illumination"}}
  ]
}}
"""

FOLLOW_USER_TEMPLATE = """Based on these liked examples and tags:
LIKED_EXAMPLES_JSON: {liked_examples_json}
SESSION_TOP_TAGS: {top_tags}
Generate {count} new unique questions (no near-duplicates of provided). Same schema as seed. Keep 30% exploration.
PG-13 only.
"""

def _call_json(model: str, system: str, user: str) -> Dict[str, Any]:
    # Chat Completions with JSON mode (stable + simple)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        response_format={"type":"json_object"},
        temperature=0.9,
        top_p=0.9,
    )
    content = resp.choices[0].message.content  # type: ignore
    data = safe_json_extract(content or "{}") or {"questions":[]}
    return data

def generate_seed(players_json: str, count: int) -> List[Dict[str, Any]]:
    data = _call_json(LLM_MODEL, SEED_SYS, SEED_USER_TEMPLATE.format(players_json=players_json, count=count))
    return data.get("questions", [])

def generate_follow(liked_examples_json: str, top_tags: List[str], count: int) -> List[Dict[str, Any]]:
    data = _call_json(LLM_MODEL, SEED_SYS, FOLLOW_USER_TEMPLATE.format(
        liked_examples_json=liked_examples_json,
        top_tags=top_tags,
        count=count
    ))
    return data.get("questions", [])
