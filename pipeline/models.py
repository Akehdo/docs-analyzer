"""Простые сериализуемые контракты. Номера страниц и строк начинаются с 1."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Issue:
    code: str
    message: str
    page: int | None = None
    table_id: str | None = None
    row: int | None = None


@dataclass
class TableData:
    table_id: str
    page: int
    bbox: list[float]
    rows: list[list[str | None]]
    cell_boxes: list[list[list[float] | None]]


@dataclass
class PageData:
    number: int
    width: float
    height: float
    raw_text: str
    text: str
    prose_text: str
    tables: list[TableData]
    removed_watermark_chars: int = 0
    # Non-table blocks preserve their position relative to tables.
    blocks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LoadedPDF:
    filename: str
    sha256: str
    size_bytes: int
    pages: list[PageData]
    pdf_metadata: dict[str, str]
    issues: list[Issue] = field(default_factory=list)


@dataclass
class ParseResult:
    schema_version: str
    parser_version: str
    profile: str
    document: dict[str, Any]
    pages: list[PageData]
    metadata: dict[str, Any]
    order149_rows: list[dict[str, Any]] = field(default_factory=list)
    order75_rows: list[dict[str, Any]] = field(default_factory=list)
    other_rows: list[dict[str, Any]] = field(default_factory=list)
    clauses: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    enrichment: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
