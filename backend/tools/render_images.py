# tools/render_images.py
import os, json, time, queue, threading, pathlib, io, sys, math, random
from typing import Optional
from PIL import Image

# allow imports from backend/
sys.path.append(str(pathlib.Path(__file__).resolve().parents[0].parents[0]))
from backend.images import MASTER_STYLE  # reuse your style text
from google.cloud import storage

# ---- Google GenAI (Vertex) ----
from google import genai
from google.genai import types

# ---- Auth bootstrap ----
sa_json = os.getenv("GCP_SA_KEY")
if not sa_json:
    raise SystemExit("Missing GCP_SA_KEY")
pathlib.Path("/tmp/sa.json").write_text(sa_json)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/sa.json"

# ---- Config ----
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "imagen-3.0-generate-002")
BUCKET_NAME = os.getenv("BUCKET_NAME", "ratherbe-assets")
SRC = pathlib.Path("data/selected.jsonl")
LOG = pathlib.Path("data/render_log.jsonl")
CONCURRENCY = int(os.getenv("CONCURRENCY", "3"))
WEBP_QUALITY = int(os.getenv("WEBP_QUALITY", "75"))
MIN_GAP = float(os.getenv("IMAGE_MIN_INTERVAL_S", "0.3"))  # global rate: ~3/s across workers
MAX_RETRIES = int(os.getenv("IMAGE_MAX_RETRIES", "3"))

# ---- Clients ----
client = genai.Client(vertexai=True, location=LOCATION)
storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

# ---- Helpers ----
def build_prompt(text: str) -> str:
    return (
        f"{MASTER_STYLE} Subject to illustrate, metaphorically: '{text}'. "
        "Avoid text, logos, brands, and real faces. Keep it calm, elegant, suitable as a webpage background."
    )

def blob_exists(path: str) -> bool:
    # Note: exists() makes a metadata call; cheap compared to re-generating images.
    return bucket.blob(path).exists()

def upload_bytes(path: str, data: bytes, content_type="image/webp") -> str:
    blob = bucket.blob(path)
    blob.cache_control = "public, max-age=31536000, immutable"
    blob.upload_from_string(data, content_type=content_type)
    blob.make_public()
    return blob.public_url

def png_to_webp(png_bytes: bytes) -> bytes:
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="WEBP", quality=WEBP_QUALITY, method=6)
    return buf.getvalue()

def extract_first_image_bytes(resp) -> Optional[bytes]:
    gimgs = getattr(resp, "generated_images", None) or []
    if not gimgs:
        return None
    img_obj = getattr(gimgs[0], "image", None)
    if not img_obj:
        return None
    for attr in ("bytes", "image_bytes", "data"):
        b = getattr(img_obj, attr, None)
        if isinstance(b, (bytes, bytearray)):
            return bytes(b)
    tb = getattr(img_obj, "to_bytes", None)
    if callable(tb):
        try:
            return tb()
        except Exception:
            pass
    return None

# Global rate limiter (shared across workers)
_last_call = 0.0
_rate_lock = threading.Lock()
def rate_limit():
    global _last_call
    with _rate_lock:
        now = time.monotonic()
        sleep = (_last_call + MIN_GAP) - now
        if sleep > 0:
            time.sleep(sleep)
        _last_call = time.monotonic()

def render_and_upload(item) -> Optional[str]:
    """
    Returns public URL or None on failure. Skips work if blob already exists.
    Retries IMAGE_MAX_RETRIES times with exponential backoff.
    """
    h = item["pairHash"]
    text = item["text"]
    path = f"images/{h}/full.webp"

    # Skip if already uploaded (idempotent resume)
    if blob_exists(path):
        blob = bucket.blob(path)
        blob.make_public()
        return blob.public_url

    attempt = 0
    while attempt < MAX_RETRIES:
        attempt += 1
        try:
            rate_limit()
            prompt = build_prompt(text)
            resp = client.models.generate_images(
                model=IMAGE_MODEL,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    output_mime_type="image/png",
                ),
            )
            png_bytes = extract_first_image_bytes(resp)
            if not png_bytes:
                raise RuntimeError("No image bytes in response")

            webp = png_to_webp(png_bytes)
            url = upload_bytes(path, webp, "image/webp")
            return url
        except Exception as e:
            wait = min(2 ** attempt, 10) + random.random() * 0.5
            print(f"[warn] {h} attempt {attempt}/{MAX_RETRIES} failed: {e}; retrying in {wait:.1f}s")
            time.sleep(wait)

    return None

# ---- Work queue ----
LOG.touch(exist_ok=True)
done = set()
try:
    # resume awareness: skip ids already logged with image_url
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            j = json.loads(line)
            if j.get("image_url"):
                done.add(j.get("id"))
        except Exception:
            pass
except FileNotFoundError:
    pass

jobs = queue.Queue()
total = 0
for line in SRC.read_text(encoding="utf-8").splitlines():
    item = json.loads(line)
    h = item["pairHash"]
    if h in done:
        continue
    jobs.put(item)
    total += 1

print(f"Queued {total} items for rendering (skipping {len(done)} already done)")

# ---- Workers ----
log_lock = threading.Lock()

def worker(idx: int):
    processed = 0
    while True:
        try:
            item = jobs.get_nowait()
        except queue.Empty:
            break
        h = item["pairHash"]
        try:
            url = render_and_upload(item)
            with log_lock:
                with LOG.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"id": h, "image_url": url}) + "\n")
            print(f"[{idx}] {h} → {url}")
        except Exception as e:
            with log_lock:
                with LOG.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"id": h, "error": str(e)}) + "\n")
            print(f"[{idx}] ERROR {h}: {e}")
        finally:
            processed += 1
            jobs.task_done()
    print(f"[{idx}] done (processed {processed})")

threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(CONCURRENCY)]
[t.start() for t in threads]
jobs.join()
print("Render complete.")
