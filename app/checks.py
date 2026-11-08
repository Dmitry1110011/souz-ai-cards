"""Автопроверки ответа модели. Главная — защита от выдуманных чисел."""

from __future__ import annotations

import re

from .schemas import Draft, ProductInput

TITLE_MAX = 70
DESC_MIN, DESC_MAX = 800, 1200
QUERIES_MIN, QUERIES_MAX = 3, 5
MISSING_MIN = 3
# Если во входных данных мало текста, 800 символов честно не набрать: не заставляем модель лить воду.
THIN_INPUT_CHARS = 700
DESC_MIN_THIN = 400
# Совсем мало входного текста (например, только название и артикул): честный текст получается коротким.
MINIMAL_INPUT_CHARS = 300
DESC_MIN_MINIMAL = 60

# Служебные фразы: текст карточки не должен ссылаться на «описание», «данные» и т.п.
_META = re.compile(
    r"в (?:исходном |входном )?описании (?:указан|сказан|говорит)\w*|\bописани\w* (?:указыва|говор|сообща|сказан)\w*|"
    r"согласно (?:описанию|данным)|по данным|во входных данных|в данных (?:указан|сказан)\w*|"
    r"отсутству\w+|не указан\w*|не предоставлен\w*|предоставленн\w+ данн\w+|нет данных|данных нет|не приведен\w*"
)


def _dedupe_names(names: list[str], product: ProductInput) -> list[str]:
    """Названия отсутствующих характеристик без повторов и без тех, что уже есть в specs."""
    have = {k.lower() for k in product.specs}
    out: list[str] = []
    for n in names:
        n = " ".join(n.split())
        if n and n.lower() not in have and n.lower() not in {o.lower() for o in out}:
            out.append(n)
    return out

# "1 000" -> "1000", чтобы разные записи одного числа совпадали
_GROUPED = re.compile(r"(?<![\d/.,])\d{1,3}(?:[   ]\d{3}(?!\d))+")
# Число в ответе: не считаем цифры внутри слов вроде "B2B" или "G1"
_OUT_NUM = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
# Число во входе: берём любые цифры, в том числе из "G1/2"
_IN_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _norm(text: str) -> str:
    return _GROUPED.sub(lambda m: re.sub(r"[   ]", "", m.group(0)), text)


def _num(token: str) -> str:
    return token.replace(",", ".")


def input_text(product: ProductInput) -> str:
    parts = [product.name or "", product.manufacturer or "", product.description or ""]
    for k, v in product.specs.items():
        parts.extend([k, v])
    return "\n".join(parts)


def input_numbers(product: ProductInput) -> set[str]:
    return {_num(t) for t in _IN_NUM.findall(_norm(input_text(product)))}


def find_unknown_numbers(draft: Draft, product: ProductInput) -> list[str]:
    """Числа из текстов модели, которых нет во входных данных."""
    known = input_numbers(product)
    texts = [
        draft.seo_title,
        draft.seo_description,
        *draft.search_queries,
        *draft.commercial.audience,
        *draft.commercial.tasks,
    ]
    unknown: list[str] = []
    for text in texts:
        for token in _OUT_NUM.findall(_norm(text)):
            if _num(token) not in known and token not in unknown:
                unknown.append(token)
    return unknown


# Оценочные слова, которые модель любит добавлять «для красоты». Если слова нет во входных данных,
# а в тексте оно есть — это выдуманное свойство товара.
_CLAIMS = {
    "надёжный": r"над[её]жн\w*",
    "компактный": r"компактн\w*",
    "удобный": r"удобн\w*",
    "простой": r"\bпрост(?:о|ой|ая|ое|ые|ого|ому|ым|ом|ых|ую|ота|ыми)\b",
    "лёгкий": r"\bл[её]гк\w*|\bлегко\b",
    "безопасность": r"безопасн\w*",
    "эффективный": r"эффективн\w*",
    "оптимальный": r"оптимальн\w*|идеальн\w*",
    "лучший": r"\bлучш\w*",
    "уникальный": r"уникальн\w*",
    "современный": r"современн\w*|инновацион\w*",
    "универсальный": r"универсальн\w*",
    "долговечный": r"долговечн\w*|износостойк\w*|\bпрочн\w*",
    "качественный": r"качественн\w*|высококачествен\w*",
}


def find_unsupported_claims(draft: Draft, product: ProductInput) -> list[str]:
    """Слова-оценки в тексте модели, которых нет во входных данных."""
    source = input_text(product).lower()
    texts = [
        draft.seo_title,
        draft.seo_description,
        *draft.search_queries,
        *draft.commercial.audience,
        *draft.commercial.tasks,
    ]
    out_text = "\n".join(texts).lower()
    found: list[str] = []
    for label, pattern in _CLAIMS.items():
        if re.search(pattern, out_text) and not re.search(pattern, source):
            found.append(label)
    return found


_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-"})
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+(?:[-./][A-Za-zА-Яа-яЁё0-9]+)*")
_LAT = re.compile(r"[A-Za-z]")
_CYR = re.compile(r"[А-Яа-яЁё]")


def normalize_hyphens(text: str) -> str:
    """Типографские дефисы и тире -> обычный дефис (в данных обычно простой «-»)."""
    return text.translate(_HYPHENS)


def find_foreign_spellings(draft: Draft, product: ProductInput) -> list[str]:
    """Слова латиницей и смесь алфавитов, которых нет во входных данных (транслитерация «от себя»)."""
    source = normalize_hyphens(input_text(product)).lower()
    texts = [draft.seo_title, draft.seo_description, *draft.search_queries,
             *draft.commercial.audience, *draft.commercial.tasks,
             *draft.expected_but_missing, *draft.needs_clarification]
    found: list[str] = []
    for text in texts:
        for token in _WORD.findall(normalize_hyphens(text)):
            letters = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", token)
            has_lat, has_cyr = bool(_LAT.search(token)), bool(_CYR.search(token))
            suspicious = (has_lat and has_cyr) or (has_lat and len(letters) >= 3)
            if suspicious and token.lower() not in source and token not in found:
                found.append(token)
    return found


def min_description_len(product: ProductInput) -> int:
    size = len(input_text(product))
    if size >= THIN_INPUT_CHARS:
        return DESC_MIN
    return DESC_MIN_THIN if size >= MINIMAL_INPUT_CHARS else DESC_MIN_MINIMAL


def claim_sentences(draft: Draft, product: ProductInput) -> list[str]:
    """Предложения, в которых есть слова-оценки без опоры на данные (для точной обратной связи модели)."""
    source = input_text(product).lower()
    active = [pat for pat in _CLAIMS.values() if not re.search(pat, source)]
    parts = [draft.seo_title, draft.seo_description, *draft.search_queries,
             *draft.commercial.audience, *draft.commercial.tasks]
    out: list[str] = []
    for part in parts:
        for sent in re.split(r"(?<=[.!?])\s+", part):
            low = sent.lower()
            if any(re.search(pat, low) for pat in active) and sent.strip() not in out:
                out.append(sent.strip()[:200])
    return out


def check_draft(draft: Draft, product: ProductInput) -> list[str]:
    """Список проблем. Пустой список = ответ принят."""
    problems: list[str] = []

    n = len(draft.seo_title)
    if n == 0 or n > TITLE_MAX:
        problems.append(f"seo_title содержит {n} символов, нужно от 1 до {TITLE_MAX}.")

    n = len(draft.seo_description)
    lo = min_description_len(product)
    if not lo <= n <= DESC_MAX:
        problems.append(
            f"seo_description содержит {n} символов, нужно от {lo} до {DESC_MAX} "
            f"({'добавь' if n < DESC_MIN else 'убери'} текст, не добавляя новых фактов)."
        )

    n = len(draft.search_queries)
    if not QUERIES_MIN <= n <= QUERIES_MAX:
        problems.append(f"search_queries содержит {n} запросов, нужно от {QUERIES_MIN} до {QUERIES_MAX}.")

    unknown = find_unknown_numbers(draft, product)
    if unknown:
        problems.append(
            "В тексте есть числа, которых нет во входных данных: "
            + ", ".join(unknown)
            + ". Удали их или замени формулировкой без чисел."
        )

    foreign = find_foreign_spellings(draft, product)
    if foreign:
        problems.append(
            "В тексте есть написания, которых нет во входных данных (транслитерация или смесь латиницы и кириллицы): "
            + ", ".join(foreign)
            + ". Названия, модели и производителя пиши ровно так, как они записаны во входных данных."
        )

    claims = find_unsupported_claims(draft, product)
    if claims:
        problems.append(
            "В тексте есть оценки, которых нет во входных данных: "
            + ", ".join(claims)
            + ". Фразы с такими оценками: "
            + "; ".join(f"«{x}»" for x in claim_sentences(draft, product))
            + ". Перепиши их, оставив только факты из данных."
        )

    mfr = (product.manufacturer or "").strip()
    if mfr and mfr.lower() not in draft.seo_title.lower() and len(draft.seo_title) + len(mfr) + 1 <= TITLE_MAX:
        problems.append(f"seo_title не содержит производителя «{mfr}», хотя он помещается в {TITLE_MAX} символов.")

    if len(_dedupe_names(draft.expected_but_missing, product)) < MISSING_MIN:
        problems.append(
            f"expected_but_missing содержит меньше {MISSING_MIN} характеристик. Назови {MISSING_MIN}–5 параметров, "
            "которые обычно указывают для такого прибора, но которых нет в specs (только названия)."
        )

    meta = _META.search(draft.seo_description.lower())
    if meta:
        problems.append(
            f"В seo_description есть служебная фраза «{meta.group(0)}»: пиши сам факт, без ссылок на «описание» и «данные». "
            "Не пиши, что чего-то нет или не указано: нехватку данных выноси в needs_clarification."
        )

    if product.specs and not draft.key_spec_names:
        problems.append("key_spec_names пуст, хотя во входных данных есть specs.")

    return problems
