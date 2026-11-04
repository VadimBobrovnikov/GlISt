"""RAG Indexer — чанкинг, эмбеддинги и запись в БД (задача T028).

Отвечает за:
- chunking текста: 800 символов, перекрытие 100 символов
- генерацию эмбеддингов через LLMProvider
- запись KnowledgeDocument и KnowledgeChunk в pgvector"""

from __future__ import annotations

import structlog

from glist.db.models import KnowledgeDocument
from glist.llm.provider import LLMProvider

logger = structlog.get_logger()

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def chunk_text(text: str) -> list[str]:
    """Разбивает текст на чанки по 800 символов с перекрытием 100.

    Args:
        text: Входной текст.

    Returns:
        Список чанков (строк).
    """
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + CHUNK_SIZE, text_len)
        chunk = text[start:end]
        chunks.append(chunk)
        start += CHUNK_SIZE - CHUNK_OVERLAP
        if start + CHUNK_OVERLAP >= text_len:
            break

    return chunks


async def index_document(
    session,
    *,
    provider: LLMProvider,
    title: str,
    source: str,
    content: str,
    doc_version: str = "1.0",
    checksum: Optional[str] = None,
) -> KnowledgeDocument:
    """Индексирует документ: чанкинг, эмбеддинги, запись в БД.

    Args:
        session: Асинхронная сессия БД.
        provider: LLM-провайдер для эмбеддингов.
        title: Заголовок документа.
        source: Источник документа (URL или путь).
        content: Полный текст документа.
        doc_version: Версия документа.
        checksum: Контрольная сумма (SHA-256). Генерируется автоматически, если не указана.

    Returns:
        Сохранённый KnowledgeDocument.
    """
    import hashlib

    if checksum is None:
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

    document = KnowledgeDocument(
        title=title,
        source=source,
        doc_version=doc_version,
        checksum=checksum,
    )
    session.add(document)
    await session.flush()
    await session.refresh(document)

    chunks = chunk_text(content)
    for idx, chunk_text_content in enumerate(chunks):
        embedding = await provider.embeddings([chunk_text_content])
        vector = embedding[0] if embedding else [0.0] * 1536

        chunk = KnowledgeDocument(
            title=document.title,
            source=document.source,
            doc_version=document.doc_version,
            checksum=document.checksum,
            content=chunk_text_content,
            ordinal=idx,
            embedding=vector,
        )
        session.add(chunk)

    await session.flush()
    logger.info("Document indexed", title=title, chunks=len(chunks), checksum=checksum)
    return document