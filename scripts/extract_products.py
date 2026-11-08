"""Достаёт товары из YML-фида (Yandex Market Language) в JSON формата тестового задания.

Фид — это XML-каталог: товары лежат в тегах <offer>. Файл большой, поэтому он читается потоком.

Примеры:
  python scripts/extract_products.py --feed feed.xml --list 30 --search манометр
  python scripts/extract_products.py --feed feed.xml --ids 12345 67890 --out data/products
  python scripts/extract_products.py --feed https://www.souz-pribor.ru/yml/any/feed.xml --search манометр --count 2 --out data/products
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import IO, Iterator


def open_feed(source: str) -> IO[bytes]:
    if re.match(r"https?://", source):
        req = urllib.request.Request(source, headers={"User-Agent": "product-card-prototype/1.0"})
        return urllib.request.urlopen(req, timeout=120)  # noqa: S310
    return open(source, "rb")


def strip_html(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<(br|/p|/li|/div)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def text_of(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


# Эти параметры есть почти у всех товаров, но о приборе они ничего не говорят.
SERVICE_PARAMS = {"артикул", "код товара"}


def real_spec_count(p: dict) -> int:
    return sum(1 for k in p["specs"] if k.strip().lower() not in SERVICE_PARAMS)


def clean(text: str) -> str:
    """В этом фиде кавычки закодированы дважды (&amp;quot;), поэтому после XML остаётся &quot;."""
    return html.unescape(text or "").strip()


def offer_to_product(offer: ET.Element) -> dict:
    name = clean(text_of(offer.find("name")))
    type_prefix = clean(text_of(offer.find("typePrefix")))
    if not name:  # "произвольный" тип описания: typePrefix + vendor + model
        name = " ".join(filter(None, (clean(text_of(offer.find(t))) for t in ("typePrefix", "vendor", "model"))))
    elif type_prefix and type_prefix.lower() not in name.lower():
        name = f"{type_prefix} {name}"  # «модуль ввода/вывода МК110-...» понятнее, чем один артикул

    specs: dict[str, str] = {}
    for p in offer.findall("param"):
        key, value, unit = clean(p.get("name")), clean(p.text), clean(p.get("unit"))
        if key and value:
            specs[key] = f"{value} {unit}".strip()

    return {
        "name": name,
        "manufacturer": clean(text_of(offer.find("vendor"))),
        "specs": specs,
        "description": strip_html(text_of(offer.find("description"))),
        "source_url": text_of(offer.find("url")),
        "offer_id": offer.get("id", ""),
    }


def iter_products(source: str) -> Iterator[dict]:
    with open_feed(source) as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag == "offer":
                yield offer_to_product(el)
                el.clear()  # не копим в памяти весь каталог


def is_good(p: dict, min_specs: int, min_desc: int) -> bool:
    return bool(p["name"]) and real_spec_count(p) >= min_specs and len(p["description"]) >= min_desc


def show_richest(source: str, n: int, search: str | None) -> int:
    """Просматривает весь фид и печатает n товаров с наибольшим числом содержательных характеристик."""
    import heapq

    heap: list[tuple[int, int, int, dict]] = []  # (характеристики, длина описания, порядок, товар)
    total = 0
    try:
        for p in iter_products(source):
            total += 1
            if not p["name"] or (search and search.lower() not in p["name"].lower()):
                continue
            item = (real_spec_count(p), len(p["description"]), total, p)
            if len(heap) < n:
                heapq.heappush(heap, item)
            else:
                heapq.heappushpop(heap, item)
    except (ET.ParseError, OSError) as exc:
        print(f"Не удалось прочитать фид: {exc}", file=sys.stderr)
        return 1

    print(f"Просмотрено товаров: {total}. Самые подробные:\n")
    for specs, desc, _, p in sorted(heap, reverse=True):
        print(f"id={p['offer_id']:<8} характеристик={specs:<3} описание={desc:<5} симв. | {p['manufacturer'] or '—'} | {p['name'][:90]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feed", required=True, help="путь к feed.xml или URL")
    ap.add_argument("--search", help="подстрока в названии (без учёта регистра)")
    ap.add_argument("--ids", nargs="*", help="id нужных offer")
    ap.add_argument("--count", type=int, default=2, help="сколько товаров взять (по умолчанию 2)")
    ap.add_argument("--list", type=int, metavar="N", help="только показать первые N подходящих кандидатов")
    ap.add_argument("--richest", type=int, metavar="N", help="просмотреть весь фид и показать N самых подробных товаров")
    ap.add_argument("--min-specs", type=int, default=3, help="минимум характеристик у кандидата")
    ap.add_argument("--min-desc", type=int, default=100, help="минимум символов в описании")
    ap.add_argument("--out", type=Path, help="папка для JSON-файлов")
    args = ap.parse_args()

    if args.richest:
        return show_richest(args.feed, args.richest, args.search)

    wanted = set(args.ids or [])
    found: list[dict] = []
    limit = args.list or args.count

    try:
        for p in iter_products(args.feed):
            if wanted:
                if p["offer_id"] not in wanted:
                    continue
            else:
                if not is_good(p, args.min_specs, args.min_desc):
                    continue
                if args.search and args.search.lower() not in p["name"].lower():
                    continue
            found.append(p)
            if (wanted and len(found) == len(wanted)) or (not wanted and len(found) >= limit):
                break
    except ET.ParseError as exc:
        print(f"Не удалось разобрать XML: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Не удалось открыть фид: {exc}", file=sys.stderr)
        return 1

    if not found:
        print("Подходящих товаров не найдено. Ослабьте --min-specs/--min-desc или поиск.", file=sys.stderr)
        return 1

    if args.list:
        for p in found:
            print(f"id={p['offer_id']:<12} specs={len(p['specs']):<3} {p['manufacturer'] or '—':<20} {p['name']}")
        return 0

    if not args.out:
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(found, 1):
        path = args.out / f"product_{i}_{p['offer_id'] or i}.json"
        path.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"записан {path}  ({p['name']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
