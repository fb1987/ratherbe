from typing import List, Dict, Any, Tuple
import numpy as np

# --- Cosine helpers ---

def cos(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a); nb = np.linalg.norm(b)
    if na == 0 or nb == 0: return 0.0
    return float(np.dot(a, b) / (na * nb))

def mmr_order(
    emb_vectors: List[np.ndarray],
    pref_vector: np.ndarray,
    k: int = 18,
    lam: float = 0.65
) -> List[int]:
    """
    Return indices in MMR order (maximize similarity to pref, minimize redundancy).
    """
    n = len(emb_vectors)
    cand = set(range(n))
    picked: List[int] = []

    # Precompute sims to pref
    sims_pref = np.array([cos(v, pref_vector) for v in emb_vectors])

    while cand and len(picked) < k:
        best_i, best_score = None, -1e9
        for i in list(cand):
            if not picked:
                rep = 0.0
            else:
                rep = max(cos(emb_vectors[i], emb_vectors[j]) for j in picked)
            score = lam * sims_pref[i] - (1 - lam) * rep
            if score > best_score:
                best_i, best_score = i, score
        picked.append(best_i)  # type: ignore
        cand.remove(best_i)    # type: ignore
    return picked

def centroid(vectors: List[np.ndarray]) -> np.ndarray:
    if not vectors: return np.zeros(1536, dtype=np.float32)
    mat = np.vstack(vectors)
    return np.mean(mat, axis=0)
