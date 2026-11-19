"""FastAPI приложение GlISt (задача T036).

Инициализирует приложение, подключает роутеры (health, ws, queries, runs, exports)
и настраивает глобальный обработчик ошибок с форматом ``{"code": "...", "message": "..."}``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from glist.api import exports, health, previews, queries, runs, scenarios, ws

app = FastAPI(
    title="GlISt — геопространственная мультиагентная платформа",
    version="0.1.0",
    description="API для запуска и мониторинга геопространственного анализа",
)

# Flutter Web запускается на другом origin (localhost:<port>) — браузеру нужен CORS.
# Аутентификация отложена (заглушка get_current_user), cookie/credentials не нужны.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    # Без этого браузер не видит имя файла артефакта (PDF/GeoJSON).
    expose_headers=["Content-Disposition", "Content-Length"],
)

app.include_router(health.router)
app.include_router(ws.router)
app.include_router(queries.router)
app.include_router(runs.router)
app.include_router(exports.router)
app.include_router(previews.router)
app.include_router(scenarios.router)


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Глобальный обработчик необработанных исключений.

    Возвращает ошибку в унифицированном формате:
    ``{"code": "<имя_исключения>", "message": "<сообщение>"}``.

    Args:
        _request: Входящий HTTP-запрос.
        exc: Исключение, которое не было обработано роутером.

    Returns:
        JSON-ответ с кодом ошибки и сообщением.
    """
    return JSONResponse(
        status_code=500,
        content={
            "code": exc.__class__.__name__,
            "message": str(exc) or "Внутренняя ошибка сервера",
        },
    )