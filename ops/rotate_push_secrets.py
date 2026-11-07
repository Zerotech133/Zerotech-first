#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""推送密钥泄露重生成（rotate）工具（K-PUSH-012 拍板档 §四 落地件）。

用途：portal/work/push_secrets.json 任一密钥疑似泄露时重生成并换血 8097。

用法：
  python ops/rotate_push_secrets.py                          # 全部密钥重生成+换血
  python ops/rotate_push_secrets.py push_admin_key push_hub_key   # 仅轮换指定键
  python ops/rotate_push_secrets.py --no-restart             # 只换钥不触发守护重启
  python ops/rotate_push_secrets.py --dry-run                # 预演：不写盘不重启

纪律（K-PUSH-012）：
  - 密钥全文只存 push_secrets.json（work/ gitignore 不入库），泄露即换
  - 审计日志 secrets_rotate.log 只记尾号 6 位，不落全文
  - 重生成后默认 marker 换血 8097（守护任务重拉即读新钥）
  - 8097 与生产者各有 60s 文件缓存：换钥后短窗内旧钥调用 403，随后自然收敛
  - _doc / decision_execute 为配置字段不轮换（决策执行开关状态保持）
"""

import argparse
import json
import os
import secrets as _pysecrets
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET_FILE = os.path.join(HERE, "portal", "work", "push_secrets.json")
AUDIT_LOG = os.path.join(HERE, "portal", "work", "secrets_rotate.log")
MARKER = r"C:\Temp\push_admin_restart.marker"
GUARD_TASK = "AI工作站推送中台守护8097"
PORT = 8097
# 回环禁代理（2026-11-07 批 5）：本机服务的请求不走系统代理，实现见 tools/safe_urlopen.py
import sys as _sys
from pathlib import Path as _Path

for _base in _Path(__file__).resolve().parents:
    for _cand in (_base, _base / "tools"):
        if (_cand / "safe_urlopen.py").exists():
            _sys.path.insert(0, str(_cand))
            break
    else:
        continue
    break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

KEY_SPECS = {
    "push_admin_key": "pk_admin_",
    "push_hub_key": "pk_hub_",
    "decision_key_admin_ui": "pk_dec_admin_",
    "decision_key_client": "pk_dec_client_",
    "decision_key_hermes": "pk_dec_hermes_",
}


def _tail(v):
    return (v or "")[-6:]


def _alive():
    try:
        safe_urlopen("http://127.0.0.1:%d/" % PORT, timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


def main():
    ap = argparse.ArgumentParser(description="推送密钥泄露重生成")
    ap.add_argument("keys", nargs="*", help="仅轮换这些字段（缺省全部）")
    ap.add_argument("--no-restart", action="store_true", help="不触发 8097 换血")
    ap.add_argument("--dry-run", action="store_true", help="预演：不写盘不重启")
    a = ap.parse_args()

    if not os.path.exists(SECRET_FILE):
        print("[err] 密钥文件不存在：%s" % SECRET_FILE)
        return 2
    with open(SECRET_FILE, encoding="utf-8") as f:
        data = json.load(f)

    bad = [k for k in a.keys if k not in KEY_SPECS]
    if bad:
        print("[err] 未知字段：%s（可选：%s）" % (",".join(bad), ",".join(KEY_SPECS)))
        return 2
    targets = a.keys or list(KEY_SPECS)

    plan = []
    for k in targets:
        new = KEY_SPECS[k] + _pysecrets.token_hex(24)
        plan.append((k, str(data.get(k) or ""), new))
        data[k] = new

    print("== 轮换计划（%d 项） ==" % len(plan))
    for k, old, new in plan:
        print("  %-24s …%s -> …%s" % (k, _tail(old) or "------", _tail(new)))
    if a.dry_run:
        print("[dry-run] 未落盘未重启")
        return 0

    tmp = SECRET_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SECRET_FILE)
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        for k, old, new in plan:
            f.write("%s rotate %-24s …%s->…%s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                k, _tail(old), _tail(new)))
    print("[ok] 新钥原子写入 %s（审计尾号见 secrets_rotate.log）" % SECRET_FILE)

    if a.no_restart:
        print("[hint] 未换血：8097/生产者 60s 缓存后读新钥，或手动触发守护任务")
    else:
        open(MARKER, "w").close()
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Start-ScheduledTask -TaskName '%s'" % GUARD_TASK],
                       capture_output=True)
        print("[ok] marker 已落 + 守护任务 %s 已触发，等待重拉…" % GUARD_TASK)
        deadline = time.time() + 60
        ok = False
        while time.time() < deadline:
            time.sleep(3)
            if _alive():
                ok = True
                break
        print("[ok] 8097 已重拉在线" if ok else "[warn] 60s 未探活，人工检查守护任务")

    print("== 新钥尾号 ==")
    for k, _, new in plan:
        print("  %-24s …%s" % (k, _tail(new)))
    print("[hint] 管理页输入 push_admin_key 全文（见 push_secrets.json）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
