# Сборка образа БД стенда: PostgreSQL 16 + PostGIS 3.5 + pgvector.
# Официального образа с одновременно PostGIS и pgvector не существует, поэтому
# расширение pgvector доустанавливается из репозитория PGDG (настроен в базовом образе).
# База пинится по digest — Принцип II (воспроизводимость); дайджест получен через
# Docker Hub Registry API 2026-10-03.
FROM postgis/postgis:16-3.5@sha256:94146ac37bc61e2322f88016056c5920729cb8c64c8542ed590af8fc2abdac07

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/* \
    && ls /usr/share/postgresql/16/extension/vector.control
