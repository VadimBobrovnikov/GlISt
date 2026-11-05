"""FastAPI приложение GlISt (задача T036).

Инициализирует приложение, подключает роутеры (health, ws) и настраивает
глобальный обработчик ошибок с форматом ``{"code": "...", "message": "..."}``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from glist.api import health, ws

app = FastAPI(
    title="GlISt — геопространственная мультиагентная платформа",
    version="0.1.0",
    description="API для запуска и мониторинга геопространственного анализа",
)

app.include_router(health.router)
app.include_router(ws.router)


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