"""МКБ: исходный текст, нормализованное выражение, аудит и трёхзначная логика."""
from functools import lru_cache
import json
from pathlib import Path
import re

LOOKALIKES = str.maketrans('АВЕКМНОРСТХ', 'ABEKMHOPCTX')
LETTERS = 'A-ZАВЕКМНОРСТХ'
CODE = rf'[{LETTERS}]\s*\d{{2}}(?:\s*\.\s*\d{{1,2}})?'
END = rf'[{LETTERS}]?\s*\d{{2}}(?:\s*\.\s*\d{{1,2}})?'
TOKEN = re.compile(rf'(?<![\w])(?P<start>{CODE})(?:\s*[-–—]\s*(?P<end>{END}))?(?![\w]|\.\d)', re.I)
MARKER = re.compile(r'\b(за\s+исключением|исключая|кроме|включая)\b', re.I)


class IcdCatalog:
    def __init__(self, payload: dict):
        self.metadata = {k: v for k, v in payload.items() if k != 'nodes'}
        self.parents = payload['nodes']
        for code, parent in self.parents.items():
            if not re.fullmatch(r'[A-Z]\d{2}(?:\.\d{1,2})?', code):
                raise ValueError(f'Некорректный код в классификаторе: {code}')
            seen = {code}
            while parent is not None:
                if parent not in self.parents or parent in seen:
                    raise ValueError(f'Нарушена иерархия классификатора: {code}')
                seen.add(parent)
                parent = self.parents[parent]
        self.ordered = sorted(self.parents)

    def covers(self, parent: str, child: str) -> bool | None:
        if parent not in self.parents or child not in self.parents:
            return None
        while child is not None:
            if child == parent:
                return True
            child = self.parents[child]
        return False

    def expand(self, start: str, end: str) -> list[str] | None:
        if start not in self.parents or end not in self.parents or start > end:
            return None
        if len(start) != len(end):
            return None
        return [code for code in self.ordered if len(code) == len(start) and start <= code <= end]


@lru_cache(maxsize=1)
def default_catalog() -> IcdCatalog:
    path = Path(__file__).parent / 'resources' / 'who_icd10_2019.json'
    return IcdCatalog(json.loads(path.read_text(encoding='utf-8')))


def canonical_code(value: str) -> str:
    return re.sub(r'\s+', '', value).upper().translate(LOOKALIKES)


def normalize_icd(raw: str, *, code_system='icd10', allow_prose=False, catalog=None) -> dict:
    catalog = catalog or default_catalog()
    result = {'raw': raw, 'code_system': code_system, 'catalog_version': catalog.metadata['version'],
              'codes': [], 'excluded_codes': [], 'included_examples': [], 'tokens': [],
              'corrections': [], 'issues': [], 'canonical': '', 'status': 'normalized'}
    if code_system != 'icd10':
        result['status'] = 'not_applicable'
        return result
    if not raw.strip():
        result['status'] = 'not_stated'
        return result
    markers = list(MARKER.finditer(raw))
    # A parenthesized qualifier ends at its matching closing parenthesis.
    # Following codes belong to the base again: E22 (except E22.8), D35.2.
    stack = []; scopes = {}
    for position, char in enumerate(raw):
        if char == '(':
            stack.append(position)
        elif char == ')':
            if stack:
                scopes[stack.pop()] = position
            else:
                result['issues'].append({'code': 'UNBALANCED_SCOPE', 'raw': raw})
    if stack:
        result['issues'].append({'code': 'UNBALANCED_SCOPE', 'raw': raw})
    marker_ends = {m.start(): min((end for start, end in scopes.items() if start < m.start() < end), default=len(raw)) for m in markers}
    consumed = []
    for match in TOKEN.finditer(raw):
        consumed.append(match.span())
        preceding = [m for m in markers if m.end() <= match.start() < marker_ends[m.start()]]
        role = 'codes'
        if preceding:
            role = 'included_examples' if preceding[-1].group().lower() == 'включая' else 'excluded_codes'
        start = canonical_code(match['start'])
        end = canonical_code(match['end']) if match['end'] else None
        if end and end[0].isdigit():
            end = start[0] + end
            result['corrections'].append({'rule': 'range_letter', 'raw': match.group(), 'value': end, 'span': list(match.span())})
        normalized = start + ('-' + end if end else '')
        if normalized != match.group():
            result['corrections'].append({'rule': 'alphabet_spacing_dash', 'raw': match.group(), 'value': normalized,
                                          'span': list(match.span())})
        codes = catalog.expand(start, end) if end else [start]
        if codes is None:
            codes = []
            result['issues'].append({'code': 'INVALID_RANGE', 'raw': match.group(), 'span': list(match.span())})
        for code in codes:
            if code not in catalog.parents:
                result['issues'].append({'code': 'UNKNOWN_CODE', 'raw': code, 'span': list(match.span())})
            if code not in result[role]:
                result[role].append(code)
        result['tokens'].append({'raw': match.group(), 'span': list(match.span()), 'normalized': normalized,
                                  'role': role, 'codes': codes})
    # Do not silently interpret unrecognised syntax as a complete expression.
    residue = list(raw)
    for start, end in consumed + [m.span() for m in markers]:
        residue[start:end] = [' '] * (end-start)
    rest = ''.join(residue)
    if not result['tokens']:
        result['issues'].append({'code': 'NO_CODES', 'raw': raw})
    if not allow_prose and re.sub(r'[\s,;./()\[\]*+]+', '', rest):
        result['issues'].append({'code': 'UNPARSED_ICD_TEXT', 'raw': rest.strip()})
    for i, marker in enumerate(markers):
        boundary = min(markers[i+1].start() if i+1 < len(markers) else len(raw), marker_ends[marker.start()])
        if not any(marker.end() <= t['span'][0] < boundary for t in result['tokens']):
            result['issues'].append({'code': 'UNRESOLVED_SCOPE', 'raw': raw[marker.start():boundary]})
    for left, right in zip(result['tokens'], result['tokens'][1:]):
        gap = raw[left['span'][1]:right['span'][0]]
        if gap.strip() == '.':
            result['corrections'].append({'rule': 'list_separator', 'raw': gap, 'value': ', '})
    if result['excluded_codes'] and not result['codes']:
        result['issues'].append({'code': 'EXCLUSION_WITHOUT_BASE', 'raw': raw})
    if result['included_examples'] and not result['codes']:
        result['issues'].append({'code': 'INCLUSION_WITHOUT_BASE', 'raw': raw})
    for example in result['included_examples']:
        if not any(catalog.covers(parent, example) is True for parent in result['codes']):
            result['issues'].append({'code': 'EXAMPLE_OUTSIDE_BASE', 'raw': example})
    if any(m.group().lower() == 'включая' for m in markers[1:]) and any(m.group().lower() != 'включая' for m in markers[:-1]):
        result['issues'].append({'code': 'AMBIGUOUS_SCOPE', 'raw': raw})
    result['canonical'] = ', '.join(result['codes'])
    if result['included_examples']:
        result['canonical'] += ', включая ' + ', '.join(result['included_examples'])
    if result['excluded_codes']:
        result['canonical'] += ', кроме ' + ', '.join(result['excluded_codes'])
    if result['issues']:
        result['status'] = 'needs_review'
    return result


def is_covered(child: str, parent: str, catalog=None) -> dict:
    catalog = catalog or default_catalog()
    value = catalog.covers(canonical_code(parent), canonical_code(child))
    return {'value': value, 'reason': 'catalog_hierarchy' if value is not None else 'unknown_code',
            'catalog_version': catalog.metadata['version']}


def contains(expression: dict, code: str, catalog=None) -> bool | None:
    catalog = catalog or default_catalog()
    code = canonical_code(code)
    if expression['status'] != 'normalized' or code not in catalog.parents:
        return None
    if any(catalog.covers(excluded, code) for excluded in expression['excluded_codes']):
        return False
    covered = any(catalog.covers(parent, code) for parent in expression['codes'])
    # A broad category is only partially covered when a descendant is excluded.
    if covered and any(catalog.covers(code, excluded) for excluded in expression['excluded_codes']):
        return None
    return covered


def intersects(left: dict, right: dict, catalog=None) -> bool | None:
    catalog = catalog or default_catalog()
    if left['status'] != 'normalized' or right['status'] != 'normalized':
        return None
    # Only leaves of the actual classification, never invented child codes.
    nonterminal = set(catalog.parents.values())
    return any(contains(left, code, catalog) is True and contains(right, code, catalog) is True
               for code in catalog.ordered if code not in nonterminal)
