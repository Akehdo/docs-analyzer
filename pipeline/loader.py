"""Этап 1: проверить PDF, извлечь страницы и таблицы, сохранить происхождение."""

from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path
import re
from typing import Callable

import pdfplumber

from .models import Issue, LoadedPDF, PageData, TableData

MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
Progress = Callable[[int, int], None]


class PDFError(ValueError):
    """Ошибка, которую можно показать пользователю без traceback."""


def read_pdf_file(path: str | Path) -> bytes:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_BYTES:
            raise PDFError("PDF больше 50 МБ. Для MVP выберите меньший файл.")
        return path.read_bytes()
    except OSError as exc:
        raise PDFError(f"Не удалось прочитать файл: {path}") from exc


def _is_adilet_style(char: dict) -> bool:
    """Узкий профиль наблюдаемого водяного знака, не удаление всего серого текста."""
    color = char.get("non_stroking_color")
    matrix = char.get("matrix", (1, 0, 0, 1, 0, 0))
    angle = math.degrees(math.atan2(matrix[1], matrix[0]))
    return (
        char.get("object_type") == "char"
        and isinstance(color, (list, tuple)) and len(color) == 3
        and all(abs(a - b) < 0.005 for a, b in zip(color, (.902, .918, .933)))
        and 33 < angle < 37 and char.get("size", 0) > 24
    )


def _clean_text(text: str) -> str:
    # Only a standalone page counter is removed. Notes and ordinary numbers stay.
    return re.sub(r"(?m)^\s*\d+\s*/\s*\d+\s*$", "", text).strip()


def _inside(char: dict, box: tuple | list) -> bool:
    x = (char["x0"] + char["x1"]) / 2
    y = (char["top"] + char["bottom"]) / 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def load_pdf(data: bytes, filename: str, progress: Progress | None = None) -> LoadedPDF:
    """Не пишет на диск. Одинаковые байты дают одинаковый LoadedPDF."""
    if not data or not data.lstrip().startswith(b"%PDF-"):
        raise PDFError("Файл не содержит корректную сигнатуру PDF.")
    if len(data) > MAX_BYTES:
        raise PDFError("PDF больше 50 МБ.")
    pages: list[PageData] = []
    issues: list[Issue] = []
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            count = len(pdf.pages)
            if not 0 < count <= MAX_PAGES:
                raise PDFError("PDF должен содержать от 1 до 500 страниц.")
            metadata = {str(k): str(v) for k, v in pdf.metadata.items()}
            for number, page in enumerate(pdf.pages, 1):
                raw = page.extract_text() or ""
                watermark = [c for c in page.chars if _is_adilet_style(c)]
                watermark_text = "".join(c["text"] for c in watermark)
                verified = "Әділет" in watermark_text and "НЦПС" in watermark_text
                cleaned = page.filter(lambda obj: not (verified and _is_adilet_style(obj)))
                text = _clean_text(cleaned.extract_text() or "")
                tables = []
                try:
                    for index, table in enumerate(cleaned.find_tables(), 1):
                        rows = table.extract()
                        # Single-cell boxes often surround a heading, not a data table.
                        if len(rows) < 2 or max((len(row) for row in rows), default=0) < 2:
                            continue
                        tables.append(TableData(
                            table_id=f"p{number:03d}_t{index:02d}", page=number,
                            bbox=[round(v, 3) for v in table.bbox], rows=rows,
                            cell_boxes=[[list(box) if box else None for box in row.cells]
                                        for row in table.rows],
                        ))
                except Exception as exc:
                    issues.append(Issue("TABLE_EXTRACTION_FAILED", f"Таблицы не извлечены: {type(exc).__name__}", number))
                boxes = [t.bbox for t in tables]
                prose = cleaned.filter(lambda o: o.get("object_type") != "char" or
                                       not any(_inside(o, box) for box in boxes))
                prose_text = _clean_text(prose.extract_text() or "")
                blocks = []
                top = 0.0
                for table in tables:
                    if table.bbox[1] > top:
                        part = cleaned.crop((0, top, page.width, table.bbox[1]))
                        block_text = _clean_text(part.extract_text() or "")
                        if block_text:
                            blocks.append({"kind": "text", "top": top, "text": block_text})
                    blocks.append({"kind": "table", "top": table.bbox[1], "table_id": table.table_id})
                    top = max(top, table.bbox[3])
                if top < page.height:
                    tail = _clean_text(cleaned.crop((0, top, page.width, page.height)).extract_text() or "")
                    if tail:
                        blocks.append({"kind": "text", "top": top, "text": tail})
                if not text:
                    issues.append(Issue("NO_TEXT_LAYER", "Нет извлекаемого текста: пустая страница или скан. OCR пока не реализован.", number))
                pages.append(PageData(number, float(page.width), float(page.height), raw, text,
                                      prose_text, tables, len(watermark) if verified else 0, blocks))
                if progress:
                    progress(number, count)
                page.close()
    except PDFError:
        raise
    except Exception as exc:
        raise PDFError("Не удалось открыть/прочитать PDF. Проверьте, что файл не повреждён и не защищён паролем.") from exc
    if not any(page.text for page in pages):
        raise PDFError("В PDF нет текстового слоя. В этом MVP нужен текстовый PDF; OCR сканов пока не поддерживается.")
    return LoadedPDF(Path(filename.replace("\\", "/")).name, sha256(data).hexdigest(), len(data), pages, metadata, issues)
