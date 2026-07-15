"""The two-tower relevance classifier.

A shallow head over the frozen wiki-page and document embeddings; torch-only.

Forward (dims shown for text-embedding-3-small):

    concat[wiki(1536), doc(1536)]  (3072)
      -> input_proj  Linear(3072 -> input_proj_dim) + ReLU
      -> hidden      Linear(input_proj_dim -> hidden_dim) + ReLU + Dropout
      -> ffn1        Linear(hidden_dim -> ffn1_dim) + ReLU + Dropout
      -> classifier  Linear(ffn1_dim -> 2)   (logits; softmax -> P(update))

Dropout is a no-op at inference (``eval()``). This is the architecture; trained
weights load at runtime from a bundle (``bundle.py``) and are checked against
this class on a strict state-dict load, so a shape mismatch fails fast.
"""
from __future__ import annotations

import torch
from torch import nn


class TwoTowerClassifier(nn.Module):
    def __init__(
        self,
        embed_dim: int,
        input_proj_dim: int,
        hidden_dim: int,
        ffn1_dim: int,
        *,
        num_classes: int = 2,
        dropout: float = 0.5,
    ) -> None:
        super().__init__()
        self.input_proj = nn.Linear(embed_dim * 2, input_proj_dim)
        self.hidden = nn.Linear(input_proj_dim, hidden_dim)
        self.ffn1 = nn.Linear(hidden_dim, ffn1_dim)
        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(ffn1_dim, num_classes)

    def forward(self, wiki_emb: torch.Tensor, doc_emb: torch.Tensor) -> torch.Tensor:
        x = torch.cat([wiki_emb, doc_emb], dim=-1)
        x = torch.relu(self.input_proj(x))
        x = self.dropout(torch.relu(self.hidden(x)))
        x = self.dropout(torch.relu(self.ffn1(x)))
        return self.classifier(x)
