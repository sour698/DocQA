# ruff: noqa: I001, E402
"""The real FastAPI app with the two heavy dependencies swapped for fakes (no model download, no
LLM key). Used by smoke.py to exercise real HTTP, real PostgreSQL and several uvicorn workers.

The import ORDER matters: tests.conftest must run first because it sets DATABASE_URL, EMBED_DIM...
before the app reads its configuration (do not let an import sorter move it).
"""

from tests.conftest import FakeEmbedder, FakeReranker

from app import llm, main, models

models.get_embedder = lambda: FakeEmbedder()
models.get_reranker = lambda: FakeReranker()
llm.chat = lambda system, user, temperature=0.0: "Employees get 24 days [1]."
llm.chat_stream = lambda *a, **k: iter(["Employees get ", "24 days [1]."])

app = main.app
