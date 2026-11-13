# Образ приложения GlISt (api и worker; команда задаётся per-service в docker-compose.yml).
# База пинится по digest — Принцип II; дайджест получен через Docker Hub Registry API 2026-10-03.
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

# uv той же версии, что зафиксирована в backend/uv.lock (см. `uv --version` в README).
RUN pip install --no-cache-dir uv==0.12.19

# Runtime-библиотеки wheels: rasterio (libexpat) и numpy/scipy/GDAL (libgomp).
# В python:*-slim их нет, без них импорт rasterio падает с ImportError.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libexpat1 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала только манифест: слой зависимостей кешируется между сборками.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src

# Alembic нужен сервису migrate (docker-compose.yml): конфиг и скрипты миграций.
COPY alembic.ini ./
COPY alembic ./alembic

# uv настроен с `package = false`, проект не устанавливается в venv.
# Пакет glist лежит в /app/src — добавляем его в PYTHONPATH, иначе
# uvicorn/arq не найдут модули glist.*.
ENV PYTHONPATH="/app/src"
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# По умолчанию — API; worker переопределяет команду в docker-compose.yml:
#   api:    uvicorn glist.main:app --host 0.0.0.0 --port 8000
#   worker: arq glist.jobs.worker.WorkerSettings
CMD ["uvicorn", "glist.main:app", "--host", "0.0.0.0", "--port", "8000"]
