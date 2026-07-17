"""Export a trained two-tower bundle to ONNX for in-process serving.

The backend scores relevance in-process with onnxruntime — no torch, no separate
model-server pod. This tool is the torch → ONNX bridge: load a bundle, export
the network to a graph whose output is P(update) per (doc, page) pair, and
verify the ONNX output matches torch before writing it.

    python export.py model.inference.pt model.onnx
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
from torch import nn

from two_tower.bundle import LoadedBundle, load_inference_bundle

log = logging.getLogger(__name__)

# Two-tower output classes: index 1 == "update" (i.e. relevant).
_UPDATE_CLASS_INDEX = 1

# Inputs match what the backend's OnnxScorer feeds: the page (wiki) side and the
# document side, one row per candidate pair.
_INPUT_NAMES = ["wiki", "doc"]
_OUTPUT_NAME = "prob"

_PARITY_ATOL = 1e-5


class _ProbHead(nn.Module):
    """Wrap the classifier so the graph outputs P(update) directly, not logits."""

    def __init__(self, model: nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, wiki_emb: torch.Tensor, doc_emb: torch.Tensor) -> torch.Tensor:
        logits = self.model(wiki_emb, doc_emb)
        return torch.softmax(logits, dim=-1)[:, _UPDATE_CLASS_INDEX]


def export_onnx(bundle_path: Path, out_path: Path) -> None:
    """Export ``bundle_path`` (a .inference.pt) to an ONNX graph at ``out_path``.

    Raises if the ONNX output doesn't match torch within tolerance. The graph
    is written to a temp sibling and renamed into ``out_path`` only after the
    parity check passes, so a failed export never leaves an unverified file at
    the destination (deploy scripts key off the file's existence).
    """
    bundle = load_inference_bundle(bundle_path)
    head = _ProbHead(bundle.model).eval()
    embed_dim = bundle.model.input_proj.in_features // 2  # concat[wiki, doc]

    example = (torch.randn(3, embed_dim), torch.randn(3, embed_dim))
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        torch.onnx.export(
            head,
            example,
            str(tmp_path),
            input_names=_INPUT_NAMES,
            output_names=[_OUTPUT_NAME],
            # Batch (number of candidate pages) varies per request.
            dynamic_axes={name: {0: "batch"} for name in [*_INPUT_NAMES, _OUTPUT_NAME]},
            # Classic TorchScript exporter — the dynamo path pulls in onnxscript and
            # is overkill for this plain MLP.
            dynamo=False,
        )
        _embed_metadata(tmp_path, bundle)
        _assert_parity(head, tmp_path, example)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    tmp_path.replace(out_path)


def _embed_metadata(out_path: Path, bundle: LoadedBundle) -> None:
    """Carry the model's serving config in the ONNX graph itself, so the served
    artifact is self-describing: the calibrated ``cutoff`` (the backend reads it
    as the two-tower threshold) and the ``embedding_model`` its vectors must
    come from. Keeps the threshold with the model instead of hand-configured.
    """
    model = onnx.load(str(out_path))
    props = {"embedding_model": bundle.embedding_model}
    if bundle.cutoff is not None:
        props["cutoff"] = str(bundle.cutoff)
    for key, value in props.items():
        entry = model.metadata_props.add()
        entry.key = key
        entry.value = value
    onnx.save(model, str(out_path))


def _assert_parity(
    head: nn.Module, onnx_path: Path, example: tuple[torch.Tensor, torch.Tensor]
) -> None:
    wiki, doc = example
    with torch.no_grad():
        torch_out = head(wiki, doc).numpy()
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    onnx_out = session.run(None, {"wiki": wiki.numpy(), "doc": doc.numpy()})[0]
    max_diff = float(np.abs(torch_out - onnx_out).max())
    if max_diff > _PARITY_ATOL:
        raise RuntimeError(f"ONNX/torch parity failed: max diff {max_diff} > {_PARITY_ATOL}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="path to a *.inference.pt bundle")
    parser.add_argument("out", type=Path, help="destination *.onnx path")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    export_onnx(args.bundle, args.out)
    log.info("exported %s (parity within %s)", args.out, _PARITY_ATOL)


if __name__ == "__main__":
    main()
