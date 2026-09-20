"""Document ingestion service for reading, chunking, embedding, and vector upsert."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from app.services.embeddings import embed_texts
from app.services.parsers import parse_file
from app.services.vector_store import DEFAULT_NAMESPACE, get_vector_store

logger = logging.getLogger(__name__)

TARGET_CHUNK_WORDS = 250
OVERLAP_WORDS = 40
MAX_CHUNK_WORDS = 300
MAX_METADATA_TEXT_CHARS = 1500


def chunk_parsed_blocks(
    blocks: list[Any], default_heading: str = "General"
) -> list[dict[str, Any]]:
    """Chunk parsed document blocks into ~200-300 word segments with ~40 word overlap.

    Respects heading hierarchy and paragraph boundaries.
    """
    chunks: list[dict[str, Any]] = []
    current_heading = default_heading
    current_words: list[str] = []

    for block in blocks:
        block_text = getattr(block, "text", "").strip()
        block_type = getattr(block, "type", "paragraph")

        if not block_text:
            continue

        if block_type in ("heading", "slide_title"):
            current_heading = block_text
            continue

        words = block_text.split()
        current_words.extend(words)

        while len(current_words) >= TARGET_CHUNK_WORDS:
            chunk_word_slice = current_words[:MAX_CHUNK_WORDS]
            chunk_text = " ".join(chunk_word_slice)
            chunks.append({
                "heading": current_heading,
                "text": chunk_text,
            })
            # Overlap with last OVERLAP_WORDS
            current_words = current_words[TARGET_CHUNK_WORDS - OVERLAP_WORDS :]

    if current_words:
        chunks.append({
            "heading": current_heading,
            "text": " ".join(current_words),
        })

    return chunks


def chunk_raw_text(text: str, default_heading: str = "General") -> list[dict[str, Any]]:
    """Chunk raw text into ~200-300 word segments with ~40 word overlap."""
    words = text.split()
    if not words:
        return []

    chunks: list[dict[str, Any]] = []
    start = 0

    while start < len(words):
        end = min(start + MAX_CHUNK_WORDS, len(words))
        chunk_words = words[start:end]
        chunks.append({
            "heading": default_heading,
            "text": " ".join(chunk_words),
        })
        if end == len(words):
            break
        start += TARGET_CHUNK_WORDS - OVERLAP_WORDS

    return chunks


def ingest_file(file_path: str | Path, namespace: str = DEFAULT_NAMESPACE) -> dict[str, Any]:
    """Parse, chunk, embed, and store document in vector store.

    Args:
        file_path: Path to DOCX, PPTX, PDF, PNG/JPG, TXT, or MD file.
        namespace: Vector store namespace (default 'enterprise-kb').

    Returns:
        Summary dict containing doc_id, source_name, chunks_count, file_type.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Document file not found: {file_path}")

    source_name = path.name
    file_type = path.suffix.lstrip(".").lower()
    doc_id = hashlib.sha256(source_name.encode("utf-8")).hexdigest()[:12]

    # 1. Parse content
    parsed = parse_file(path)
    
    # 2. Chunk content
    if parsed.blocks:
        raw_chunks = chunk_parsed_blocks(parsed.blocks, default_heading=path.stem)
    else:
        raw_chunks = chunk_raw_text(parsed.raw_text, default_heading=path.stem)

    if not raw_chunks:
        logger.warning(f"No text extracted from file '{source_name}'. Skipping ingestion.")
        return {
            "doc_id": doc_id,
            "source_name": source_name,
            "chunks_count": 0,
            "file_type": file_type,
        }

    # 3. Embed text chunks
    texts_to_embed = [c["text"] for c in raw_chunks]
    embeddings = embed_texts(texts_to_embed)

    # 4. Prepare vector records with deterministic IDs
    vectors: list[dict[str, Any]] = []
    for idx, (chunk_data, emb) in enumerate(zip(raw_chunks, embeddings)):
        chunk_id = f"{doc_id}_c{idx}"
        chunk_text = chunk_data["text"]
        metadata = {
            "doc_id": doc_id,
            "source_name": source_name,
            "file_type": file_type,
            "chunk_index": idx,
            "heading": chunk_data["heading"],
            "text": chunk_text[:MAX_METADATA_TEXT_CHARS],
        }
        vectors.append({
            "id": chunk_id,
            "values": emb,
            "metadata": metadata,
        })

    # 5. Upsert to VectorStore
    store = get_vector_store()
    upserted_count = store.upsert(vectors, namespace=namespace)

    logger.info(f"Ingested '{source_name}' ({upserted_count} chunks upserted, doc_id={doc_id}).")
    return {
        "doc_id": doc_id,
        "source_name": source_name,
        "chunks_count": upserted_count,
        "file_type": file_type,
    }


def ingest_directory(
    directory_path: str | Path, namespace: str = DEFAULT_NAMESPACE
) -> list[dict[str, Any]]:
    """Ingest all supported documents in a directory."""
    dir_path = Path(directory_path)
    if not dir_path.exists() or not dir_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory_path}")

    results = []
    supported_exts = {".docx", ".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md"}

    for file_path in sorted(dir_path.iterdir()):
        if file_path.is_file() and file_path.suffix.lower() in supported_exts:
            try:
                res = ingest_file(file_path, namespace=namespace)
                results.append(res)
            except Exception as e:
                logger.error(f"Failed to ingest file '{file_path.name}': {e}")

    return results
