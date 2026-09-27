"""Проверка нового этапа на ранее извлечённых оригинальных PDF и XLSX."""
import argparse
from collections import Counter
import json
from pathlib import Path
from pipeline.enrichment import enrich_payload
from pipeline.erdb import load_erdb, ErdbRepository
from pipeline.storage import save_enriched, save_erdb


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=Path, required=True)
    parser.add_argument('--xlsx', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {}
    for label, prefix in [('75','7cc815c7d2c4'),('149A','e00646e4c707'),('149B','d1989543f607')]:
        source = next(args.runs.glob(prefix+'*/result.json'))
        original = json.loads(source.read_text(encoding='utf-8'))
        result = enrich_payload(original)
        assert result['summary'] == original['summary']
        mentions = []
        for row in result['order149_rows'] + result['order75_rows']:
            for field in row['ukd']['fields']:
                for mention in field['mentions']:
                    assert mention['raw'] == field['raw'][slice(*mention['span'])]
                    assert mention['source_refs']
                    mentions.append(mention)
        folder = save_enriched(result, (source.parent/'source.pdf').read_bytes(), args.output)
        report[label] = {'enrichment': result['enrichment'], 'ukd_types': dict(Counter(m['type'] for m in mentions)),
                         'icd_issues': dict(Counter(i['code'] for r in result['order149_rows']+result['order75_rows'] for i in r['icd']['issues'])),
                         'saved_to': str(folder)}
        print(label, json.dumps(report[label], ensure_ascii=False), flush=True)
    data = args.xlsx.read_bytes()
    snapshot = load_erdb(data, args.xlsx.name)
    expected = {'sp_section':71, 'sp_section_point':149, 'section_point_filter':153, 'section_diagnoses_filter':6241,
                'unique_icd':2657, 'linked_groups':69, 'invalid_rows':0}
    for key, value in expected.items():
        assert snapshot['summary'][key] == value, (key, snapshot['summary'])
    repo = ErdbRepository(snapshot)
    assert repo.has_link('J45','CHP3')
    assert {p['point_id'] for p in repo.paths_for_icd('J45')} >= {'CHP1','CHP2','CHP3'}
    folder = save_erdb(snapshot, data, args.output)
    assert (folder/'source.xlsx').read_bytes() == data
    report['erdb'] = {'summary': snapshot['summary'], 'issues': dict(Counter(i['code'] for i in snapshot['issues'])),
                      'J45_paths': repo.paths_for_icd('J45'), 'saved_to': str(folder)}
    print('ERDB', json.dumps({'summary':report['erdb']['summary'], 'issues':report['erdb']['issues']}, ensure_ascii=False), flush=True)
    (args.output/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__': main()
