"""真实本地仓服务适配；无凭据导入、远端请求或旧任务历史倒推。

工厂不初始化任务库；GET只读已存在的任务。未知来源资格继续保留未知。
"""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from warehouse_update_api import PlanService, safe


class LazyStore:
    """查询空历史不创建SQLite；首次真实POST登记时才初始化。"""
    def __init__(self,path):
        self.path = str(path)

    def _store(self):
        from warehouse_runs import RunStore
        return RunStore(self.path)

    def list_runs(self,day,*,limit=100):
        return self._store().list_runs(day,limit=limit) if Path(self.path).exists() else []

    def get_run(self,run_id,**kwargs):
        if not Path(self.path).exists():
            raise KeyError(run_id)
        return self._store().get_run(run_id,**kwargs)

    def reconcile(self,process_probe,*,now):
        return self._store().reconcile(process_probe,now=now) if Path(self.path).exists() else []

    def __getattr__(self,name):
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self._store(),name)


class RunLogs:
    def __init__(self,folder):
        self.folder = Path(folder).resolve()

    def read_log(self,run_id,*,offset,limit):
        if not isinstance(run_id,str) or not re.fullmatch(r'[a-f0-9]{32}',run_id):
            raise ValueError('日志任务标识不合法')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=65536:
            raise ValueError('日志分页不合法')
        path = self.folder/(run_id+'.log')
        if not path.exists():
            return dict(available=False,association='unavailable',text='',next_offset=offset,has_more=False)
        if path.is_symlink() or not path.resolve().is_relative_to(self.folder):
            raise ValueError('日志路径越出受控目录')
        with path.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            # stdout没有可靠的多行认证边界。整份日志出现认证标签则所有页隐藏，
            # 防止从JSON值行或下一页开始绕过标签。过大日志不提供未审计原文。
            if size > 8*1024*1024:
                return dict(available=False,association='run',text='日志过大，请查看结构化事件',
                            next_offset=offset,has_more=False,reason='raw_log_unreviewable')
            from warehouse_update_api import SECRET
            tail, scanned, redact_all = '', 0, False
            while scanned<size:
                block = stream.read(min(65536,size-scanned))
                if not block:
                    break
                text = tail+block.decode('utf8','replace')
                if SECRET.search(text) or re.search(r'(?i)\b(?:basic|digest|bearer)\s+',text):
                    redact_all = True
                tail, scanned = text[-256:], scanned+len(block)
            if offset>size:
                return dict(available=True,association='run',text='',next_offset=0,has_more=size>0,reset=True)
            # 往前找完整行头；不允许偏移进入认证值而绕过标签脱敏。
            begin = max(0,offset-65536)
            stream.seek(begin)
            prefix = stream.read(offset-begin)
            boundary = prefix.rfind(b'\n')
            aligned = begin+boundary+1 if boundary>=0 else begin
            if begin>0 and boundary<0:
                stream.seek(offset)
                fragment = stream.readline(min(limit,65536))
                next_offset = stream.tell()
                text = '[过长日志行，已隐藏]'
            else:
                stream.seek(aligned)
                lines = []
                while stream.tell()<size and (stream.tell()<offset+limit or not lines):
                    raw = stream.readline(65537)
                    if len(raw)>65536 or not raw.endswith(b'\n') and stream.tell()<size:
                        lines.append('[过长日志行，已隐藏]\n')
                        break
                    lines.append(safe(raw.decode('utf8','replace')))
                next_offset = stream.tell()
                text = ''.join(lines)
            if redact_all:
                text = '[认证信息已隐藏]'
        return dict(available=True,association='run',text=text,next_offset=next_offset,
                    has_more=next_offset<size,offset_basis='raw_bytes_complete_lines')


def make_services(project_root, *, clock=time.time, execution_enabled=False):
    project = Path(project_root).resolve()
    qbt, data = project/'quant_backtest', project/'quant_backtest'/'data'
    registry_path = data/'warehouse_registry.json'
    store = LazyStore(data/'warehouse_runs.sqlite3')

    def snapshot(*,scan_mode='all'):
        from warehouse_metadata import snapshot as read_snapshot
        return read_snapshot(data,registry_path,scan_mode=scan_mode)

    def preview(request,*,now):
        from warehouse_update import build_plan, plan_digest
        catalog,registry,baseline = snapshot(scan_mode='all' if execution_enabled else 'qualified')
        # 未获得远端目录回执时不猜表或请求网络；专用适配器后续供应remote_tables。
        plan = build_plan(request['source'],request['market'],request['date'],
            now=datetime.fromtimestamp(now,timezone.utc),catalog=catalog,registry=registry,
            baseline=baseline,remote_tables=None,selected_tables=request.get('selected_tables'),
            retry_of=request.get('retry_of'),retry_units=request.get('retry_units'))
        if not execution_enabled:
            plan['unsupported'].append({'table':None,'reason':'采集入口尚未启用；当前仅安装每日展示，不启动旧采集器'})
            for operation in plan['operations']:
                operation['executable'] = False
            plan['plan_digest'] = plan_digest(plan)
        return plan

    def validate(plan,*,now):
        if not execution_enabled:
            raise ValueError('采集入口尚未启用；需要完成定时批次兼容接入')
        from warehouse_update import validate_plan
        _,registry,baseline = snapshot()
        validate_plan(plan,current_baseline=baseline,current_registry=registry)

    def registry():
        return snapshot(scan_mode='none')[1]

    def process_probe(pid,created_at):
        from warehouse_sync_runtime import probe_process
        return probe_process(pid,created_at)

    class Launcher(RunLogs):
        def __call__(self,plan,run_id,*,entry):
            if not execution_enabled:
                raise ValueError('采集入口尚未启用；禁止启动旧采集器')
            from warehouse_sync_runtime import process_identity
            source = plan.get('source')
            if source not in {'tdx','tushare'} or entry!='manual' or not re.fullmatch(r'[a-f0-9]{32}',run_id):
                raise ValueError('采集启动范围无效')
            resource = data/source
            if Path(plan.get('resource','')).resolve()!=resource.resolve():
                raise ValueError('采集资源与本机数据仓不一致')
            digest = plan.get('plan_digest','')
            if not re.fullmatch(r'[a-f0-9]{64}',digest):
                raise ValueError('计划摘要无效')
            validate(plan,now=clock())
            folder = data/'warehouse_plans'
            folder.mkdir(exist_ok=True)
            target = folder/(digest+'.json')
            encoded = json.dumps(plan,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')
            if target.exists():
                if target.read_bytes()!=encoded:
                    raise ValueError('计划文件冲突')
            else:
                temporary = folder/(digest+'.'+run_id+'.tmp')
                with temporary.open('xb') as stream:
                    stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary,target)
            self.folder.mkdir(exist_ok=True)
            command = [sys.executable,'-X','utf8','-B',str(qbt/(source+'_sync.py')),
                '--entry',entry,'--run-id',run_id,'--plan-id',digest]
            if source=='tushare':
                command += ['--incremental','--market',plan['market']]
            else:
                command += ['--tables',','.join(plan['selected_tables'])]
            process = None
            try:
                with (self.folder/(run_id+'.log')).open('ab') as log:
                    process = subprocess.Popen(command,cwd=qbt,stdout=log,stderr=log,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except Exception:
                if process is None:
                    raise
                return {'pid':process.pid,'created_at':None}
            try:
                created_at = process_identity(process.pid)
            except Exception:
                created_at = None
            return {'pid':process.pid,'created_at':created_at}

    def daily_view(selected,runs):
        from warehouse_update import build_daily_view, metadata_digest, resolve_target
        from warehouse_read_guard import read_resources
        from data_integrity import verify_targets
        catalog,reg,baseline = snapshot(scan_mode='qualified')
        reference = datetime.fromtimestamp(clock(),timezone.utc)
        targets = {}
        for row in reg['physical_tables']:
            for table in row['physical_tables']:
                target = resolve_target(table,date=selected,now=reference,calendars=reg.get('calendars',{}),policy=row)
                target['verification_rule'] = copy.deepcopy(row.get('verification_rule'))
                targets[table] = target
        reader = None
        # 不篡改datamart模块全局路径；只有已绑定当前生产根的读层才可核验查询。
        import datamart
        if (Path(datamart.CATALOG).resolve()==(data/'catalog.json').resolve()
                and Path(datamart.tdxr.OUT).resolve()==(data/'tdx').resolve()
                and Path(datamart.tdxr.INFO_DB).resolve()==(data/'tdx'/'tdx_info.db').resolve()
                and Path(datamart.tdxr.MANIFEST).resolve()==(data/'tdx'/'_manifest.json').resolve()):
            class BoundReader:
                root = data
                def read(self,*args,**kwargs):
                    frame = datamart.read(*args,**kwargs)
                    entry = catalog['tables'].get(args[0],{})
                    code = entry.get('code_col')
                    if code and code!='ts_code' and code not in frame.columns and 'ts_code' in frame.columns:
                        frame = frame.rename(columns={'ts_code':code})
                    return frame
            reader = BoundReader()
        resource_paths = [item.get('manifest') for item in catalog['tables'].values() if item.get('manifest')]
        resources = [Path(p).parent for p in resource_paths]
        resources += [Path(item['store']).parent for item in catalog['tables'].values()]
        for target in targets.values():
            rule = target.get('verification_rule') or {}
            for source in (rule.get('source'),(rule.get('reconcile') or {}).get('source')):
                if source in {'tdx','tushare'}:
                    resources.append(data/source)
        with read_resources(resources):
            checked = verify_targets(data,list(targets.values()),reader=reader)
        verification, known_receipts = {}, {}
        for run in runs:
            cursor = 0
            while True:
                try:
                    page = store.get_run(run['run_id'],after_seq=cursor,limit=500)
                except KeyError:
                    break
                for event in page['receipts']:
                    receipt = event['payload']
                    unit = next((unit['plan'] for unit in page['units']
                                 if unit['plan']['unit_id']==receipt['unit_id']), None)
                    desired = targets.get(receipt['table'],{}).get('target')
                    if (receipt['result_kind'] in {'authorized_data','source_empty'} and unit
                            and isinstance(desired,str) and isinstance(unit.get('target'),str)
                            and desired.replace('-','')==unit['target'].replace('-','')):
                        known_receipts[receipt['receipt_id']] = receipt
                if not page['has_more']:
                    break
                cursor = page['next_seq']
        for row in reg['physical_tables']:
            members = row['physical_tables']
            rule = row.get('verification_rule') or {}
            receipt_ids = rule.get('receipt_ids')
            if (not isinstance(receipt_ids,list) or not receipt_ids
                    or any(rid not in known_receipts or known_receipts[rid]['table'] not in members for rid in receipt_ids)
                    or any(not row.get(key) or known_receipts[rid].get(key)!=row[key]
                           for rid in receipt_ids if rid in known_receipts for key in ('provider','channel','source_id'))
                    or any(checked[t]['status']=='unknown' for t in members)):
                continue
            statuses = {checked[t]['status'] for t in members}
            status = 'passed' if statuses=={'passed'} else 'missing' if statuses=={'missing'} else 'partial'
            verification[row['asset_id']] = dict(date=selected,status=status,
                target=targets[members[0]]['target'] if len(members)==1 else {t:targets[t]['target'] for t in members},
                registry_digest=metadata_digest(reg),baseline_digest=metadata_digest(baseline),
                receipt_ids=receipt_ids,rule_id=rule.get('rule_id'),verified_at=reference.isoformat(),
                valid_until=(reference+timedelta(seconds=60)).isoformat(),members={t:checked[t] for t in members})
        # 数据或资格在核验中变化，不能将结果绑定核验前基线。
        _,after_reg,after_base = snapshot(scan_mode='qualified')
        if after_reg!=reg or after_base!=baseline:
            verification = {}
        result = build_daily_view(selected,runs=runs,assets=dict(registry=reg,targets=targets,
            now=reference,baseline_digest=metadata_digest(baseline)),verification=verification)
        result['target_verification'] = checked
        result['as_of'] = reference.isoformat()
        result['read_only'] = False
        result['execution_enabled'] = bool(execution_enabled)
        result['execution_reason'] = None if execution_enabled else '采集入口尚未启用；定时批次仍使用原入口'
        for asset in result['assets']:
            if not execution_enabled:
                asset['update_enabled'] = False
            tables = set(asset['physical_tables'])
            asset['latest_receipts'] = [receipt for run in runs for receipt in run.get('receipts',[])
                if receipt['payload']['table'] in tables]
        return result

    return dict(store=store,planner=PlanService(preview,validate),registry=registry,
                launcher=Launcher(data/'warehouse_run_logs'),daily_view=daily_view,
                process_probe=process_probe,clock=clock)
