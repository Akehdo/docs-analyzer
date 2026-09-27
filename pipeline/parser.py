"""Этап 2: базовый разбор таблиц 149/75 без клинических выводов и AI.

Сырые страницы и ячейки никогда не теряются. Неоднозначные продолжения
остаются в other_rows с предупреждением, а не присоединяются по догадке.
"""

import re
from typing import Any

from . import __version__
from .models import Issue, LoadedPDF, ParseResult, TableData

PROFILES = ("auto", "149", "75", "generic")


def compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def detect_profile(loaded: LoadedPDF) -> str:
    first = compact(loaded.pages[0].text)
    if re.search(r"ДСМ\s*[-–]\s*149/2020", first):
        return "149"
    if re.search(r"ДСМ\s*[-–]\s*75\b", first):
        return "75"
    return "generic"


def source_ref(table: TableData, row_index: int) -> dict[str, Any]:
    return {"page": table.page, "table_id": table.table_id, "row": row_index + 1,
            "quote": " | ".join(value or "" for value in table.rows[row_index])}


def _icd_fragments(text: str) -> list[str]:
    # Lexical excerpts only: no expansion, typo repair or coverage in this MVP.
    pattern = r"(?<![\w])(?:[A-ZАВЕКМНОРСТХ]\s*\d{2}(?:\.\d{1,2})?)(?:\s*[-–]\s*(?:[A-ZАВЕКМНОРСТХ]\s*)?\d{2}(?:\.\d{1,2})?)?"
    return list(dict.fromkeys(m.group() for m in re.finditer(pattern, text)))


def _metadata(loaded: LoadedPDF) -> dict:
    first = compact(loaded.pages[0].text)
    identity = re.search(r"от\s+(\d{1,2}\s+[а-яё]+\s+\d{4})\s+года\s+№\s*((?:ҚР\s*ДСМ\s*[-–]\s*)?\d+(?:/\d{4})?)", first)
    return {
        "order_number": identity.group(2) if identity else None,
        "order_date_raw": identity.group(1) if identity else None,
        "edition": None,
        "edition_note": "Редакция автоматически не назначается. Сноски и примечания сохранены отдельно.",
        "pdf_metadata": loaded.pdf_metadata,
    }


class Parser:
    def __init__(self, loaded: LoadedPDF, profile: str):
        self.loaded = loaded
        self.result = ParseResult("1.0", __version__, profile,
            {"filename": loaded.filename, "sha256": loaded.sha256,
             "size_bytes": loaded.size_bytes, "page_count": len(loaded.pages)},
            loaded.pages, _metadata(loaded), issues=list(loaded.issues))
        self.appendix: int | None = None
        self.scope = "order"
        self.chapter: str | None = None
        self.disease_class: str | None = None
        self.section_no: int | None = None
        self.section_name: str | None = None
        self.current149: dict | None = None
        self.context75: dict[str, Any] = {}
        self.context_refs75: dict[str, dict] = {}
        self.last_table_page = 0
        self.last_width = 0
        self.clause: dict | None = None

    def issue(self, code: str, message: str, table: TableData, row_index: int):
        self.result.issues.append(Issue(code, message, table.page, table.table_id, row_index + 1))

    def other(self, table: TableData, index: int, reason: str, kind: str = "unresolved"):
        self.result.other_rows.append({"row_id": f"{table.table_id}_r{index+1}",
            "kind": kind, "reason": reason, "appendix": self.appendix,
            "code_system": "nursing" if self.appendix == 4 else None,
            "cells": table.rows[index], "source_ref": source_ref(table, index)})

    def text(self, text: str, page: int):
        # Edition notices are evidence, never a fabricated new edition identifier.
        for match in re.finditer(r"Примечание\s+ИЗПИ!\s*(.*?\(вводится.*?\)\.?)", text, re.S):
            self.result.notes.append({"kind": "izpi", "page": page, "text": compact(match.group())})
        for match in re.finditer(r"(?m)^Сноска\.[\s\S]*?(?=\n\s*(?:Глава\s+\d|Приложение\b|\d+\.\s|Примечание\s+ИЗПИ)|\n\s*\n|\Z)", text):
            self.result.notes.append({"kind": "footnote", "page": page, "text": compact(match.group())})
        lines = text.splitlines()
        for i, line in enumerate(lines):
            appendix = re.fullmatch(r"\s*Приложение\s+([1-5])(?:\s+к\s+приказу)?\s*", line)
            if appendix:
                following = " ".join(lines[i:i+7])
                if "к Правилам" in following:
                    self.appendix = int(appendix.group(1)); self.scope = "rules_appendix"
                else:
                    self.appendix = None; self.scope = f"order_appendix_{appendix.group(1)}"
                self.current149 = None; self.context75.clear(); self.context_refs75.clear()
                self.clause = None; self.disease_class = None
            chapter = re.match(r"Глава\s+(\d+)\.?", line)
            if chapter:
                self.chapter = compact(line); self.scope = "rules"; self.clause = None
            if self.result.profile == "149" and self.scope in ("order", "rules"):
                item = re.match(r"^(\d{1,3})\.\s+(.+)", line)
                if item:
                    self.clause = {"clause_id": f"{self.scope}_p{page}_{item.group(1)}",
                        "scope": self.scope, "chapter": self.chapter, "item_no": item.group(1),
                        "text": line, "source_pages": [page]}
                    self.result.clauses.append(self.clause)
                elif self.clause and line.strip():
                    self.clause["text"] += "\n" + line
                    if page not in self.clause["source_pages"]:
                        self.clause["source_pages"].append(page)

    def table(self, table: TableData):
        width = max((len(row) for row in table.rows), default=0)
        contiguous = table.page <= self.last_table_page + 1 and width == self.last_width
        if not contiguous:
            self.current149 = None
            self.context75.clear(); self.context_refs75.clear()
        for index, raw in enumerate(table.rows):
            cells = [compact(v) for v in raw]
            if self.result.profile == "generic":
                self.other(table, index, "Универсальное извлечение таблицы", "generic")
            elif self.result.profile == "75":
                self.row75(table, index, raw, cells)
            elif self.appendix in (4, 5):
                self.other(table, index, "Приложение 4: сестринская классификация" if self.appendix == 4
                           else "Приложение 5: сегментация ПУЗ", "nursing" if self.appendix == 4 else "segmentation")
            elif width == 8 and self.appendix in (1, 2, 3):
                self.row149(table, index, raw, cells)
            else:
                self.other(table, index, "Неизвестный формат или контекст таблицы")
                self.issue("UNKNOWN_TABLE", "Таблица сохранена, но профиль её не распознал.", table, index)
        self.last_table_page = table.page; self.last_width = width

    def row149(self, table: TableData, index: int, raw: list, cells: list[str]):
        cells += [""] * (8-len(cells))
        ref = source_ref(table, index)
        header_text = " ".join(cells[:5]).lower()
        if cells[0].startswith("№") or (not cells[0] and "осмотр смр" in header_text and "осмотр врачом" in header_text):
            self.other(table, index, "Шапка таблицы", "header"); return
        if cells == [str(i) for i in range(1, 9)]:
            self.other(table, index, "Номера колонок", "header"); return
        if cells[0] and not re.fullmatch(r"\d+(?:\.\d+)*\.?", cells[0]):
            self.disease_class = cells[0]; self.current149 = None
            self.other(table, index, "Класс заболеваний", "heading"); return
        subitems = re.findall(r"(?<!\d)(\d+\.\d+)\.?(?=\s)", cells[1])
        starts_item = bool(cells[0]) or (bool(subitems) and any(cells[2:5]))
        if starts_item:
            item_no = subitems[0] if len(subitems) == 1 else cells[0].rstrip(".")
            record = {"row_id": f"{table.table_id}_r{index+1}", "appendix": self.appendix,
                "appendix_path": f"order/annex1/rules/annex{self.appendix}",
                "item_no": item_no, "subitem_numbers": subitems,
                "disease_class": self.disease_class, "nosology": cells[1],
                "icd_raw_fragments": _icd_fragments(cells[1]),
                "periodicity_smr": cells[2], "periodicity_pmsp": cells[3],
                "periodicity_specialist": cells[4], "duration": cells[7], "exams": [],
                "source_refs": [ref], "parse_state": "parsed",
                "ukd_state": "not_extracted_in_mvp"}
            self.result.order149_rows.append(record); self.current149 = record
            if len(subitems) > 1:
                record["parse_state"] = "needs_review"
                self.issue("NESTED_ITEMS", "Несколько подпунктов сохранены общим блоком. Разделение нужно проверить.", table, index)
        elif self.current149:
            record = self.current149
            record["source_refs"].append(ref)
            # Across pages, nonempty cells may be fragments of the same long cell.
            for column, field in [(1, "nosology"), (2, "periodicity_smr"), (3, "periodicity_pmsp"),
                                  (4, "periodicity_specialist"), (7, "duration")]:
                if cells[column] and cells[column] != record[field]:
                    record[field] = compact(record[field] + " " + cells[column])
                    record["parse_state"] = "needs_review"
                    self.issue("TEXT_CONTINUATION", f"Продолжение поля {field}: проверьте склейку.", table, index)
            record["icd_raw_fragments"] = _icd_fragments(record["nosology"])
        else:
            self.other(table, index, "Продолжение без однозначной родительской записи")
            self.issue("ORPHAN_CONTINUATION", "Не найден предыдущий пункт 149.", table, index); return
        if cells[5] or cells[6]:
            self.current149["exams"].append({"name": cells[5], "freq": cells[6], "source_ref": ref})

    def row75(self, table: TableData, index: int, raw: list, cells: list[str]):
        nonempty = [c for c in cells if c]
        if not nonempty:
            self.other(table, index, "Пустая строка", "empty"); return
        first = cells[0]
        section = re.match(r"^([1-6])\.\s+(Лекарственные средства|Медицинские изделия)", first)
        if section:
            self.section_no = int(section.group(1)); self.section_name = first
            self.context75.clear(); self.context_refs75.clear(); self.disease_class = None
            self.other(table, index, "Раздел перечня", "section"); return
        if first.startswith("№") or "Код МКБ" in " ".join(cells[:2]):
            self.other(table, index, "Шапка таблицы", "header"); return
        if len(nonempty) == 1 and first and not re.fullmatch(r"\d+", first):
            self.disease_class = first; self.context75.clear(); self.context_refs75.clear()
            self.other(table, index, "Класс заболеваний", "heading"); return
        if len(cells) not in (6, 7) or self.section_no is None:
            self.other(table, index, "Неизвестная схема или отсутствует раздел 75")
            self.issue("UNKNOWN_TABLE", "Ожидалось 6 или 7 колонок внутри раздела 75.", table, index); return
        cells += [""] * (7-len(cells)); raw = raw + [None] * (7-len(raw))
        ref = source_ref(table, index)
        if first:
            if not re.fullmatch(r"\d+", first):
                self.other(table, index, "Нераспознанный номер пункта")
                self.issue("UNKNOWN_ITEM", "Номер пункта не распознан.", table, index); return
            self.context75.clear(); self.context_refs75.clear()
        fields = ("item_no", "icd_raw", "disease", "category", "indication_raw")
        inherited = {}; uncertain = False
        for column, field in enumerate(fields):
            if cells[column]:
                if (not first and index == 0 and field in self.context_refs75
                        and self.context_refs75[field]["page"] != table.page):
                    uncertain = True
                self.context75[field] = cells[column]; self.context_refs75[field] = ref
            elif field in self.context_refs75 and (raw[column] is None or
                    (index == 0 and self.context_refs75[field]["page"] != table.page)):
                inherited[field] = self.context_refs75[field]
                # None is the table extractor's merged-cell continuation marker.
                # A genuine empty cell on another page is only a hypothesis.
                if raw[column] == "" and self.context_refs75[field]["page"] != table.page:
                    uncertain = True
            else:
                self.context75[field] = ""
                self.context_refs75.pop(field, None)
        if not cells[5]:
            self.other(table, index, "Нет названия продукта; строка сохранена для проверки")
            self.issue("MISSING_PRODUCT", "Не удалось выделить продукт из строки 75.", table, index); return
        if not self.context75.get("item_no") or not self.context75.get("disease"):
            self.other(table, index, "Продукт без родительского пункта/заболевания")
            self.issue("ORPHAN_CONTINUATION", "Не найден контекст продукта 75.", table, index); return
        # Split only at the first comma; retain the complete product text as evidence.
        name, sep, form = cells[5].partition(",")
        row = {"row_id": f"{table.table_id}_r{index+1}", "section_no": self.section_no,
            "section_name": self.section_name, "disease_class": self.disease_class,
            "product_kind": "drug" if self.section_no in (1, 3, 5) else "device_or_nutrition",
            **self.context75, "product_raw": cells[5], "drug_name": name.strip(),
            "drug_form": form.strip() if sep else "", "atc_raw": cells[6],
            "icd_raw_fragments": _icd_fragments(self.context75.get("icd_raw", "")),
            "inherited_from": inherited, "source_refs": [ref],
            "parse_state": "needs_review" if uncertain else "parsed",
            "ukd_state": "not_extracted_in_mvp"}
        if uncertain:
            self.issue("PAGE_CONTINUATION", "Поля унаследованы через границу страницы; проверьте исходные ячейки.", table, index)
        if row["product_kind"] == "drug" and not row["atc_raw"]:
            row["parse_state"] = "needs_review"
            self.issue("MISSING_ATC", "У лекарственной строки отсутствует АТХ.", table, index)
        self.result.order75_rows.append(row)

    def run(self) -> ParseResult:
        for page in self.loaded.pages:
            lookup = {table.table_id: table for table in page.tables}
            for block in page.blocks:
                if block["kind"] == "text":
                    self.text(block["text"], page.number)
                else:
                    self.table(lookup[block["table_id"]])
        rows149 = self.result.order149_rows; rows75 = self.result.order75_rows
        expected = {(t.table_id, i+1) for p in self.loaded.pages for t in p.tables for i in range(len(t.rows))}
        accounted = {(r["source_ref"]["table_id"], r["source_ref"]["row"]) for r in self.result.other_rows}
        for record in rows149 + rows75:
            accounted.update((r["table_id"], r["row"]) for r in record["source_refs"])
        missing = expected - accounted
        if missing:
            self.result.issues.append(Issue("UNACCOUNTED_ROWS", f"Не учтено строк таблиц: {len(missing)}. Сырые ячейки сохранены."))
        if self.result.profile in ("149", "75") and not (rows149 or rows75):
            self.result.issues.append(Issue("NO_DOMAIN_ROWS", "Профиль не нашёл предметные строки. Текст и таблицы сохранены; проверьте профиль/формат PDF."))
        self.result.summary = {
            "pages": len(self.loaded.pages), "tables": sum(len(p.tables) for p in self.loaded.pages),
            "raw_table_rows": sum(len(t.rows) for p in self.loaded.pages for t in p.tables),
            "accounted_table_rows": len(expected & accounted),
            "order149_rows": len(rows149), "order75_rows": len(rows75),
            "other_rows": len(self.result.other_rows), "clauses": len(self.result.clauses),
            "notes": len(self.result.notes), "issues": len(self.result.issues),
            "sections75": sorted({r["section_no"] for r in rows75}),
            "watermark_chars_removed": sum(p.removed_watermark_chars for p in self.loaded.pages),
            "status": "needs_review" if self.result.issues else "parsed",
            "completeness": "not_verified",  # A successful extraction is not a completeness audit.
        }
        return self.result


def parse_pdf(loaded: LoadedPDF, profile: str = "auto", *, enrich=True) -> ParseResult:
    if profile not in PROFILES:
        raise ValueError(f"Неизвестный профиль: {profile}")
    result = Parser(loaded, detect_profile(loaded) if profile == "auto" else profile).run()
    if enrich:
        from .enrichment import enrich_rows
        result.enrichment = enrich_rows(result.order149_rows, result.order75_rows, result.pages)
        result.schema_version = '1.1'
    return result
