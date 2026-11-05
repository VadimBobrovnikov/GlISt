"""Трассировка LangFuse (задача T025).

Инициализация клиента LangFuse, создание трасс и спанов для узлов графа,
вызовов инструментов и вызовов LLM. При недоступности LangFuse выбрасывает
LangfuseError — вызывающий код должен перевести запуск в статус "ошибка"
(Принцип III Конституции).
"""

from __future__ import annotations

from typing import Any

from glist.config import get_settings

try:
    from langfuse import Langfuse
except ImportError as exc:  # pragma: no cover — langfuse обязателен в зависимостях
    raise ImportError("langfuse не установлен: uv add langfuse") from exc


class LangfuseError(Exception):
    """Ошибка взаимодействия с LangFuse (сеть, таймаут, недоступность).

    Выбрасывается всеми методами :class:`LangfuseTracer` при сетевых сбоях,
    таймаутах или если клиент не может быть создан. Граф должен поймать это
    исключение и перевести запуск в статус "ошибка" (Принцип III).
    """


class LangfuseTracer:
    """Клиент LangFuse для трассировки запусков анализа.

    Создаёт трассу на запуск (trace_id) и спаны для узлов графа,
    вызовов инструментов и вызовов LLM.

    Args:
        public_key: Открытый ключ LangFuse (по умолчанию из настроек).
        secret_key: Секретный ключ LangFuse (по умолчанию из настроек).
        host: Хост LangFuse (по умолчанию из настроек).
    """

    def __init__(
        self,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
    ) -> None:
        self._public_key = public_key
        self._secret_key = secret_key
        self._host = host
        self._client: Langfuse | None = None
        self._initialized = False

    def _get_client(self) -> Langfuse:
        """Возвращает клиент LangFuse, инициализируя его при первом вызове.

        Raises:
            LangfuseError: если клиент не может быть создан.
        """
        if self._initialized:
            assert self._client is not None
            return self._client

        settings = get_settings()
        try:
            self._client = Langfuse(
                public_key=self._public_key or settings.langfuse_public_key,
                secret_key=self._secret_key or settings.langfuse_secret_key.get_secret_value(),
                host=self._host or settings.langfuse_host,
            )
        except Exception as exc:
            self._initialized = True  # не пытаться снова
            raise LangfuseError(f"Не удалось инициализировать LangFuse: {exc}") from exc

        self._initialized = True
        return self._client

    def create_trace(
        self,
        trace_id: str,
        run_id: str,
        name: str,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> Any:
        """Создаёт трассу LangFuse для запуска анализа.

        Args:
            trace_id: Идентификатор трассы (из состояния графа).
            run_id: Идентификатор запуска analysis_run.
            name: Название трассы.
            user_id: Идентификатор пользователя (опционально).
            session_id: Идентификатор сессии (опционально).
            metadata: Дополнительные метаданные.
            tags: Теги трассы.

        Returns:
            Объект трассы LangFuse.

        Raises:
            LangfuseError: если LangFuse недоступен.
        """
        client = self._get_client()
        try:
            return client.trace(  # type: ignore[attr-defined]
                id=trace_id,
                name=name,
                user_id=user_id,
                session_id=session_id,
                metadata=metadata or {},
                tags=tags or [],
            )
        except Exception as exc:
            raise LangfuseError(f"Ошибка создания трассы LangFuse: {exc}") from exc

    def create_span(
        self,
        trace_id: str,
        span_id: str,
        name: str,
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Создаёт спан внутри трассы (узел графа, вызов инструмента).

        Args:
            trace_id: Идентификатор трассы.
            span_id: Идентификатор спана.
            name: Название спана.
            input: Входные данные спана.
            metadata: Дополнительные метаданные.

        Returns:
            Объект спана LangFuse.

        Raises:
            LangfuseError: если LangFuse недоступен.
        """
        client = self._get_client()
        try:
            return client.span(  # type: ignore[attr-defined]
                trace_id=trace_id,
                id=span_id,
                name=name,
                input=input or {},
                metadata=metadata or {},
            )
        except Exception as exc:
            raise LangfuseError(f"Ошибка создания спана LangFuse: {exc}") from exc

    def create_generation(
        self,
        trace_id: str,
        span_id: str | None,
        generation_id: str,
        name: str,
        model: str,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> Any:
        """Создаёт генерацию LLM внутри спана.

        Args:
            trace_id: Идентификатор трассы.
            span_id: Идентификатор родительского спана (или None для корня).
            generation_id: Идентификатор генерации.
            name: Название генерации.
            model: Название модели LLM.
            input: Входные данные (промпт).
            output: Выходные данные (ответ).
            usage: Статистика использования токенов.

        Returns:
            Объект генерации LangFuse.

        Raises:
            LangfuseError: если LangFuse недоступен.
        """
        client = self._get_client()
        try:
            return client.generation(  # type: ignore[attr-defined]
                trace_id=trace_id,
                id=generation_id,
                name=name,
                model=model,
                input=input or {},
                output=output or {},
                usage=usage or {},
                span_id=span_id,
            )
        except Exception as exc:
            raise LangfuseError(f"Ошибка создания генерации LangFuse: {exc}") from exc

    def flush(self) -> None:
        """Принудительно отправляет накопленные события в LangFuse.

        Raises:
            LangfuseError: если сброс не удался.
        """
        if self._client is not None:
            try:
                self._client.flush()
            except Exception as exc:
                raise LangfuseError(f"Ошибка сброса событий LangFuse: {exc}") from exc

    def shutdown(self) -> None:
        """Завершает работу клиента LangFuse.

        Raises:
            LangfuseError: если завершение не удалось.
        """
        if self._client is not None:
            try:
                self._client.shutdown()
            except Exception as exc:
                raise LangfuseError(f"Ошибка завершения LangFuse: {exc}") from exc


# Глобальный экземпляр трассировщика (один на процесс)
_tracer: LangfuseTracer | None = None


def get_tracer(
    public_key: str | None = None,
    secret_key: str | None = None,
    host: str | None = None,
) -> LangfuseTracer:
    """Возвращает глобальный экземпляр LangfuseTracer.

    При первом вызове создаёт трассировщик с указанными (или из настроек)
    учётными данными. Последующие вызовы возвращают тот же экземпляр.

    Args:
        public_key: Открытый ключ LangFuse.
        secret_key: Секретный ключ LangFuse.
        host: Хост LangFuse.

    Returns:
        Глобальный экземпляр :class:`LangfuseTracer`.
    """
    global _tracer
    if _tracer is None:
        _tracer = LangfuseTracer(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
    return _tracer
