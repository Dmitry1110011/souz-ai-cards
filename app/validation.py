"""Разбор и проверка входных данных. Никакого обращения к LLM."""

from __future__ import annotations

import json

from pydantic import ValidationError

from .errors import InputError
from .schemas import ProductInput


def parse_product(raw: str | bytes | dict) -> ProductInput:
    """Принимает JSON-строку или словарь, возвращает ProductInput либо бросает InputError."""
    if isinstance(raw, (str, bytes)):
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
        if not text.strip():
            raise InputError("Пустой ввод: ожидается JSON с данными о товаре.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputError(
                f"Некорректный JSON: {exc.msg} (строка {exc.lineno}, столбец {exc.colno})."
            ) from exc
    else:
        data = raw

    if not isinstance(data, dict):
        raise InputError("Ожидается JSON-объект {...}, а не список или значение.")

    try:
        product = ProductInput.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'вход'}: {e['msg']}" for e in exc.errors()
        )
        raise InputError(f"Неверная структура данных: {details}") from exc

    if not product.name and not product.specs and not product.description:
        raise InputError(
            "В данных нет ни названия, ни характеристик, ни описания: карточку не из чего собирать."
        )
    return product


def find_missing_fields(product: ProductInput) -> list[str]:
    """Какие из четырёх входных полей пусты. Определяется кодом, а не моделью."""
    missing = []
    if not product.name:
        missing.append("name (название товара)")
    if not product.manufacturer:
        missing.append("manufacturer (производитель)")
    if not product.specs:
        missing.append("specs (характеристики)")
    if not product.description:
        missing.append("description (описание или фрагмент паспорта)")
    return missing
