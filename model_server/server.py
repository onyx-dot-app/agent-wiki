"""FastAPI model server for two-tower relevance scoring.

Loads the trained bundle once at startup from ``MODEL_BUNDLE_PATH`` and serves:

  POST /score   ScoreRequest (embedding vectors) -> ScoreResponse (probabilities)
  GET  /health  readiness

Vectors in, probabilities out — the server does not embed. It runs one model,
loaded from the path the deployment points it at (the backend reaches this over
HTTP; see ``app/ingest/relevance/two_tower_scorer.py``).

``ScoreRequest`` / ``ScoreResponse`` mirror the backend's wire contract; the two
sides own their own copies of these DTOs and agree on the JSON shape.
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from two_tower.scorer import BundleScorer

MODEL_BUNDLE_PATH_ENV = "MODEL_BUNDLE_PATH"


class ScoreRequest(BaseModel):
    doc_vec: list[float]
    page_vecs: list[list[float]]


class ScoreResponse(BaseModel):
    probs: list[float]


# The loaded model, set once at startup. Module-level so the request handlers
# reach it without threading it through every call.
_scorer: BundleScorer | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    global _scorer
    path = os.environ.get(MODEL_BUNDLE_PATH_ENV)
    if not path:
        raise RuntimeError(f"{MODEL_BUNDLE_PATH_ENV} is not set")
    _scorer = BundleScorer.load(path)
    yield
    _scorer = None


app = FastAPI(title="agent-wiki model server", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ready": _scorer is not None}


@app.post("/score")
def score(request: ScoreRequest) -> ScoreResponse:
    if _scorer is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    return ScoreResponse(probs=_scorer.score_batch(request.doc_vec, request.page_vecs))
