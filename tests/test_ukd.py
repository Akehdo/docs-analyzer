import unittest
from pipeline.ukd import extract_ukd_field, extract_row_ukd, number_list


def extract(raw):
    return extract_ukd_field(raw, field='indication_raw', row_id='r1', context='J45', source_refs=[{'page': 3}])


class UkdTests(unittest.TestCase):
    def test_functional_classes(self):
        r = extract('Стенокардия напряжения III-IV ФК')
        self.assertEqual([m['value'] for m in r['mentions']], [3,4])
        self.assertEqual(r['expression']['operator'], 'or')
        self.assertEqual(number_list('II и IV'), [2,4])
        for m in r['mentions']:
            self.assertEqual(r['raw'][slice(*m['span'])], m['raw'])
            self.assertEqual(m['source_refs'], [{'page': 3}])

    def test_dimensions_stay_separate(self):
        r = extract('III стадия и 2 степень и ФК IV')
        self.assertEqual({m['type'] for m in r['mentions']}, {'stage','degree','functional_class'})
        self.assertEqual(r['expression']['operator'], 'and')

    def test_frequency_and_severity(self):
        r = extract('1 раз в год (лёгкой степени), 1 раз в 6 месяцев (средней и тяжелой степени)')
        self.assertEqual([m['value'] for m in r['mentions']], ['mild','moderate','severe'])
        self.assertEqual(len(r['frequency_branches']), 2)
        self.assertEqual(len(r['frequency_branches'][1]['mention_ids']), 2)
        self.assertEqual(r['expression']['operator'], 'frequency_branches')

    def test_bpd_and_no_cartesian_product(self):
        r = extract('при лёгкой степени тяжести, при среднетяжёлой и тяжёлой степени тяжести')
        self.assertEqual([m['value'] for m in r['mentions']], ['mild','moderate_severe','severe'])
        c = extract('тип А, В, лёгкой, средней степени')
        self.assertEqual({m['value'] for m in c['mentions']}, {'A','B','mild','moderate'})
        self.assertEqual(c['expression']['operator'], 'unresolved')
        self.assertEqual(c['status'], 'needs_review')

    def test_exclusion_and_ambiguity(self):
        r = extract('III-IV ФК, кроме пациентов после операции')
        self.assertEqual(r['expression']['operator'], 'and')
        event = next(m for m in r['mentions'] if m['type'] == 'post_event')
        self.assertTrue(event['negated'])
        self.assertEqual(r['expression']['children'][1]['operator'], 'not')
        self.assertEqual(extract('не менее III стадии')['status'], 'needs_review')
        self.assertEqual(extract('IV-II ФК')['status'], 'needs_review')
        self.assertEqual(extract('после операции, инфаркта')['status'], 'needs_review')

    def test_universal_is_dimension_specific_and_blank_is_not_new_ukd(self):
        for text, dimension in [('Все стадии','stage'),('Все степени тяжести','severity'),('Все ФК','functional_class')]:
            r = extract(text)
            self.assertEqual(r['mentions'][0]['value'], 'any')
            self.assertEqual(r['mentions'][0]['type'], dimension)
        self.assertFalse(extract('')['mentions'])
        self.assertEqual(extract('После неэффективной терапии решение комиссии')['status'], 'needs_review')

    def test_stable_concepts_separate_occurrences_and_provenance(self):
        base = {'row_id': 'r1', 'source_refs': [{'page': 3}], 'indication_raw': 'III-IV ФК',
                'inherited_from': {'indication_raw': {'page': 2}}, 'category': 'Взрослые'}
        a = extract_row_ukd(base, '75', context='I20')
        b = extract_row_ukd({**base, 'row_id': 'r2'}, '75', context='I20')
        self.assertEqual(a['mentions'][0]['concept_id'], b['mentions'][0]['concept_id'])
        self.assertNotEqual(a['mentions'][0]['mention_id'], b['mentions'][0]['mention_id'])
        self.assertEqual(a['mentions'][0]['source_refs'], [{'page': 2}])
        self.assertEqual(a, extract_row_ukd(base, '75', context='I20'))


if __name__ == '__main__': unittest.main()
