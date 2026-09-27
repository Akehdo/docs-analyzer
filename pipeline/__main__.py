"""CLI: python -m pipeline load|parse document.pdf."""

import argparse
from pathlib import Path
import sys
import json

from .loader import PDFError, load_pdf, read_pdf_file
from .parser import PROFILES, parse_pdf
from .storage import save_result, save_enriched, save_erdb, json_text
from .icd import normalize_icd
from .enrichment import enrich_payload
from .erdb import load_erdb, ErdbRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Загрузка PDF и базовый парсер приказов 149/75")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("load", "parse"):
        cmd = sub.add_parser(command, help="Извлечь страницы/таблицы" if command == "load" else "Извлечь и разобрать документ")
        cmd.add_argument("pdf", type=Path, help="Путь к исходному PDF")
        cmd.add_argument("--output", type=Path, default=Path("data"), help="Каталог запусков (по умолчанию data)")
        if command == "parse":
            cmd.add_argument("--profile", choices=PROFILES, default="auto")
            cmd.add_argument("--strict", action="store_true", help="Код возврата 2, если есть замечания; результат всё равно сохраняется")
            cmd.add_argument('--no-enrich', action='store_true', help='Только базовый парсер, без нормализации и УКД')
    norm = sub.add_parser('normalize', help='Нормализовать выражение МКБ')
    norm.add_argument('expression')
    enrich = sub.add_parser('enrich', help='Обработать ранее сохранённый result.json без повторного чтения PDF')
    enrich.add_argument('result', type=Path)
    enrich.add_argument('--output', type=Path, default=Path('data'))
    erdb = sub.add_parser('erdb', help='Загрузить и проверить справочник XLSX')
    erdb.add_argument('xlsx', type=Path)
    erdb.add_argument('--output', type=Path, default=Path('data'))
    erdb.add_argument('--icd', help='Показать прямые связи указанного МКБ с группами и УКД')
    args = parser.parse_args(argv)
    try:
        if args.command == 'normalize':
            result = normalize_icd(args.expression)
            print(json_text(result))
            return 2 if result['status'] == 'needs_review' else 0
        if args.command == 'enrich':
            payload = enrich_payload(json.loads(args.result.read_text(encoding='utf-8')))
            original = read_pdf_file(args.result.parent / 'source.pdf')
            folder = save_enriched(payload, original, args.output)
            print(f'Сохранено: {folder}')
            print(json_text(payload['enrichment']))
            return 0
        if args.command == 'erdb':
            if args.xlsx.stat().st_size > 50 * 1024 * 1024:
                raise ValueError('XLSX больше 50 МБ.')
            data = args.xlsx.read_bytes()
            snapshot = load_erdb(data, args.xlsx.name)
            print(f'Сохранено: {save_erdb(snapshot, data, args.output)}')
            print(json_text(snapshot['summary']))
            if args.icd:
                print(json_text({'paths': ErdbRepository(snapshot).paths_for_icd(args.icd)}))
            return 0
        data = read_pdf_file(args.pdf)
        loaded = load_pdf(data, args.pdf.name,
            lambda done, total: print(f"\rСтраница {done}/{total}", end="", file=sys.stderr, flush=True))
        print(file=sys.stderr)
        result = parse_pdf(loaded, args.profile, enrich=not args.no_enrich) if args.command == "parse" else loaded
        folder = save_result(result, data, args.output)
        print(f"Сохранено: {folder}")
        if args.command == "parse":
            summary = result.summary
            print(f"Профиль: {result.profile}; страниц: {summary['pages']}; таблиц: {summary['tables']}")
            print(f"Строк 149: {summary['order149_rows']}; строк 75: {summary['order75_rows']}; замечаний: {summary['issues']}")
            print("Полнота предметного разбора автоматически не подтверждена. Исходные ячейки сохранены.")
            if result.enrichment:
                print(f"УКД: {result.enrichment['ukd_mentions']}; записей для проверки: {result.enrichment['review_rows']}")
            return 2 if args.strict and (result.issues or result.enrichment.get('review_rows')) else 0
        return 0
    except (PDFError, OSError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
