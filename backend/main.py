# backend/main.py — /next waits for on-disk image (/img/{qid}.png); longer wait on first draw; deck=11

import os, json, time, pathlib
from typing import List, Dict, Any
import numpy as np
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .schemas import CreateSessionReq, SessionResp, NextResp, VoteReq, MoreReq, QuestionDTO
from . import db
from .llm import generate_seed, generate_follow, embed_texts, sliders_to_pref
from .rank import centroid, mmr_order
from .utils import normalize_item, to_pair_text, pair_hash
from .images import (
    start_workers,
    enqueue_for_session,
    image_path_for,    # serve from /tmp
    has_image,         # file existence check
)

# ----- Config -----
DECK_TARGET = int(os.environ.get("DECK_TARGET", "11"))
DECK_MIN = int(os.environ.get("DECK_MIN", "3"))
SEED_BATCH_SIZE = DECK_TARGET
TOPUP_BATCH_SIZE = DECK_TARGET

# Image wait controls
IMAGE_WAIT_MS = int(os.environ.get("IMAGE_WAIT_MS", "2500"))
IMAGE_WAIT_STEP_MS = int(os.environ.get("IMAGE_WAIT_STEP_MS", "150"))
FIRST_IMAGE_WAIT_MS = int(os.environ.get("FIRST_IMAGE_WAIT_MS", "6500"))  # extra budget for first card

app = FastAPI(title="RatherBe API", version="0.4.1")

origins = os.environ.get("ALLOW_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if origins == "*" else [origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Static frontend ----
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))  # repo root
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        # avoid stale HTML after deploy
        headers = {"Cache-Control": "no-store"}
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"), headers=headers)

# Serve generated images from /tmp (no DB storage)
@app.get("/img/{qid}.png")
def get_image(qid: str):
    p = image_path_for(qid)
    if not p.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(str(p), media_type="image/png")

# Start async image workers once
start_workers()

# ---- Helpers ----

def _prepare_questions(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for q in raw:
        x = normalize_item(q.get("x","").strip())
        y = normalize_item(q.get("y","").strip())
        if not x or not y or x == y:
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
    # de-dup by pair hash
    seen = set(); uniq = []
    for q in out:
        h = pair_hash(q["x_norm"], q["y_norm"])
        if h in seen: continue
        seen.add(h); q["pairHash"] = h; uniq.append(q)
    return uniq

def _order_and_store(session_id: str, qs: List[Dict[str, Any]], pref_vec: np.ndarray) -> List[str]:
    """Embed pair text + x/y; rank by pair embedding; store all embeddings."""
    if not qs: return []
    pair_texts = [q["text"] for q in qs]
    xy_texts = [q["x"] for q in qs] + [q["y"] for q in qs]
    pair_vecs = embed_texts(pair_texts)
    xy_vecs = embed_texts(xy_texts)
    x_vecs = xy_vecs[:len(qs)]
    y_vecs = xy_vecs[len(qs):]
    for q, pv, xv, yv in zip(qs, pair_vecs, x_vecs, y_vecs):
        q["embedding"] = pv.tolist()
        q["xEmb"] = xv.tolist()
        q["yEmb"] = yv.tolist()
    order = mmr_order([np.array(v) for v in pair_vecs], pref_vec, k=min(len(qs), DECK_TARGET))
    ordered = [qs[i] for i in order]
    ids = db.upsert_questions(session_id, ordered)
    return ids

def _pref_from_choices(session_id: str) -> np.ndarray:
    cur = db.votes.find({"sessionId": session_id}).sort("createdAt", -1).limit(200)
    choice_vecs = []
    for v in cur:
        q = db.get_question(v["questionId"])
        if not q: continue
        key = "xEmb" if v.get("chosen") == "x" else "yEmb" if v.get("chosen") == "y" else None
        if key and q.get(key):
            choice_vecs.append(np.array(q[key], dtype=np.float32))
    if choice_vecs:
        return centroid(choice_vecs)
    return np.zeros(1536, dtype=np.float32)

def _session_pref(session: Dict[str, Any]) -> np.ndarray:
    ch = _pref_from_choices(str(session["_id"]))
    if np.linalg.norm(ch) > 0:
        return ch
    return sliders_to_pref(session.get("players", []))

def _top_tags(session_id: str, n: int = 6) -> List[str]:
    cur = db.votes.find({"sessionId": session_id}).sort("createdAt", -1).limit(200)
    tag_count = {}
    for v in cur:
        q = db.get_question(v["questionId"])
        if not q: continue
        for t in (q.get("tags") or []):
            tag_count[t] = tag_count.get(t, 0) + 1
    return [t for t, _ in sorted(tag_count.items(), key=lambda kv: kv[1], reverse=True)[:n]]

def _liked_examples_json(session_id: str, n: int = 8) -> str:
    cur = db.votes.find({"sessionId": session_id}).sort("createdAt", -1).limit(30)
    items = []
    for v in cur:
        q = db.get_question(v["questionId"])
        if q:
            items.append({"x": q["x"], "y": q["y"], "tags": q.get("tags", []), "theme": q.get("theme")})
            if len(items) >= n: break
    return json.dumps(items, ensure_ascii=False)

def _need_count(session_id: str) -> int:
    rem = db.remaining_count(session_id)
    return max(0, DECK_TARGET - rem)

def _background_topup(session_id: str):
    session = db.get_session(session_id)
    if not session: return
    need = _need_count(session_id)
    if need <= 0: return
    pref = _session_pref(session)
    liked_json = _liked_examples_json(session_id)
    tags = _top_tags(session_id)
    raw = generate_follow(liked_json, tags, need)
    prepped = _prepare_questions(raw)
    ids = _order_and_store(session_id, prepped, pref)
    if ids:
        db.push_deck(session_id, ids)
        enqueue_for_session(db.get_session(session_id), ids)  # queue images for new deck

def _await_image_file(qid: str, wait_ms: int = None) -> bool:
    """Poll for /tmp image to appear (bounded)."""
    if wait_ms is None:
        wait_ms = IMAGE_WAIT_MS
    deadline = time.time() + (wait_ms / 1000.0)
    if has_image(qid):
        return True
    while time.time() < deadline:
        time.sleep(IMAGE_WAIT_STEP_MS / 1000.0)
        if has_image(qid):
            return True
    return False

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
        # mark as not yet served first question
        "firstServed": False,
    }
    sid = db.ins(db.sessions, sdoc)

    players_json = json.dumps(players, ensure_ascii=False)
    raw = generate_seed(players_json, SEED_BATCH_SIZE)
    prepped = _prepare_questions(raw)

    pref = sliders_to_pref(players)
    ids = _order_and_store(sid, prepped, pref)
    if ids:
        db.push_deck(sid, ids)
        enqueue_for_session(db.get_session(sid), ids)  # start generating images
    else:
        bg.add_task(_background_topup, sid)

    return SessionResp(sessionId=sid, createdAt=sdoc["createdAt"], pg13=sdoc["pg13"])

@app.get("/next", response_model=NextResp)
def next_question(sessionId: str, bg: BackgroundTasks):
    s = db.get_session(sessionId)
    if not s:
        raise HTTPException(404, "session not found")

    if db.remaining_count(sessionId) <= 0:
        _background_topup(sessionId)
        if db.remaining_count(sessionId) <= 0:
            raise HTTPException(503, "Deck empty; try again.")

    qid = db.pop_next(sessionId)
    if not qid:
        raise HTTPException(503, "No question available.")

    # First draw gets a longer wait budget
    is_first_draw = not bool(s.get("firstServed"))
    wait_budget = FIRST_IMAGE_WAIT_MS if is_first_draw else IMAGE_WAIT_MS
    _await_image_file(qid, wait_ms=wait_budget)

    q = db.get_question(qid)
    if not q:
        raise HTTPException(500, "Question missing.")

    rem_after = db.remaining_count(sessionId)
    if rem_after < DECK_MIN:
        bg.add_task(_background_topup, sessionId)

    # Point client at the served file if it exists
    img_url = f"/img/{qid}.png" if has_image(qid) else None
    dto = QuestionDTO.model_validate({**q, "_id": str(q["_id"]), "imageDataUrl": img_url})

    # Mark that first card has been served so future draws use normal wait
    try:
        db.sessions.update_one({"_id": db.oid(sessionId)}, {"$set": {"firstServed": True}})
    except Exception:
        pass

    return NextResp(sessionId=sessionId, remaining=rem_after, question=dto)

@app.post("/vote")
def vote(body: VoteReq):
    q = db.get_question(body.questionId)
    if not q:
        raise HTTPException(404, "question not found")
    vdoc = {
        "sessionId": q["sessionId"],
        "questionId": body.questionId,
        "chosen": body.chosen,
        "like": body.like if body.like is not None else True,
        "player": body.player,
        "createdAt": time.time()
    }
    db.record_vote(vdoc)
    return {"ok": True}

@app.post("/batch/more")
def batch_more(sessionId: str, body: MoreReq, bg: BackgroundTasks):
    s = db.get_session(sessionId)
    if not s:
        raise HTTPException(404, "session not found")
    bg.add_task(_background_topup, sessionId)
    return {"queued": True}

# Optional: probe (now file-based)
@app.get("/q/{qid}/image")
def question_image(qid: str):
    exists = has_image(qid)
    return {
        "id": qid,
        "hasImage": exists,
        "imageDataUrl": f"/img/{qid}.png" if exists else None,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=int(os.environ.get("PORT","8000")), reload=True)
