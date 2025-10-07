# tools/seed_db.py
import os, json, pathlib, sys, numpy as np
sys.path.append(str(pathlib.Path(__file__).resolve().parents[0].parents[0]))
from backend import db
from backend.llm import embed_texts

CAT = pathlib.Path("data/catalog.jsonl")
rows = [json.loads(l) for l in CAT.read_text(encoding="utf-8").splitlines()]

texts = [r["text"] for r in rows]
print(f"Embedding {len(texts)} items…")
vecs = embed_texts(texts)

bulk = []
for r, v in zip(rows, vecs):
    bulk.append({
        "pairHash": r["pairHash"],
        "x": r["x"], "y": r["y"],
        "text": r["text"],
        "tags": r.get("tags") or [],
        "theme": r.get("theme"),
        "embedding": list(map(float, v)),
        "image_url": r["image_url"],
        "createdAt": float(os.environ.get("NOW", "0")) or __import__("time").time(),
        "source": "pregenerated",
    })

# Upsert by pairHash
ops = []
for doc in bulk:
    ops.append((
        {"pairHash": doc["pairHash"]},
        {"$set": doc},
        True
    ))

# small helper since we don't have db.bulk_upsert:
for f, s, up in ops:
    db.questions.update_one(f, s, upsert=True)

# Helpful indexes:
db.questions.create_index("pairHash", unique=True)
db.questions.create_index([("tags", 1)])
db.questions.create_index([("source", 1)])
print("Seed complete.")
