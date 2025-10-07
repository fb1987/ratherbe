# tools/build_manifest.py
import os, json, pathlib

SEL = pathlib.Path("data/selected.jsonl")
LOG = pathlib.Path("data/render_log.jsonl")
OUT = pathlib.Path("data/catalog.jsonl")

urls = {}
for line in LOG.read_text(encoding="utf-8").splitlines():
    try:
        row = json.loads(line); urls[row["id"]] = row.get("image_url")
    except: pass

out = OUT.open("w", encoding="utf-8")
kept = 0
for line in SEL.read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    h = row["pairHash"]
    url = urls.get(h)
    if not url: continue
    row["image_url"] = url
    out.write(json.dumps(row, ensure_ascii=False) + "\n")
    kept += 1
out.close()
print(f"Wrote {kept} entries → {OUT}")
