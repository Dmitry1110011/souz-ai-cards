"""Представление карточки в Markdown (удобно копировать контент-менеджеру)."""

from __future__ import annotations

from .schemas import ProductCard


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(card: ProductCard) -> str:
    lines = [
        f"# {card.product_name}",
        f"Производитель: {card.manufacturer}",
        "",
        f"## SEO-заголовок ({len(card.seo_title)} симв.)",
        card.seo_title,
        "",
        f"## SEO-описание ({len(card.seo_description)} симв.)",
        card.seo_description,
        "",
        "## Ключевые характеристики",
    ]
    if card.key_specs:
        lines += ["| Характеристика | Значение |", "|---|---|"]
        lines += [f"| {_cell(s.name)} | {_cell(s.value)} |" for s in card.key_specs]
    else:
        lines.append("данных нет")

    lines += ["", "## Поисковые запросы"]
    lines += [f"- {q}" for q in card.search_queries]

    lines += ["", "## Для менеджера", "**Кому подходит:**"]
    lines += [f"- {a}" for a in card.commercial_block.audience]
    lines += ["", "**Какие задачи решает:**"]
    lines += [f"- {t}" for t in card.commercial_block.tasks]

    lines += ["", "## Требует уточнения"]
    lines += [f"- {c}" for c in card.needs_clarification] or ["- модель не нашла вопросов (проверьте характеристики вручную)"]

    if card.warnings:
        lines += ["", "## Предупреждения автопроверки"]
        lines += [f"- {w}" for w in card.warnings]

    return "\n".join(lines) + "\n"
