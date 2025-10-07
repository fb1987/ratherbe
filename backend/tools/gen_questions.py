# tools/gen_questions.py
import os, json, time, sys, math, random, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parents[0].parents[0]))
from backend.llm import generate_seed
from backend.utils import normalize_item, to_pair_text, pair_hash

OUT = pathlib.Path("data/generated_raw.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)

RAW_CANDIDATES = int(os.getenv("RAW_CANDIDATES", "20000"))
BATCH = 100

# Several style mixes to diversify prompts
SEEDS = [
    {"players":[{"name":"A","sliders":{"creativity":0.8,"absurdism":0.6,"romance":0.2,"pop_culture":0.3,"spice":0.1,"decade":"2000s"}},
                {"name":"B","sliders":{"creativity":0.6,"absurdism":0.4,"romance":0.2,"pop_culture":0.5,"spice":0.1,"decade":"2010s"}}], "pg13":True},
    {"players":[{"name":"A","sliders":{"creativity":0.4,"absurdism":0.8,"romance":0.1,"pop_culture":0.2,"spice":0.1,"decade":"1990s"}},
                {"name":"B","sliders":{"creativity":0.7,"absurdism":0.7,"romance":0.1,"pop_culture":0.1,"spice":0.1,"decade":"1980s"}}], "pg13":True},
    {"players":[{"name":"A","sliders":{"creativity":0.5,"absurdism":0.3,"romance":0.4,"pop_culture":0.7,"spice":0.1,"decade":"2010s"}},
                {"name":"B","sliders":{"creativity":0.5,"absurdism":0.3,"romance":0.2,"pop_culture":0.8,"spice":0.1,"decade":"2000s"}}], "pg13":True},
]

def prep(raw):
    x = normalize_item((raw.get("x") or "").strip())
    y = normalize_item((raw.get("y") or "").strip())
    if not x or not y or x == y:
        return None
    return {
        "x": x, "y": y,
        "text": to_pair_text(x, y),
        "pairHash": pair_hash(x, y),
        "tags": raw.get("tags", [])[:6],
        "theme": raw.get("theme")
    }

seen = set()
count = 0
with OUT.open("w", encoding="utf-8") as f:
    while count < RAW_CANDIDATES:
        body = random.choice(SEEDS)
        raw = generate_seed(json.dumps(body["players"]),  BATCH)
        for r in raw:
            item = prep(r)
            if not item: continue
            h = item["pairHash"]
            if h in seen: continue
            seen.add(h)
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            count += 1
            if count >= RAW_CANDIDATES: break

print(f"Wrote {count} items to {OUT}")
