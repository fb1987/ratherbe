import os, json, time, random
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import numpy as np

from .schemas import CreateSessionReq, SessionResp, NextResp, VoteReq, MoreReq, QuestionDTO
from . import db
from .llm import generate_seed, generate_follow, embed_texts, sliders_to_pref
from .rank import centroid, mmr_order
from .utils import normalize_item, to_pair_text, pair_hash

SEED_BATCH_SIZE = int(os.environ.get("SEED_BATCH_SIZE", "40"))
TOPUP_BATCH_SIZE = int(os.environ.get("TOPUP_BATCH_SIZE", "40"))
DECK_TARGET = int(os.environ.get("DECK_TARGET", "18"))
DECK_MIN = int(os.environ.get("DECK_MIN", "8"))

app = FastAPI(title="RatherBe API", version="0.1.0")

origins = os.environ.get("ALLOW_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if origins == "*" else [origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Helpers ----

def _prepare_questions(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for q in raw:
        x = normalize_item(q.get("x","").strip())
        y = normalize_item(q.get("y","").strip())
        if not x or not y or x == y:  # invalid
            continue
        text = to_pair_text(x, y)
        out.append({
            "text": text,
            "x": x, "y": y,
            "x_norm": x, "y_norm": y,
            "tags": q.get("tags", [])[:6],
            "theme": q.get("theme"),
            "createdAt": time.time(),
        })
    # dedupe within batch
    seen = set(); uniq = []
    for q in out:
        h = pair_hash(q["x_norm"], q["y_norm"])
        if h in seen: continue
        seen.add(h); q["pairHash"] = h; uniq.append(q)
    return uniq

def _order_and_store(session_id: str, qs: List[Dict[str, Any]], pref_vec: np.ndarray) -> List[str]:
    if not qs: return []
    # Embed once
    texts = [q["text"] for q in qs]
    vecs = embed_texts(texts)
    for q, v in zip(qs, vecs):
        q["embedding"] = v.tolist()
    # MMR ordering
    order = mmr_order(vecs, pref_vec, k=min(len(vecs), DECK_TARGET))
    ordered = [qs[i] for i in order]
    ids = db.upsert_questions(session_id, ordered)
    return ids

def _liked_vectors(session_id: str) -> List[np.ndarray]:
    # Pull last ~50 likes for centroid
    cur = db.votes.find({"sessionId": session_id, "like": True}).sort("createdAt", -1).limit(50)
    qids = [v["questionId"] for v in cur]
    if not qids: return []
    docs = db.questions.find({"_id": {"$in": [db.oid(i) for i in qids]}})
    vecs = []
    for d in docs:
        if "embedding" in d:
            vecs.append(np.array(d["embedding"], dtype=np.float32))
    return vecs

def _session_pref(session: Dict[str, Any]) -> np.ndarray:
    liked = _liked_vectors(str(session["_id"]))
    if liked:
        return centroid(liked)
    return sliders_to_pref(session.get("players", []))

def _top_tags(session_id: str, n: int = 6) -> List[str]:
    cur = db.votes.find({"sessionId": session_id, "like": True}).sort("createdAt", -1).limit(100)
    tag_count = {}
    for v in cur:
        q = db.get_question(v["questionId"])
        if not q: continue
        for t in (q.get("tags") or []):
            tag_count[t] = tag_count.get(t, 0) + 1
    return [t for t, _ in sorted(tag_count.items(), key=lambda kv: kv[1], reverse=True)[:n]]

def _liked_examples_json(session_id: str, n: int = 8) -> str:
    cur = db.votes.find({"sessionId": session_id, "like": True}).sort("createdAt", -1).limit(n)
    items = []
    for v in cur:
        q = db.get_question(v["questionId"])
        if q:
            items.append({"x": q["x"], "y": q["y"], "tags": q.get("tags", []), "theme": q.get("theme")})
    return json.dumps(items, ensure_ascii=False)

def _background_topup(session_id: str):
    session = db.get_session(session_id)
    if not session: return
    pref = _session_pref(session)
    liked_json = _liked_examples_json(session_id)
    tags = _top_tags(session_id)
    raw = generate_follow(liked_json, tags, TOPUP_BATCH_SIZE)
    prepped = _prepare_questions(raw)
    ids = _order_and_store(session_id, prepped, pref)
    if ids:
        db.push_deck(session_id, ids)

# ---- Routes ----

@app.get("/healthz")
def healthz():
    return {"ok": True, "time": time.time()}

@app.post("/session", response_model=SessionResp)
def create_session(body: CreateSessionReq, bg: BackgroundTasks):
    players = body.players
    sdoc = {
        "players": players,
        "pg13": bool(body.pg13),
        "deck": [],
        "createdAt": time.time(),
        "updatedAt": time.time(),
    }
    sid = db.ins(db.sessions, sdoc)

    # Seed generation
    players_json = json.dumps(players, ensure_ascii=False)
    raw = generate_seed(players_json, SEED_BATCH_SIZE)
    prepped = _prepare_questions(raw)

    # Cold-start pref from sliders
    pref = sliders_to_pref(players)
    ids = _order_and_store(sid, prepped, pref)

    if ids:
        db.push_deck(sid, ids)
    else:
        # fallback: immediately top up in background
        bg.add_task(_background_topup, sid)

    return SessionResp(sessionId=sid, createdAt=sdoc["createdAt"], pg13=sdoc["pg13"])

@app.get("/next", response_model=NextResp)
def next_question(sessionId: str, bg: BackgroundTasks):
    s = db.get_session(sessionId)
    if not s: raise HTTPException(404, "session not found")

    # Pop next; if deck low, top-up asynchronously
    rem = db.remaining_count(sessionId)
    if rem <= 0:
        # No items; try to top-up sync once
        _background_topup(sessionId)
        rem = db.remaining_count(sessionId)
        if rem <= 0:
            raise HTTPException(503, "Deck empty; try again in a moment.")

    # pop
    qid = db.pop_next(sessionId)
    if not qid:
        raise HTTPException(503, "No question available.")

    q = db.get_question(qid)
    if not q:
        raise HTTPException(500, "Question missing.")

    # schedule background top-up if low
    rem_after = db.remaining_count(sessionId)
    if rem_after < int(os.environ.get("DECK_MIN", DECK_MIN)):
        bg.add_task(_background_topup, sessionId)

    dto = QuestionDTO.model_validate({**q, "_id": str(q["_id"])})
    return NextResp(sessionId=sessionId, remaining=rem_after, question=dto)

@app.post("/vote")
def vote(body: VoteReq):
    q = db.get_question(body.questionId)
    if not q: raise HTTPException(404, "question not found")
    vdoc = {
        "sessionId": q["sessionId"],
        "questionId": body.questionId,
        "like": bool(body.like),
        "player": body.player,
        "createdAt": time.time()
    }
    db.record_vote(vdoc)
    return {"ok": True}

@app.post("/batch/more")
def batch_more(sessionId: str, body: MoreReq, bg: BackgroundTasks):
    s = db.get_session(sessionId)
    if not s: raise HTTPException(404, "session not found")
    bg.add_task(_background_topup, sessionId)
    return {"queued": True}

# Local dev entry
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT","8000")), reload=True)
