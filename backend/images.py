# backend/images.py — Vertex AI (Imagen 3) background generator

import os, json, time, threading, queue, traceback, base64
from typing import Dict, Any, List, Tuple

# ---- Auth bootstrap: service account JSON in GCP_SA_KEY ----
sa_json = os.getenv("GCP_SA_KEY")
if not sa_json:
    raise SystemExit("Missing GCP_SA_KEY env var (paste full service account JSON).")
try:
    sa = json.loads(sa_json)
except Exception:
    raise SystemExit("GCP_SA_KEY must be valid JSON (service account key).")

project_id = sa.get("project_id")
if not project_id:
    raise SystemExit("GCP_SA_KEY JSON missing project_id")

os.makedirs("/tmp", exist_ok=True)
with open("/tmp/sa.json", "w") as f:
    f.write(sa_json)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/sa.json"

# ---- GenAI (Vertex) client ----
from google import genai
from google.genai import types

LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
client = genai.Client(vertexai=True, project=project_id, location=LOCATION)

from . import db

# Model + params
# (Imagen 3 “generate” model id per SDK docs)
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "imagen-3.0-generate-002")
IMAGE_SIZE = os.environ.get("IMAGE_SIZE", "768x768")     # 1024x1024 also fine
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

def _player_line(p):
    s = p.get("sliders", {})
    return (
        f"{p.get('name','')}: creativity {s.get('creativity',0.5)}, "
        f"absurdism {s.get('absurdism',0.5)}, romance {s.get('romance',0.2)}, "
        f"pop {s.get('pop_culture',0.3)}, spice {s.get('spice',0.1)}, "
        f"decade {s.get('decade','2000s')}"
    )

def build_prompt(question_text: str, players: List[Dict[str, Any]]) -> str:
    audience = "; ".join(_player_line(p) for p in (players or []))
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
        if not q or q.get("imageDataUrl"): 
            continue
        _jobs.put((session, qid, q))
        added += 1
    print(f"[images] enqueued {added} jobs for session {session.get('_id')}")

def _img_bytes_from_response(resp) -> bytes:
    """
    google-genai returns response.generated_images[...].image with helpers.
    Try to extract bytes robustly.
    """
    try:
        gimgs = getattr(resp, "generated_images", None) or []
        if not gimgs: 
            return b""
        img_obj = getattr(gimgs[0], "image", None)
        if not img_obj:
            return b""
        # Common attrs on SDK image wrapper
        for attr in ("bytes", "image_bytes", "data"):
            b = getattr(img_obj, attr, None)
            if isinstance(b, (bytes, bytearray)):
                return bytes(b)
        # Some SDK builds expose .to_bytes()
        to_bytes = getattr(img_obj, "to_bytes", None)
        if callable(to_bytes):
            return to_bytes()
        # Fallback: PIL-like .save into buffer (rare)
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
    print(f"[images] worker {idx} started (model={IMAGE_MODEL}, size={IMAGE_SIZE}, loc={LOCATION})")
    while True:
        session, qid, qdoc = _jobs.get()
        try:
            cur = db.get_question(qid)
            if not cur:
                print(f"[images] q {qid} vanished; skipping")
                continue
            if cur.get("imageDataUrl"):
                continue

            prompt = build_prompt(cur["text"], session.get("players", []))

            # Generate 1 image via Imagen 3
            resp = client.models.generate_images(
                model=IMAGE_MODEL,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    output_mime_type="image/png",
                    # include_rai_reason=True,  # optional
                    # You can set safety settings here if needed
                ),
            )

            img_bytes = _img_bytes_from_response(resp)
            if not img_bytes:
                print(f"[images] empty image bytes for q {qid}")
                continue

            data_url = "data:image/png;base64," + base64.b64encode(img_bytes).decode("ascii")
            db.questions.update_one({"_id": db.oid(qid)}, {"$set": {
                "imageDataUrl": data_url,
                "imageAt": time.time(),
                "imageModel": IMAGE_MODEL,
                "imageSize": IMAGE_SIZE,
                "imageProvider": "vertex-ai",
            }})
            print(f"[images] stored image for q {qid}")

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
