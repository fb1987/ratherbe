import os, base64, time, threading, queue
from typing import Dict, Any, List
from openai import OpenAI
from . import db

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "dall-e-3")
IMAGE_SIZE = os.environ.get("IMAGE_SIZE", "1024x1024")
IMAGE_CONCURRENCY = int(os.environ.get("IMAGE_CONCURRENCY", "2"))

# Simple in-process queue worker for MVP
_jobs = queue.Queue()
_workers_started = False

MASTER_STYLE = (
  "A sophisticated single-line-style illustration with a witty, whimsical editorial feel, "
  "suited to luxury magazines and well-educated media outlets. Refined, minimal, consistent, "
  "and subtle—supporting the story as a tasteful webpage background. High contrast linework, "
  "ample negative space, soft paper texture, no text."
)

def build_prompt(question_text: str, players: List[Dict[str, Any]]) -> str:
    # compress players to a brief flavor
    def player_line(p):
        s = p.get("sliders", {})
        return f"{p.get('name','')}: creativity {s.get('creativity',0.5)}, absurdism {s.get('absurdism',0.5)}, romance {s.get('romance',0.2)}, pop {s.get('pop_culture',0.3)}, spice {s.get('spice',0.1)}, decade {s.get('decade','2000s')}"
    audience = "; ".join(player_line(p) for p in (players or []))
    return (
        f"{MASTER_STYLE} Consider the audience: {audience}. "
        f"Subject to illustrate (metaphorically, not literally): '{question_text}'. "
        "Avoid text, logos, faces of real people, and brand marks. Keep it calm and elegant."
    )

def enqueue_for_session(session: Dict[str, Any], question_ids: List[str]):
    # only enqueue those without images yet (first ~12 is enough)
    to_fetch = []
    for qid in question_ids[:12]:
        q = db.get_question(qid)
        if q and not q.get("imageDataUrl"):
            to_fetch.append((session, qid, q))
    for item in to_fetch:
        _jobs.put(item)

def _worker_loop():
    while True:
        try:
            session, qid, qdoc = _jobs.get()
            if not session: continue
            # re-check if already filled
            cur = db.get_question(qid)
            if not cur or cur.get("imageDataUrl"):
                _jobs.task_done(); continue

            prompt = build_prompt(qdoc["text"], session.get("players", []))
            rsp = client.images.generate(
                model=IMAGE_MODEL,
                prompt=prompt,
                size=IMAGE_SIZE,
                # note: background images -> let it be default background (no transparency)
            )
            b64 = rsp.data[0].b64_json  # type: ignore
            data_url = "data:image/png;base64," + b64
            db.questions.update_one({"_id": db.oid(qid)}, {"$set": {"imageDataUrl": data_url, "imageAt": time.time()}})
        except Exception as e:
            # soft-fail; don't crash thread
            # optional: log to a 'logs' collection
            pass
        finally:
            _jobs.task_done()

def start_workers():
    global _workers_started
    if _workers_started: return
    _workers_started = True
    for _ in range(max(1, IMAGE_CONCURRENCY)):
        t = threading.Thread(target=_worker_loop, daemon=True)
        t.start()
