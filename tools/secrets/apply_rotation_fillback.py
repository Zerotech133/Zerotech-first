# -*- coding: utf-8 -*-
"""批量应用「轮换回填表」到本地目标（A3 · R1/R2/R4/R7）

背景：平台重置后新值由老田写入 `_vault/secrets/ROTATION_FILLBACK.env`
（现有既有机制，见同目录 ROTATION_FILLBACK_TEMPLATE.md）；本工具把非空项
批量写到本地目标，保险库内的目标自动 encrypt+verify，MCP token 变更后自动 render。

安全：
  · 只读 fillback 文件，不打印任何值；输出仅 key/len/sha8/目标；
  · 空值跳过；已是同值则跳过（幂等）；
  · 写前对每个目标做 *.bak-<ts> 备份；失败即停并给出恢复包路径；
  · 不登录外部平台、不改平台、不重启服务。

用法：
  C:\\项目\\py312\\python.exe tools/secrets/apply_rotation_fillback.py --dry-run
  C:\\项目\\py312\\python.exe tools/secrets/apply_rotation_fillback.py --apply
  ... --file <其他 fillback 路径> --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]


def _workspace() -> Path:
    for anc in [REPO] + list(REPO.parents):
        if (anc / "_vault" / "secrets").is_dir():
            return anc
    return REPO.parent


WS = _workspace()
VAULT = WS / "_vault" / "secrets"
DEFAULT_FILLBACK = VAULT / "ROTATION_FILLBACK.env"
VAULT_PY = REPO / "tools" / "secrets" / "vault.py"
RENDER_PY = REPO / "tools" / "secrets" / "render_mcp.py"
ZHICE_ENV = Path(r"C:\项目\zhice-os-v2-windows-20260920\code\.env.local")
LARK_CFG = Path.home() / ".lark-cli" / "config.json"
QUANT_ENV = REPO / "quant_backtest" / ".env.local"

# key -> (target, mode, target_key, vault_encrypt?, note)
MAP = {
    "R1_TUSHARE_TOKEN": (QUANT_ENV, "env", "TUSHARE_TOKEN", False, "quant 采集链"),
    "R1_TUSHARE_MCP_TOKEN": (VAULT / "mcp" / "tushare_mcp_token", "text", "", True, "MCP token（随后 render_mcp）"),
    "R2_LLM_API_KEY": (VAULT / "deepseek_api_key.txt", "text", "", True, "deepseek 引擎"),
    "R4_FEISHU_APP_SECRET": (LARK_CFG, "json", "apps[0].appSecret", False, "飞书 bot/lark-cli"),
    "R7_TELEGRAM_BOT_TOKEN": (ZHICE_ENV, "env", "TELEGRAM_BOT_TOKEN", False, "torch 采集"),
    "R7_WXPUSHER_APP_TOKEN": (ZHICE_ENV, "env", "WXPUSHER_APP_TOKEN", False, "WxPusher 推送"),
    "R7_WXPUSHER_WEBHOOK_PATH_KEY": (ZHICE_ENV, "env", "WXPUSHER_WEBHOOK_PATH_KEY", False, "WxPusher 回调"),
    "R7_FINNHUB_TOKEN": (ZHICE_ENV, "env", "FINNHUB_TOKEN", False, "行情采集"),
    "R7_JEV_BASE_TOKEN": (VAULT / "news_config_private.json", "json", "jev_base_token", True, "JEV Base"),
}
SKIP_NON_SECRET = {"R2_LLM_BASE_URL", "R4_FEISHU_APP_ID"}
UNMAPPED_HINT = {
    "R2_ZHIPU_API_KEY": "留待手工：消费方未定位",
    "R7_CAINIAO_CLIENT_SECRET": "留待手工：菜鸟 OAuth 私有区",
    "R7_OSINT_PASSWORD": "留待手工：OSINT 私有区",
}


def _mask(v: str) -> str:
    return "len=%d sha8=%s" % (len(v), hashlib.sha256(v.encode()).hexdigest()[:8])


def _read_fillback(path: Path) -> dict:
    out = {}
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _target_hash(target: Path, mode: str, tkey: str) -> str | None:
    if not target.is_file():
        return None
    if mode == "text":
        return hashlib.sha256(target.read_bytes()).hexdigest()[:8]
    text = target.read_text(encoding="utf-8", errors="replace")
    if mode == "env":
        for ln in text.splitlines():
            if ln.split("=", 1)[0].strip() == tkey and "=" in ln:
                return hashlib.sha256(ln.split("=", 1)[1].strip().encode()).hexdigest()[:8]
        return None
    if mode == "json":
        try:
            obj = json.loads(text)
        except ValueError:
            return None
        cur = obj
        try:
            for seg in tkey.replace("[", ".").replace("]", "").split("."):
                cur = cur[int(seg)] if seg.isdigit() else cur[seg]
        except (KeyError, IndexError, TypeError):
            return None
        return hashlib.sha256(str(cur).encode()).hexdigest()[:8]
    return None


def _backup(target: Path) -> Path | None:
    if not target.is_file():
        return None
    b = target.with_name(target.name + ".bak-" + time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(target, b)
    return b


def _set_json_pointer(obj, pointer: str, value: str):
    parts = []
    for seg in pointer.split("."):
        while "[" in seg:
            name, rest = seg.split("[", 1)
            if name:
                parts.append(name)
            idx, seg = rest.split("]", 1)
            parts.append(int(idx))
        if seg:
            parts.append(seg)
    cur = obj
    for i, part in enumerate(parts[:-1]):
        nxt = parts[i + 1]
        if isinstance(part, int):
            while len(cur) <= part:
                cur.append({} if not isinstance(nxt, int) else [])
            cur = cur[part]
        else:
            if part not in cur:
                cur[part] = [] if isinstance(nxt, int) else {}
            cur = cur[part]
    last = parts[-1]
    if isinstance(last, int):
        while len(cur) <= last:
            cur.append("")
        cur[last] = value
    else:
        cur[last] = value


def _apply(target: Path, mode: str, tkey: str, value: str) -> None:
    if mode == "text":
        target.write_text(value, encoding="utf-8")
        return
    raw = target.read_bytes()
    crlf = b"\r\n" in raw[:2000]
    text = raw.decode("utf-8-sig")
    if mode == "env":
        lines = text.splitlines()
        prefix = tkey + "="
        done = False
        for i, ln in enumerate(lines):
            head = ln.split("=", 1)[0].strip() if "=" in ln else ""
            if head == tkey:
                lines[i] = prefix + value
                done = True
                break
        if not done:
            lines.append(prefix + value)
        out = "\n".join(lines) + "\n"
        data = out.replace("\n", "\r\n") if crlf else out
        target.write_bytes(data.encode("utf-8"))
    elif mode == "json":
        obj = json.loads(text) if text.strip() else {}
        _set_json_pointer(obj, tkey, value)
        out = json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
        data = out.replace("\n", "\r\n") if crlf else out
        target.write_bytes(data.encode("utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="批量应用 ROTATION_FILLBACK.env 到本地目标")
    ap.add_argument("--file", default=str(DEFAULT_FILLBACK))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if not args.dry_run and not args.apply:
        ap.error("必须显式 --dry-run 或 --apply")

    path = Path(args.file)
    if not path.is_file():
        print("[fillback] 文件不存在: %s" % path)
        return 2
    values = _read_fillback(path)
    print("[fillback] %s keys=%d" % (path, len(values)))
    plan, skipped, missing = [], [], []
    for key, (target, mode, tkey, need_vault, note) in MAP.items():
        val = values.get(key, "")
        if not val:
            missing.append(key)
            continue
        print("[item] %-30s %s target=%s note=%s" % (key, _mask(val), target, note))
        cur = _target_hash(target, mode, tkey)
        if cur is not None and cur == hashlib.sha256(val.encode()).hexdigest()[:8]:
            skipped.append(key)
            print("       -> 已是同值，跳过")
            continue
        plan.append((key, target, mode, tkey, val, need_vault))
    for k in UNMAPPED_HINT:
        if values.get(k):
            print("[warn] %s 非空但未映射：%s（需手工）" % (k, UNMAPPED_HINT[k]))
    print("[summary] to_apply=%d skipped=%d empty=%d" % (len(plan), len(skipped), len(missing)))
    if missing:
        print("[empty] 未填/为空: " + ", ".join(missing))
    if args.dry_run:
        print("[dry-run] 未写入。补齐 fillback 后执行 --apply")
        return 0
    if not plan:
        print("[apply] 无可应用项（全空或已是同值）")
        return 0

    backups = []
    for key, target, mode, tkey, val, need_vault in plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        b = _backup(target)
        if b:
            backups.append(b)
        _apply(target, mode, tkey, val)
        print("[ok] %-30s -> %s (%s)" % (key, target, _mask(val)))

    vault_targets = [p for (k, p, m, t, v, nv) in plan if nv]
    if vault_targets:
        print("[run] vault encrypt --recursive")
        r = subprocess.run([sys.executable, "-X", "utf8", str(VAULT_PY), "encrypt", str(VAULT), "--recursive"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        print("[encrypt] rc=%s" % r.returncode)
        if r.returncode != 0:
            print((r.stdout or "") + (r.stderr or ""))
            print("[rollback] 备份: " + ", ".join(str(x) for x in backups))
            return 2
        r = subprocess.run([sys.executable, "-X", "utf8", str(VAULT_PY), "verify", "--json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        tail = (r.stdout or "").strip().splitlines()
        try:
            data = json.loads(tail[-1])
        except (ValueError, IndexError):
            data = {}
        print("[verify] rc=%s ok=%s failures=%s file_count=%s"
              % (r.returncode, data.get("ok"), data.get("failures"), data.get("file_count")))
        if r.returncode != 0:
            print("[rollback] 备份: " + ", ".join(str(x) for x in backups))
            return 2
    if any(k == "R1_TUSHARE_MCP_TOKEN" for (k, *_rest) in plan):
        print("[run] render_mcp + --check")
        for cmd in ([sys.executable, "-X", "utf8", str(RENDER_PY)],
                    [sys.executable, "-X", "utf8", str(RENDER_PY), "--check"]):
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            print("[render] rc=%s %s" % (r.returncode, tail[-1] if tail else ""))
            if r.returncode != 0:
                print("[rollback] 备份: " + ", ".join(str(x) for x in backups))
                return 2
    print("[done] 已应用 %d 项；备份: %s" % (len(plan), ", ".join(str(x) for x in backups) or "无"))
    print("[next] 按命令卡做真实调用验证；重启对应消费方；回填模板（时间+指纹）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
