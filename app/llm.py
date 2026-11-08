"""Клиент LLM. Работает с любым OpenAI-совместимым API: OpenAI, Ollama, vLLM, llama.cpp server."""

from __future__ import annotations

import sys
import time
from typing import Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    RateLimitError,
)

from .config import Settings
from .errors import LLMError, RateLimitedError


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
            max_retries=2,  # SDK сам повторяет 429/5xx/сетевые сбои с паузой
        )
        self._json_mode = True
        self._sleep = time.sleep

    def complete(self, system: str, user: str) -> str:
        """Один запрос к модели. При лимите API (429) ждёт и повторяет, не сдаваясь сразу."""
        attempts = 0
        while True:
            try:
                return self._complete_once(system, user)
            except RateLimitedError as exc:
                if attempts >= self._s.rate_limit_retries:
                    raise
                attempts += 1
                wait = min(max(exc.retry_after or 20.0, 1.0), 90.0)
                print(
                    f"  лимит запросов API, жду {wait:.0f} с и повторяю ({attempts}/{self._s.rate_limit_retries})...",
                    file=sys.stderr,
                )
                self._sleep(wait)

    def _complete_once(self, system: str, user: str) -> str:
        try:
            return self._call(system, user)
        except BadRequestError as exc:
            # Не все серверы/модели справляются с режимом JSON (response_format).
            # Тогда отключаем его и просим JSON только промптом: ответ всё равно разбирается и проверяется кодом.
            msg = str(exc).lower()
            if self._json_mode and ("response_format" in msg or "json" in msg):
                self._json_mode = False
                return self._wrap(lambda: self._call(system, user))
            raise LLMError(f"API отклонил запрос: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

    def _wrap(self, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

    def _call(self, system: str, user: str) -> str:
        kwargs: dict = {
            "model": self._s.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._s.temperature is not None:
            kwargs["temperature"] = self._s.temperature
        if self._s.max_tokens:
            kwargs["max_tokens"] = self._s.max_tokens
        if self._s.reasoning_effort:
            kwargs["extra_body"] = {"reasoning_effort": self._s.reasoning_effort}
        if self._json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0] if resp.choices else None
        content = choice.message.content if choice else None
        if not content or not content.strip():
            reason = getattr(choice, "finish_reason", None)
            hint = ""
            if reason == "length":
                hint = " Ответ упёрся в лимит длины: увеличьте LLM_MAX_TOKENS или задайте LLM_REASONING_EFFORT=low."
            raise LLMError(f"Модель вернула пустой ответ (finish_reason={reason}).{hint}")
        return content

    @staticmethod
    def _translate(exc: Exception) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        if isinstance(exc, AuthenticationError):
            return LLMError("API отверг ключ (401/403). Проверьте LLM_API_KEY.")
        if isinstance(exc, RateLimitError):
            retry_after = None
            try:
                retry_after = float(exc.response.headers.get("retry-after"))
            except (AttributeError, TypeError, ValueError):
                pass
            return RateLimitedError(
                "Превышен лимит запросов API (429): подождите минуту или проверьте тариф и баланс.", retry_after
            )
        if isinstance(exc, NotFoundError):
            return LLMError("Модель или адрес API не найдены (404). Проверьте LLM_MODEL и LLM_BASE_URL.")
        if isinstance(exc, APITimeoutError):
            return LLMError("Превышено время ожидания ответа модели. Увеличьте LLM_TIMEOUT.")
        if isinstance(exc, APIConnectionError):
            return LLMError("Не удалось подключиться к API. Проверьте сеть и LLM_BASE_URL.")
        if isinstance(exc, APIStatusError):
            return LLMError(f"Ошибка API ({exc.status_code}): {exc.message}")
        return LLMError(f"Неожиданная ошибка при обращении к модели: {exc}")
