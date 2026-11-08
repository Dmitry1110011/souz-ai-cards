"""CLI: python -m app.cli data/products/*.json --out examples/"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from .config import load_settings
from .errors import CardError
from .generator import generate_card
from .llm import OpenAICompatClient
from .render import to_markdown
from .validation import parse_product


def expand_patterns(paths: list[Path]) -> list[Path]:
    """Раскрывает маски вроде data/products/*.json (командная строка Windows этого не делает)."""
    out: list[Path] = []
    for p in paths:
        if not p.exists() and any(ch in str(p) for ch in "*?["):
            out.extend(Path(m) for m in sorted(glob.glob(str(p))))
        else:
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Черновик карточки товара по JSON с данными о приборе")
    ap.add_argument("files", nargs="+", type=Path, help="JSON-файлы с товарами")
    ap.add_argument("--out", type=Path, help="папка для результатов (.md и .json); без неё печать в консоль")
    ap.add_argument("--format", choices=["md", "json"], default="md", help="формат вывода в консоль")
    args = ap.parse_args(argv)
    args.files = expand_patterns(args.files)

    try:
        settings = load_settings()
        llm = OpenAICompatClient(settings)
    except CardError as exc:
        print(f"Ошибка настройки: {exc}", file=sys.stderr)
        return 2

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)

    failed = 0
    for path in args.files:
        try:
            product = parse_product(path.read_text(encoding="utf-8-sig"))
            card = generate_card(product, llm, verify=settings.verify)
        except (CardError, OSError) as exc:
            failed += 1
            print(f"[{path.name}] ОШИБКА: {exc}", file=sys.stderr)
            continue  # один плохой файл не останавливает остальные

        md, js = to_markdown(card), card.model_dump_json(indent=2)
        if args.out:
            (args.out / f"{path.stem}.md").write_text(md, encoding="utf-8")
            (args.out / f"{path.stem}.json").write_text(js, encoding="utf-8")
            print(f"[{path.name}] готово -> {args.out / (path.stem + '.md')}")
        else:
            print(md if args.format == "md" else js)
        if card.warnings:
            print(f"[{path.name}] есть предупреждения автопроверки, см. результат", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
