import unittest
from pipeline.icd import IcdCatalog, default_catalog, normalize_icd, is_covered, contains, intersects


class IcdTests(unittest.TestCase):
    def test_alphabet_and_idempotence(self):
        a = normalize_icd('В 18.1; К74; Р27.1; а15; Е11')
        self.assertEqual(a['codes'], ['B18.1', 'K74', 'P27.1', 'A15', 'E11'])
        self.assertTrue(a['corrections'])
        self.assertEqual(normalize_icd(a['canonical'])['codes'], a['codes'])
        self.assertFalse(normalize_icd(a['canonical'])['corrections'])

    def test_ranges_only_real_catalog_codes(self):
        self.assertEqual(normalize_icd('I21-I22')['codes'], ['I21', 'I22'])
        self.assertEqual(normalize_icd('А15 – А19')['codes'], ['A15','A16','A17','A18','A19'])
        self.assertEqual(normalize_icd('C00-03')['codes'], ['C00', 'C01', 'C02', 'C03'])
        self.assertEqual(normalize_icd('J44.0-J44.9')['codes'], ['J44.0', 'J44.1', 'J44.8', 'J44.9'])
        for value in ['I22-I21', 'J44-J44.1', 'J44-J44.77', 'A00-Z99']:
            result = normalize_icd(value)
            if value == 'A00-Z99':
                self.assertTrue(len(result['codes']) > 1000)
            else:
                self.assertEqual(result['status'], 'needs_review')

    def test_examples_do_not_narrow_parent(self):
        r = normalize_icd('B18, включая В18.0, B18.1. B18.2, B18.8')
        self.assertEqual(r['codes'], ['B18'])
        self.assertEqual(len(r['included_examples']), 4)
        self.assertTrue(contains(r, 'B18.9'))
        self.assertTrue(any(c['rule'] == 'list_separator' for c in r['corrections']))

    def test_exclusions_and_coverage_direction(self):
        scoped = normalize_icd('E22 (исключая Е22.8), D35.2')
        self.assertEqual(scoped['status'], 'normalized')
        self.assertEqual(scoped['codes'], ['E22', 'D35.2'])
        self.assertEqual(scoped['excluded_codes'], ['E22.8'])
        self.assertTrue(contains(scoped, 'D35.2'))
        self.assertFalse(contains(scoped, 'E22.8'))
        self.assertEqual(normalize_icd('E23 (за исключением Е23.0), Q96.9')['codes'], ['E23', 'Q96.9'])
        r = normalize_icd('C00-C97, кроме C81-C96')
        self.assertFalse(contains(r, 'C90.0'))
        self.assertTrue(contains(r, 'C50.9'))
        self.assertIsNone(contains(normalize_icd('J44, кроме J44.0'), 'J44'))
        self.assertTrue(is_covered('J44.0', 'J44')['value'])
        self.assertFalse(is_covered('J44', 'J44.0')['value'])
        self.assertIsNone(is_covered('J44.77', 'J44')['value'])
        self.assertFalse(contains(normalize_icd('J44'), 'J45'))
        self.assertIsNone(contains(r, 'J44.77'))

    def test_expression_intersection(self):
        a = normalize_icd('J44, кроме J44.0')
        self.assertFalse(intersects(a, normalize_icd('J44.0')))
        self.assertTrue(intersects(a, normalize_icd('J44.1')))
        self.assertIsNone(intersects(a, normalize_icd('invalid')))

    def test_invalid_scope_and_syntax(self):
        for value in ['ZZZ', 'J44.77', 'J44.999', 'J44 ???', 'кроме J44', 'включая J44',
                      'J44, включая J45', 'J44, кроме J44.0, включая J44.1', 'J44 (кроме J44.0', 'J44)', 'J44 (кроме астмы), J45']:
            self.assertEqual(normalize_icd(value)['status'], 'needs_review', value)
        self.assertEqual(normalize_icd('J44, J44')['codes'], ['J44'])
        self.assertEqual(normalize_icd('  ')['status'], 'not_stated')
        self.assertEqual(normalize_icd('A01.2', code_system='nursing')['status'], 'not_applicable')
        self.assertEqual(normalize_icd('ХОБЛ J44', allow_prose=True)['codes'], ['J44'])
        self.assertEqual(normalize_icd('МКБ нет', allow_prose=True)['status'], 'needs_review')
        self.assertEqual(normalize_icd('J44 кроме астмы', allow_prose=True)['status'], 'needs_review')

    def test_catalog_integrity(self):
        for nodes in [{'bad': None}, {'J44': 'J45'}, {'J44': 'J44'}]:
            with self.assertRaises(ValueError):
                IcdCatalog({'version': 'test', 'nodes': nodes})
        c = IcdCatalog({'version': 'test', 'nodes': {'J44': None, 'J44.0': 'J44'}})
        self.assertTrue(is_covered('J44.0', 'J44', c)['value'])
        self.assertGreater(len(default_catalog().parents), 12000)


if __name__ == '__main__': unittest.main()
