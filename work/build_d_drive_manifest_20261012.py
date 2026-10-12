import json
from pathlib import Path

src = Path(r'E:\老田的AI助手\.worktrees\K-STOCK-210\work\academy-source-evidence-v0.1\d-drive-quantum-double-blade-source-evidence-v0.1.json')
out = src.parent
ledger = json.loads(src.read_text(encoding='utf-8'))
root = Path(ledger['source_root'])
records = []
duplicates_excluded = []
seen_hashes = {}
for item in ledger['sources']:
    digest = item['sha256'].lower()
    path = Path(item['original_path'])
    relative = path.relative_to(root).as_posix()
    if digest in seen_hashes:
        duplicates_excluded.append({
            'source': item['file_name'],
            'source_id': item['source_id'],
            'duplicate_of': seen_hashes[digest],
            'sha256': digest,
            'reason': 'byte_identical_duplicate_excluded_from_candidate_rows',
        })
        continue
    seen_hashes[digest] = item['source_id']
    records.append({
        'sample_id': item['source_id'],
        'source': item['file_name'],
        'source_path': relative,
        'sample_sha256': item['sha256'],
        'license_id': None,
        'license_subject': None,
        'purpose': '策略知识提炼和只读评估',
        'valid_from': None,
        'valid_until': None,
        'commercial_scope': None,
        'source_reference': 'local://d-drive/' + relative,
        'split': None,
        'split_group': None,
        'offline_review_receipt': None,
        'license_approved': False,
        'permission_snapshot': None,
        'training_eligible': False,
        'strategy_training_eligible': False,
        'production_eligible': False,
        'evaluation_only': True,
    })
manifest = {
    'schema_version': 'training-eligible-manifest-v0.1',
    'generated_at': ledger['generated_at'],
    'source_root': str(root),
    'policy': {
        'default_status': 'evaluation_only',
        'training_enabled': False,
        'strategy_training_enabled': False,
        'financial_execution_enabled': False,
        'simulated_execution_enabled': False,
        'commercialization_enabled': False,
    },
    'deduplication': {
        'raw_files': len(ledger['sources']),
        'unique_hashes': len(records),
        'duplicates_excluded': duplicates_excluded,
        'originals_retained': True,
    },
    'records': records,
}
path = out / 'd-drive-quantum-double-blade-candidate-manifest-v0.1.json'
path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'records': len(records), 'duplicates_excluded': len(duplicates_excluded), 'path': str(path)}, ensure_ascii=False))
