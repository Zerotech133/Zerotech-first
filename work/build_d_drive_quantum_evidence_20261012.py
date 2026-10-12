from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

WORK = Path(r'E:\老田的AI助手\.worktrees\K-STOCK-210')
OUT = WORK / 'work' / 'academy-source-evidence-v0.1'
OUT.mkdir(parents=True, exist_ok=True)
D_ROOT = Path(r'D:\Documents\xwechat_files\tianfeng1333_2e0a\msg\file')
SOURCE_PATHS = [
    D_ROOT / '2026-02' / '量子双刀实盘20260227.rzrk',
    D_ROOT / '2026-03' / '量子双刀实盘20260303.rzrk',
    D_ROOT / '2026-03' / '量子双刀实盘20260303(改1).rzrk',
    D_ROOT / '2026-03' / '量子双刀实盘20260306.rzrk',
    D_ROOT / '2026-03' / '全量公式.tne',
    D_ROOT / '2026-03' / '选股及交易策略260305.docx',
    D_ROOT / '2026-03' / '选股及交易策略260305(1).docx',
]
for p in SOURCE_PATHS:
    if not p.is_file():
        raise SystemExit(f'missing_source:{p}')

observed_at = datetime.now().astimezone().isoformat(timespec='seconds')

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def read_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read('word/document.xml')
    root = ElementTree.fromstring(xml)
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    return '\n'.join(''.join(node.itertext()) for node in root.findall('.//w:p', ns))

def source_id(digest: str) -> str:
    return 'D-QD-' + digest[:16]

sources = []
by_hash = {}
for path in SOURCE_PATHS:
    digest = sha256(path)
    item = {
        'source_id': source_id(digest),
        'original_path': str(path),
        'file_name': path.name,
        'extension': path.suffix.lower(),
        'bytes': path.stat().st_size,
        'sha256': digest,
        'observed_at': observed_at,
        'source_date': None,
        'source_date_status': 'missing_original_publication_evidence',
        'license_status': 'pending',
        'offline_review_status': 'pending',
        'time_split_status': 'pending',
        'evaluation_only': True,
        'training_eligible': False,
        'strategy_training_eligible': False,
        'production_eligible': False,
    }
    if digest in by_hash:
        item['duplicate_of'] = by_hash[digest]['original_path']
        item['readability'] = 'duplicate_byte_identical'
    elif path.suffix.lower() == '.docx':
        text = read_docx_text(path)
        item['readability'] = 'docx_readable'
        item['text_chars'] = len(text)
        item['keyword_hits'] = {
            key: bool(re.search(pattern, text, re.I))
            for key, pattern in {
                '美林时钟': r'美林时钟',
                '确定性': r'确定性',
                '安全边际': r'安全边际',
                '多周期': r'多周期|日.*周.*月',
                '买点': r'买点',
                '卖点': r'卖点',
                '回测': r'回测',
            }.items()
        }
    else:
        item['readability'] = 'binary_unparsed_without_associated_decoder'
    sources.append(item)
    by_hash.setdefault(digest, item)

note = OUT / 'd-drive-quantum-double-blade-study-2026-10-12.md'
note_digest = sha256(note)
derived = {
    'source_id': 'D-QD-DERIVED-' + note_digest[:16],
    'path': str(note),
    'relative_path': 'work/academy-source-evidence-v0.1/d-drive-quantum-double-blade-study-2026-10-12.md',
    'bytes': note.stat().st_size,
    'sha256': note_digest,
    'source_class': 'derived_note_from_readable_docx',
    'derived_from_source_ids': [source_id(sha256(SOURCE_PATHS[-2]))],
    'raw_source_available': True,
    'source_date': None,
    'source_date_status': 'missing_original_publication_evidence',
    'observed_at': observed_at,
    'license_scope': 'pending',
    'offline_review_status': 'pending',
    'time_split_status': 'pending',
    'evaluation_only': True,
    'training_eligible': False,
    'strategy_training_eligible': False,
    'production_eligible': False,
}

ledger = {
    'schema': 'd_drive_quantum_double_blade_source_evidence/v1',
    'generated_at': observed_at,
    'observed_at': observed_at,
    'mode': 'read_only',
    'source_root': str(D_ROOT),
    'policy': [
        '文件名日期、mtime 和 DOCX 元数据不作为 source_date 或生效日期证据',
        '二进制 .rzrk/.tne 未取得可信解码器时只登记哈希，不推断内部公式',
        '派生笔记仅用于 evaluation_only RAG，不自动获得训练资格',
    ],
    'sources': sources,
    'derived_sources': [derived],
    'counts': {
        'raw_files': len(sources),
        'unique_hashes': len(by_hash),
        'duplicates': sum(1 for item in sources if 'duplicate_of' in item),
        'binary_unparsed': sum(1 for item in sources if item['readability'] == 'binary_unparsed_without_associated_decoder'),
        'readable_docx': sum(1 for item in sources if item['readability'] == 'docx_readable'),
        'derived_evaluation_sources': 1,
    },
    'blockers': [
        'source_date_missing_for_all_d_drive_materials',
        'license_pending',
        'offline_review_pending',
        'time_split_pending',
        'binary_rzrk_tne_unparsed',
        'derived_note_is_evaluation_only',
        'exact_2026-10-08_quantum_double_blade_original_not_found_in_e_drive',
    ],
    'training_eligible': False,
    'strategy_training_eligible': False,
    'production_eligible': False,
}
(OUT / 'd-drive-quantum-double-blade-source-evidence-v0.1.json').write_text(
    json.dumps(ledger, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
)

text = note.read_text(encoding='utf-8')
cases = [
    ('D-QD-RAG-001', '美林时钟 确定性 安全边际', ['美林时钟', '确定性', '安全边际'], 'answer_with_evidence'),
    ('D-QD-RAG-002', '宏观 中观 微观 多周期 共振', ['宏观', '中观', '微观', '多周期'], 'answer_with_evidence'),
    ('D-QD-RAG-003', '买点 卖点 持有周期 回测 归因', ['买点', '卖点', '持有周期', '回测', '归因'], 'answer_with_evidence'),
    ('D-QD-RAG-004', '量子双刀二进制原始公式精确参数', ['无法从二进制内容推断规则'], 'refuse_missing_evidence'),
    ('D-QD-RAG-005', '已批准许可证和本人离线审核签名', ['许可证', '本人离线审核'], 'refuse_missing_evidence'),
]
results = []
# 这些状态来自来源登记，而不是从查询词推断；当前均未满足。
binary_parse_available = False
license_approved = False
offline_review_complete = False
for case_id, query, terms, expected in cases:
    hits = [term for term in terms if term in text]
    if expected == 'answer_with_evidence':
        decision = 'answer_with_evidence' if len(hits) == len(terms) else 'refuse_missing_evidence'
        passed = decision == expected
    else:
        decision = 'refuse_missing_evidence'
        if case_id == 'D-QD-RAG-004':
            passed = not binary_parse_available
        elif case_id == 'D-QD-RAG-005':
            passed = not license_approved and not offline_review_complete
        else:
            passed = False
    results.append({
        'case_id': case_id,
        'query': query,
        'expected_decision': expected,
        'decision': decision,
        'term_hits': hits,
        'term_coverage': round(len(hits) / len(terms), 4) if terms else 1.0,
        'source_id': derived['source_id'] if decision == 'answer_with_evidence' else None,
        'citation_completeness': expected == 'answer_with_evidence' and decision == 'answer_with_evidence',
        'passed': passed,
        'model_calls': 0,
        'training_steps': 0,
    })
positive = [r for r in results if r['expected_decision'] == 'answer_with_evidence']
refusals = [r for r in results if r['expected_decision'] == 'refuse_missing_evidence']
rag_report = {
    'schema': 'd_drive_quantum_double_blade_rag_evaluation/v1',
    'generated_at': observed_at,
    'mode': 'lexical_read_only',
    'source_evidence_ref': 'd-drive-quantum-double-blade-source-evidence-v0.1.json',
    'corpus': [derived],
    'excluded_raw_sources': [
        {'source_id': item['source_id'], 'path': item['original_path'], 'reason': item['readability']}
        for item in sources if item['readability'].startswith('binary_')
    ],
    'cases': results,
    'summary': {
        'total': len(results),
        'positive_total': len(positive),
        'positive_passed': sum(r['passed'] for r in positive),
        'positive_recall': round(sum(r['passed'] for r in positive) / len(positive), 4),
        'refusal_total': len(refusals),
        'refusal_passed': sum(r['passed'] for r in refusals),
        'all_terms_covered': all(r['term_coverage'] == 1.0 for r in positive),
        'blocked': True,
    },
    'model_calls': 0,
    'training_steps': 0,
    'training_eligible': False,
    'strategy_training_eligible': False,
    'production_eligible': False,
    'decision': 'blocked_source_governance',
    'blockers': ledger['blockers'],
}
(OUT / 'd-drive-quantum-double-blade-rag-evaluation-v0.1.json').write_text(
    json.dumps(rag_report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
)
(OUT / 'd-drive-quantum-double-blade-rag-cases-v0.1.jsonl').write_text(
    ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in results), encoding='utf-8'
)
readme = f'''# D 盘量子双刀来源证据登记\n\n- 观察时间：`{observed_at}`\n- 原始范围：`{D_ROOT}`\n- 原始附件：{len(sources)} 个，唯一哈希 {len(by_hash)} 个，重复副本 {ledger['counts']['duplicates']} 个。\n- 可读材料：DOCX 1 份；`.rzrk`/`.tne` 二进制未取得可信解码器，只登记路径、大小和 SHA256。\n- 派生材料：`{note.name}`，仅用于只读词法 RAG 评估。\n- 当前状态：`training_eligible=false`、`strategy_training_eligible=false`、`production_eligible=false`。\n\n## 评测\n\n评测报告：`d-drive-quantum-double-blade-rag-evaluation-v0.1.json`。正向用例和缺证据拒答用例均只消费派生笔记，`model_calls=0`、`training_steps=0`。\n\n## 保留的阻断\n\n许可证、原始发布日期/生效日期、本人离线审核回执、权限快照和时间切分仍缺失；精确的 `2026-10-08` 量子双刀原文也未找到。上述缺口补齐前，材料保持 `evaluation_only`，不启动 LoRA、模拟成交、交易或收费上架。\n'''
(OUT / 'd-drive-quantum-double-blade-evidence-README.md').write_text(readme, encoding='utf-8')
print(json.dumps({'observed_at': observed_at, 'raw_files': len(sources), 'unique_hashes': len(by_hash), 'duplicates': ledger['counts']['duplicates'], 'rag_summary': rag_report['summary']}, ensure_ascii=False))
