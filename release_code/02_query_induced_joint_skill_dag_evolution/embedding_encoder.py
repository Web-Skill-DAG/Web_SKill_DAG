#!/usr/bin/env python3
"""Local embedding encoder for representative Potential Query selection."""

from __future__ import annotations

from typing import Any

import numpy as np
from tqdm import tqdm

import sys as _prompt_sys
from pathlib import Path as _PromptPath

_PROMPTS_ROOT = str(_PromptPath(__file__).resolve().parents[1])
if _PROMPTS_ROOT not in _prompt_sys.path:
    _prompt_sys.path.insert(0, _PROMPTS_ROOT)

from prompts import (
    DEFAULT_QUERY_INSTRUCTION,
)



def last_token_pool(last_hidden_state: Any, attention_mask: Any) -> Any:
    """Pool the final non-padding token from each encoded sequence."""
    import torch
    if bool((attention_mask[:, -1].sum() == attention_mask.shape[0]).item()):
        return last_hidden_state[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    batch_indices = torch.arange(last_hidden_state.shape[0], device=last_hidden_state.device)
    return last_hidden_state[batch_indices, sequence_lengths]


def query_text(query: str, instruction: str = DEFAULT_QUERY_INSTRUCTION) -> str:
    return f"Instruct: {instruction}\nQuery:{query}"


def encode_texts(texts: list[str], encoder_path: str, is_query: bool, batch_size: int,
                 max_length: int, device: str, revision: str = "",
                 instruction: str = DEFAULT_QUERY_INSTRUCTION) -> np.ndarray:
    """Encode generated Goals for PQR/GMM component selection."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    revision_args = {"revision": revision} if revision else {}
    tokenizer = AutoTokenizer.from_pretrained(
        encoder_path, padding_side="left", **revision_args
    )
    dtype = torch.bfloat16 if str(device).startswith("cuda") else torch.float32
    encoder = AutoModel.from_pretrained(
        encoder_path, dtype=dtype, **revision_args
    ).to(device).eval()
    encoder.config.use_cache = False
    chunks = []
    kind = "query" if is_query else "document"
    for start in tqdm(range(0, len(texts), batch_size), desc=f"Encode {kind}"):
        batch = texts[start:start + batch_size]
        if is_query:
            batch = [query_text(x, instruction) for x in batch]
        encoded = tokenizer(batch, max_length=max_length, padding=True, truncation=True,
                            return_tensors="pt")
        encoded = {k: v.to(device) for k, v in encoded.items()}
        with torch.inference_mode():
            output = encoder(**encoded)
            pooled = last_token_pool(output.last_hidden_state, encoded["attention_mask"])
            pooled = torch.nn.functional.normalize(pooled.float(), p=2, dim=1)
        chunks.append(pooled.cpu().numpy().astype(np.float32))
    del encoder
    if str(device).startswith("cuda"):
        torch.cuda.empty_cache()
    return np.concatenate(chunks, axis=0)
