import logging
import os
from typing import List, Optional

import numpy as np

log = logging.getLogger(__name__)

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception:  # package optional at runtime
    SentenceTransformer = None  # type: ignore

_model = None
_interest_vec = None


def _try_import_model():
    """Lazy load the sentence transformer model.

    Returns None if embeddings are disabled or model loading fails.
    This is called automatically during module initialization if EMBEDDINGS_MODEL is set.
    """
    global _model
    try:
        if SentenceTransformer is None:
            raise ImportError("sentence-transformers not available")
        name = os.getenv("EMBEDDINGS_MODEL")
        if not name:
            return None
        log.info(
            "[SEMANTIC] Loading embeddings model: %s (this happens once at boot)", name
        )
        _model = SentenceTransformer(name)
        log.info("[SEMANTIC] ✓ Embeddings model loaded successfully")
        return _model
    except Exception as e:
        log.warning(f"[SEMANTIC] Embeddings disabled: {e}")
        return None


# Preload model at module import time (container boot) if EMBEDDINGS_MODEL is set
# This ensures the model is loaded once and reused across all authentications
if os.getenv("EMBEDDINGS_MODEL"):
    log.info("[SEMANTIC] Preloading embeddings model at container startup...")
    _try_import_model()
else:
    log.info("[SEMANTIC] EMBEDDINGS_MODEL not set, semantic scoring disabled")


def load_interests(interests: List[str]):
    """Load and encode user interests for semantic matching.

    The model is preloaded at container startup, so this function only
    encodes the interest keywords and computes their mean vector.
    """
    global _interest_vec
    # Model should already be loaded at module import time
    if _model is None:
        log.debug("[SEMANTIC] Model not available, skipping interest encoding")
        return
    if interests:
        log.info("[SEMANTIC] Encoding %d interest keywords", len(interests))
        encoded = _model.encode(interests, normalize_embeddings=True)
        encoded_array = np.asarray(encoded)
        if encoded_array.size > 0:
            _interest_vec = encoded_array.mean(axis=0)
            log.info("[SEMANTIC] ✓ Interest vector computed")
        else:
            _interest_vec = None
    else:
        log.debug("[SEMANTIC] No interests provided, semantic scoring inactive")


def score_text(text: str) -> Optional[float]:
    if not text or _model is None or _interest_vec is None:
        return None
    v = _model.encode([text], normalize_embeddings=True)[0]
    # cosine similarity - ensure we get a scalar value
    similarity = np.dot(v, _interest_vec)
    # Handle both scalar and array results from np.dot
    return float(similarity.item() if hasattr(similarity, "item") else similarity)
