"""Local, reproducible embeddings for the Latentscape corpus.

The model files are deliberately vendored by ``scripts/fetch-model-assets.mjs``.
This module never contacts Hugging Face or another embedding service at runtime.
The pooling and normalisation match Transformers.js feature extraction with
``pooling: 'mean'`` and ``normalize: true``.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL_ID = "Xenova/all-MiniLM-L6-v2"
MAX_LENGTH = 256
_EPSILON = 1e-12


def _require_runtime() -> tuple[object, object]:
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError as error:  # pragma: no cover - depends on optional local setup
        raise RuntimeError(
            "Embedding requires onnxruntime and tokenizers. Install pipeline dependencies "
            "with `uv sync` before running this stage."
        ) from error
    return ort, Tokenizer


def _model_root(model_name: str, cache_dir: str | Path) -> Path:
    """Resolve a local model identifier below the pipeline cache directory."""
    root = Path(cache_dir).expanduser().resolve()
    candidate = root.joinpath(*model_name.split("/"))
    if not (candidate / "tokenizer.json").is_file():
        raise FileNotFoundError(
            f"No local tokenizer for {model_name!r} at {candidate}. "
            "Run the vendored model asset fetch script first."
        )
    if not (candidate / "onnx" / "model_quantized.onnx").is_file():
        raise FileNotFoundError(
            f"No quantized ONNX model at {candidate / 'onnx/model_quantized.onnx'}"
        )
    return candidate


@lru_cache(maxsize=4)
def _load_runtime(model_name: str, cache_dir: str) -> tuple[object, object, list[str]]:
    ort, tokenizer_cls = _require_runtime()
    root = _model_root(model_name, cache_dir)
    tokenizer = tokenizer_cls.from_file(str(root / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=MAX_LENGTH)
    # Dynamic axes permit a compact batch, which makes the 500-paper MVP quick
    # while preventing full-corpus embedding from accumulating tensors in memory.
    session = ort.InferenceSession(
        str(root / "onnx" / "model_quantized.onnx"),
        providers=["CPUExecutionProvider"],
    )
    input_names = [input_.name for input_ in session.get_inputs()]
    return tokenizer, session, input_names


def _mean_pool(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Mask-aware mean pooling, then L2 normalise rows like Transformers.js."""
    mask = attention_mask.astype(np.float32)[..., None]
    pooled = (last_hidden_state.astype(np.float32) * mask).sum(axis=1)
    pooled /= np.maximum(mask.sum(axis=1), _EPSILON)
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return (pooled / np.maximum(norms, _EPSILON)).astype(np.float32)


def embed_texts(
    texts: Iterable[str],
    model_name: str = MODEL_ID,
    cache_dir: str | Path = "pipeline/.cache/models",
    *,
    batch_size: int = 32,
) -> np.ndarray:
    """Embed text locally using the browser-identical MiniLM model.

    Returned vectors are 384-dimensional, float32 and unit normalized. Inputs are
    truncated to 256 tokens including special tokens. This function deliberately
    processes bounded batches so a complete proceedings corpus does not require
    retaining model input arrays for every paper.
    """
    values = list(texts)
    if not values:
        return np.empty((0, 384), dtype=np.float32)
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    tokenizer, session, input_names = _load_runtime(model_name, str(Path(cache_dir).expanduser()))
    chunks: list[np.ndarray] = []
    for start in range(0, len(values), batch_size):
        current = values[start : start + batch_size]
        batch = [value if isinstance(value, str) else str(value) for value in current]
        encodings = tokenizer.encode_batch(batch, add_special_tokens=True)
        sequence_length = max(len(encoding.ids) for encoding in encodings)
        input_ids = np.zeros((len(encodings), sequence_length), dtype=np.int64)
        attention_mask = np.zeros_like(input_ids)
        token_type_ids = np.zeros_like(input_ids)
        for row, encoding in enumerate(encodings):
            length = len(encoding.ids)
            input_ids[row, :length] = encoding.ids
            attention_mask[row, :length] = encoding.attention_mask
            token_type_ids[row, :length] = encoding.type_ids

        available = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }
        outputs = session.run(None, {name: available[name] for name in input_names})
        # The MiniLM ONNX export's first output is last_hidden_state.
        chunks.append(_mean_pool(np.asarray(outputs[0]), attention_mask))
    return np.concatenate(chunks, axis=0)


def quantize_unit_vectors(vectors: np.ndarray) -> np.ndarray:
    """Quantize unit vectors using the public data-contract scale of 127."""
    values = np.asarray(vectors, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError("vectors must be a two-dimensional array")
    return np.clip(np.rint(values * 127), -127, 127).astype(np.int8)
