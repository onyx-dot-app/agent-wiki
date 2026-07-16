"""Thin ONNX-runtime wrapper: load a graph, run a feed.

Model-agnostic — it runs whatever ONNX graph it's given. It also isolates
onnxruntime's untyped surface in one place, so callers (e.g. the two-tower
scorer) stay strict-clean.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import onnxruntime


class OnnxModel:
    """A loaded ONNX graph, run on CPU."""

    def __init__(self, model_path: str) -> None:
        # onnxruntime is thinly typed; confine the untyped surface to this class.
        self._session: Any = onnxruntime.InferenceSession(  # pyright: ignore[reportUnknownMemberType]
            model_path, providers=["CPUExecutionProvider"]
        )

    def run(self, feed: dict[str, np.ndarray], output: str) -> np.ndarray:
        """Run the graph on ``feed`` and return the named ``output`` tensor."""
        return np.asarray(self._session.run([output], feed)[0])
