"""Оркестрация: вход -> промпт -> LLM -> автопроверки (с повтором) -> карточка."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from .checks import DESC_MAX, DESC_MIN, check_draft, min_description_len, normalize_hyphens
from .errors import GenerationError
from .errors import LLMError
from .llm import LLMClient
from .prompts import SYSTEM_PROMPT, VERIFY_SYSTEM_PROMPT, build_user_prompt, build_verify_prompt
from .schemas import NO_DATA, CommercialDraft, Draft, KeySpec, ProductCard, ProductInput
from .validation import find_missing_fields

MAX_ATTEMPTS = 3


def parse_draft(raw: str) -> Draft:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("в ответе нет JSON-объекта")
    try:
        return Draft.model_validate(json.loads(raw[start : end + 1]))
    except json.JSONDecodeError as exc:
        raise ValueError(f"невалидный JSON: {exc.msg}") from exc
    except ValidationError as exc:
        raise ValueError("не совпадает со схемой: " + "; ".join(e["msg"] for e in exc.errors())) from exc


def _digit_ratio(text: str) -> float:
    return sum(ch.isdigit() for ch in text) / max(len(text), 1)


def fit_description(text: str, lo: int, hi: int) -> tuple[str, list[str]]:
    """Сокращает описание до hi символов, удаляя целые предложения из середины (первое и последнее остаются).

    Удаление предложения не может добавить фактов, поэтому это безопасная автоправка. Возвращает
    (текст, удалённые предложения); если уложиться в [lo, hi] нельзя, возвращает исходный текст.
    """
    if len(text) <= hi:
        return text, []
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    removed: list[str] = []
    while len(" ".join(sents)) > hi and len(sents) > 2:
        total = len(" ".join(sents))
        middle = range(1, len(sents) - 1)
        enough = [i for i in middle if lo <= total - len(sents[i]) - 1 <= hi]
        # Предпочитаем убирать «набор цифр» (значения уже есть в таблице), а не предложение о назначении прибора.
        i = (
            max(enough, key=lambda k: (_digit_ratio(sents[k]), -len(sents[k])))
            if enough
            else max(middle, key=lambda k: len(sents[k]))
        )
        removed.append(sents.pop(i))
    result = " ".join(sents)
    return (result, removed) if lo <= len(result) <= hi else (text, [])


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яё]{4,}", text.lower().replace("ё", "е"))}


def _already_covered(name: str, product: ProductInput) -> bool:
    """«Масса в кг» не нужна, если в specs уже есть «Масса, не более»: все значимые слова названия уже там встречаются."""
    mine = _stems(name)
    existing = set().union(*(_stems(k) for k in product.specs)) if product.specs else set()
    return bool(mine) and mine <= existing


def _cap(text: str) -> str:
    """Заглавная первая буква (в фиде тип прибора записан со строчной)."""
    return text[:1].upper() + text[1:]


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        item = " ".join(item.split())
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def _norm_text(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def verify_draft(draft: Draft, product: ProductInput, llm: LLMClient) -> tuple[list[str], str | None]:
    """Вторая проверка: модель-фактчекер цитирует фразы без опоры на данные.

    Возвращает (цитаты, сообщение_об_ошибке). Цитаты, которых нет в тексте, отбрасываются:
    фактчекер не должен сам выдумывать замечания. Сбой проверки не ломает генерацию.
    """
    # Блок для менеджера по заданию содержит разумные выводы из данных, поэтому здесь проверяется только
    # текст, который пойдёт на сайт: заголовок и описание.
    parts = [draft.seo_title, draft.seo_description]
    card_text = "\n".join(parts)
    try:
        raw = llm.complete(VERIFY_SYSTEM_PROMPT, build_verify_prompt(product, card_text))
    except LLMError as exc:
        return [], f"Проверка фактов не выполнена: {exc}"

    start, end = raw.find("{"), raw.rfind("}")
    try:
        items = json.loads(raw[start : end + 1]).get("unsupported", [])
    except (ValueError, AttributeError):
        return [], "Проверка фактов не выполнена: ответ фактчекера не удалось разобрать."
    if not isinstance(items, list):
        return [], None

    haystack = _norm_text(card_text)
    quotes: list[str] = []
    for q in items:
        if isinstance(q, str) and len(q.strip()) >= 8 and _norm_text(q) in haystack and q.strip() not in quotes:
            quotes.append(q.strip())
    return quotes, None


def build_card(draft: Draft, product: ProductInput, warnings: list[str]) -> ProductCard:
    """Значения характеристик берутся из входных данных по ключу, а не из ответа модели."""
    warnings = list(warnings)

    key_specs: list[KeySpec] = []
    used: set[str] = set()
    for name in draft.key_spec_names:
        name = name.strip()
        if name in product.specs and name not in used:
            key_specs.append(KeySpec(name=name, value=product.specs[name]))
            used.add(name)
        elif name not in product.specs:
            warnings.append(f"Модель назвала характеристику «{name}», которой нет во входных данных: пропущена.")

    for name in _dedupe(draft.expected_but_missing):
        if name not in product.specs and not _already_covered(name, product):
            key_specs.append(KeySpec(name=name, value=NO_DATA))

    clarification = [f"Нет поля: {m}" for m in find_missing_fields(product)]
    clarification += _dedupe(draft.needs_clarification)
    if len(draft.seo_description) < DESC_MIN:
        clarification.append(
            f"SEO-описание короче {DESC_MIN} символов: во входных данных мало фактов, и набирать объём "
            "без выдумок нельзя. Дополните данные о товаре (описание, характеристики) и повторите генерацию."
        )

    return ProductCard(
        product_name=_cap(product.name) if product.name else NO_DATA,
        manufacturer=product.manufacturer or NO_DATA,
        seo_title=_cap(draft.seo_title.strip()),
        seo_description=draft.seo_description.strip(),
        key_specs=key_specs,
        search_queries=_dedupe(draft.search_queries),
        commercial_block=CommercialDraft(
            audience=_dedupe(draft.commercial.audience) or [NO_DATA],
            tasks=_dedupe(draft.commercial.tasks) or [NO_DATA],
        ),
        needs_clarification=clarification,
        warnings=warnings,
    )


def generate_card(
    product: ProductInput, llm: LLMClient, max_attempts: int = MAX_ATTEMPTS, verify: bool = False
) -> ProductCard:
    feedback: str | None = None
    last_draft: Draft | None = None
    last_problems: list[str] = []
    last_notes: list[str] = []
    last_error = ""
    verify_note: str | None = None

    for _ in range(max_attempts):
        raw = llm.complete(SYSTEM_PROMPT, build_user_prompt(product, feedback))
        try:
            draft = parse_draft(raw)
        except ValueError as exc:
            last_error = str(exc)
            feedback = f"Ответ не удалось разобрать: {last_error}. Верни только один JSON-объект по схеме."
            continue

        draft = draft.model_copy(update={
            "seo_title": normalize_hyphens(draft.seo_title),
            "seo_description": normalize_hyphens(draft.seo_description),
            "search_queries": [normalize_hyphens(q) for q in draft.search_queries],
            "needs_clarification": [normalize_hyphens(q) for q in draft.needs_clarification],
            "commercial": CommercialDraft(
                audience=[normalize_hyphens(a) for a in draft.commercial.audience],
                tasks=[normalize_hyphens(t) for t in draft.commercial.tasks],
            ),
        })
        notes: list[str] = []
        if len(draft.seo_description) > DESC_MAX:
            # Модель не умеет считать символы: сокращаем сами, не добавляя ни одного нового факта.
            trimmed, removed = fit_description(draft.seo_description, min_description_len(product), DESC_MAX)
            if removed:
                draft = draft.model_copy(update={"seo_description": trimmed})
                notes.append(
                    f"Автоправка: описание сокращено до {DESC_MAX} символов, удалено: "
                    + "; ".join(f"«{r}»" for r in removed)
                )

        problems = check_draft(draft, product)
        if verify:
            # Фактчекер работает вместе с остальными проверками: модель получает все замечания сразу.
            quotes, verify_note = verify_draft(draft, product, llm)
            if quotes:
                problems.append(
                    "Фактчекер нашёл утверждения, которых нет во входных данных: "
                    + "; ".join(f"«{q}»" for q in quotes)
                    + ". Удали или перепиши их, оставив только факты из данных (без выводов и оценок)."
                )
        if not problems:
            return build_card(draft, product, notes + ([verify_note] if verify_note else []))
        last_draft, last_problems, last_notes = draft, problems, notes
        feedback = "\n".join(f"- {p}" for p in problems)

    if last_draft is None:
        raise GenerationError(f"Модель {max_attempts} раза подряд вернула неразборчивый ответ ({last_error}).")

    # Лучше отдать черновик с явными предупреждениями, чем ничего: решает человек.
    return build_card(
        last_draft,
        product,
        last_notes + [f"Автопроверка не пройдена после {max_attempts} попыток: {p}" for p in last_problems],
    )
