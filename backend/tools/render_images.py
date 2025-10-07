# tools/render_images.py
import os, json, time, queue, threading, pathlib, io, sys
from PIL import Image
sys.path.append(str(pathlib.Path(__file__).resolve().parents[0].parents[0]))
from backend.images import MASTER_STYLE  # reuse your style text
from backend.utils import to_pair_text
from google.cloud import storage

# ---- Google GenAI (Vertex) ----
from google import genai
from google.genai import types

sa_json = os.getenv("GCP_SA_KEY")
if not sa_json: raise SystemExit("Missing GCP_SA_KEY")
open("/tmp/sa.json","w").write(sa_json)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/sa.json"

LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "imagen-3.0-generate-002")
client = genai.Client(vertexai=True, location=LOCATION)

# ---- Storage ----
BUCKET_NAME = os.getenv("BUCKET_NAME", "ratherbe-assets")
storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

def blob_exists(path: str) -> bool:
    return bucket.blob(path).exists()

def render_one(item):
    h = item["pairHash"]
    path = f"images/{h}/full.webp"

    # SKIP if already uploaded
    if blob_exists(path):
        blob = bucket.blob(path)
        blob.make_public()
        return blob.public_url

    text = item["text"]
    prompt = build_prompt(text)
    resp = client.models.generate_images(
        model=IMAGE_MODEL,
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            output_mime_type="image/png",
        ),
    )
    ...
    url = upload_bytes(path, webp, "image/webp")
    return url


# ---- Params ----
SRC = pathlib.Path("data/selected.jsonl")
LOG = pathlib.Path("data/render_log.jsonl"); LOG.touch(exist_ok=True)
CONCURRENCY = int(os.getenv("CONCURRENCY", "3"))
WEBP_QUALITY = int(os.getenv("WEBP_QUALITY", "75"))

def build_prompt(text: str) -> str:
    return (f"{MASTER_STYLE} Subject to illustrate, metaphorically: '{text}'. "
            "Avoid text, brands, faces. Calm, elegant background.")

def upload_bytes(path: str, data: bytes, content_type="image/webp"):
    blob = bucket.blob(path)
    blob.cache_control = "public, max-age=31536000, immutable"
    blob.upload_from_string(data, content_type=content_type)
    blob.make_public()
    return blob.public_url

def render_one(item):
    text = item["text"]
    prompt = build_prompt(text)
    resp = client.models.generate_images(
        model=IMAGE_MODEL,
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            output_mime_type="image/png",
        ),
    )
    gimgs = getattr(resp, "generated_images", None) or []
    if not gimgs: return None
    img_obj = getattr(gimgs[0], "image", None)
    # Extract bytes robustly
    data = None
    for attr in ("bytes","image_bytes","data"):
        b = getattr(img_obj, attr, None)
        if isinstance(b, (bytes, bytearray)): data = bytes(b); break
    if data is None and hasattr(img_obj, "to_bytes"):
        data = img_obj.to_bytes()

    if not data: return None
    im = Image.open(io.BytesIO(data)).convert("RGB")
    # Save WebP
    buf = io.BytesIO()
    im.save(buf, format="WEBP", quality=WEBP_QUALITY, method=6)
    webp = buf.getvalue()

    # Upload
    h = item["pairHash"]
    path = f"images/{h}/full.webp"
    url = upload_bytes(path, webp, "image/webp")
    return url

# Worker pool
jobs = queue.Queue()
for line in SRC.read_text(encoding="utf-8").splitlines():
    jobs.put(json.loads(line))

lock = threading.Lock()
last_call = 0.0
MIN_GAP = float(os.getenv("IMAGE_MIN_INTERVAL_S", "0.3"))  # 1/0.3 ≈ 3/s across workers

def rate_limit():
    global last_call
    with lock:
        now = time.monotonic()
        sleep = (last_call + MIN_GAP) - now
        if sleep > 0: time.sleep(sleep)
        last_call = time.monotonic()

def worker(idx):
    while True:
        try:
            item = jobs.get_nowait()
        except queue.Empty:
            return
        try:
            rate_limit()
            url = render_one(item)
            line = {"id": item["pairHash"], "image_url": url}
            with LOG.open("a", encoding="utf-8") as f: f.write(json.dumps(line)+"\n")
            print(f"[{idx}] {item['pairHash']} → {url}")
        except Exception as e:
            print(f"[{idx}] ERROR {item.get('pairHash')}: {e}")
        finally:
            jobs.task_done()

threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(CONCURRENCY)]
[t.start() for t in threads]
jobs.join()
print("Render complete.")
