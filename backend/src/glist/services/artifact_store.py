"""ArtifactStore — сервис сохранения и индексирования артефактов (задача T027).

Отвечает за:
- сохранение файлов в директорию ARTIFACTS_DIR/{run_id}/...
- расчёт size_bytes и sha256 хеша файла
- определение флага is_heavy (файл > 100 МБ)
- запись метаданных в таблицу artifact через SQLModel"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from glist.db.models import Artifact

ARTIFACTS_DIR = Path("artifacts")
HEAVY_FILE_SIZE_THRESHOLD = 100 * 1024 * 1024  # 100 MB


def _ensure_artifact_dir(run_id: str) -> Path:
    """Создаёт и возвращает директорию артефактов для данного run_id."""
    run_dir = ARTIFACTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _compute_sha256(filepath: Path) -> str:
    """Вычисляет SHA-256 хеш файла."""
    sha256 = hashlib.sha256()
    with filepath.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _get_retention_until(kind: str) -> datetime | None:
    """Возвращает retention_until на основе kind артефакта.

    По CONSTRAINT: retention_until NOT NULL для raw_scene/intermediate_*,
    иначе NULL.
    """
    retention_kinds = {"raw_scene", "intermediate_ndvi", "intermediate_classified"}
    if kind in retention_kinds:
        return datetime.now(UTC) + timedelta(days=365)
    return None


async def save_artifact(
    session: AsyncSession,
    *,
    run_id: str,
    filename: str,
    filepath: Path,
    mime_type: str | None = None,
) -> Artifact:
    """Сохраняет файл и записывает метаданные в таблицу artifact.

    Args:
        session: Асинхронная сессия БД.
        run_id: Идентификатор запуска (связь с analysis_run).
        filename: Имя файла (без пути).
        filepath: Полный путь к файлу на диске.
        mime_type: MIME-тип файла (опционально).

    Returns:
        Сохранённый объект Artifact.
    """
    size_bytes = filepath.stat().st_size
    sha256 = _compute_sha256(filepath)
    is_heavy = size_bytes > HEAVY_FILE_SIZE_THRESHOLD

    kind_map = {
        "png": "preview_png",
        "geotiff": "final_geotiff",
        "geojson": "geojson",
        "pdf": "pdf",
        "scene": "raw_scene",
        "ndvi": "intermediate_ndvi",
        "classified": "intermediate_classified",
    }
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    kind = kind_map.get(extension, "raw_scene")

    retention_until = _get_retention_until(kind)

    artifact = Artifact(
        run_id=uuid.UUID(run_id),
        uri=str(filepath),
        size_bytes=size_bytes,
        checksum=sha256,
        is_heavy=is_heavy,
        kind=kind,
        retention_until=retention_until,
    )

    session.add(artifact)
    await session.flush()
    await session.refresh(artifact)
    return artifact