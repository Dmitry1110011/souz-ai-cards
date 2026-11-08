"""Настройки из переменных окружения (и .env). Ключ в коде не хранится."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .errors import ConfigError


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    base_url: str | None
    timeout: float
    temperature: float | None  # None = не передавать параметр
    max_tokens: int = 4096
    reasoning_effort: str | None = None  # low | medium | high; только для «думающих» моделей
    verify: bool = True  # вторая проверка: модель-фактчекер ищет утверждения без опоры на данные
    rate_limit_retries: int = 3  # сколько раз ждать и повторять запрос при лимите API (429)


def load_settings() -> Settings:
    load_dotenv()

    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    base_url = os.getenv("LLM_BASE_URL") or None
    model = os.getenv("LLM_MODEL") or "gpt-4o-mini"

    if not api_key:
        if base_url:
            # Локальные серверы ключ не проверяют, но SDK требует непустую строку.
            api_key = "local"
        else:
            raise ConfigError(
                "Не задан LLM_API_KEY. Скопируйте .env.example в .env и укажите ключ "
                "(для Ollama/vLLM укажите ещё LLM_BASE_URL)."
            )

    try:
        timeout = float(os.getenv("LLM_TIMEOUT") or 60)
    except ValueError as exc:
        raise ConfigError("LLM_TIMEOUT должен быть числом.") from exc

    raw_temp = (os.getenv("LLM_TEMPERATURE") or "0.2").strip().lower()
    if raw_temp in {"off", "none"}:
        temperature = None
    else:
        try:
            temperature = float(raw_temp)
        except ValueError as exc:
            raise ConfigError("LLM_TEMPERATURE должен быть числом или 'off'.") from exc

    try:
        max_tokens = int(os.getenv("LLM_MAX_TOKENS") or 4096)
    except ValueError as exc:
        raise ConfigError("LLM_MAX_TOKENS должен быть целым числом.") from exc

    effort = (os.getenv("LLM_REASONING_EFFORT") or "").strip().lower() or None
    if effort not in {None, "low", "medium", "high"}:
        raise ConfigError("LLM_REASONING_EFFORT должен быть low, medium или high (или пустым).")

    verify = (os.getenv("LLM_VERIFY") or "1").strip().lower() not in {"0", "off", "false", "no", "нет"}

    try:
        rate_limit_retries = int(os.getenv("LLM_RATE_LIMIT_RETRIES") or 3)
    except ValueError as exc:
        raise ConfigError("LLM_RATE_LIMIT_RETRIES должен быть целым числом.") from exc

    return Settings(api_key, model, base_url, timeout, temperature, max_tokens, effort, verify, rate_limit_retries)
