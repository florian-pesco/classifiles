"""Local text embeddings for the example index.

Everything runs in this process: model weights are downloaded once from Hugging Face (nothing is
uploaded) and afterwards loaded offline. Resource use is capped: GPU memory fraction, CPU threads,
short inputs and small batches.
"""

from __future__ import annotations

import logging
import os
import threading

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

log = logging.getLogger(__name__)

# Qwen3-Embedding expects an instruction on the query side only.
QUERY_INSTRUCTION = "Instruct: Given a file, retrieve files stored in the same folder\nQuery: "


class Embedder:
    def __init__(self, model: str = "Qwen/Qwen3-Embedding-0.6B", gpu_memory_fraction: float = 0.25,
                 cpu_threads: int = 2, max_seq_length: int = 256, batch_size: int = 32):
        self.model_name = model
        self.gpu_memory_fraction = gpu_memory_fraction
        self.cpu_threads = cpu_threads
        self.max_seq_length = max_seq_length
        self.batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        import torch

        torch.set_num_threads(self.cpu_threads)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda":
            torch.cuda.set_per_process_memory_fraction(self.gpu_memory_fraction)
        from sentence_transformers import SentenceTransformer

        kwargs = {"torch_dtype": torch.float16} if device == "cuda" else {}
        try:  # offline first: after the one-time download nothing touches the network
            model = SentenceTransformer(self.model_name, device=device, model_kwargs=kwargs, local_files_only=True)
        except Exception:
            log.info("Downloading embedding model %s (one time)", self.model_name)
            model = SentenceTransformer(self.model_name, device=device, model_kwargs=kwargs)
        os.environ["HF_HUB_OFFLINE"] = "1"
        model.max_seq_length = self.max_seq_length
        return model

    def model(self):
        with self._lock:
            if self._model is None:
                self._model = self._load()
            return self._model

    def encode(self, texts: list[str], query: bool = False):
        """L2-normalised float32 vectors, one row per text."""
        import numpy as np

        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        if query:
            texts = [QUERY_INSTRUCTION + t for t in texts]
        m = self.model()
        with self._lock:
            vecs = m.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                            show_progress_bar=False, convert_to_numpy=True)
        return vecs.astype(np.float32)

    def unload(self) -> None:
        with self._lock:
            self._model = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
