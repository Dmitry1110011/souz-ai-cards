"""Исключения сервиса. Все они содержат сообщение, которое можно показать пользователю."""


class CardError(Exception):
    """Базовая ошибка сервиса."""


class ConfigError(CardError):
    """Не настроены переменные окружения."""


class InputError(CardError):
    """Входные данные непригодны для работы."""


class LLMError(CardError):
    """Ошибка при обращении к LLM API."""


class RateLimitedError(LLMError):
    """Лимит запросов API (429). retry_after — сколько секунд просит подождать сервер, если сказал."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GenerationError(CardError):
    """Модель не вернула пригодный ответ даже после повторных попыток."""
