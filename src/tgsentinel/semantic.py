import logging, os
from typing import Optional, List
import numpy as np

log = logging.getLogger(__name__)

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception:  # package optional at runtime
    SentenceTransformer = None  # type: ignore

_model = None
_interest_vec = None


def _try_import_model():
    global _model
    try:
        if SentenceTransformer is None:
            raise ImportError("sentence-transformers not available")
        name = os.getenv("EMBEDDINGS_MODEL")
        if not name:
            return None
        _model = SentenceTransformer(name)
        return _model
    except Exception as e:
        log.warning(f"Embeddings disabled: {e}")
        return None


def load_interests(interests: List[str]):
    global _interest_vec
    if _model is None:
        _try_import_model()
    if _model:
        encoded = _model.encode(interests, normalize_embeddings=True)
        _interest_vec = np.asarray(encoded).mean(axis=0)


def score_text(text: str) -> Optional[float]:
    if not text or _model is None or _interest_vec is None:
        return None
    v = _model.encode([text], normalize_embeddings=True)[0]
    # cosine similarity
    return float(np.dot(v, _interest_vec))
