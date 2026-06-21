"""Embedding-based semantic retrieval (opt-in).

When config.EMBED_ENDPOINT is set, claims are embedded on write and claim
search re-ranks a BM25 candidate pool by cosine similarity — so a query
paraphrase or a different language still finds the right belief. With no
endpoint configured, available() is False and every caller falls back to pure
BM25, leaving the substrate's behavior exactly as it was.

Embeddings are stored as little packs of float32 bytes in the claims.embedding
column. No numpy dependency: cosine is computed in plain Python (vectors are a
few hundred dims, called on small candidate pools).
"""

import logging
import math
import struct

import requests

from . import config

log = logging.getLogger("engram.embeddings")


def available() -> bool:
    return bool(config.EMBED_ENDPOINT and config.EMBED_API_KEY)


def embed(text: str) -> list:
    """Return the embedding vector for one string, or None on any failure."""
    vecs = embed_many([text])
    return vecs[0] if vecs else None


def embed_many(texts: list) -> list:
    """Return a list of vectors (one per input), or None if unavailable/failed."""
    if not available() or not texts:
        return None
    url = f"{config.EMBED_ENDPOINT}/embeddings"
    headers = {
        "Authorization": f"Bearer {config.EMBED_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            url,
            headers=headers,
            json={"model": config.EMBED_MODEL, "input": texts},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        vecs = [item.get("embedding") for item in data]
        if len(vecs) != len(texts) or any(v is None for v in vecs):
            return None
        return vecs
    except Exception:
        log.debug("embedding request failed", exc_info=True)
        return None


def pack(vector: list) -> bytes:
    """Serialize a float vector to bytes for the BLOB column."""
    return struct.pack(f"{len(vector)}f", *vector) if vector else b""


def unpack(blob: bytes) -> list:
    if not blob:
        return []
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def cosine(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
