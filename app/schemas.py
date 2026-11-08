"""Схемы данных: вход, черновик от модели и итоговая карточка."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

NO_DATA = "данных нет"


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


class ProductInput(BaseModel):
    name: str | None = None
    manufacturer: str | None = None
    specs: dict[str, str] = Field(default_factory=dict)
    description: str | None = None

    @field_validator("name", "manufacturer", mode="before")
    @classmethod
    def _clean_str(cls, v: object) -> str | None:
        return _clean(v)

    @field_validator("description", mode="before")
    @classmethod
    def _clean_description(cls, v: object) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator("specs", mode="before")
    @classmethod
    def _clean_specs(cls, v: object) -> dict[str, str]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("specs должен быть объектом вида {\"характеристика\": \"значение\"}")
        cleaned: dict[str, str] = {}
        for key, val in v.items():
            k, s = _clean(key), _clean(val)
            if k and s:  # пустые значения считаем отсутствующими
                cleaned[k] = s
        return cleaned


class CommercialDraft(BaseModel):
    audience: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    """То, что возвращает модель. Значения характеристик модель НЕ возвращает."""

    seo_title: str
    seo_description: str
    key_spec_names: list[str] = Field(default_factory=list)
    expected_but_missing: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    commercial: CommercialDraft = Field(default_factory=CommercialDraft)
    needs_clarification: list[str] = Field(default_factory=list)


class KeySpec(BaseModel):
    name: str
    value: str


class ProductCard(BaseModel):
    product_name: str
    manufacturer: str
    seo_title: str
    seo_description: str
    key_specs: list[KeySpec]
    search_queries: list[str]
    commercial_block: CommercialDraft
    needs_clarification: list[str]  # что нужно уточнить у поставщика/менеджера
    warnings: list[str]  # результаты автопроверок
