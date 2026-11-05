"""Unit-тесты RAG Indexer (задача T028).

Проверяет чанкинг, эмбеддинги и запись в БД."""

from unittest.mock import AsyncMock, patch

import pytest

from glist.db.models import KnowledgeChunk
from glist.llm.mock_provider import MockLLMProvider
from glist.rag.indexer import chunk_text, index_document


@pytest.fixture
def sample_text():
    """Длинный текст для тестов чанкинга."""
    return "А" * 1000


@pytest.fixture
def mock_provider():
    """Мок LLM-провайдер для тестов."""
    return MockLLMProvider(seed=42, scenario="success")


@pytest.mark.asyncio
async def test_chunk_text_creates_two_chunks(sample_text):
    """Текст 1000 символов дает 2 чанка (800 + 200 с перекрытием)."""
    chunks = chunk_text(sample_text)
    assert len(chunks) == 2
    assert len(chunks[0]) == 800
    assert len(chunks[1]) == 200


@pytest.mark.asyncio
async def test_chunk_text_empty():
    """Пустой текст дает пустой список."""
    chunks = chunk_text("")
    assert chunks == []


@pytest.mark.asyncio
async def test_chunk_text_single_line():
    """Короткий текст (меньше 800 символов) дает один чанк."""
    short = "Hello world"
    chunks = chunk_text(short)
    assert len(chunks) == 1
    assert chunks[0] == "Hello world"


@pytest.mark.asyncio
async def test_index_document_creates_knowledge_document_and_chunks(
    db_session, mock_provider, sample_text
):
    """index_document сохраняет документ и чанки в БД."""
    with patch.object(mock_provider, 'embeddings', async_new_callable=AsyncMock) as mock_emb:
        mock_emb.return_value = [[0.1] * 1536]

        document = await index_document(
            db_session,
            provider=mock_provider,
            title="Тестовый документ",
            source="test://source",
            content=sample_text,
            doc_version="1.0",
        )

    assert document is not None
    assert document.title == "Тестовый документ"
    assert document.source == "test://source"
    assert document.checksum is not None

    chunks = await db_session.query(KnowledgeChunk).filter(
        KnowledgeChunk.title == "Тестовый документ"
    ).all()
    assert len(chunks) == 2
    assert all(hasattr(c, "embedding") for c in chunks)
    assert all(len(c.embedding) == 1536 for c in chunks)


@pytest.mark.asyncio
async def test_index_document_generates_checksum(db_session, mock_provider):
    """Проверяется генерация checksum при отсутствии явного значения."""
    short_text = "Короткий текст документа для тестирования"

    with patch.object(mock_provider, 'embeddings', async_new_callable=AsyncMock) as mock_emb:
        mock_emb.return_value = [[0.1] * 1536]

        document = await index_document(
            db_session,
            provider=mock_provider,
            title="Doc Checksum Test",
            source="test://src",
            content=short_text,
        )

    assert document.checksum is not None
    assert len(document.checksum) == 64


@pytest.mark.asyncio
async def test_index_document_embedding_dimension(db_session, mock_provider):
    """Проверяется, что эмбеддинги имеют размерность 1536."""
    with patch.object(mock_provider, 'embeddings', async_new_callable=AsyncMock) as mock_emb:
        mock_emb.return_value = [[0.5] * 1536]

        await index_document(
            db_session,
            provider=mock_provider,
            title="Embedding Test",
            source="test://src",
            content="some content",
        )

    chunk = await db_session.query(KnowledgeChunk).filter(
        KnowledgeChunk.title == "Embedding Test"
    ).first()
    assert chunk is not None
    assert len(chunk.embedding) == 1536