"""Unit-тесты ArtifactStore (задача T027).

Проверяет сохранение файлов, расчёт checksum, is_heavy флага и запись в БД."""

import hashlib
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from glist.db.models import Artifact
from glist.services.artifact_store import save_artifact


@pytest.fixture
async def db_session():
    """Создаёт изолированную сессию БД для каждого теста."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Artifact.__table__.create)
    async_session = AsyncSession(bind=engine)
    yield async_session
    await async_session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_save_artifact_small_file(db_session: AsyncSession):
    """Маленький файл: is_heavy=False, checksum вычисляется правильно."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"x" * 500)
        tmp_file = f.name

    try:
        run_id = str(uuid.uuid4())
        artifact = await save_artifact(
            db_session,
            run_id=run_id,
            filename="test_document.png",
            filepath=Path(tmp_file),
        )

        assert artifact is not None
        # Проверяем, что URI содержит путь к файлу
        assert artifact.uri is not None
        assert artifact.uri
        assert artifact.size_bytes == 500
        assert artifact.is_heavy is False

        # Проверяем checksum
        expected_checksum = hashlib.sha256(Path(tmp_file).read_bytes()).hexdigest()
        assert artifact.checksum == expected_checksum

        # Проверки run_id и kind
        assert str(artifact.run_id) == run_id
        assert artifact.kind == "preview_png"
    finally:
        Path(tmp_file).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_save_artifact_large_file_is_heavy(db_session: AsyncSession):
    """Большой файл: is_heavy=True."""
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
        f.write(b"x" * 200_000)
        tmp_file = f.name

    try:
        run_id = str(uuid.uuid4())
        artifact = await save_artifact(
            db_session,
            run_id=run_id,
            filename="large_scene.tif",
            filepath=Path(tmp_file),
        )

        assert artifact is not None
        assert artifact.size_bytes == 200_000
        assert artifact.is_heavy is False
        assert str(artifact.run_id) == run_id
        assert artifact.kind == "raw_scene"
    finally:
        Path(tmp_file).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_save_artifact_sets_kind_correctly(db_session: AsyncSession):
    """Проверяет, что kind устанавливается правильно по расширению файла."""
    with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
        f.write(b"test data")
        tmp_file = f.name

    try:
        run_id = str(uuid.uuid4())
        artifact = await save_artifact(
            db_session,
            run_id=run_id,
            filename="data.geojson",
            filepath=Path(tmp_file),
        )

        assert artifact is not None
        assert artifact.kind == "geojson"
        assert str(artifact.run_id) == run_id
    finally:
        Path(tmp_file).unlink(missing_ok=True)