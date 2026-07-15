"""HTTP :class:`~app.ingest.relevance.two_tower_filter.Scorer` client for the
relevance model server.

Scores embedding pairs by POSTing them to a separate model-server service (the
Onyx ``model_server`` pattern) that hosts the trained two-tower network. Keeping
the model in its own service means the backend carries no ML runtime — only
this thin HTTP client and the shared request/response schema.

Raises on any transport or protocol failure; ``TwoTowerFilter`` catches it and
fails open (keeps the candidates), so a slow or down model server degrades to
"keep everything" rather than blocking ingestion.

The ``ScoreRequest`` / ``ScoreResponse`` schema is the wire contract the model
server must implement; both sides share it.
"""
from __future__ import annotations

from collections.abc import Sequence

import requests
from pydantic import BaseModel

# Path on the model server that scores a batch of (doc, page) embedding pairs.
SCORE_PATH = "/score"

# Conservative default: the filter only saves reconcile cost, so a scorer that
# can't answer quickly should fail open fast rather than stall the pipeline.
DEFAULT_TIMEOUT_SECONDS = 5.0


class ScoreRequest(BaseModel):
    """One document vector scored against many candidate-page vectors."""

    doc_vec: list[float]
    page_vecs: list[list[float]]


class ScoreResponse(BaseModel):
    """P(relevant) per page, aligned to ``ScoreRequest.page_vecs``."""

    probs: list[float]


class TwoTowerScorer:
    """The two-tower :class:`Scorer`, backed by an HTTP call to the model server.

    Named for the model it serves, though the wire contract itself is a plain
    vectors-in/probs-out call — the two-tower is what the server hosts.
    """

    def __init__(
        self, base_url: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._url = base_url.rstrip("/") + SCORE_PATH
        self._timeout = timeout_seconds
        # One persistent session: the scorer is hit once per document during an
        # ingestion run, so reusing the connection avoids a per-call TCP/TLS
        # handshake to the same host.
        self._session = requests.Session()

    def score_batch(
        self, doc_vec: Sequence[float], page_vecs: list[Sequence[float]]
    ) -> list[float]:
        payload = ScoreRequest(
            doc_vec=list(doc_vec), page_vecs=[list(v) for v in page_vecs]
        )
        resp = self._session.post(
            self._url, json=payload.model_dump(), timeout=self._timeout
        )
        resp.raise_for_status()
        return ScoreResponse.model_validate(resp.json()).probs
