# backend/images.py — Vertex Imagen 3 to /tmp files (no DB storage)

import os, json, time, threading, queue, traceback, pathlib
from typing import Dict, Any, List, Tuple

# ---- Auth (GCP service account JSON in GCP_SA_KEY) ----
sa_json = os.getenv("GCP_SA_KEY")
if not sa_json:
    raise SystemExit("Missing GCP_SA_KEY env var (paste full service account JSON).")
try:
    sa = json.loads(sa_json)
except Exception:
    raise SystemExit("GCP_SA_KEY must be valid JSON (service account key).")
project_id = sa.get("project_id") or os.getenv("GOOGLE_CLOUD_PROJECT")
if not project_id:
    raise SystemExit("GCP_SA_KEY JSON missing project_id")

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/sa.json"
os.makedirs("/tmp", exist_ok=True)
with open("/tmp/sa.json", "w") as f:
    f.write(sa_json)

# ---- Client ----
from google import genai
from google.genai import types
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
client = genai.Client(vertexai=True, project=project_id, location=LOCATION)

from . import db

IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "imagen-3.0-generate-002")
IMAGE_CONCURRENCY = int(os.environ.get("IMAGE_CONCURRENCY", "1"))
IMAGE_MIN_INTERVAL_MS = int(os.environ.get("IMAGE_MIN_INTERVAL_MS", "300"))
IMAGE_DIR = pathlib.Path(os.environ.get("IMAGE_DIR", "/tmp/rb_images"))
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

_jobs: "queue.Queue[Tuple[Dict[str, Any], str, Dict[str, Any]]]" = queue.Queue()
_workers_started = False

_last_call_ts = 0.0
_rl_lock = threading.Lock()
def _rate_limit():
    global _last_call_ts
    with _rl_lock:
        now = time.monotonic()
        wait = (_last_call_ts + IMAGE_MIN_INTERVAL_MS/1000.0) - now
        if wait > 0: time.sleep(wait)
        _last_call_ts = time.monotonic()

MASTER_STYLE = (
  "A sophisticated single-line-style illustration with a witty, whimsical editorial feel, "
  "suited to luxury magazines and well-educated media outlets. Refined, minimal, consistent, "
  "and subtle—supporting the story as a tasteful webpage background. High-contrast linework, "
  "ample negative space, soft paper texture, no text. "
)

def _player_line(p):
    s = p.get("sliders", {})
    return (f"{p.get('name','')}: creativity {s.get('creativity',0.5)}, "
            f"absurdism {s.get('absurdism',0.5)}, romance {s.get('romance',0.2)}, "
            f"pop {s.get('pop_culture',0.3)}, spice {s.get('spice',0.1)}, "
            f"decade {s.get('decade','2000s')}")

def build_prompt(question_text: str, players: List[Dict[str, Any]]) -> str:
    audience = "; ".join(_player_line(p) for p in (players or []))
    return (f"{MASTER_STYLE} Consider the audience: {audience}. "
            f"Subject to illustrate (metaphorically, not literally): '{question_text}'. "
            "Avoid text, logos, faces of real people, and brand marks. Keep it calm and elegant.")

def image_path_for(qid: str) -> pathlib.Path:
    return IMAGE_DIR / f"{qid}.png"

def has_image(qid: str) -> bool:
    p = image_path_for(qid)
    return p.exists() and p.stat().st_size > 0

def enqueue_for_session(session: Dict[str, Any], question_ids: List[str], limit: int = 12):
    if not session: return
    added = 0
    for qid in question_ids:
        if added >= limit: break
        if has_image(qid):  # already on disk
            continue
        q = db.get_question(qid)
        if not q:
            continue
        _jobs.put((session, qid, q))
        added += 1
    print(f"[images] enqueued {added} jobs for session {session.get('_id')}")

def _img_bytes_from_response(resp) -> bytes:
    try:
        gimgs = getattr(resp, "generated_images", None) or []
        if not gimgs: return b""
        img_obj = getattr(gimgs[0], "image", None)
        if not img_obj: return b""
        for attr in ("bytes", "image_bytes", "data"):
            b = getattr(img_obj, attr, None)
            if isinstance(b, (bytes, bytearray)):
                return bytes(b)
        to_bytes = getattr(img_obj, "to_bytes", None)
        if callable(to_bytes):
            return to_bytes()
        from io import BytesIO
        buf = BytesIO()
        try:
            img_obj.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return b""
    except Exception:
        return b""

def _worker_loop(idx: int):
    print(f"[images] worker {idx} started (model={IMAGE_MODEL}, dir={IMAGE_DIR}, loc={LOCATION})")
    while True:
        session, qid, qdoc = _jobs.get()
        try:
            if has_image(qid):
                continue
            q = db.get_question(qid)
            if not q:
                print(f"[images] q {qid} vanished; skipping")
                continue

            prompt = build_prompt(q["text"], session.get("players", []))

            _rate_limit()
            resp = client.models.generate_images(
                model=IMAGE_MODEL,
                prompt=prompt,
                config=types.GenerateImagesConfig(number_of_images=1, output_mime_type="image/png"),
            )

            img_bytes = _img_bytes_from_response(resp)
            if not img_bytes:
                print(f"[images] empty image bytes for q {qid}")
                continue

            out_path = image_path_for(qid)
            with open(out_path, "wb") as f:
                f.write(img_bytes)
            print(f"[images] wrote {out_path}")

        except Exception as e:
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
