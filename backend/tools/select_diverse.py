# tools/select_diverse.py
import os, json, pathlib, sys, numpy as np
sys.path.append(str(pathlib.Path(__file__).resolve().parents[0].parents[0]))
from backend.llm import embed_texts
from backend.rank import mmr_order

RAW = pathlib.Path("data/generated_raw.jsonl")
OUT = pathlib.Path("data/selected.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)

TARGET = int(os.getenv("TARGET_COUNT", "2000"))

rows = [json.loads(l) for l in RAW.read_text(encoding="utf-8").splitlines()]
texts = [r["text"] for r in rows]
print(f"Embedding {len(texts)} items…")
vecs = embed_texts(texts)  # returns list[list[float]]
V = np.array(vecs, dtype=np.float32)

# Reference vector: zero (pure novelty) — or use a mixed centroid if you want a bias
pref = np.zeros(V.shape[1], dtype=np.float32)

order = mmr_order(V, pref, k=min(TARGET, len(rows)))
sel = [rows[i] for i in order]

with OUT.open("w", encoding="utf-8") as f:
    for r in sel:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print(f"Selected {len(sel)} diverse items → {OUT}")
