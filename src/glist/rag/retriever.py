async def retrieve_context(
    session: AsyncSession,
    query_embedding: list[float],
    *,
    top_k: int = 5,
    similarity_threshold: float = 0.72,
    document_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Ищет наиболее релевантные чанки для given query embedding.

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
    import math

    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        if len(a) != len(b):
            raise ValueError("Векторы должны иметь одинаковую размерность")
        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    # Получаем все чанки с данными документов
    stmt = select(KnowledgeChunk, KnowledgeDocument).join(
        KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id
    )

    if document_ids:
        stmt = stmt.where(KnowledgeChunk.document_id.in_(document_ids))

    result = await session.execute(stmt)
    rows = result.all()

    # Вычисляем сходство для каждого чанка и собираем уникальные документы
    chunks = []
    documents_dict: dict[str, dict[str, Any]] = {}
    similarities = []

    for chunk, doc in rows:
        sim = _cosine_similarity(query_embedding, chunk.embedding)

        if sim < similarity_threshold:
            continue

        if document_ids and chunk.document_id not in document_ids:
            continue

        doc_id = str(doc.id)
        if doc_id in documents_dict:
            continue

        documents_dict[doc_id] = {
            "id": doc_id,
            "title": doc.title,
            "source": doc.source,
            "checksum": doc.checksum,
        }

        chunks.append(
            {
                "chunk_id": str(chunk.id),
                "ordinal": chunk.ordinal,
                "content": chunk.content,
                "similarity": float(sim),
                "document_title": doc.title,
                "document_source": doc.source,
            }
        )
        similarities.append(float(sim))

    documents = list(documents_dict.values())

    chunks.sort(key=lambda c: c["similarity"], reverse=True)
    documents.sort(
        key=lambda d: d.get("similarity", 0) if isinstance(d, dict) else 0,
        reverse=True,
    )

    return {
        "documents": documents[:top_k],
        "chunks": chunks[:top_k],
        "similarities": similarities[:top_k],
    }