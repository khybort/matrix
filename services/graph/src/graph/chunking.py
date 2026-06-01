"""Semantic text chunking for graph extraction.

Splits long article bodies at topic boundaries using sentence/paragraph-level
bag-of-words cosine similarity (no embedding API). Each chunk fits the agent
prompt budget while preserving coherent passages for entity extraction.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[a-z][a-z0-9]{1,32}")
_PARA_RE = re.compile(r"\n\s*\n+")
_SENT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\']|\d)')


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str


@dataclass(frozen=True)
class ChunkConfig:
    enabled: bool
    target_chars: int
    max_chars: int
    min_chars: int
    sim_threshold: float
    max_chunks: int

    @classmethod
    def from_env(cls) -> ChunkConfig:
        def _bool(name: str, default: bool) -> bool:
            raw = os.environ.get(name)
            if raw is None:
                return default
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            enabled=_bool("GRAPH_SEMANTIC_CHUNKING", True),
            target_chars=int(os.environ.get("GRAPH_CHUNK_TARGET_CHARS", "1200")),
            max_chars=int(os.environ.get("GRAPH_CHUNK_MAX_CHARS", "1800")),
            min_chars=int(os.environ.get("GRAPH_CHUNK_MIN_CHARS", "200")),
            sim_threshold=float(os.environ.get("GRAPH_CHUNK_SIM_THRESHOLD", "0.25")),
            max_chunks=max(1, int(os.environ.get("GRAPH_MAX_CHUNKS_PER_DOC", "6"))),
        )


def _tokenize(text: str) -> dict[str, float]:
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return {}
    inv = 1.0 / len(tokens)
    return {t: inv for t in tokens}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a.get(k, 0.0) * v for k, v in b.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _centroid(vecs: list[dict[str, float]]) -> dict[str, float]:
    acc: dict[str, float] = {}
    for vec in vecs:
        for k, v in vec.items():
            acc[k] = acc.get(k, 0.0) + v
    n = float(len(vecs))
    return {k: v / n for k, v in acc.items()}


def split_units(text: str, *, max_chars: int) -> list[str]:
    """Paragraph-first units; oversized paragraphs fall back to sentences."""
    units: list[str] = []
    for para in _PARA_RE.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            units.append(para)
            continue
        for sent in _SENT_RE.split(para):
            sent = sent.strip()
            if sent:
                units.append(sent)
    return units


def plan_semantic_chunks(text: str, cfg: ChunkConfig | None = None) -> list[TextChunk]:
    """Return semantic chunks for `text`. Short bodies stay a single chunk."""
    cfg = cfg or ChunkConfig.from_env()
    body = (text or "").strip()
    if not body:
        return []
    if len(body) <= cfg.max_chars:
        return [TextChunk(0, body)]

    units = split_units(body, max_chars=cfg.max_chars)
    if not units:
        return [TextChunk(0, body[: cfg.max_chars])]

    grouped: list[list[str]] = []
    current: list[str] = []
    current_vecs: list[dict[str, float]] = []

    for unit in units:
        vec = _tokenize(unit)
        prospective = " ".join(current + [unit])
        if len(prospective) > cfg.max_chars and current:
            grouped.append(current)
            current = [unit]
            current_vecs = [vec]
            continue

        if not current:
            current = [unit]
            current_vecs = [vec]
            continue

        centroid = _centroid(current_vecs)
        sim = _cosine(centroid, vec)
        if sim >= cfg.sim_threshold or len(prospective) < cfg.target_chars:
            current.append(unit)
            current_vecs.append(vec)
        else:
            grouped.append(current)
            current = [unit]
            current_vecs = [vec]

    if current:
        grouped.append(current)

    chunks = [
        TextChunk(i, " ".join(parts))
        for i, parts in enumerate(grouped[: cfg.max_chunks])
    ]
    if len(grouped) > cfg.max_chunks:
        tail = " ".join(" ".join(parts) for parts in grouped[cfg.max_chunks - 1 :])
        chunks[-1] = TextChunk(
            cfg.max_chunks - 1,
            (chunks[-1].text + " " + tail)[: cfg.max_chars],
        )
    return chunks
