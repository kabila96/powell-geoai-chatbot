"""Local vector store: a NumPy matrix of normalised embeddings plus a JSONL file of chunks.

For a portfolio-sized knowledge base (hundreds of chunks) exact cosine search in NumPy takes
well under a millisecond, so no external vector database is needed. The class exposes a small
interface (`search`, `ready`, `stats`) so it can later be replaced by Chroma, FAISS, pgvector, etc.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.config import settings
from app.openai_client import embed_texts

log = logging.getLogger(__name__)

CHUNKS_FILE = "chunks.jsonl"
EMBEDDINGS_FILE = "embeddings.npy"
MANIFEST_FILE = "manifest.json"


@dataclass
class RetrievedChunk:
    id: str
    title: str
    url: str
    section: str
    source_type: str
    text: str
    score: float


class VectorStore:
    def __init__(self, index_dir: Path):
        self.index_dir = Path(index_dir)
        self._lock = threading.Lock()
        self._chunks: list[dict] = []
        self._matrix: np.ndarray | None = None
        self._manifest: dict = {}
        self._loaded_mtime: float | None = None
        self.load()

    # -- loading ----------------------------------------------------------------
    def _manifest_mtime(self) -> float | None:
        path = self.index_dir / MANIFEST_FILE
        return path.stat().st_mtime if path.exists() else None

    def load(self) -> None:
        mtime = self._manifest_mtime()
        if mtime is None:
            log.warning("No index found in %s. Run: python -m ingestion.build_index", self.index_dir)
            return
        with self._lock:
            chunks = [
                json.loads(line)
                for line in (self.index_dir / CHUNKS_FILE).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            matrix = np.load(self.index_dir / EMBEDDINGS_FILE)
            if len(chunks) != matrix.shape[0]:
                raise RuntimeError("Index is corrupt: chunk count does not match embedding rows.")
            self._chunks, self._matrix = chunks, matrix.astype(np.float32)
            self._manifest = json.loads((self.index_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
            self._loaded_mtime = mtime
        log.info("Loaded %d chunks (built %s)", len(chunks), self._manifest.get("built_at"))

    def reload_if_changed(self) -> None:
        """Pick up a rebuilt index without restarting the server."""
        mtime = self._manifest_mtime()
        if mtime is not None and mtime != self._loaded_mtime:
            self.load()

    # -- querying ---------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._matrix is not None and len(self._chunks) > 0

    def stats(self) -> dict:
        return {
            "chunks": len(self._chunks),
            "built_at": self._manifest.get("built_at"),
            "sources": self._manifest.get("sources", []),
        }

    def search(self, query: str, top_k: int | None = None, min_score: float | None = None) -> list[RetrievedChunk]:
        self.reload_if_changed()
        if not self.ready:
            return []
        top_k = top_k or settings.top_k
        min_score = settings.min_similarity if min_score is None else min_score

        query_vec = embed_texts([query])[0]
        with self._lock:
            scores = self._matrix @ query_vec
            order = np.argsort(-scores)

            results: list[RetrievedChunk] = []
            per_source: dict[str, int] = {}
            for idx in order:
                score = float(scores[idx])
                if score < min_score or len(results) >= top_k:
                    break
                chunk = self._chunks[idx]
                # Keep some diversity: at most 4 chunks from any single source.
                if per_source.get(chunk["url"], 0) >= 4:
                    continue
                per_source[chunk["url"]] = per_source.get(chunk["url"], 0) + 1
                results.append(
                    RetrievedChunk(
                        id=chunk["id"],
                        title=chunk["title"],
                        url=chunk["url"],
                        section=chunk.get("section", ""),
                        source_type=chunk["source_type"],
                        text=chunk["text"],
                        score=round(score, 4),
                    )
                )
        return results


_store: VectorStore | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore(settings.index_dir)
    return _store
