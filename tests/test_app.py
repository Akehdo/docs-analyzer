from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from pipeline.storage import save_result, save_erdb
from pipeline.loader import load_pdf
from test_pipeline import make_pdf
from test_erdb import make_xlsx


class Upload(BytesIO):
    name = "sample.pdf"


class AppTests(unittest.TestCase):
    def test_multiple_pdfs_switching_and_partial_failure(self):
        first = Upload(make_pdf('First document')); first.name = 'first.pdf'
        second = Upload(make_pdf('Second document', blank_page=True)); second.name = 'second.pdf'
        invalid = Upload(b'not a pdf'); invalid.name = 'broken.pdf'
        selected = [first, invalid, second]
        with tempfile.TemporaryDirectory() as tmp:
            with patch('streamlit.file_uploader', side_effect=lambda label, **kw: None if kw.get('key') == 'erdb_upload' else selected) as uploader, \
                 patch('pipeline.storage.save_result', side_effect=lambda result, data, _: save_result(result, data, tmp)), \
                 patch('pipeline.loader.load_pdf', wraps=load_pdf) as loader:
                app = AppTest.from_file(str(Path(__file__).parents[1] / 'app.py'), default_timeout=15).run()
                self.assertTrue(uploader.call_args_list[0].kwargs['accept_multiple_files'])
                app.button[0].click().run()
                self.assertFalse(app.exception)
                self.assertEqual(len(list(Path(tmp).glob('*/result.json'))), 2)
                self.assertIn('broken.pdf', app.error[0].value)
                self.assertIn('First document', app.text_area[0].value)
                selector = next(s for s in app.selectbox if s.label == 'Результат документа')
                selector.set_value(1).run()
                self.assertFalse(app.exception)
                self.assertIn('Second document', app.text_area[0].value)
                app.number_input[0].set_value(2).run()
                next(s for s in app.selectbox if s.label == 'Результат документа').set_value(0).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.number_input[0].value, 1)
                self.assertIn('First document', app.text_area[0].value)
                self.assertEqual(loader.call_count, 3)
                selected.pop()
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.metric), 0)

    def test_erdb_upload_lookup_and_changed_input_reset(self):
        upload = Upload(make_xlsx()); upload.name = 'ukd.xlsx'
        with tempfile.TemporaryDirectory() as tmp:
            with patch('streamlit.file_uploader', side_effect=lambda label, **kwargs: upload if kwargs.get('key') == 'erdb_upload' else None), \
                 patch('pipeline.storage.save_erdb', side_effect=lambda s, data, _: save_erdb(s, data, tmp)):
                app = AppTest.from_file(str(Path(__file__).parents[1] / 'app.py'), default_timeout=15).run()
                next(b for b in app.button if b.label == 'Загрузить справочник').click().run()
                self.assertFalse(app.exception)
                self.assertTrue(list(Path(tmp).glob('*/erdb.json')))
                self.assertEqual(app.dataframe[0].value['point_id'].tolist(), ['CHP1', 'CHP3'])
                app.text_input[0].set_value('J45.0').run()
                self.assertFalse(app.exception)
                self.assertTrue(any('Прямой связи' in msg.value for msg in app.info))
            with patch('streamlit.file_uploader', return_value=None):
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.get('download_button')), 0)

    def test_empty_form(self):
        app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=15).run()
        self.assertFalse(app.exception)
        self.assertTrue(app.button[0].disabled)

    def test_upload_parse_downloads_and_change_resets_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            def save_to_temp(result, data, _output):
                return save_result(result, data, tmp)

            with patch("streamlit.file_uploader", side_effect=lambda label, **kw: None if kw.get('key') == 'erdb_upload' else [Upload(make_pdf())]), \
                 patch("pipeline.storage.save_result", side_effect=save_to_temp):
                # The first dataframe imports pandas/Arrow; allow a cold Windows start.
                app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=15).run()
                app.button[0].click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.metric[0].value, "1")
                self.assertEqual(app.metric[1].value, "1")
                self.assertEqual(len(app.get("download_button")), 2)
                self.assertTrue(list(Path(tmp).glob("*/result.json")))
            with patch("streamlit.file_uploader", side_effect=lambda label, **kw: None if kw.get('key') == 'erdb_upload' else [Upload(b"invalid")]):
                app.run()
                self.assertEqual(len(app.metric), 0)
                app.button[0].click().run()
                self.assertFalse(app.exception)
                self.assertTrue(app.error)


if __name__ == "__main__":
    unittest.main()
