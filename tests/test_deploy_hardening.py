"""Regression tests for the Render/Neon deployment fixes."""

import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config, db, ingest, main, models


def test_database_url_paste_mistakes_are_forgiven():
    clean = config._clean_database_url
    url = "postgresql://u:p@host/db?sslmode=require"
    assert clean(f"  {url}  ") == url
    assert clean(f"'{url}'") == url
    assert clean(f"psql '{url}'") == url
    assert clean(f'"{url}"') == url


def test_pool_kwargs_have_keepalives_and_timeouts():
    kw = db.pool_kwargs("postgresql://u:p@host/db")
    assert kw["keepalives"] == 1 and kw["connect_timeout"] > 0
    assert kw["prepare_threshold"] is None


def test_heartbeat_sends_pings_while_work_is_slow_and_passes_events_through():
    def slow():
        time.sleep(0.35)
        yield {"type": "token", "text": "a"}
        yield {"type": "final", "data": {}}

    got = list(main._heartbeat(slow(), interval=0.1))
    types = [e["type"] for e in got]
    assert types[0] == "ping" and types.count("ping") >= 3
    assert [t for t in types if t != "ping"] == ["token", "final"]


def test_heartbeat_reraises_errors_from_the_worker():
    def boom():
        yield {"type": "token", "text": "a"}
        raise ValueError("bad")

    with pytest.raises(ValueError):
        list(main._heartbeat(boom(), interval=0.1))


def test_heartbeat_stops_the_worker_when_the_client_disconnects():
    produced = []

    def endless():
        for i in range(10_000):
            produced.append(i)
            time.sleep(0.01)
            yield {"type": "token", "text": str(i)}

    gen = main._heartbeat(endless(), interval=0.05)
    next(gen), next(gen)
    gen.close()
    time.sleep(0.2)
    n = len(produced)
    time.sleep(0.2)
    assert len(produced) == n  # the producer thread has stopped


def test_pdf_page_limit_is_a_clear_message(monkeypatch, tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "big.pdf"
    c = canvas.Canvas(str(path))
    for i in range(4):
        c.drawString(72, 700, f"page {i} " + "word " * 30)
        c.showPage()
    c.save()
    monkeypatch.setattr(ingest, "MAX_PDF_PAGES", 3)
    with pytest.raises(ingest.IngestLimitError, match="4 pages"):
        ingest.load_pdf(path)


def test_busy_server_answers_503_instead_of_piling_up(monkeypatch):
    monkeypatch.setattr(main, "_ingest_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(main, "INGEST_WAIT_SECONDS", 0.05)
    main._ingest_slots.acquire()  # someone else is indexing
    with pytest.raises(main.HTTPException) as exc:
        main._ingest_bytes("u", "a.txt", b"hello world " * 30)
    assert exc.value.status_code == 503 and exc.value.headers["Retry-After"]
    main._ingest_slots.release()


def test_slot_is_released_after_a_failed_ingest(monkeypatch):
    monkeypatch.setattr(main, "_ingest_slots", threading.BoundedSemaphore(1))
    with pytest.raises(main.HTTPException):
        main._ingest_bytes("u", "a.txt", b"   ")  # no readable text
    assert main._ingest_slots.acquire(timeout=0.1)  # would hang if the slot had leaked


# ---- ONNX adapters: the same interface the rest of the app (and sentence-transformers) uses
class _FakeTextEmbedding:
    def __init__(self, model_name, cache_dir=None, threads=None):
        self.model_name = model_name

    def embed(self, docs, batch_size=256):
        for d in docs:
            yield np.array([len(d), 1.0, 0.0], dtype="float32")


class _FakeCrossEncoder:
    def __init__(self, model_name, cache_dir=None, threads=None):
        pass

    def rerank(self, query, docs, batch_size=64):
        for d in docs:
            yield float(len(set(query.split()) & set(d.split())))


def test_onnx_adapters_match_the_sentence_transformers_interface(monkeypatch):
    import fastembed
    from fastembed.rerank import cross_encoder

    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeTextEmbedding)
    monkeypatch.setattr(cross_encoder, "TextCrossEncoder", _FakeCrossEncoder)
    emb = models._OnnxEmbedder("m").encode(["abc", "de"], normalize_embeddings=True)
    assert emb.shape == (2, 3) and np.allclose(np.linalg.norm(emb, axis=1), 1.0)
    assert models._OnnxEmbedder("m").encode([]).shape[0] == 0
    rr = models._OnnxReranker("m").predict([("a b", "a b c"), ("a b", "x"), ("z", "z")])
    assert list(rr) == [2.0, 0.0, 1.0]  # order preserved across different queries
    assert models._OnnxReranker("m").predict([]).shape == (0,)


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setattr(models, "MODEL_BACKEND", "magic")
    with pytest.raises(RuntimeError, match="MODEL_BACKEND"):
        models._build_embedder()


def test_health_does_not_need_the_database(monkeypatch):
    monkeypatch.setattr(db, "ping", lambda: False)
    c = TestClient(main.app)
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/ready").status_code == 503
