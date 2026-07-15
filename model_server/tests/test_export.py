"""Test the torch → ONNX export: the exported graph must match the torch scorer."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
import onnxruntime as ort

from export import export_onnx
from two_tower.scorer import BundleScorer


def test_export_matches_torch(make_bundle: Callable[..., Path], embed_dim: int, tmp_path: Path):
    bundle_path = make_bundle()
    onnx_path = tmp_path / "model.onnx"

    export_onnx(bundle_path, onnx_path)  # raises if parity fails
    assert onnx_path.exists()

    doc = [0.1] * embed_dim
    pages = [[0.2] * embed_dim, [0.5] * embed_dim, [-0.3] * embed_dim]

    # torch reference
    torch_probs = BundleScorer.load(bundle_path).score_batch(doc, pages)

    # onnxruntime: wiki = pages, doc tiled across them
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    onnx_out = np.asarray(
        session.run(
            None,
            {
                "wiki": np.asarray(pages, dtype=np.float32),
                "doc": np.asarray([doc] * len(pages), dtype=np.float32),
            },
        )[0]
    ).ravel()

    assert onnx_out.shape == (len(pages),)
    for t, o in zip(torch_probs, onnx_out):
        assert abs(t - float(o)) < 1e-5
