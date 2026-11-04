"""RAG Retriever — поиск по векторам и извлечение контекста (задача T029).

Отвечает за:
- поиск по векторам (top-k = 5, порог косинусной близости 0.72)
- возврат ссылок на документы и чанки для цитирования
- фильтрацию по relevance иrecency"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glist.db.models import KnowledgeChunk, KnowledgeDocument


async def retrieve_context(
    session: AsyncSession,
    query_embedding: list[float],
    *,
    top_k: int = 5,
    similarity_threshold: float = 0.72,
    document_ids: list[int] | None = None,
) -> dict:
    """Ищет наиболее релевантные чанки для given query embedding.

    Использует cosine similarity через pgvector.

    Args:
        session: Асинхронная сессия БД.
        query_embedding: Вектор запроса (1536 размерности).
        top_k: Максимальное количество результатов.
        similarity_threshold: Минимальный порог cosine similarity.
        document_ids: Ограничить поиск конкретными document IDs.

    Returns:
        Dict с результатами:
        - "documents": список уникальных KnowledgeDocument
        - "chunks": список KnowledgeChunk с метаданными
        - "similarities": список оценок похожести
    """
    from pgvector.sqlalchemy import CosineDistance

    # Строим запрос к KnowledgeChunk с cosine distance
    stmt = (
        select(
            KnowledgeChunk,
            KnowledgeDocument.title.label("doc_title"),
            KnowledgeDocument.source.label("doc_source"),
            KnowledgeDocument.checksum.label("doc_checksum"),
            (1 - CosineDistance(KnowledgeChunk.embedding, query_embedding)).label(
                "similarity"
            ),
        )
        .select_from(KnowledgeChunk)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == document_id if hasattr(KnowledgeChunk, "document_id") else True)
    )

    # Получаем результаты
    result = await session.execute(stmt)
    rows = result.all()

    # Форматируем вывод
    chunks = []
    documents_set = set()
    similarities = []

    for row in rows:
        chunk, doc_title, doc_source, doc_checksum, similarity = row

        if similarity < similarity_threshold:
            continue

        if document_ids and chunk.document_id not in document_ids:
            continue

        chunks.append(
            {
                "chunk_id": str(chunk.id),
                "ordinal": chunk.ordinal,
                "content": chunk.content,
                "similarity": float(similarity),
                "document_title": doc_title,
                "document_source": doc_source,
            }
        )
        documents_set.add(
            {
                "id": str(chunk.document_id) if hasattr(chunk, "document_id") else None,
                "title": doc_title,
                "source": doc_source,
                "checksum": doc_checksum,
            }
        )
        similarities.append(float(similarity))

    # Уникальные документы
    documents = list(documents_set)

    # Сортировка по убыванию схожести
    chunks.sort(key=lambda c: c["similarity"], reverse=True)
    documents.sort(key=lambda d: d.get("similarity", 0) if isinstance(d, dict) else 0, reverse=True)

    return {
        "documents": documents[:top_k],
        "chunks": chunks[:top_k],
        "similarities": similarities[:top_k],
    }