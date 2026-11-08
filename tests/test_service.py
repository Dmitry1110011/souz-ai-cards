import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.checks import find_unknown_numbers
from app.errors import GenerationError, InputError
from app.generator import generate_card, parse_draft
from app.render import to_markdown
from app.validation import find_missing_fields, parse_product

ROOT = Path(__file__).resolve().parent.parent

PRODUCT = {
    "name": "Манометр показывающий DM 02",
    "manufacturer": "Манотомь",
    "specs": {
        "диапазон измерений": "0–1,6 МПа",
        "класс точности": "1,5",
        "присоединение": "G1/2",
        "диаметр корпуса": "100 мм",
    },
    "description": "Прибор предназначен для измерения избыточного давления.",
}


def make_draft(**over) -> dict:
    body = (
        "Манометр показывающий DM 02 производства Манотомь предназначен для измерения избыточного давления. "
        "Диапазон измерений составляет 0–1,6 МПа, класс точности 1,5. "
    )
    desc = (body * 6)[:900]
    d = {
        "seo_title": "Манометр показывающий DM 02 Манотомь, 0–1,6 МПа",
        "seo_description": desc,
        "key_spec_names": ["диапазон измерений", "класс точности"],
        "expected_but_missing": ["температура рабочей среды", "степень защиты", "материал корпуса"],
        "search_queries": ["манометр DM 02", "манометр 0–1,6 МПа", "купить манометр показывающий"],
        "commercial": {"audience": ["Инженеры КИПиА"], "tasks": ["Контроль избыточного давления"]},
        "needs_clarification": ["Уточнить степень защиты корпуса"],
    }
    d.update(over)
    return d


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user):
        self.calls.append(user)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)


# ---------- вход ----------
def test_invalid_json():
    with pytest.raises(InputError, match="Некорректный JSON"):
        parse_product("{не json")


def test_empty_and_wrong_type():
    with pytest.raises(InputError):
        parse_product("   ")
    with pytest.raises(InputError, match="объект"):
        parse_product("[1, 2]")


def test_nothing_to_work_with():
    with pytest.raises(InputError, match="не из чего"):
        parse_product({"name": "", "specs": {}, "description": None})


def test_specs_wrong_type():
    with pytest.raises(InputError, match="specs"):
        parse_product({"name": "X", "specs": ["a", "b"]})


def test_empty_values_count_as_missing():
    p = parse_product({"name": "X", "manufacturer": "  ", "specs": {"a": "", "b": None, "c": 5}})
    assert p.specs == {"c": "5"}
    assert find_missing_fields(p) == [
        "manufacturer (производитель)",
        "description (описание или фрагмент паспорта)",
    ]


# ---------- защита от выдумок ----------
def test_unknown_numbers_detected():
    p = parse_product(PRODUCT)
    d = parse_draft(json.dumps(make_draft(seo_title="Манометр DM 02 до 2,5 МПа, гарантия 24 мес"), ensure_ascii=False))
    assert set(find_unknown_numbers(d, p)) == {"2,5", "24"}


def test_known_numbers_and_b2b_pass():
    p = parse_product(PRODUCT)
    d = parse_draft(json.dumps(make_draft(search_queries=["a", "b b2b", "манометр G1/2 100 мм"]), ensure_ascii=False))
    assert find_unknown_numbers(d, p) == []


def test_spec_values_come_from_input_not_model():
    card = generate_card(parse_product(PRODUCT), FakeLLM(make_draft()))
    values = {s.name: s.value for s in card.key_specs}
    assert values["диапазон измерений"] == "0–1,6 МПа"
    assert values["температура рабочей среды"] == "данных нет"
    assert card.warnings == []


def test_invented_spec_name_is_dropped_with_warning():
    card = generate_card(parse_product(PRODUCT), FakeLLM(make_draft(key_spec_names=["класс точности", "вес"])))
    assert [s.name for s in card.key_specs if s.value != "данных нет"] == ["класс точности"]
    assert any("вес" in w for w in card.warnings)


# ---------- повторы и ошибки модели ----------
def test_retry_on_hallucinated_number_then_ok():
    bad = make_draft(seo_title="Манометр DM 02 до 2,5 МПа")
    llm = FakeLLM(bad, make_draft())
    card = generate_card(parse_product(PRODUCT), llm)
    assert len(llm.calls) == 2 and "2,5" in llm.calls[1]
    assert card.warnings == []


def test_returns_draft_with_warnings_if_checks_keep_failing():
    bad = make_draft(seo_title="x" * 90)
    card = generate_card(parse_product(PRODUCT), FakeLLM(bad), max_attempts=2)
    assert any("seo_title" in w for w in card.warnings)


def test_garbage_answer_raises():
    with pytest.raises(GenerationError):
        generate_card(parse_product(PRODUCT), FakeLLM("извините, не могу"), max_attempts=2)


def test_json_inside_markdown_fence_is_parsed():
    raw = "```json\n" + json.dumps(make_draft(), ensure_ascii=False) + "\n```"
    card = generate_card(parse_product(PRODUCT), FakeLLM(raw))
    assert card.seo_title.startswith("Манометр")


# ---------- неполные данные ----------
def test_incomplete_input_reports_missing_fields():
    p = parse_product({"name": "Датчик температуры", "specs": {"диапазон": "-50…+150 °C"}})
    draft = make_draft(
        seo_title="Датчик температуры",
        key_spec_names=["диапазон"],
        search_queries=["датчик температуры", "датчик температуры -50…+150 °C", "купить датчик температуры"],
    )
    # у -50…+150 числа 50 и 150 есть во входе, так что проверка проходит
    card = generate_card(p, FakeLLM(draft))
    assert card.manufacturer == "данных нет"
    assert any("manufacturer" in c for c in card.needs_clarification)
    assert any("description" in c for c in card.needs_clarification)


def test_markdown_render():
    md = to_markdown(generate_card(parse_product(PRODUCT), FakeLLM(make_draft())))
    assert "| диапазон измерений | 0–1,6 МПа |" in md
    assert "данных нет" in md
    assert "## Требует уточнения" in md


# ---------- скрипт фида ----------
FEED = """<?xml version="1.0" encoding="utf-8"?>
<yml_catalog date="2026-11-03 08:10"><shop><offers>
<offer id="1"><name>Манометр ДМ 05</name><vendor>Тест</vendor><url>https://x/1</url>
<description>&lt;p&gt;Измерение давления газов и жидкостей в технологических линиях.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Корпус сталь&lt;/li&gt;&lt;/ul&gt;</description>
<param name="Диапазон" unit="МПа">0-1,6</param><param name="Класс точности">1,5</param><param name="Присоединение">M20x1,5</param></offer>
<offer id="2"><name>Пустой</name></offer>
</offers></shop></yml_catalog>"""


def test_extract_script(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text(FEED, encoding="utf-8")
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "extract_products.py"), "--feed", str(feed),
         "--min-desc", "20", "--out", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))
    assert data["name"] == "Манометр ДМ 05"
    assert data["specs"]["Диапазон"] == "0-1,6 МПа"
    assert "<" not in data["description"]
    parse_product(data)  # формат совместим с сервисом


# ---------- клиент LLM ----------
def test_llm_falls_back_when_json_mode_rejected():
    try:
        import httpx2 as httpx  # openai 3.x
    except ImportError:
        import httpx  # openai 1.x/2.x
    from openai import BadRequestError

    from app.config import Settings
    from app.llm import OpenAICompatClient

    client = OpenAICompatClient(Settings("k", "m", None, 5.0, 0.2))
    calls = []

    class _Msg:
        content = '{"ok": 1}'

    class _Resp:
        choices = [type("C", (), {"message": _Msg})()]

    def fake_create(**kw):
        calls.append(kw)
        if "response_format" in kw:
            req = httpx.Request("POST", "http://x")
            raise BadRequestError(
                "Failed to generate JSON. Please adjust your prompt.",
                response=httpx.Response(400, request=req),
                body=None,
            )
        return _Resp()

    client._client.chat.completions.create = fake_create
    assert client.complete("s", "u") == '{"ok": 1}'
    assert "response_format" in calls[0] and "response_format" not in calls[1]


def test_empty_answer_error_mentions_finish_reason():
    from app.config import Settings
    from app.errors import LLMError
    from app.llm import OpenAICompatClient

    client = OpenAICompatClient(Settings("k", "m", None, 5.0, 0.2))
    sent = {}

    class _Msg:
        content = ""

    class _Resp:
        choices = [type("C", (), {"message": _Msg, "finish_reason": "length"})()]

    def fake_create(**kw):
        sent.update(kw)
        return _Resp()

    client._client.chat.completions.create = fake_create
    client._s = Settings("k", "m", None, 5.0, 0.2, 4096, "low")
    with pytest.raises(LLMError, match="LLM_MAX_TOKENS"):
        client.complete("s", "u")
    assert sent["max_tokens"] == 4096 and sent["extra_body"] == {"reasoning_effort": "low"}


# ---------- выдуманные оценки ----------
USER_FLUFF = (
    "Корпус прибора имеет диаметр 100 мм, что делает его компактным и удобным для установки в ограниченных "
    "пространствах. Благодаря простому подключению манометр легко интегрируется в системы, повышая безопасность."
)


def test_unsupported_claims_detected():
    from app.checks import find_unsupported_claims

    p = parse_product(PRODUCT)
    d = parse_draft(json.dumps(make_draft(seo_description=USER_FLUFF), ensure_ascii=False))
    found = set(find_unsupported_claims(d, p))
    assert {"компактный", "удобный", "простой", "лёгкий", "безопасность"} <= found


def test_claims_ok_if_present_in_input():
    from app.checks import find_unsupported_claims

    data = dict(PRODUCT, description="Компактный прибор для измерения давления.")
    d = parse_draft(json.dumps(make_draft(seo_description="Компактный манометр."), ensure_ascii=False))
    assert find_unsupported_claims(d, parse_product(data)) == []


def test_clean_text_has_no_false_claims():
    from app.checks import find_unsupported_claims

    d = parse_draft(json.dumps(make_draft(), ensure_ascii=False))
    assert find_unsupported_claims(d, parse_product(PRODUCT)) == []


def test_retry_when_fluff_is_written():
    llm = FakeLLM(make_draft(seo_description=USER_FLUFF * 3), make_draft())
    card = generate_card(parse_product(PRODUCT), llm)
    assert len(llm.calls) == 2 and "оценк" in llm.calls[1]
    assert card.warnings == []


# ---------- новые проверки заголовка, пропусков и служебных фраз ----------
def _problems(**over):
    from app.checks import check_draft

    d = parse_draft(json.dumps(make_draft(**over), ensure_ascii=False))
    return check_draft(d, parse_product(PRODUCT))


def test_title_without_manufacturer_is_flagged():
    assert any("производител" in p for p in _problems(seo_title="Манометр показывающий DM 02"))


def test_too_few_missing_specs_is_flagged():
    assert any("expected_but_missing" in p for p in _problems(expected_but_missing=[]))
    assert not any("expected_but_missing" in p for p in _problems(expected_but_missing=["масса", "вес", "гарантия"]))


def test_missing_specs_already_in_input_do_not_count():
    # «класс точности» уже есть в specs, значит это не «отсутствующая» характеристика
    probs = _problems(expected_but_missing=["класс точности", "диапазон измерений", "масса"])
    assert any("expected_but_missing" in p for p in probs)


def test_meta_phrase_is_flagged():
    text = make_draft()["seo_description"] + " В описании указано, что прибор применяется в отоплении."
    assert any("служебная фраза" in p for p in _problems(seo_description=text))


def test_clean_draft_passes_all_checks():
    assert _problems() == []


# ---------- фактчекер ----------
class _FailingSecondCall(FakeLLM):
    def complete(self, system, user):
        from app.errors import LLMError

        if len(self.calls) >= 1:
            self.calls.append(user)
            raise LLMError("лимит")
        return super().complete(system, user)


def test_verify_quote_triggers_rewrite():
    llm = FakeLLM(
        make_draft(),
        {"unsupported": ["производства Манотомь предназначен"]},
        make_draft(),
        {"unsupported": []},
    )
    card = generate_card(parse_product(PRODUCT), llm, verify=True)
    assert len(llm.calls) == 4
    assert "производства Манотомь предназначен" in llm.calls[2]  # цитата ушла в повторный запрос
    assert card.warnings == []


def test_verify_ignores_quotes_not_in_text():
    llm = FakeLLM(make_draft(), {"unsupported": ["этой фразы в тексте нет совсем"]})
    card = generate_card(parse_product(PRODUCT), llm, verify=True)
    assert len(llm.calls) == 2 and card.warnings == []


def test_verify_failure_is_warning_not_crash():
    card = generate_card(parse_product(PRODUCT), _FailingSecondCall(make_draft()), verify=True)
    assert any("Проверка фактов не выполнена" in w for w in card.warnings)


def test_verify_garbage_answer_is_warning():
    llm = FakeLLM(make_draft(), "не знаю")
    card = generate_card(parse_product(PRODUCT), llm, verify=True)
    assert any("не удалось разобрать" in w for w in card.warnings)


def test_persistent_unsupported_claims_end_as_warning():
    llm = FakeLLM(make_draft(), {"unsupported": ["производства Манотомь предназначен"]})
    card = generate_card(parse_product(PRODUCT), llm, verify=True, max_attempts=2)
    assert any("Фактчекер" in w and "производства Манотомь" in w for w in card.warnings)


def test_description_указывает_is_meta_phrase():
    text = make_draft()["seo_description"] + " Описание указывает, что прибор применяется в отоплении."
    assert any("служебная фраза" in p for p in _problems(seo_description=text))


# ---------- бедные данные и фактчекер вместе с остальными проверками ----------
def test_thin_input_accepts_shorter_description_and_flags_it():
    short = make_draft()["seo_description"][:450]
    card = generate_card(parse_product(PRODUCT), FakeLLM(make_draft(seo_description=short)))
    assert card.warnings == []
    assert any("короче 800" in c for c in card.needs_clarification)


def test_rich_input_still_requires_800():
    from app.checks import check_draft

    rich = dict(PRODUCT, description="Назначение прибора. " * 60)  # много входного текста
    d = parse_draft(json.dumps(make_draft(seo_description="x" * 450), ensure_ascii=False))
    assert any("seo_description" in p for p in check_draft(d, parse_product(rich)))


def test_verifier_runs_even_if_cheap_checks_fail():
    bad = make_draft(seo_description=USER_FLUFF * 3)  # слова-оценки -> проблема в дешёвой проверке
    llm = FakeLLM(bad, {"unsupported": []}, make_draft(), {"unsupported": []})
    card = generate_card(parse_product(PRODUCT), llm, verify=True)
    assert len(llm.calls) == 4 and card.warnings == []


def test_claim_feedback_contains_the_sentence():
    llm = FakeLLM(make_draft(seo_description=USER_FLUFF * 3), make_draft())
    generate_card(parse_product(PRODUCT), llm)
    assert "компактным и удобным" in llm.calls[1]  # в повторный запрос ушла цитата, а не только слово


def test_extract_keeps_type_prefix_and_ignores_service_params(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text(
        """<yml_catalog><shop><offers>
<offer id="1"><name>МК110</name><typePrefix>модуль ввода/вывода</typePrefix><vendor>ОВЕН</vendor>
<param name="Артикул">1</param><param name="Код товара">2</param></offer>
<offer id="2"><name>ТРМ1</name><typePrefix>измеритель</typePrefix><vendor>ОВЕН</vendor>
<param name="Артикул">3</param><param name="Питание">24 В</param><param name="Вход">ТСМ</param></offer>
</offers></shop></yml_catalog>""",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "extract_products.py"), "--feed", str(feed), "--richest", "5"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("id=")]
    assert lines[0].startswith("id=2") and "характеристик=2" in lines[0]  # служебные параметры не считаются
    assert "модуль ввода/вывода МК110" in r.stdout  # тип прибора добавлен к названию


def test_minimal_input_allows_very_short_description():
    from app.checks import min_description_len

    tiny = parse_product({"name": "источник питания АКИП-1186/1", "manufacturer": "АКИП"})
    assert min_description_len(tiny) == 60
    assert min_description_len(parse_product(PRODUCT)) == 60  # ~180 символов входного текста
    middle = dict(PRODUCT, description="Назначение прибора и области его применения. " * 8)  # ~500 символов
    assert min_description_len(parse_product(middle)) == 400
    rich = dict(PRODUCT, description="Назначение прибора и области его применения. " * 25)  # ~1300 символов
    assert min_description_len(parse_product(rich)) == 800


def test_extract_decodes_double_escaped_quotes(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text(
        '<yml_catalog><shop><offers><offer id="1"><name>комплект для &amp;quot;Коэффициент-3.3&amp;quot;</name>'
        "<vendor>НИИЭМП</vendor></offer></offers></shop></yml_catalog>",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "extract_products.py"), "--feed", str(feed), "--ids", "1", "--out", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))
    assert data["name"] == 'комплект для "Коэффициент-3.3"'


def test_title_and_name_start_with_capital_letter():
    p = parse_product(dict(PRODUCT, name="манометр показывающий DM 02"))
    card = generate_card(p, FakeLLM(make_draft(seo_title="манометр DM 02 Манотомь, 0–1,6 МПа")))
    assert card.product_name[0] == "М" and card.seo_title[0] == "М"


def test_all_problems_reach_the_model_together():
    """Регрессия: замечания фактчекера не должны затирать остальные (длина и т.п.)."""
    too_long = make_draft(seo_description="а" * 1300)
    llm = FakeLLM(too_long, {"unsupported": ["аааааааааа"]}, make_draft(), {"unsupported": []})
    card = generate_card(parse_product(PRODUCT), llm, verify=True)
    retry = llm.calls[2]
    assert "1300 символов" in retry and "Фактчекер" in retry
    assert card.warnings == []


def test_verifier_does_not_see_commercial_block():
    seen = {}

    class Spy(FakeLLM):
        def complete(self, system, user):
            if "<card_text>" in user:
                seen["text"] = user
            return super().complete(system, user)

    d = make_draft(commercial={"audience": ["УНИКАЛЬНАЯ_ЦЕЛЕВАЯ_АУДИТОРИЯ"], "tasks": ["x"]})
    generate_card(parse_product(PRODUCT), Spy(d, {"unsupported": []}), verify=True)
    assert "УНИКАЛЬНАЯ_ЦЕЛЕВАЯ_АУДИТОРИЯ" not in seen["text"]


# ---------- автосокращение, транслитерация, дефисы ----------
def test_fit_description_removes_middle_sentence_and_keeps_edges():
    from app.generator import fit_description

    first, last = "Первое предложение. ", "Последнее предложение."
    mid = ["Среднее номер один тут. ", "Среднее номер два длиннее, чем первое среднее. ", "Среднее номер три. "]
    text = first + "".join(mid) + last
    out, removed = fit_description(text, lo=40, hi=len(text) - 10)
    assert out.startswith("Первое") and out.endswith("Последнее предложение.")
    assert removed and len(out) <= len(text) - 10


def test_fit_description_gives_up_if_cannot_reach_range():
    from app.generator import fit_description

    text = "Раз. Два. Три."
    assert fit_description(text, lo=100, hi=200) == (text, [])  # короче lo
    assert fit_description("А. Б.", lo=1, hi=2)[1] == []  # всего два предложения: нечего удалять


def test_overlong_description_is_trimmed_automatically_with_note():
    sent = "Прибор предназначен для измерения избыточного давления жидкостей и газов. "
    long_desc = sent * 20  # ~1480 символов без новых фактов и чисел
    card = generate_card(parse_product(PRODUCT), FakeLLM(make_draft(seo_description=long_desc)))
    assert len(card.seo_description) <= 1200
    assert any("Автоправка" in w for w in card.warnings)


def test_transliteration_is_flagged():
    probs = _problems(seo_title="Регулятор TRM138В Манотомь")
    assert any("транслитерация" in p and "TRM138В" in p for p in probs)


def test_latin_words_from_input_are_fine():
    # "G1/2" и "DM" есть во входных данных
    assert not any("транслитерация" in p for p in _problems(search_queries=["манометр DM 02", "манометр G1/2", "купить манометр"]))


def test_typographic_hyphens_are_normalized():
    draft = make_draft(search_queries=["манометр DM 02", "манометр\u2011G1/2", "купить манометр"])
    card = generate_card(parse_product(PRODUCT), FakeLLM(draft))
    assert "\u2011" not in "".join(card.search_queries)


# ---------- лимит запросов (429) ----------
def _rate_limited_client(fail_times: int, retry_after=None):
    try:
        import httpx2 as httpx
    except ImportError:
        import httpx
    from openai import RateLimitError

    from app.config import Settings
    from app.llm import OpenAICompatClient

    client = OpenAICompatClient(Settings("k", "m", None, 5.0, 0.2, 4096, None, True, 3))
    sleeps, state = [], {"n": 0}

    class _Msg:
        content = '{"ok": 1}'

    class _Resp:
        choices = [type("C", (), {"message": _Msg, "finish_reason": "stop"})()]

    def fake_create(**kw):
        state["n"] += 1
        if state["n"] <= fail_times:
            headers = {"retry-after": str(retry_after)} if retry_after else {}
            raise RateLimitError(
                "slow down",
                response=httpx.Response(429, request=httpx.Request("POST", "http://x"), headers=headers),
                body=None,
            )
        return _Resp()

    client._client.chat.completions.create = fake_create
    client._sleep = sleeps.append
    return client, sleeps, state


def test_rate_limit_waits_and_retries():
    client, sleeps, state = _rate_limited_client(fail_times=2, retry_after=7)
    assert client.complete("s", "u") == '{"ok": 1}'
    assert sleeps == [7.0, 7.0] and state["n"] == 3


def test_rate_limit_gives_up_after_retries():
    from app.errors import RateLimitedError

    client, sleeps, _ = _rate_limited_client(fail_times=99)
    with pytest.raises(RateLimitedError, match="429"):
        client.complete("s", "u")
    assert len(sleeps) == 3  # три ожидания по умолчанию, затем ошибка


def test_commercial_block_hyphens_are_normalized():
    d = make_draft(commercial={"audience": ["целлюлозно\u2011бумажная промышленность"], "tasks": ["регулирование"]})
    card = generate_card(parse_product(PRODUCT), FakeLLM(d))
    assert "\u2011" not in card.commercial_block.audience[0]


# ---------- дубли пропусков, ATEX, «данных нет» в описании ----------
def test_missing_spec_already_present_is_not_listed():
    d = make_draft(expected_but_missing=["Диапазон в МПа", "Степень защиты", "Температура рабочей среды"])
    card = generate_card(parse_product(PRODUCT), FakeLLM(d))
    names = [k.name for k in card.key_specs if k.value == "данных нет"]
    assert "Диапазон в МПа" not in names  # «диапазон измерений» в specs уже есть
    assert "Степень защиты" in names and "Температура рабочей среды" in names


def test_missing_spec_with_new_word_is_kept():
    # «Материал корпуса»: слово «корпус» есть в specs, но «материал» нет, значит это действительно пропуск
    d = make_draft(expected_but_missing=["Материал корпуса", "масса", "гарантия"])
    card = generate_card(parse_product(PRODUCT), FakeLLM(d))
    assert "Материал корпуса" in [k.name for k in card.key_specs]


def test_atex_in_missing_names_is_flagged():
    probs = _problems(expected_but_missing=["Сертификация ATEX", "масса", "гарантия"])
    assert any("транслитерация" in p and "ATEX" in p for p in probs)


def test_absence_statement_in_description_is_flagged():
    text = make_draft()["seo_description"] + " В предоставленных данных отсутствует описание продукта."
    assert any("служебная фраза" in p for p in _problems(seo_description=text))


def test_fit_description_prefers_removing_number_heavy_sentence():
    from app.generator import fit_description

    first, last = "Прибор предназначен для регулирования температуры. ", "Применяется в химической промышленности."
    purpose = "Прибор работает как регулятор, сигнализатор и регистратор в технологических установках. "
    numbers = "Питание 90…253 В, 47…63 Гц, мощность 30 ВА, ток 200 мА, напряжение 24 В. "
    text = first + purpose + numbers + last
    out, removed = fit_description(text, lo=50, hi=len(text) - len(numbers) // 2)
    assert removed == [numbers.strip()] and "регулятор, сигнализатор" in out


def test_cli_expands_wildcards(tmp_path):
    from pathlib import Path

    from app.cli import expand_patterns

    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    got = expand_patterns([Path(str(tmp_path / "*.json"))])
    assert [g.name for g in got] == ["a.json", "b.json"]
    assert expand_patterns([tmp_path / "a.json"]) == [tmp_path / "a.json"]  # обычные пути не трогаем
