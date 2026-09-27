from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from xml.sax.saxutils import escape
import zipfile

from pipeline.erdb import load_erdb, ErdbRepository, SCHEMA
from pipeline.storage import save_erdb


def fixture_tables():
    return {
        'sp_section': [['CH1','Степень тяжести','Ауырлық'], ['CH2','Другая группа','Топ']],
        'sp_section_point': [['CHP1','Лёгкое течение','Жеңіл'], ['CHP3','Тяжелое течение','Ауыр']],
        'section_point_filter': [[1,'CH1','CHP1'],[2,'CH1','CHP3']],
        'section_diagnoses_filter': [[17506,'CH1','J45']],
    }


def make_xlsx(tables=None, headers=None):
    """Tiny OOXML fixture; production workbook remains read-only."""
    tables = fixture_tables() if tables is None else tables
    headers = SCHEMA if headers is None else headers
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(tables)+1)) + '</Types>')
        z.writestr('_rels/.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>' + ''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i,name in enumerate(tables,1)) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="{rel}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(tables)+1)) + '</Relationships>')
        for i, (name, rows) in enumerate(tables.items(),1):
            xml = []
            for number, values in enumerate([headers[name]] + rows,1):
                xml.append(f'<row r="{number}">' + ''.join(f'<c r="{chr(65+j)}{number}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>' for j,value in enumerate(values)) + '</row>')
            z.writestr(f'xl/worksheets/sheet{i}.xml', f'<worksheet xmlns="{ns}"><sheetData>{"".join(xml)}</sheetData></worksheet>')
    return output.getvalue()


class ErdbTests(unittest.TestCase):
    def test_chain_and_no_parent_inheritance(self):
        snapshot = load_erdb(make_xlsx(), 'ukd.xlsx')
        repo = ErdbRepository(snapshot)
        self.assertTrue(repo.has_link('J45', 'CHP3'))
        self.assertFalse(repo.has_link('J45.0', 'CHP3'))
        path = repo.paths_for_icd('J45')[1]
        self.assertEqual(path['diagnosis_link_id'], '17506')
        self.assertEqual(len(path['source_refs']), 4)
        self.assertEqual(repo.search_points('легкое')[0]['id'], 'CHP1')
        self.assertEqual(snapshot['summary']['unique_icd'], 1)
        self.assertTrue(any(i['code'] == 'GROUP_WITHOUT_ICD' for i in snapshot['issues']))

    def test_broken_duplicate_and_missing_keys_are_not_silently_joined(self):
        data = fixture_tables()
        data['sp_section_point'].append(['CHP3','Duplicate','Ауыр'])
        data['section_point_filter'] += [[3,'CH9','CHP1'],[4,'','CHP1'],[5,'CH1','CHP1']]
        s = load_erdb(make_xlsx(data), 'bad.xlsx')
        codes = {i['code'] for i in s['issues']}
        self.assertTrue({'DUPLICATE_ID','BROKEN_LINK','EMPTY_KEY','DUPLICATE_LINK'} <= codes)
        self.assertEqual(len(s['tables']['section_point_filter']), 5)
        self.assertFalse(ErdbRepository(s).has_link('J45','CHP3'))

    def test_names_and_icd_keep_raw_values(self):
        data = fixture_tables()
        data['sp_section_point'][0][1] = ' Лёгкое течение '
        data['sp_section_point'][1][1] = 'Легкое течение'
        data['section_diagnoses_filter'][0][2] = 'В18.1'
        s = load_erdb(make_xlsx(data), 'test.xlsx')
        self.assertEqual(s['tables']['sp_section_point'][0]['raw']['rus_name'], ' Лёгкое течение ')
        self.assertEqual(len(ErdbRepository(s).search_points('легкое течение')), 2)
        self.assertEqual(s['summary']['unique_icd'], 1)
        self.assertTrue(ErdbRepository(s).has_link('B18.1', 'CHP3'))
        self.assertTrue({'DUPLICATE_NAME','OUTER_WHITESPACE','ICD_CORRECTED'} <= {i['code'] for i in s['issues']})

    def test_unknown_code_and_formula_remain_in_audit(self):
        data = fixture_tables()
        data['section_diagnoses_filter'] += [[2,'CH1','J44.77'],[3,'CH1','=A1'],[4,'CH1','I21-I22']]
        data['sp_section_point'][0][2] = ''
        s = load_erdb(make_xlsx(data), 'test.xlsx')
        codes = {i['code'] for i in s['issues']}
        self.assertTrue({'ICD_NEEDS_REVIEW','FORMULA_OR_ERROR','EMPTY_NAME'} <= codes)
        self.assertEqual(len(s['tables']['section_diagnoses_filter']),4)

    def test_invalid_file_and_schema(self):
        for data in [b'', b'not xlsx', make_xlsx({})]:
            with self.assertRaises(ValueError): load_erdb(data,'bad.xlsx')
        headers = deepcopy(SCHEMA); headers['sp_section'] = ['id','id','kaz_name']
        with self.assertRaises(ValueError): load_erdb(make_xlsx(headers=headers),'bad.xlsx')

    def test_saved_snapshot_and_source_are_reproducible(self):
        data = make_xlsx(); s = load_erdb(data,'test.xlsx')
        self.assertEqual(s, load_erdb(data,'test.xlsx'))
        with tempfile.TemporaryDirectory() as tmp:
            folder = save_erdb(s, data, tmp)
            self.assertEqual((folder/'source.xlsx').read_bytes(), data)
            self.assertEqual(json.loads((folder/'erdb.json').read_text(encoding='utf-8')), s)
            with self.assertRaises(ValueError): save_erdb(s, b'wrong', tmp)


if __name__ == '__main__': unittest.main()
