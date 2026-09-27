"""Правила УКД без LLM: цитаты, диапазоны, отрицания и неразрешённые условия."""
from hashlib import sha256
import re

VERSION = 'rules-1.0'
NUM = r'(?:VIII|VII|VI|IV|III|II|IX|V|I|X|[1-9])'
NUM_LIST = rf'{NUM}(?:\s*(?:[-–—,/]|или|и)\s*{NUM})*'
NUMBERS = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10}
FREQUENCY = r'\d+\s*раз(?:а)?\s+в\s+(?:\d+\s+)?(?:год(?:а)?|месяц\w*|недел\w*)'
SEVERITY = re.compile(r'\b(?:крайне\s+тяжел\w*|средне[\s-]?тяжел\w*|легк\w*|средн\w*|тяжел\w*)\b', re.I)


def number_list(raw: str) -> list[int]:
    values = []
    for part in re.split(r'\s*(?:,|/|\bи\b|\bили\b)\s*', raw.upper(), flags=re.I):
        ends = re.split(r'\s*[-–—]\s*', part)
        nums = [NUMBERS[v] if v in NUMBERS else int(v) for v in ends]
        if len(nums) == 2:
            if nums[0] > nums[1]:
                return []
            values.extend(range(nums[0], nums[1] + 1))
        else:
            values.extend(nums)
    return list(dict.fromkeys(values))


def extract_ukd_field(raw: str, *, field: str, row_id: str, context: str, source_refs: list,
                      context_only=False) -> dict:
    text = raw.lower().replace('ё', 'е')
    mentions = []; groups = []; issues = []; occupied = []

    def add(kind, values, start, end, *, operator='or', confidence='high'):
        if any(start < b and end > a for a, b in occupied):
            return
        occupied.append((start, end))
        prefix = text[max(0, start-90):start]
        clause_prefix = re.split(r'[.;]', prefix)[-1]
        negated = bool(re.search(r'(?:\bне\s*$|\bбез\s*$|\bкроме\b|за исключением)', clause_prefix))
        if re.search(r'(?:не менее|не более|выше|ниже|свыше|\bдо|\bот)\s*$', prefix):
            confidence = 'needs_review'; operator = 'unresolved'
            issues.append('COMPARISON_OPERATOR')
            negated = False
        children = []
        for value in values:
            concept = sha256(f'{context}|{kind}|{value}|{negated}'.encode()).hexdigest()[:16]
            mention_id = sha256(f'{row_id}|{field}|{start}|{end}|{kind}|{value}'.encode()).hexdigest()[:16]
            item = {'mention_id': mention_id, 'concept_id': concept, 'type': kind, 'value': value,
                    'negated': negated, 'raw': raw[start:end], 'span': [start, end], 'field': field,
                    'source_refs': source_refs, 'method': VERSION, 'confidence': confidence}
            mentions.append(item)
            children.append({'operator': 'mention', 'id': mention_id})
        node = children[0] if len(children) == 1 and operator != 'unresolved' else {'operator': operator, 'children': children}
        if negated:
            node = {'operator': 'not', 'children': [node]}
        groups.append({'span': [start, end], 'node': node, 'type': kind})

    for m in re.finditer(r'\bвсе\s+(стади\w*|степени(?:\s+тяжести)?|функциональн\w*\s+класс\w*|фк)\b', text):
        dimension = 'stage' if m[1].startswith('стади') else 'severity' if m[1].startswith('степени') else 'functional_class'
        add(dimension, ['any'], *m.span(), operator='unrestricted')
    for kind, label in [('functional_class', r'(?:фк|функциональн\w*\s+класс\w*)'),
                        ('stage', r'стади[яиюей]+'), ('degree', r'степен[ьи]+')]:
        pattern = re.compile(rf'(?<!\w)(?:(?P<before>{NUM_LIST})\s*{label}|{label}\s*(?P<after>{NUM_LIST}))(?!\w)', re.I)
        for m in pattern.finditer(raw):
            values = number_list(m['before'] or m['after'])
            if values:
                add(kind, values, *m.span())
            else:
                issues.append('REVERSED_UKD_RANGE')
    for m in SEVERITY.finditer(text):
        nearby = text[max(0, m.start()-25):min(len(text), m.end()+65)]
        if not re.search(r'степен|тяжест|течен|форм', nearby):
            continue
        word = m.group()
        value = ('very_severe' if word.startswith('крайне') else
                 'moderate_severe' if re.match(r'средне[\s-]?тяжел', word) else
                 'mild' if word.startswith('легк') else 'moderate' if word.startswith('средн') else 'severe')
        add('severity', [value], *m.span())
    for m in re.finditer(r'\bтип[аы]?\s+([a-dавсд1-4](?:\s*[,/]\s*[a-dавсд1-4])*)\b', text):
        values = [v.strip().upper().translate(str.maketrans('АВСД', 'ABCD')) for v in re.split(r'[,/]', m[1])]
        add('type', values, *m.span())
    for m in re.finditer(r'\bпосле\s+[^.;()]+', text):
        add('post_event', [re.sub(r'\s+', ' ', raw[m.start():m.end()]).strip()], *m.span(),
            confidence='high' if re.fullmatch(r'после\s+(?:операции|стентирования(?:\s+коронарных\s+сосудов)?|инфаркта(?:\s+миокарда)?|аортокоронарного\s+шунтирования|акш)', m.group().strip()) else 'needs_review')

    groups.sort(key=lambda g: g['span'][0])
    # Adjacent severities are alternatives in one dimension. Mixed dimensions
    # must not silently become a Cartesian product (e.g. types A/B × severity).
    merged = []
    for group in groups:
        if merged and group['type'] == merged[-1]['type'] == 'severity':
            gap = text[merged[-1]['span'][1]:group['span'][0]]
            if re.fullmatch(r'\s*(?:,|и|или)\s*', gap):
                previous = merged.pop()
                merged.append({'span': [previous['span'][0], group['span'][1]], 'type': 'severity',
                               'node': {'operator': 'or', 'children': [previous['node'], group['node']]}})
                continue
        merged.append(group)
    branches = []
    for m in re.finditer(rf'(?P<frequency>{FREQUENCY})\s*\((?P<condition>[^()]*)\)', text):
        ids = [item['mention_id'] for item in mentions if m.start('condition') <= item['span'][0] < m.end('condition')]
        if ids:
            branches.append({'frequency_raw': raw[m.start('frequency'):m.end('frequency')],
                             'condition_raw': raw[m.start('condition'):m.end('condition')],
                             'span': list(m.span()), 'mention_ids': ids, 'source_refs': source_refs})
    children = [g['node'] for g in merged]
    operator = 'mention' if len(children) == 1 else 'unresolved'
    if len(children) > 1:
        gaps = [text[a['span'][1]:b['span'][0]] for a, b in zip(merged, merged[1:])]
        if all(re.fullmatch(r'\s*(?:и|,?\s*кроме(?:\s+пациентов)?)\s*', gap) for gap in gaps):
            operator = 'and'
        elif branches and all(any(item['mention_id'] in branch['mention_ids'] for branch in branches) for item in mentions):
            operator = 'frequency_branches'
        else:
            issues.append('UNRESOLVED_RELATION')
    tree = children[0] if len(children) == 1 else {'operator': operator, 'children': children}
    # Keep the entire field and all surrounding restrictions, even when a leaf
    # is recognized. Unknown text is a review queue, never proof of absence.
    residue = list(text)
    for start, end in occupied:
        residue[start:end] = [' '] * (end-start)
    remaining = ''.join(residue)
    remaining = re.sub(FREQUENCY, ' ', remaining)
    remaining = re.sub(r'\b(?:степен\w*|тяжести|течени\w*|при|и|или|кроме|пациентов|не|по показаниям|пожизненно)\b', ' ', remaining)
    remaining = re.sub(r'[\s,.;():\-–—]+', ' ', remaining).strip()
    if remaining and not context_only:
        issues.append('UNPARSED_CONDITION')
    if any(m['confidence'] != 'high' for m in mentions):
        issues.append('COMPLEX_MENTION')
    if context_only and not mentions:
        status = 'context_only'
    elif issues:
        status = 'needs_review'
    elif mentions:
        status = 'extracted'
    else:
        status = 'not_stated' if not raw.strip() else 'no_explicit_ukd'
    return {'raw': raw, 'field': field, 'mentions': mentions, 'expression': tree,
            'frequency_branches': branches, 'unparsed_text': remaining, 'issues': list(dict.fromkeys(issues)),
            'source_refs': source_refs, 'status': status, 'method': VERSION}


def extract_row_ukd(row: dict, profile: str, *, context='') -> dict:
    fields = (['nosology', 'periodicity_smr', 'periodicity_pmsp', 'periodicity_specialist', 'duration']
              if profile == '149' else ['disease', 'indication_raw', 'category'])
    results = []
    for field in fields:
        refs = row.get('field_source_refs', {}).get(field, row['source_refs'])
        if field not in row.get('field_source_refs', {}) and field in row.get('inherited_from', {}):
            refs = [row['inherited_from'][field]]
        results.append(extract_ukd_field(row.get(field, ''), field=field, row_id=row['row_id'], context=context,
            source_refs=refs, context_only=field in ('nosology', 'disease', 'category', 'duration')))
    mentions = [mention for field in results for mention in field['mentions']]
    status = 'needs_review' if any(f['status'] == 'needs_review' for f in results) else 'extracted' if mentions else 'no_explicit_ukd'
    return {'fields': results, 'mentions': mentions, 'status': status, 'method': VERSION,
            'clinical_completeness': 'not_verified', 'llm_used': False}
