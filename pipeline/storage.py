"""Сохранение исходного PDF и результата в отдельный каталог запуска."""

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from .models import LoadedPDF, ParseResult


def json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def document_text(pages) -> str:
    return "\n\n".join(f"[СТРАНИЦА {p.number if hasattr(p, 'number') else p['number']}]\n{p.text if hasattr(p, 'text') else p['text']}" for p in pages)


def save_result(result: ParseResult | LoadedPDF, original: bytes, output: str | Path) -> Path:
    """Отдельный каталог запуска; manifest записывается последним как признак завершения.

    Не переименовываем каталог: в Windows наблюдатель файлов Streamlit может
    удерживать его открытым. Папка без корректного manifest считается неполной.
    """
    is_parsed = isinstance(result, ParseResult)
    payload = result.to_dict() if is_parsed else asdict(result)
    digest = result.document["sha256"] if is_parsed else result.sha256
    if sha256(original).hexdigest() != digest:
        raise ValueError("Исходный PDF не соответствует хэшу результата.")
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix=f"{digest[:12]}-", dir=root))
    result_name = "result.json" if is_parsed else "loaded.json"
    (destination / "source.pdf").write_bytes(original)
    (destination / result_name).write_text(json_text(payload), encoding="utf-8")
    (destination / "text.txt").write_text(document_text(result.pages), encoding="utf-8")
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "sha256": digest,
                "stage": "parse" if is_parsed else "load", "files": ["source.pdf", result_name, "text.txt"]}
    (destination / "manifest.json").write_text(json_text(manifest), encoding="utf-8")
    return destination


def save_enriched(payload: dict, original: bytes, output: str | Path) -> Path:
    # The JSON contract accepts page dictionaries as well as PageData instances.
    return save_result(ParseResult(**payload), original, output)


def save_erdb(snapshot: dict, original: bytes, output: str | Path) -> Path:
    digest = snapshot['sha256']
    if sha256(original).hexdigest() != digest:
        raise ValueError('Исходный XLSX не соответствует хешу снимка.')
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix=f'erdb-{digest[:12]}-', dir=root))
    (destination / 'source.xlsx').write_bytes(original)
    (destination / 'erdb.json').write_text(json_text(snapshot), encoding='utf-8')
    (destination / 'manifest.json').write_text(json_text({'created_at': datetime.now(timezone.utc).isoformat(),
        'stage': 'erdb', 'sha256': digest, 'files': ['source.xlsx', 'erdb.json']}), encoding='utf-8')
    return destination
