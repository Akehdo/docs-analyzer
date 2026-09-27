from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.enrichment import enrich_payload
from pipeline.__main__ import main
from test_erdb import make_xlsx


def payload():
    ref = {'page': 1, 'table_id': 'p1t1', 'row': 1}
    cells = ['1', 'J45', 'Астма', 'Взрослые', 'Тяжелой степени', 'Препарат']
    row = {'row_id': '75:1', 'source_refs': [ref], 'icd_raw': 'J45', 'disease': 'Астма',
           'category': 'Взрослые', 'indication_raw': cells[4], 'parse_state': 'parsed'}
    return {'document': {}, 'pages': [{'tables': [{'table_id': 'p1t1', 'rows': [cells]}]}],
            'order149_rows': [], 'order75_rows': [row], 'other_rows': [{'classification': 'nursing'}],
            'summary': {'records': 1}, 'schema_version': '1.0'}


class EnrichmentTests(unittest.TestCase):
    def test_preserves_input_and_source_columns(self):
        original = payload(); before = deepcopy(original)
        result = enrich_payload(original)
        self.assertEqual(original, before)
        self.assertEqual(result['other_rows'], original['other_rows'])
        self.assertEqual(result['summary'], original['summary'])
        row = result['order75_rows'][0]
        self.assertEqual(row['icd_codes'], ['J45'])
        self.assertEqual(row['ukd']['mentions'][0]['source_refs'][0]['column'], 5)
        self.assertEqual(row['ukd']['mentions'][0]['source_refs'][0]['cell_quote'], 'Тяжелой степени')
        self.assertEqual(enrich_payload(result), result)

    def test_inherited_condition_points_to_parent_cell(self):
        original = payload(); row = original['order75_rows'][0]
        row['inherited_from'] = {'indication_raw': deepcopy(row['source_refs'][0])}
        row['source_refs'] = [{'page': 2, 'table_id': 'p2t1', 'row': 1}]
        row['parse_state'] = 'needs_review'
        result = enrich_payload(original); row = result['order75_rows'][0]
        ref = row['ukd']['mentions'][0]['source_refs'][0]
        self.assertEqual((ref['page'], ref['table_id'], ref['column']), (1, 'p1t1', 5))
        self.assertEqual(row['ukd_state'], 'needs_review')
        self.assertTrue(row['ukd']['upstream_review'])

    def test_invalid_payload_and_cli_status(self):
        with self.assertRaises(ValueError): enrich_payload({})
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(['normalize', 'В18.1']), 0)
            self.assertEqual(main(['normalize', 'J44.77']), 2)
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / 'reference.xlsx'; source.write_bytes(make_xlsx())
                output = Path(tmp) / 'runs'
                self.assertEqual(main(['erdb', str(source), '--output', str(output), '--icd', 'J45']), 0)
                saved = next(output.glob('*/erdb.json'))
                self.assertEqual(json.loads(saved.read_text(encoding='utf-8'))['summary']['unique_icd'], 1)
                self.assertEqual(main(['erdb', str(Path(tmp) / 'missing.xlsx')]), 1)
                invalid = Path(tmp) / 'result.json'; invalid.write_text('{}')
                self.assertEqual(main(['enrich', str(invalid)]), 1)


if __name__ == '__main__': unittest.main()
