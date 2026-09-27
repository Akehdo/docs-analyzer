"""Не включаем исходные приказы в репозиторий. Проверяем локальные файлы явно."""
import argparse
import json
from pathlib import Path
import re

from pipeline.loader import load_pdf, read_pdf_file
from pipeline.parser import parse_pdf
from pipeline.storage import save_result


FILES = [
    ("75", "Об_утверждении_Перечня_лекарственных_средств_и_мед.pdf", 37, "75"),
    ("149A", "Об_утверждении_правил_организации_оказания_медицин.pdf", 97, "149"),
    ("149B", "Об_утверждении_правил_организации_оказания_медицин_1.pdf", 97, "149"),
]


def canonical(text):
    text = re.sub(r"Примечание\s+ИЗПИ!\s*.*?\(вводится.*?\)\.?", "", text, flags=re.S)
    return re.sub(r"\s+", "", text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/smoke"))
    args = parser.parse_args()
    report = {}; control_text = {}; control_notes = {}
    for label, name, page_count, profile in FILES:
        print(f"Проверка {label}…", flush=True)
        data = read_pdf_file(args.input_dir / name)
        loaded = load_pdf(data, name)
        result = parse_pdf(loaded)
        assert result.profile == profile, (label, result.profile)
        assert len(result.pages) == page_count, label
        assert result.summary["raw_table_rows"] == result.summary["accounted_table_rows"], label
        assert not any(issue.code == "UNACCOUNTED_ROWS" for issue in result.issues), label
        if profile == "75":
            assert result.summary["sections75"] == list(range(1, 7))
            assert any(row["product_kind"] == "device_or_nutrition" for row in result.order75_rows)
        else:
            assert {row["appendix"] for row in result.order149_rows} == {1, 2, 3}
            assert {row["kind"] for row in result.other_rows} >= {"nursing", "segmentation"}
            # Notes shift page breaks; compare the whole document, not page pairs.
            control_text[label] = canonical("\n".join(page.text for page in loaded.pages))
            control_notes[label] = sum(note["kind"] == "izpi" for note in result.notes)
        folder = save_result(result, data, args.output)
        assert (folder / "source.pdf").read_bytes() == data
        report[label] = {"filename": name, "sha256": loaded.sha256, "summary": result.summary,
                         "saved_to": str(folder)}
        print(json.dumps(report[label], ensure_ascii=False), flush=True)
    assert control_notes == {"149A": 2, "149B": 0}, control_notes
    assert control_text["149A"] == control_text["149B"], "Контрольные версии различаются после исключения ИЗПИ"
    report["control_pair"] = {"izpi_notes": control_notes, "same_text_except_izpi_and_whitespace": True}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Проверка трёх файлов завершена.", flush=True)


if __name__ == "__main__":
    main()
