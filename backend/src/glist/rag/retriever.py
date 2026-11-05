"""RAG Retriever — поиск по векторам и извлечение контекста (задача T029).

Отвечает за:
- поиск по векторам (top-k = 5, порог косинусной близости 0.72)
- возврат ссылок на документы и чанки для цитирования
- фильтрацию по relevance иrecency"""

from __future__ import annotations

from typing import Any

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
) -> dict[str, Any]:
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
    from sqlalchemy import literal_column

    query_embedding_str = "[" + ",".join(f"{v:.8f}" for v in query_embedding) + "]"

    # Cosine distance через pgvector-оператор <=> (1 - cosine_distance = similarity)
    similarity_expr: Any = literal_column(
        f"1 - (embedding <=> '{query_embedding_str}'::vector)"
    ).label("similarity")

    # Строим запрос к KnowledgeChunk с cosine distance
    stmt: Any = (
        select(
            KnowledgeChunk,
            KnowledgeDocument.title.label("doc_title"),  # type: ignore[attr-defined]
            KnowledgeDocument.source.label("doc_source"),  # type: ignore[attr-defined]
            KnowledgeDocument.checksum.label("doc_checksum"),  # type: ignore[attr-defined]
            similarity_expr,
        )
        .select_from(KnowledgeChunk)
        .join(
            KnowledgeDocument,
            KnowledgeChunk.document_id == KnowledgeDocument.id,  # type: ignore[arg-type]
        )
    )

    if document_ids:
        stmt = stmt.where(
            KnowledgeChunk.document_id.in_(document_ids)  # type: ignore[attr-defined]
        )

    # Получаем результаты
    result = await session.execute(stmt)
    rows = result.all()

    # Форматируем вывод
    chunks: list[dict[str, Any]] = []
    documents_list: list[dict[str, Any]] = []
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
        doc_id = str(chunk.document_id) if hasattr(chunk, "document_id") else None
        doc_entry = {
            "id": doc_id,
            "title": doc_title,
            "source": doc_source,
            "checksum": doc_checksum,
        }
        if doc_id and doc_id not in [d.get("id") for d in documents_list]:
            documents_list.append(doc_entry)

        similarities.append(float(similarity))


# Сортировка по убыванию схожести
    chunks.sort(key=lambda c: c["similarity"], reverse=True)

    return {
        "documents": documents_list[:top_k],
        "chunks": chunks[:top_k],
        "similarities": similarities[:top_k],
    }