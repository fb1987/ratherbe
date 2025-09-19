import re, json, hashlib
from typing import Tuple, Optional

def normalize_item(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^a\s+|^an\s+|^the\s+", "", s, flags=re.I)  # drop articles
    s = re.sub(r"\s+", " ", s)
    return s.lower()

def to_pair_text(x: str, y: str) -> str:
    return f"Would you rather be {x} or {y}?"

def pair_hash(x_norm: str, y_norm: str) -> str:
    key = "|".join(sorted([x_norm, y_norm]))
    return hashlib.sha1(key.encode()).hexdigest()

def safe_json_extract(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None
