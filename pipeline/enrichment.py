"""Нормализация и УКД поверх результата парсера; исходные поля не меняются."""
from copy import deepcopy
from .icd import default_catalog, normalize_icd
from .ukd import extract_row_ukd


def enrich_rows(rows149, rows75, pages) -> dict:
    lookup = {}
    for page in pages:
        tables = page.tables if hasattr(page, 'tables') else page['tables']
        for table in tables:
            lookup[table.table_id if hasattr(table, 'table_id') else table['table_id']] = table.rows if hasattr(table, 'rows') else table['rows']
    concepts = set(); review_rows = 0; mentions = 0; icd_review = 0
    for profile, records in [('149', rows149), ('75', rows75)]:
        for row in records:
            mapping = ({'nosology': 1, 'periodicity_smr': 2, 'periodicity_pmsp': 3, 'periodicity_specialist': 4, 'duration': 7}
                       if profile == '149' else {'disease': 2, 'indication_raw': 4, 'category': 3, 'icd_raw': 1})
            field_refs = {}
            for field, column in mapping.items():
                source_refs = [row['inherited_from'][field]] if field in row.get('inherited_from', {}) else row['source_refs']
                found = []
                for ref in source_refs:
                    table_rows = lookup.get(ref['table_id'], [])
                    cells = table_rows[ref['row']-1] if 0 < ref['row'] <= len(table_rows) else []
                    if len(cells) > column and cells[column]:
                        found.append({**ref, 'column': column + 1, 'cell_quote': cells[column]})
                field_refs[field] = found or source_refs
            row['field_source_refs'] = field_refs
            raw = row.get('nosology', '') if profile == '149' else row.get('icd_raw', '')
            row['icd'] = normalize_icd(raw, allow_prose=profile == '149')
            row['icd_codes'] = row['icd']['codes']
            context = '|'.join(row['icd_codes']) + '|' + row.get('nosology', row.get('disease', '')).lower()
            row['ukd'] = extract_row_ukd(row, profile, context=context)
            row['ukd_state'] = row['ukd']['status']
            if row.get('parse_state') == 'needs_review':
                row['ukd']['status'] = row['ukd_state'] = 'needs_review'
                row['ukd']['upstream_review'] = True
            concepts.update(m['concept_id'] for m in row['ukd']['mentions'])
            mentions += len(row['ukd']['mentions'])
            icd_review += row['icd']['status'] != 'normalized'
            review_rows += row['icd']['status'] != 'normalized' or row['ukd_state'] == 'needs_review'
    return {'version': '1.0', 'catalog': default_catalog().metadata, 'llm_used': False,
            'ukd_mentions': mentions, 'unique_ukd_concepts': len(concepts),
            'icd_review_rows': icd_review, 'review_rows': review_rows,
            'status': 'needs_review' if review_rows else 'processed', 'clinical_completeness': 'not_verified'}


def enrich_payload(payload: dict) -> dict:
    if not all(key in payload for key in ('document', 'pages', 'order149_rows', 'order75_rows')):
        raise ValueError('Ожидается result.json, полученный командой parse.')
    result = deepcopy(payload)
    result['enrichment'] = enrich_rows(result['order149_rows'], result['order75_rows'], result['pages'])
    result['schema_version'] = '1.1'
    return result
