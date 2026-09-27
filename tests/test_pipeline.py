from dataclasses import asdict
from contextlib import redirect_stdout, redirect_stderr
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reportlab.pdfgen.canvas import Canvas

from pipeline.loader import PDFError, load_pdf
from pipeline.models import LoadedPDF, PageData, TableData
from pipeline.parser import parse_pdf
from pipeline.storage import save_result
from pipeline.__main__ import main


def make_pdf(text="Test PDF", watermark=False, blank_page=False):
    output = BytesIO(); c = Canvas(output)
    c.drawString(40, 790, text)
    c.drawString(40, 40, "1 / 1")
    # Real ruled table exercises pdfplumber, not just a mock extractor.
    for x in [40, 180, 320]: c.line(x, 600, x, 720)
    for y in [600, 640, 680, 720]: c.line(40, y, 320, y)
    c.drawString(50, 700, "Code"); c.drawString(190, 700, "Name")
    c.drawString(50, 660, "J45"); c.drawString(190, 660, "Asthma")
    c.drawString(50, 620, "J44"); c.drawString(190, 620, "COPD")
    if watermark:
        c.saveState(); c.translate(100, 300); c.rotate(35)
        c.setFillColorRGB(.902, .918, .933); c.setFont("Helvetica", 40)
        c.drawString(0, 0, "UNRELATED WATERMARK"); c.restoreState()
    c.showPage()
    if blank_page: c.showPage()
    c.save(); return output.getvalue()


def sample_document(profile, page_rows, intro=""):
    pages = []
    for number, rows in enumerate(page_rows, 1):
        table = TableData(f"p{number}_t1", number, [10, 100, 800, 500], rows,
                          [[None] * len(r) for r in rows])
        block_text = intro if number == 1 else ""
        pages.append(PageData(number, 842, 595, block_text, block_text, block_text, [table], 0,
            ([{"kind": "text", "text": block_text, "top": 0}] if block_text else []) +
            [{"kind": "table", "table_id": table.table_id, "top": 100}]))
    return LoadedPDF("sample.pdf", "a" * 64, 100, pages, {})


class LoaderTests(unittest.TestCase):
    def test_real_pdf_text_table_and_page_counter(self):
        data = make_pdf()
        loaded = load_pdf(data, "../sample.pdf")
        self.assertEqual(loaded.sha256, sha256(data).hexdigest())
        self.assertEqual(loaded.filename, "sample.pdf")
        self.assertIn("Test PDF", loaded.pages[0].text)
        self.assertNotIn("1 / 1", loaded.pages[0].text)
        self.assertEqual(loaded.pages[0].tables[0].rows[1], ["J45", "Asthma"])
        self.assertTrue(loaded.pages[0].tables[0].cell_boxes[0][0])
        self.assertNotIn("Asthma", loaded.pages[0].prose_text)

    def test_does_not_remove_unrecognized_light_rotated_text(self):
        page = load_pdf(make_pdf(watermark=True), "test.pdf").pages[0]
        self.assertEqual(page.removed_watermark_chars, 0)

    def test_invalid_or_corrupt_pdf(self):
        for content in [b"", b"not PDF", b"%PDF-1.7\ncorrupt"]:
            with self.subTest(content=content), self.assertRaises(PDFError):
                load_pdf(content, "test.pdf")

    def test_blank_pdf_is_not_successful_parse(self):
        stream = BytesIO(); c = Canvas(stream); c.showPage(); c.save()
        with self.assertRaisesRegex(PDFError, "текстового слоя"):
            load_pdf(stream.getvalue(), "scan.pdf")

    def test_mixed_blank_page_is_reported(self):
        loaded = load_pdf(make_pdf(blank_page=True), "mixed.pdf")
        self.assertEqual(len(loaded.pages), 2)
        self.assertEqual(loaded.issues[0].page, 2)

    def test_outputs_preserve_bytes_and_previous_run(self):
        data = make_pdf(); loaded = load_pdf(data, "test.pdf"); result = parse_pdf(loaded)
        with tempfile.TemporaryDirectory() as tmp:
            first = save_result(result, data, tmp); second = save_result(result, data, tmp)
            self.assertNotEqual(first, second)
            self.assertEqual((first / "source.pdf").read_bytes(), data)
            self.assertEqual((first / "result.json").read_bytes(), (second / "result.json").read_bytes())
            self.assertTrue((first / "manifest.json").exists())
            with self.assertRaises(ValueError): save_result(result, b"wrong", tmp)

    def test_failed_write_does_not_publish_manifest(self):
        data = make_pdf(); result = parse_pdf(load_pdf(data, "test.pdf"))
        with tempfile.TemporaryDirectory() as tmp:
            real_write = Path.write_text

            def fail_on_result(path, text, *args, **kwargs):
                if path.name == "result.json":
                    raise PermissionError("Simulated blocked write")
                return real_write(path, text, *args, **kwargs)

            with patch.object(Path, "write_text", fail_on_result), self.assertRaises(PermissionError):
                save_result(result, data, tmp)
            self.assertFalse(list(Path(tmp).glob("*/manifest.json")))
            self.assertEqual(len(list(Path(tmp).glob("*/source.pdf"))), 1)


class CLITests(unittest.TestCase):
    def test_load_and_strict_parse(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            source = Path(tmp) / "sample.pdf"
            source.write_bytes(make_pdf())
            output = Path(tmp) / "results"
            self.assertEqual(main(["load", str(source), "--output", str(output)]), 0)
            self.assertEqual(len(list(output.glob("*/loaded.json"))), 1)
            self.assertEqual(main(["parse", str(source), "--profile", "149", "--strict", "--output", str(output)]), 2)
            self.assertEqual(len(list(output.glob("*/result.json"))), 1)

    def test_missing_file_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
            self.assertEqual(main(["load", str(Path(tmp) / "missing.pdf")]), 1)
            self.assertIn("Не удалось прочитать", errors.getvalue())


class Order75Tests(unittest.TestCase):
    section = ["1. Лекарственные средства в рамках ГОБМП"] + [None]*6
    base = ["1", "I21-I22", "Ишемическая болезнь", "Взрослые", "III-IV ФК", "Клопидогрел, таблетка", "B01AC04"]

    def parse(self, rows):
        return parse_pdf(sample_document("75", rows), "75")

    def test_inheritance_category_change_and_multiple_atc(self):
        result = self.parse([[self.section, self.base,
            [None, None, None, "Дети", None, "Препарат, раствор, для инъекций", "J05AP01/J05AB04"]]])
        row = result.order75_rows[1]
        self.assertEqual(row["icd_raw"], "I21-I22")
        self.assertEqual(row["category"], "Дети")
        self.assertEqual(row["indication_raw"], "III-IV ФК")
        self.assertEqual(row["drug_form"], "раствор, для инъекций")
        self.assertEqual(row["atc_raw"], "J05AP01/J05AB04")
        self.assertEqual(row["inherited_from"]["icd_raw"]["row"], 2)
        self.assertEqual(len(result.order75_rows), 2)

    def test_new_item_does_not_inherit_diagnosis(self):
        result = self.parse([[self.section, self.base,
            ["2", None, None, None, None, "Other", "A01AA01"]]])
        self.assertEqual(len(result.order75_rows), 1)
        self.assertTrue(any(i.code == "ORPHAN_CONTINUATION" for i in result.issues))

    def test_missing_icd_does_not_crash_or_borrow_previous(self):
        result = self.parse([[self.section, self.base,
            ["2", "", "Болезнь без МКБ", "Все", "Все стадии", "ЛС", "A01AA01"],
            [None, None, None, None, None, "Другое ЛС", "A01AA02"]]])
        self.assertEqual(result.order75_rows[-1]["icd_raw"], "")

    def test_explicit_empty_category_is_not_merged_cell(self):
        result = self.parse([[self.section, self.base,
            [None, None, None, "", None, "Other", "A01AA01"]]])
        self.assertEqual(result.order75_rows[1]["category"], "")

    def test_section_reset_and_device_without_atc(self):
        result = self.parse([[self.section, self.base,
            ["2. Медицинские изделия ГОБМП"] + [None]*6,
            ["2", "J45", "Астма", "Дети", "Все", "Ингалятор", ""]]])
        device = result.order75_rows[-1]
        self.assertEqual(device["product_kind"], "device_or_nutrition")
        self.assertFalse(any(i.code == "MISSING_ATC" for i in result.issues))
        self.assertEqual(result.summary["sections75"], [1, 2])

    def test_cross_page_inheritance_is_flagged(self):
        result = self.parse([[self.section, self.base],
            [["", "", "", "", "", "Other", "A01AA01"]]])
        row = result.order75_rows[-1]
        self.assertEqual(row["icd_raw"], "I21-I22")
        self.assertEqual(row["parse_state"], "needs_review")
        self.assertEqual(row["inherited_from"]["icd_raw"]["page"], 1)


class Order149Tests(unittest.TestCase):
    intro = "Приложение 1\nк Правилам оказания медицинской помощи"
    base = ["8", "8.1. ХОБЛ J44", "1 раз в год", "2 раза в год", "Пульмонолог", "Спирография", "1 раз в год", "Пожизненно"]

    def test_footnote_stops_before_chapter_or_numbered_clause(self):
        for following in ["Глава 1. Общие положения", "1. Утвердить перечень"]:
            doc = sample_document("149", [[]], "Сноска. В редакции приказа\nот 01.01.2026 (вводится в действие).\n" + following)
            result = parse_pdf(doc, "149")
            self.assertEqual(result.notes[0]["text"], "Сноска. В редакции приказа от 01.01.2026 (вводится в действие).")

    def test_exams_and_cross_page_continuation(self):
        doc = sample_document("149", [[self.base], [[None]*5 + ["Пульсоксиметрия", "При приеме", None]]], self.intro)
        result = parse_pdf(doc, "149")
        self.assertEqual(len(result.order149_rows), 1)
        self.assertEqual(len(result.order149_rows[0]["exams"]), 2)
        self.assertEqual(result.order149_rows[0]["source_refs"][-1]["page"], 2)
        self.assertEqual(result.summary["raw_table_rows"], result.summary["accounted_table_rows"])

    def test_subitem_is_a_new_record(self):
        rows = [self.base, [None, "8.2. Астма J45", "1 раз в год", "1 раз в год", "Врач", "Спирография", "2 раза в год", "Пожизненно"]]
        result = parse_pdf(sample_document("149", [rows], self.intro), "149")
        self.assertEqual([r["item_no"] for r in result.order149_rows], ["8.1", "8.2"])
        self.assertEqual(result.order149_rows[1]["icd_raw_fragments"], ["J45"])

    def test_continuation_with_word_osmotr_is_not_a_header(self):
        rows = [self.base, [None, None, None, None, "Осмотр по показаниям", "Анализ", "Ежегодно", None]]
        result = parse_pdf(sample_document("149", [rows], self.intro), "149")
        self.assertEqual(len(result.order149_rows[0]["exams"]), 2)
        self.assertIn("Осмотр по показаниям", result.order149_rows[0]["periodicity_specialist"])

    def test_nursing_codes_are_not_icd(self):
        doc = sample_document("149", [[["A01.2", "Сестринский диагноз"]]], "Приложение 4\nк Правилам оказания помощи")
        result = parse_pdf(doc, "149")
        self.assertFalse(result.order149_rows)
        self.assertEqual(result.other_rows[0]["code_system"], "nursing")

    def test_new_appendix_resets_row_context(self):
        doc = sample_document("149", [[self.base], [[None]*5+["Анализ", "1 раз", None]]], self.intro)
        doc.pages[1].blocks.insert(0, {"kind": "text", "text": "Приложение 2\nк Правилам оказания помощи", "top": 0})
        result = parse_pdf(doc, "149")
        self.assertEqual(len(result.order149_rows[0]["exams"]), 1)
        self.assertTrue(any(i.code == "ORPHAN_CONTINUATION" for i in result.issues))

    def test_repeated_output_and_generic_accounting(self):
        doc = sample_document("generic", [[self.base, ["Heading", None]]])
        a = parse_pdf(doc, "generic"); b = parse_pdf(doc, "generic")
        self.assertEqual(asdict(a), asdict(b))
        self.assertEqual(a.summary["accounted_table_rows"], 2)


if __name__ == "__main__":
    unittest.main()
