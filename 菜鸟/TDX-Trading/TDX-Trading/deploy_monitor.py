#!/usr/bin/env python3
"""部署监控系统到 192.168.203.48（包化重构版）。

2026-08-19 起 tdx_monitor_system.py 拆为 monitor/ 包 + templates/index.html，
部署从「单文件 sftp.put」改为「tar 打包整个 monitor/ + 门面 + 模板推送」。
包含备份、py_compile 语法护栏（失败自动回滚）、服务重启、健康验证。

用法:
    python deploy_monitor.py           # 正常部署
"""
import hashlib
import os
import sys
import time
import subprocess

import paramiko

HOST = '192.168.203.48'
USER = 'root'
REMOTE_DIR = '/home/xundao/TDX-Trading'
LOCAL_ROOT = r'E:\老田的AI助手\菜鸟\TDX-Trading\TDX-Trading'


def _load_pwd():
    """SSH 口令不落代码：优先环境变量 TDX_DEPLOY_PWD，其次同目录 deploy_monitor.pwd（不入库）。"""
    pwd = os.environ.get('TDX_DEPLOY_PWD')
    if pwd:
        return pwd
    pwd_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deploy_monitor.pwd')
    if os.path.exists(pwd_file):
        with open(pwd_file, encoding='utf-8') as f:
            pwd = f.read().strip()
        if pwd:
            return pwd
    sys.exit('!! 缺 SSH 口令：设 TDX_DEPLOY_PWD 环境变量，或在本目录放置 deploy_monitor.pwd（该文件已被 .gitignore 忽略）')


PWD = _load_pwd()

# 需要部署的文件清单（相对 LOCAL_ROOT）
DEPLOY_FILES = [
    'tdx_monitor_system.py',          # 门面入口（systemd 跑的就是它）
    'monitor/__init__.py',
    'monitor/config.py',
    'monitor/common.py',
    'monitor/cond_utils.py',
    'monitor/engine.py',
    'monitor/server.py',
    'monitor/ui.py',
    'monitor/models/__init__.py',
    'monitor/models/base.py',
    'monitor/models/cond_model.py',
    'monitor/models/metric_model.py',
    'templates/index.html',           # 简化预警首页（2026-10-14 重写）
    'templates/advanced.html',        # 旧完整功能页（原 index.html 平移）
]

# 2026-10-14 简化预警页改造涉及文件：这些文件本地与线上有差异属预期。
# 其余文件若指纹不一致，说明本地包落后于线上（可能错过热修复），部署中止。
EXPECTED_CHANGED = {
    'monitor/config.py', 'monitor/engine.py', 'monitor/server.py',
    'monitor/ui.py', 'templates/index.html', 'templates/advanced.html',
}

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())


def run(cmd, check=True, quiet=False):
    _, so, se = cli.exec_command(cmd, timeout=120)
    out = so.read().decode('utf-8', 'replace')
    err = se.read().decode('utf-8', 'replace')
    code = so.channel.recv_exit_status()
    combined = out + (('\n[stderr] ' + err) if err.strip() else '')
    if not quiet:
        print(f'$ {cmd}\n{combined}\n[exit={code}]')
    if check and code != 0:
        print('!! 命令失败，中止')
        sys.exit(1)
    return code, combined


def main():
    # 0) 本地语法护栏：所有 py 文件必须先过 py_compile，坏文件不出门
    print('== 0. 本地语法护栏 ==')
    for rel in DEPLOY_FILES:
        if not rel.endswith('.py'):
            continue
        local = os.path.join(LOCAL_ROOT, rel)
        if not os.path.exists(local):
            print(f'!! 本地缺文件: {local}')
            sys.exit(1)
        r = subprocess.run([sys.executable, '-m', 'py_compile', local],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f'!! 本地编译失败 {rel}:\n{r.stderr}')
            sys.exit(1)
    print('本地 py_compile 全部通过\n')

    print(f'连接 {USER}@{HOST} ...')
    cli.connect(HOST, username=USER, password=PWD, timeout=15)
    print('已连接\n')

    # 0.5) 指纹安全检查：非预期变更文件若本地≠线上，说明本地包过旧，中止
    print('== 0.5 指纹安全检查 ==')
    _, so, _ = cli.exec_command(
        f'cd {REMOTE_DIR} && md5sum ' + ' '.join(DEPLOY_FILES) + ' 2>&1')
    remote_md5 = {}
    for line in so.read().decode('utf-8', 'replace').strip().splitlines():
        parts = line.split()
        if len(parts) == 2:
            remote_md5[parts[1]] = parts[0]
    stale = []
    for rel in DEPLOY_FILES:
        local = os.path.join(LOCAL_ROOT, rel)
        if not os.path.exists(local) or rel not in remote_md5:
            continue
        if rel in EXPECTED_CHANGED:
            continue
        lm = hashlib.md5(open(local, 'rb').read()).hexdigest()
        if lm != remote_md5[rel]:
            stale.append(rel)
    if stale:
        print('!! 以下文件本地与线上不一致且不在本轮预期变更内（本地包可能过旧）：')
        for rel in stale:
            print('  -', rel)
        print('!! 请先与线上核对/拉取最新版本后重试。部署中止。')
        cli.close()
        sys.exit(1)
    print('指纹检查通过\n')

    # 1) 远端备份（monitor 包 + 门面 + 模板，有则备）
    ts = time.strftime('%Y%m%d_%H%M%S')
    print('== 1. 备份 ==')
    run(f'cd {REMOTE_DIR} && '
        f'mkdir -p backup_{ts} && '
        f'cp -p tdx_monitor_system.py backup_{ts}/ 2>/dev/null; '
        f'cp -rp monitor backup_{ts}/ 2>/dev/null; '
        f'cp -rp templates backup_{ts}/ 2>/dev/null; '
        f'ls backup_{ts}/', check=False)
    print()

    # 2) 上传（sftp 逐文件，保留目录结构）
    print('== 2. 上传 ==')
    sftp = cli.open_sftp()
    for rel in DEPLOY_FILES:
        local = os.path.join(LOCAL_ROOT, rel)
        remote = f'{REMOTE_DIR}/{rel}'
        # 确保远端目录存在
        rdir = os.path.dirname(remote)
        try:
            sftp.stat(rdir)
        except FileNotFoundError:
            # 逐级建目录
            parts = rdir.split('/')
            for i in range(2, len(parts) + 1):
                sub = '/'.join(parts[:i])
                try:
                    sftp.stat(sub)
                except FileNotFoundError:
                    sftp.mkdir(sub)
        sftp.put(local, remote)
        print(f'  {rel}  ({os.path.getsize(local)} bytes)')
    sftp.close()
    print('上传完成\n')

    # 3) 远端语法护栏：门面 + monitor 包整体编译（python3.9）
    print('== 3. 远端语法护栏 ==')
    code, out = run(
        f'cd {REMOTE_DIR} && python3 -m py_compile tdx_monitor_system.py '
        f'&& python3 -m compileall -q monitor && echo COMPILE_OK')
    if 'COMPILE_OK' not in out:
        print('!! 远端编译失败，回滚')
        run(f'cd {REMOTE_DIR} && '
            f'cp -p backup_{ts}/tdx_monitor_system.py . 2>/dev/null; '
            f'rm -rf monitor && cp -rp backup_{ts}/monitor . 2>/dev/null; '
            f'rm -rf templates && cp -rp backup_{ts}/templates . 2>/dev/null; echo ROLLED_BACK',
            check=False)
        cli.close()
        sys.exit(1)
    print('远端编译通过\n')

    # 4) 重启（先停服务，pkill 用精确脚本名）
    print('== 4. 重启服务 ==')
    run('systemctl stop tdx.service', check=False)
    run("pkill -f 'python.*tdx_monitor_system.py'", check=False)
    time.sleep(2)
    run('systemctl start tdx.service')
    time.sleep(5)
    run('systemctl is-active tdx.service')

    # 5) 健康验证
    print('== 5. 健康验证 ==')
    run("curl -s -m 8 http://127.0.0.1:8088/api/models | head -c 300", check=False)
    run("curl -s -m 8 http://127.0.0.1:8088/api/qmt/status | head -c 300", check=False)
    run("curl -s -m 8 -o /dev/null -w 'index.html HTTP %{http_code}\\n' http://127.0.0.1:8088/", check=False)
    run('tail -n 10 /home/xundao/TDX-Trading/tdx_service.log', check=False)

    cli.close()
    print('\n✅ 部署完成')


if __name__ == '__main__':
    main()
