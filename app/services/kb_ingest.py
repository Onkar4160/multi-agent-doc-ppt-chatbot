"""Knowledge base ingestion: parse documents, chunk, embed, and upsert to Pinecone."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from app.services.embeddings import embed_texts
from app.services.vector_store import get_vector_store

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500   # approximate tokens (chars / 4)
CHUNK_OVERLAP = 50


def ingest_document(path: str | Path, project_id: int, namespace: str = "default") -> int:
    """Parse a document, split into chunks, embed, and upsert to Pinecone."""
    path = Path(path)
    ext = path.suffix.lower()

    # Extract text
    text = _extract_text(path, ext)
    if not text.strip():
        logger.warning("No text extracted from %s", path.name)
        return 0

    # Chunk
    chunks = _split_text(text, chunk_size=CHUNK_SIZE * 4, overlap=CHUNK_OVERLAP * 4)
    logger.info("Split %s into %d chunks", path.name, len(chunks))

    # Embed
    embeddings = embed_texts(chunks)

    # Prepare vectors
    vectors = []
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        vec_id = f"{project_id}_{path.stem}_{i}_{uuid.uuid4().hex[:6]}"
        vectors.append({
            "id": vec_id,
            "values": emb,
            "metadata": {
                "project_id": project_id,
                "source_filename": path.name,
                "chunk_index": i,
                "text": chunk[:1000],  # Pinecone metadata size limit
            },
        })

    # Upsert
    vs = get_vector_store()
    count = vs.upsert_chunks(vectors, namespace=namespace)
    return count


def _extract_text(path: Path, ext: str) -> str:
    """Extract plaintext from a document file."""
    if ext == ".docx":
        from docx import Document
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)

    elif ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)

    elif ext == ".pptx":
        from pptx import Presentation
        prs = Presentation(str(path))
        texts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    texts.append(shape.text_frame.text)
        return "\n".join(texts)

    elif ext == ".txt":
        return path.read_text(encoding="utf-8")

    else:
        logger.warning("Unsupported KB file type: %s", ext)
        return ""


def _split_text(text: str, chunk_size: int = 2000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks by character count."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap
    return chunks if chunks else [text[:chunk_size]]
