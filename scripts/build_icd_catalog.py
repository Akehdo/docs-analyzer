"""Build a codes/parents-only snapshot from the official WHO tabular download."""
import argparse
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('archive', type=Path)
parser.add_argument('--retrieved-on', required=True)
parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'pipeline/resources/who_icd10_2019.json')
args = parser.parse_args()
source = args.archive
with zipfile.ZipFile(source) as archive:
    name = next(n for n in archive.namelist() if n.endswith('_codes.txt'))
    raw = archive.read(name)
rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig')), delimiter=';'))
nodes = {}; stack = {}
for row in rows:
    level = int(row[0]); code = row[6]
    assert re.fullmatch(r'[A-Z]\d{2}(?:\.\d{1,2})?', code), code
    parent = stack.get(level-1) if level > 3 else None
    assert level == 3 or parent is not None, (level, code)
    assert parent is None or code.startswith(parent), (code, parent)
    assert code not in nodes, code
    nodes[code] = parent
    stack[level] = code
    stack = {k: v for k, v in stack.items() if k <= level}
payload = {'version': 'WHO-ICD10-2019-covid-expanded',
    'source_url': 'https://icdcdn.who.int/icd10/meta/icd102019enMeta.zip',
    'documentation_url': 'https://icdcdn.who.int/icd10/metainfo.html',
    'source_sha256': sha256(source.read_bytes()).hexdigest(),
    'retrieved_on': args.retrieved_on,
    'scope': 'Structural validation only. Not a Kazakhstan edition certification or coding guidance.',
    'nodes': nodes}
target = args.output
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
print({'codes': len(nodes), 'levels': {n: sum(len(c.replace('.', '')) == n for c in nodes) for n in [3,4,5]},
       'sha256': payload['source_sha256']})

