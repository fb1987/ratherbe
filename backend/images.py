import os, time, threading, queue, traceback
from typing import Dict, Any, List, Tuple
from openai import OpenAI
from . import db

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "gpt-image-1")
IMAGE_SIZE = os.environ.get("IMAGE_SIZE", "768x768")
IMAGE_CONCURRENCY = int(os.environ.get("IMAGE_CONCURRENCY", "2"))

# In-memory job queue
_jobs: "queue.Queue[Tuple[Dict[str, Any], str, Dict[str, Any]]]" = queue.Queue()
_workers_started = False

MASTER_STYLE = (
  "A sophisticated single-line-style illustration with a witty, whimsical editorial feel, "
  "suited to luxury magazines and well-educated media outlets. Refined, minimal, consistent, "
  "and subtle—supporting the story as a tasteful webpage background. High-contrast linework, "
  "ample negative space, soft paper texture, no text."
)

def build_prompt(question_text: str, players: List[Dict[str, Any]]) -> str:
    def player_line(p):
        s = p.get("sliders", {})
        return (
            f"{p.get('name','')}: creativity {s.get('creativity',0.5)}, "
            f"absurdism {s.get('absurdism',0.5)}, romance {s.get('romance',0.2)}, "
            f"pop {s.get('pop_culture',0.3)}, spice {s.get('spice',0.1)}, "
            f"decade {s.get('decade','2000s')}"
        )
    audience = "; ".join(player_line(p) for p in (players or []))
    return (
        f"{MASTER_STYLE} Consider the audience: {audience}. "
        f"Subject to illustrate (metaphorically, not literally): '{question_text}'. "
        "Avoid text, logos, faces of real people, and brand marks. Keep it calm and elegant."
    )

def enqueue_for_session(session: Dict[str, Any], question_ids: List[str], limit: int = 12):
    if not session: return
    added = 0
    for qid in question_ids:
        if added >= limit: break
        q = db.get_question(qid)
        if not q: continue
        if q.get("imageDataUrl"): continue
        _jobs.put((session, qid, q))
        added += 1
    print(f"[images] enqueued {added} jobs for session {session.get('_id')}")

def _worker_loop(idx: int):
    print(f"[images] worker {idx} started (model={IMAGE_MODEL}, size={IMAGE_SIZE})")
    while True:
        session, qid, qdoc = _jobs.get()
        try:
            # Double-check still needs image
            cur = db.get_question(qid)
            if not cur:
                print(f"[images] q {qid} vanished; skipping")
                continue
            if cur.get("imageDataUrl"):
                continue

            prompt = build_prompt(cur["text"], session.get("players", []))

            # Generate image
            rsp = client.images.generate(
                model=IMAGE_MODEL,
                prompt=prompt,
                size=IMAGE_SIZE,
            )

            # Store as data URL
            b64 = rsp.data[0].b64_json  # type: ignore[attr-defined]
            data_url = "data:image/png;base64," + b64
            db.questions.update_one({"_id": db.oid(qid)}, {"$set": {
                "imageDataUrl": data_url,
                "imageAt": time.time(),
                "imageModel": IMAGE_MODEL,
                "imageSize": IMAGE_SIZE,
            }})
            print(f"[images] stored image for q {qid} ({IMAGE_SIZE})")

        except Exception as e:
            # Log loudly so you can see it in Render logs
            print("[images] ERROR generating image for", qid, ":", repr(e))
            traceback.print_exc()
        finally:
            _jobs.task_done()

def start_workers():
    global _workers_started
    if _workers_started: return
    _workers_started = True
    n = max(1, IMAGE_CONCURRENCY)
    for i in range(n):
        t = threading.Thread(target=_worker_loop, args=(i,), daemon=True)
        t.start()
