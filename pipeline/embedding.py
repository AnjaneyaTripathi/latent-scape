"""Local, reproducible embeddings for the Latentscape corpus.

The model files are deliberately vendored by ``scripts/fetch-model-assets.mjs``.
This module never contacts Hugging Face or another embedding service at runtime.
The pooling and normalisation match Transformers.js feature extraction with
``pooling: 'mean'`` and ``normalize: true``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile

import numpy as np

from .corpus import select, write_meta
from .settings import Settings
from .util import write_json

MODEL_ID = "Xenova/all-MiniLM-L6-v2"
MAX_LENGTH = 256
_EPSILON = 1e-12


def _file_digest(path: Path) -> str:
    """Hash the two model assets that affect the emitted vectors."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def model_fingerprint(model_name: str, cache_dir: str | Path) -> str:
    """Identify the exact local model/tokenizer pair used for a cache entry."""
    root = _model_root(model_name, cache_dir)
    digest = hashlib.sha256()
    for path in (root / "tokenizer.json", root / "onnx" / "model_quantized.onnx"):
        digest.update(path.name.encode("utf-8"))
        digest.update(_file_digest(path).encode("ascii"))
    return digest.hexdigest()


def source_hash(paper: dict) -> str:
    """Bind a vector cache entry to the text the browser-compatible model saw."""
    text = f"{paper.get('title', '')}\n{paper.get('abstract', '')}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    # This one-time offline builder must not emit ONNX Runtime telemetry when a
    # session is created. The setting is process-wide and is deliberately made
    # before constructing any InferenceSession.
    ort.disable_telemetry_events()
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


def _read_chunk(
    path: Path,
    expected_hashes: list[str],
    expected_model: str,
) -> np.ndarray | None:
    """Return a valid completed chunk, or ``None`` when it must be rebuilt.

    A chunk is only trusted when every source hash and the model fingerprint
    match. This makes it safe to resume a long build after a source correction
    without accidentally pairing a vector with an older abstract.
    """
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as cached:
            hashes = cached["sourceHashes"].astype(str).tolist()
            model = str(cached["modelFingerprint"].item())
            vectors = np.asarray(cached["vectors"], dtype=np.float32)
    except (BadZipFile, OSError, ValueError, KeyError):
        return None
    if hashes != expected_hashes or model != expected_model:
        return None
    if vectors.ndim != 2 or len(vectors) != len(expected_hashes) or vectors.shape[1] != 384:
        return None
    norms = np.linalg.norm(vectors, axis=1)
    if not np.all(np.isfinite(vectors)) or not np.allclose(norms, 1.0, atol=1e-3):
        return None
    return vectors


def _write_chunk(path: Path, hashes: list[str], model: str, vectors: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".npz.tmp")
    # Passing an open file prevents numpy from appending an unexpected suffix,
    # and the final replace keeps a checkpoint atomic.
    with temporary.open("wb") as handle:
        np.savez_compressed(
            handle,
            sourceHashes=np.asarray(hashes),
            modelFingerprint=np.asarray(model),
            vectors=np.asarray(vectors, dtype=np.float32),
        )
    temporary.replace(path)


def embed_corpus(
    settings: Settings,
    corpus: str = "full",
    *,
    chunk_size: int = 256,
    batch_size: int = 32,
) -> dict:
    """Embed a selected corpus with atomic, source-bound chunk checkpoints.

    The final ``embeddings-<corpus>.npy`` remains the public pipeline contract.
    Chunks live only in the offline cache and are deliberately small enough that
    a source repair reruns at most one chunk instead of the full proceedings.
    """
    if chunk_size < 1 or batch_size < 1:
        raise ValueError("chunk_size and batch_size must be positive")
    papers, corpus_fingerprint = select(settings, corpus)
    settings.prepare()
    model = model_fingerprint(settings.embedding_model, settings.cache_dir / "models")
    cache_root = settings.cache_dir / "embedding-chunks" / corpus / model[:16]
    vectors: list[np.ndarray] = []
    rebuilt = 0
    reused = 0
    source_hashes = [source_hash(paper) for paper in papers]
    progress_path = settings.derived_dir / f"embedding-progress-{corpus}.json"
    for start in range(0, len(papers), chunk_size):
        stop = min(start + chunk_size, len(papers))
        hashes = source_hashes[start:stop]
        path = cache_root / f"{start:08d}-{stop:08d}.npz"
        current = _read_chunk(path, hashes, model)
        if current is None:
            texts = [
                f"{paper['title']}\n{paper.get('abstract', '')}" for paper in papers[start:stop]
            ]
            current = embed_texts(
                texts,
                model_name=settings.embedding_model,
                cache_dir=settings.cache_dir / "models",
                batch_size=batch_size,
            )
            _write_chunk(path, hashes, model, current)
            rebuilt += len(current)
        else:
            reused += len(current)
        vectors.append(current)
        write_json(
            progress_path,
            {
                "corpus": corpus,
                "corpusFingerprint": corpus_fingerprint,
                "modelFingerprint": model,
                "total": len(papers),
                "completed": stop,
                "rebuiltVectors": rebuilt,
                "reusedVectors": reused,
            },
        )
    output = settings.derived_dir / f"embeddings-{corpus}.npy"
    temporary = output.with_suffix(".npy.tmp")
    output.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("wb") as handle:
        np.save(
            handle,
            np.concatenate(vectors, axis=0) if vectors else np.empty((0, 384), dtype=np.float32),
        )
    temporary.replace(output)
    write_meta(output, corpus, corpus_fingerprint)
    return {
        "papers": len(papers),
        "dimensions": 384,
        "reusedVectors": reused,
        "rebuiltVectors": rebuilt,
        "path": str(output),
        "progress": str(progress_path),
    }


def quantize_unit_vectors(vectors: np.ndarray) -> np.ndarray:
    """Quantize unit vectors using the public data-contract scale of 127."""
    values = np.asarray(vectors, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError("vectors must be a two-dimensional array")
    return np.clip(np.rint(values * 127), -127, 127).astype(np.int8)
