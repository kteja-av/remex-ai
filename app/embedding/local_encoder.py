import os
from functools import lru_cache
from importlib import import_module
from typing import Protocol, cast

EMBEDDING_DIMENSION = 384
DEFAULT_MODEL_PATH = "/opt/models/all-MiniLM-L6-v2"


class Encoder(Protocol):
    def encode(self, text: str) -> list[float]: ...


class _EmbeddingArray(Protocol):
    def tolist(self) -> list[float]: ...


class _SentenceTransformerModel(Protocol):
    def get_embedding_dimension(self) -> int | None: ...

    def encode(
        self,
        text: str,
        *,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> _EmbeddingArray: ...


class LocalEncoder:
    """CPU-local sentence encoder; model artifacts are baked into the image."""

    def __init__(self, model_path: str | None = None) -> None:
        path = model_path or os.environ.get(
            "EMBEDDING_MODEL_PATH", DEFAULT_MODEL_PATH
        )
        model_class = getattr(
            import_module("sentence_transformers"), "SentenceTransformer"
        )
        self._model = cast(
            _SentenceTransformerModel,
            model_class(path, local_files_only=True, device="cpu"),
        )
        dimension = self._model.get_embedding_dimension()
        if dimension != EMBEDDING_DIMENSION:
            raise RuntimeError(
                f"encoder emits {dimension} dimensions; schema requires "
                f"{EMBEDDING_DIMENSION}"
            )

    def encode(self, text: str) -> list[float]:
        embedding = self._model.encode(
            text,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return embedding.tolist()


@lru_cache(maxsize=1)
def get_encoder() -> LocalEncoder:
    return LocalEncoder()
