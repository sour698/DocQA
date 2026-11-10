"""Process-wide singletons for the heavy objects (embedder and reranker).

Two interchangeable backends, selected with ``MODEL_BACKEND``:

* ``onnx`` (default): fastembed + onnxruntime. The SAME models as the PyTorch backend
  (all-MiniLM-L6-v2 for embeddings, ms-marco-MiniLM-L-6-v2 for reranking) without PyTorch, so a
  worker needs a few hundred MB instead of close to 1 GB. This is what lets the API run on a
  512 MB Render instance.
* ``torch``: sentence-transformers (install it yourself; it is not in requirements-api.txt).

Both expose the tiny interface the rest of the app uses:
``embedder.encode(texts, normalize_embeddings=True) -> ndarray`` and
``reranker.predict([(query, passage), ...]) -> ndarray``.

Objects are created lazily and exactly once, so 50 users still means ONE copy of each model in RAM,
and importing the app (e.g. in tests) does not import onnxruntime or torch.
"""

import logging
import threading

import numpy as np

from .config import EMBED_MODEL, MODEL_BACKEND, MODEL_CACHE_DIR, MODEL_THREADS, RERANK_MODEL

log = logging.getLogger(__name__)

_lock = threading.Lock()
_embedder = None
_reranker = None


class _OnnxEmbedder:
    """fastembed's TextEmbedding behind the sentence-transformers ``encode`` signature."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(
            model_name=model_name, cache_dir=MODEL_CACHE_DIR, threads=MODEL_THREADS
        )

    def encode(self, texts, normalize_embeddings: bool = True):
        texts = [str(t) for t in texts]
        if not texts:
            return np.zeros((0, 0), dtype="float32")
        # fastembed yields one vector per text, lazily; a small batch keeps peak memory low.
        vectors = np.asarray(list(self._model.embed(texts, batch_size=16)), dtype="float32")
        if normalize_embeddings:  # fastembed already normalises; this is a harmless safeguard
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            vectors = vectors / np.where(norms == 0, 1.0, norms)
        return vectors


class _OnnxReranker:
    """fastembed's TextCrossEncoder behind the sentence-transformers ``predict`` signature."""

    def __init__(self, model_name: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(
            model_name=model_name, cache_dir=MODEL_CACHE_DIR, threads=MODEL_THREADS
        )

    def predict(self, pairs):
        pairs = list(pairs)
        out = np.zeros(len(pairs), dtype="float32")
        if not pairs:
            return out
        # fastembed scores a list of passages against ONE query; group the pairs by query.
        by_query: dict[str, list[tuple[int, str]]] = {}
        for i, (query, passage) in enumerate(pairs):
            by_query.setdefault(query, []).append((i, passage))
        for query, items in by_query.items():
            scores = list(self._model.rerank(query, [p for _, p in items], batch_size=16))
            for (i, _), score in zip(items, scores, strict=True):
                out[i] = float(score)
        return out


def _build_embedder():
    if MODEL_BACKEND == "torch":
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(EMBED_MODEL)
    if MODEL_BACKEND != "onnx":
        raise RuntimeError(f"MODEL_BACKEND must be 'onnx' or 'torch', not '{MODEL_BACKEND}'.")
    return _OnnxEmbedder(EMBED_MODEL)


def _build_reranker():
    if MODEL_BACKEND == "torch":
        from sentence_transformers import CrossEncoder

        return CrossEncoder(RERANK_MODEL)
    return _OnnxReranker(RERANK_MODEL)


def get_embedder():
    global _embedder
    with _lock:
        if _embedder is None:
            _embedder = _build_embedder()
        return _embedder


def get_reranker():
    global _reranker
    with _lock:
        if _reranker is None:
            _reranker = _build_reranker()
        return _reranker


def warm_up() -> None:
    """Load both models and run one tiny prediction (first use is the slow one). Never raises:
    a failure here only means the first real request pays the loading cost instead."""
    try:
        get_embedder().encode(["warm up"], normalize_embeddings=True)
        get_reranker().predict([("warm up", "warm up")])
        log.info("Models loaded and warm (backend=%s)", MODEL_BACKEND)
    except Exception:  # noqa: BLE001
        log.warning("Model warm-up failed", exc_info=True)


def reset() -> None:
    """Forget the cached objects (used by tests)."""
    global _embedder, _reranker
    with _lock:
        _embedder = _reranker = None
