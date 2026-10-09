"""计划预览与采集入口共用快照；不导入采集器、秘密配置或访问远端。

baseline文件体量/修改指纹只用于计划变化检测，不代表业务完整度证据。
发布锁文件属于管理元数据；已初始化仓的扫描不修改业务文件。
"""
import copy
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re

from warehouse_commit import publication_guard


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _inside(root, value):
    path = Path(value).resolve()
    if not path.is_relative_to(root):
        raise ValueError('登记路径不属于当前数据仓')
    return path


def _day(value):
    text = str(value).replace('-', '').split(' ')[0].split('T')[0][:8]
    return text if re.fullmatch(r'\d{8}', text) else None


def _latest(path, column):
    """使用真实footer统计，无法证明时保留未知，不凭文件名或mtime推业务日期。"""
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    if not column or column not in pf.schema_arrow.names:
        return None
    index, values = pf.schema_arrow.names.index(column), []
    for group in range(pf.metadata.num_row_groups):
        statistics = pf.metadata.row_group(group).column(index).statistics
        if statistics is None or not statistics.has_min_max:
            return None
        value = _day(statistics.max)
        if value is None:
            return None
        values.append(value)
    return max(values) if values else None


def _unknown(name, entry):
    return dict(asset_id=name, physical_tables=[name], aliases=[], provider=None,
        channel=entry.get('source'), market=None, frequency=entry.get('frequency'),
        primary_key=entry.get('primary_key'), business_columns=None, event_date_kind=None,
        policy_evidence=None, supported_scope=None, verification_rule=None,
        qualification_status='unknown', unsupported_reason='来源、发布规则及执行资格尚待核实')


def _unique_code(path, column):
    """真实代码列的每个非空行组均须证明同一代码，不能依据文件名猜映射。"""
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    if not column or column not in pf.schema_arrow.names:
        raise ValueError('逐码文件缺少明确代码列')
    index, codes = pf.schema_arrow.names.index(column), set()
    for group in range(pf.metadata.num_row_groups):
        rows = pf.metadata.row_group(group)
        if rows.num_rows == 0:
            continue
        stats = rows.column(index).statistics
        if (stats is None or not stats.has_min_max or stats.null_count != 0
                or stats.min != stats.max or not isinstance(stats.min,str) or not stats.min):
            raise ValueError('逐码文件统计无法证明唯一代码')
        codes.add(stats.min)
    if len(codes) != 1:
        raise ValueError('逐码文件没有唯一代码')
    return next(iter(codes))


def snapshot(data_root, registry_path, *, scan_mode='all'):
    """返回(catalog,registry,baseline)，绝对仓根一致、锁内枚举与footer物化。"""
    root = Path(data_root).resolve()
    if scan_mode not in {'all','qualified','none'}:
        raise ValueError('元数据扫描范围无效')
    catalog_path, registry_path = root/'catalog.json', _inside(root,registry_path)
    catalog = json.loads(catalog_path.read_bytes())
    if not isinstance(catalog.get('tables'),dict):
        raise ValueError('数据目录登记无效')
    registry_bytes = registry_path.read_bytes() if registry_path.exists() else None
    registry = json.loads(registry_bytes) if registry_bytes is not None else {'physical_tables':[], 'display_assets':[]}
    registry_read_sha = hashlib.sha256(registry_bytes).hexdigest() if registry_bytes is not None else None
    if not isinstance(registry.get('physical_tables'),list) or not isinstance(registry.get('display_assets',[]),list):
        raise ValueError('来源资格登记无效')
    registry = copy.deepcopy(registry)
    registered = {name for row in registry['physical_tables'] for name in row['physical_tables']}
    for name,entry in catalog['tables'].items():
        if name not in registered:
            registry['physical_tables'].append(_unknown(name,entry))
    registry.setdefault('display_assets',[])
    scan_tables = set(catalog['tables']) if scan_mode=='all' else set()
    if scan_mode=='qualified':
        for row in registry['physical_tables']:
            if row.get('qualification_status')=='qualified':
                scan_tables.update(row['physical_tables'])
                reconcile = (row.get('verification_rule') or {}).get('reconcile') or {}
                if reconcile.get('table'):
                    scan_tables.add(reconcile['table'])
    resources = dict(tdx=str((root/'tdx').resolve()),tushare=str((root/'tushare').resolve()))
    for source, supplied in registry.get('resource',{}).items():
        if source not in resources or Path(supplied).resolve()!=Path(resources[source]):
            raise ValueError('资格仓根与当前数据仓不一致')
    registry['resource'] = resources
    roots, entries = set(), {}
    for name,entry in catalog['tables'].items():
        store = _inside(root,entry['store'])
        if store == root:
            raise ValueError('表存储路径不能为整个数据根')
        manifest = _inside(root,entry['manifest']) if entry.get('manifest') else None
        resource = manifest.parent if manifest else store.parent
        if resource.is_dir():
            roots.add(resource)
        entries[name] = (entry,store,manifest)
    with ExitStack() as stack:
        for resource in sorted(roots,key=lambda p:str(p).casefold()):
            stack.enter_context(publication_guard(resource,mode='read'))
        # catalog/registry原子发布也不能在枚举期间被另一进程悄悄替换。
        initial = {'catalog':_sha(catalog_path),'registry':_sha(registry_path)}
        if initial['registry'] != registry_read_sha:
            raise ValueError('加锁前资格元数据发生变化，请重新预览')
        manifests, tables = {}, {}
        for name,(entry,store,manifest) in entries.items():
            if manifest and str(manifest) not in manifests:
                manifests[str(manifest)] = dict(sha256=_sha(manifest),
                    content=json.loads(manifest.read_bytes()) if manifest.exists() else {})
            state = manifests[str(manifest)]['content'].get(name,{}) if manifest else {}
            item = {**copy.deepcopy(state), 'done':state.get('done',entry.get('done',False)),
                    'source':entry.get('source'),'layout':entry.get('layout'),
                    'store':str(store),'date_col':entry.get('date_col'),
                    'scan_status':'scanned' if name in scan_tables else 'not_scanned',
                    'files':{},'latest_date':None,'codes':[],'code_files':{},
                    'latest_by_code':{},'read_errors':[]}
            files = ([store] if entry.get('layout')=='sqlite' and store.is_file() else sorted(store.glob('*.parquet')) if store.is_dir() else []) if name in scan_tables else []
            latests = []
            for path in files:
                path = _inside(root,path)
                stat = path.stat()
                item['files'][path.relative_to(root).as_posix()] = {'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns}
                if path.suffix != '.parquet':
                    continue
                try:
                    value = _latest(path,entry.get('date_col'))
                    if value:
                        latests.append(value)
                    if entry.get('layout') in {'by_code','by_code_month'}:
                        code = _unique_code(path,entry.get('code_col'))
                        item['codes'].append(code)
                        item['code_files'].setdefault(code,[]).append(path.relative_to(root).as_posix())
                        prior = item['latest_by_code'].get(code)
                        item['latest_by_code'][code] = max(prior,value) if prior and value else value or prior
                except Exception:
                    item['read_errors'].append(path.relative_to(root).as_posix())
            item['codes'] = sorted(set(item['codes']))
            item['latest_date'] = max(latests) if latests and not item['read_errors'] else None
            tables[name] = item
        final = {'catalog':_sha(catalog_path),'registry':_sha(registry_path)}
        if initial != final or catalog != json.loads(catalog_path.read_bytes()):
            raise ValueError('元数据扫描期间目录或资格发生变化，请重新预览')
        for path,record in manifests.items():
            if _sha(Path(path)) != record['sha256']:
                raise ValueError('元数据扫描期间manifest发生变化，请重新预览')
        baseline = dict(version=1,tables=tables,manifests=manifests,metadata_sha256=initial,
                        fingerprint_basis='manifest_sha256_and_physical_file_stat')
        return catalog,registry,baseline
