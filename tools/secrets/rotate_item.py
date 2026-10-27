# -*- coding: utf-8 -*-
"""R2/R4/R7 凭据本地轮换引导（A3 · 2026-10-27）

定位：外部平台重置由老田完成；本工具只做**本地写入 + 加密 + 校验**，
      让每项从「找文件/改格式/漏加密」缩成一条命令。

安全红线：
  · 新值只从 getpass 读；不回显、不进 shell 历史、不落日志；
  · 只打印 len/sha8 指纹；旧值备份到同目录 *.bak-<ts>（离线保留 24h，勿外发）；
  · 不登录外部平台；不重启服务；不做 git 操作。

用法（先干跑）：
  C:\\项目\\py312\\python.exe tools/secrets/rotate_item.py --list
  C:\\项目\\py312\\python.exe tools/secrets/rotate_item.py <preset> --dry-run
  C:\\项目\\py312\\python.exe tools/secrets/rotate_item.py <preset>
  # 自定义目标：
  ... rotate_item.py custom --target <path> --mode text|env|json --key <KEY>

内置 preset：
  r2-llm        R2 LLM sk- Key      -> _vault/secrets/deepseek_api_key.txt (text, 需 vault 加密)
  r4-feishu     R4 飞书 App Secret  -> ~/.lark-cli/config.json apps[0].appSecret (json)
  r7-telegram   R7 Telegram         -> zhice .env.local TELEGRAM_BOT_TOKEN (env)
  r7-wxpusher   R7 WxPusher         -> zhice .env.local 3 keys (env)
  r7-finnhub    R7 Finnhub          -> zhice .env.local FINNHUB_TOKEN (env)
  r7-jev        R7 飞书 Base        -> _vault/secrets/news_config_private.json jev_base_token (json, 需 vault 加密)
"""
from __future__ import annotations
from typing import NoReturn

import argparse
import getpass
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


WORKSPACE = _workspace()
VAULT = WORKSPACE / "_vault" / "secrets"
VAULT_PY = REPO / "tools" / "secrets" / "vault.py"
ZHICE_ENV = Path(r"C:\项目\zhice-os-v2-windows-20260920\code\.env.local")
LARK_CFG = Path.home() / ".lark-cli" / "config.json"

PRESETS = {
    "r2-llm": {
        "label": "R2 LLM sk- Key",
        "target": VAULT / "deepseek_api_key.txt",
        "mode": "text",
        "keys": ["value"],
        "vault": True,
        "verify": "旧 key 401/新 key 跑一次 llm-deepseek 验签；常驻进程重启后生效",
    },
    "r4-feishu": {
        "label": "R4 飞书 App Secret",
        "target": LARK_CFG,
        "mode": "json",
        "keys": ["apps[0].appSecret"],
        "vault": False,
        "verify": "重启 feishu bot 并收发一条；旧 secret 应 401",
    },
    "r7-telegram": {
        "label": "R7 Telegram Bot Token",
        "target": ZHICE_ENV,
        "mode": "env",
        "keys": ["TELEGRAM_BOT_TOKEN"],
        "vault": False,
        "verify": "发一条测试消息（torch 采集器下次启动生效）",
    },
    "r7-wxpusher": {
        "label": "R7 WxPusher 三值",
        "target": ZHICE_ENV,
        "mode": "env",
        "keys": ["WXPUSHER_APP_TOKEN", "WXPUSHER_APP_ID", "WXPUSHER_APP_KEY"],
        "vault": False,
        "verify": "一条测试推送；回调 URL 同步",
    },
    "r7-finnhub": {
        "label": "R7 Finnhub Token",
        "target": ZHICE_ENV,
        "mode": "env",
        "keys": ["FINNHUB_TOKEN"],
        "vault": False,
        "verify": "一次行情调用",
    },
    "r7-jev": {
        "label": "R7 飞书 Base token",
        "target": VAULT / "news_config_private.json",
        "mode": "json",
        "keys": ["jev_base_token"],
        "vault": True,
        "verify": "JEV Base 增量写入一次",
    },
}


def _fail(msg: str) -> "NoReturn":
    print("[rotate-item] FAIL: %s" % msg)
    print("[rollback] 用同目录 *.bak-<ts> 备份恢复目标文件后重跑；"
          "若已加密，恢复 _vault/secrets 明文后再 encrypt。")
    raise SystemExit(2)


def _mask(value: str) -> str:
    return "len=%d sha8=%s" % (len(value), hashlib.sha256(value.encode()).hexdigest()[:8])


def _read_text(path: Path) -> tuple[str, bool]:
    raw = path.read_bytes()
    crlf = b"\r\n" in raw[:4000]
    return raw.decode("utf-8-sig"), crlf


def _write_text(path: Path, text: str, crlf: bool) -> None:
    data = text.replace("\n", "\r\n") if crlf else text
    path.write_bytes(data.encode("utf-8"))


def _backup(path: Path) -> Path:
    backup = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(path, backup)
    return backup


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


def _apply(path: Path, mode: str, key: str, value: str) -> None:
    if mode == "text":
        path.write_text(value, encoding="utf-8")
        return
    text, crlf = _read_text(path) if path.is_file() else ("", False)
    if mode == "env":
        lines = text.splitlines()
        prefix = key + "="
        replaced = False
        for i, ln in enumerate(lines):
            if ln.startswith(prefix) or (ln.startswith(key) and "=" in ln and ln.split("=", 1)[0].strip() == key):
                lines[i] = prefix + value
                replaced = True
                break
        if not replaced:
            lines.append(prefix + value)
        out = "\n".join(lines) + ("\n" if text.endswith("\n") or True else "")
        _write_text(path, out, crlf)
    elif mode == "json":
        obj = json.loads(text) if text.strip() else {}
        _set_json_pointer(obj, key, value)
        out = json.dumps(obj, ensure_ascii=False, indent=1)
        _write_text(path, out + "\n", crlf)
    else:
        _fail("未知 mode: %s" % mode)


def _run_vault() -> None:
    cmd = [sys.executable, "-X", "utf8", str(VAULT_PY), "encrypt", str(VAULT), "--recursive"]
    print("[run] vault encrypt --recursive")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if r.returncode != 0:
        print((r.stdout or "") + (r.stderr or ""))
        _fail("vault encrypt 失败")
    print("[run] vault verify --json")
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
        _fail("vault verify 失败")


def _resolve(args) -> dict:
    if args.preset == "custom":
        if not (args.target and args.mode and args.key):
            _fail("custom 需要 --target/--mode/--key")
        return {"label": "custom", "target": Path(args.target), "mode": args.mode,
                "keys": [args.key], "vault": False, "verify": "按目标消费方自验"}
    p = dict(PRESETS[args.preset])
    p["target"] = Path(p["target"])
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R2/R4/R7 凭据本地轮换引导")
    ap.add_argument("preset", nargs="?", choices=sorted(PRESETS) + ["custom"])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--target")
    ap.add_argument("--mode", choices=["text", "env", "json"])
    ap.add_argument("--key")
    args = ap.parse_args(argv)

    if args.list or not args.preset:
        for name, p in sorted(PRESETS.items()):
            print("%-12s %-22s %s (%s%s)" % (name, p["label"], p["target"], p["mode"],
                                             ", vault" if p["vault"] else ""))
        return 0

    spec = _resolve(args)
    target: Path = spec["target"]
    print("[preset] %s  target=%s mode=%s vault=%s" % (spec["label"], target, spec["mode"], spec["vault"]))
    print("[target] exists=%s" % target.is_file())
    for k in spec["keys"]:
        label = k if spec["mode"] != "text" else "token"
        print("[key] %s" % label)
    if spec["mode"] == "env" and not target.is_file():
        _fail("env 目标不存在: %s" % target)
    if spec["mode"] == "json" and target.is_file():
        try:
            json.loads(_read_text(target)[0])
        except ValueError:
            _fail("json 目标解析失败: %s" % target)

    if args.dry_run:
        print("[dry-run] steps: backup -> write %d value(s) -> %s"
              % (len(spec["keys"]), "vault encrypt+verify" if spec["vault"] else "no vault (outside)"))
        print("[dry-run] 平台重置完成后去掉 --dry-run 执行；不会打印任何值")
        return 0

    values = []
    for k in spec["keys"]:
        label = k if spec["mode"] != "text" else "new token"
        v = getpass.getpass("%s (不回显): " % label).strip()
        if not v:
            _fail("空输入: %s" % label)
        if any(ch.isspace() for ch in v):
            _fail("%s 含空白字符" % label)
        confirm = getpass.getpass("%s 再次输入: " % label).strip()
        if v != confirm:
            _fail("%s 两次输入不一致（未写入）" % label)
        values.append(v)

    target.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(target) if target.is_file() else None
    if backup:
        print("[ok] 旧值快照:", backup, "(离线保留 24h，勿外发)")
    for k, v in zip(spec["keys"], values):
        _apply(target, spec["mode"], k, v)
        print("[ok] 已写 %s: %s" % (k, _mask(v)))

    if spec["vault"]:
        _run_vault()
    else:
        print("[note] 目标在保险库外（%s），不执行 vault 加密；请确认该文件权限与是否应迁入 _vault。" % target)

    print("[done] %s 本地写入完成。验证：%s" % (spec["label"], spec["verify"]))
    print("[next] 回填 ROTATION_FILLBACK_TEMPLATE.md：完成时间 + 上述 len/sha8（不要写值）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
