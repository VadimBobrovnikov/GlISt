# Сборка образа БД стенда: PostgreSQL 16 + PostGIS + pgvector.
# Официального образа с одновременно PostGIS и pgvector не существует, поэтому
# оба расширения ставятся из репозитория PGDG (bookworm-pgdg настроен в базе).
# 2026-10-08: база postgis/postgis:16-3.5 (Debian 11) перестала собираться — PGDG
# удалил репозиторий bullseye-pgdg (HTTP 404), официальные образы postgis для PG16
# остаются bullseye-based. База заменена на официальный postgres:16-bookworm:
# сервер установлен apt-пакетом, поэтому расширения из PGDG ложатся в его каталоги.
# Фактически ставится PostGIS 3.6.x и pgvector 0.8.x (версия PostGIS спекой не
# закреплена; PostgreSQL 16 закреплён конституцией).
# База пинится по digest — Принцип II (воспроизводимость); дайджест получен
# через Docker Hub Registry API 2026-10-08.
FROM postgres:16-bookworm@sha256:0ea6700a3b4f0ae6ce746519073558aed4d88a79d8d07622a9a644946c7319c4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-16-postgis-3 \
        postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/* \
    && ls /usr/share/postgresql/16/extension/postgis.control \
    && ls /usr/share/postgresql/16/extension/vector.control

