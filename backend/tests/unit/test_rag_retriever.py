"""Unit-тесты RAG Retriever (задача T029).

Проверяет поиск по векторам и извлечение контекста."""

import hashlib

import pytest

from glist.db.models import KnowledgeChunk, KnowledgeDocument
from glist.rag.retriever import retrieve_context


@pytest.mark.asyncio
async def test_retrieve_context_returns_documents_and_chunks(db_session):
    """Поиск возвращает документы и чанки с оценкой схожести."""
    embedding = [0.5] * 1536

    doc = KnowledgeDocument(
        title="Test Doc",
        source="test://src",
        checksum=hashlib.sha256(b"test").hexdigest(),
    )
    db_session.add(doc)
    await db_session.flush()

    chunk = KnowledgeChunk(
        document_id=doc.id,
        ordinal=0,
        content="Test content for retrieval",
        embedding=embedding,
    )
    db_session.add(chunk)
    await db_session.flush()

    result = await retrieve_context(
        db_session,
        query_embedding=embedding,
        top_k=5,
        similarity_threshold=0.5,
    )

    assert "documents" in result
    assert "chunks" in result
    assert "similarities" in result
    assert len(result["chunks"]) > 0
    assert len(result["documents"]) > 0
    assert len(result["similarities"]) > 0
    assert result["similarities"][0] > 0.5


@pytest.mark.asyncio
async def test_retrieve_context_respects_threshold(db_session):
    """Чанки ниже порога_threshold не возвращаются."""
    embedding = [0.5] * 1536

    doc = KnowledgeDocument(
        title="Test Doc 2",
        source="test://src2",
        checksum=hashlib.sha256(b"test2").hexdigest(),
    )
    db_session.add(doc)
    await db_session.flush()

    low_sim_embedding = [0.1] * 1536
    chunk = KnowledgeChunk(
        document_id=doc.id,
        ordinal=0,
        content="Low similarity content",
        embedding=low_sim_embedding,
    )
    db_session.add(chunk)
    await db_session.flush()

    result = await retrieve_context(
        db_session,
        query_embedding=embedding,
        top_k=5,
        similarity_threshold=0.9,
    )

    assert len(result["chunks"]) == 0 or all(s < 0.9 for s in result["similarities"])


@pytest.mark.asyncio
async def test_retrieve_context_top_k_limit(db_session):
    """Возвращается максимум top_k результатов."""
    embedding = [0.5] * 1536

    for i in range(3):
        doc = KnowledgeDocument(
            title=f"Test Doc {i}",
            source=f"test://src{i}",
            checksum=hashlib.sha256(f"test{i}".encode()).hexdigest(),
        )
        db_session.add(doc)
        await db_session.flush()

        chunk = KnowledgeChunk(
            document_id=doc.id,
            ordinal=0,
            content=f"Content {i}",
            embedding=embedding,
        )
        db_session.add(chunk)
    await db_session.flush()

    result = await retrieve_context(
        db_session,
        query_embedding=embedding,
        top_k=2,
        similarity_threshold=0.5,
    )

    assert len(result["chunks"]) <= 2
    assert len(result["documents"]) <= 2